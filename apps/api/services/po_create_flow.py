"""The purchase-order HEADER create saga.

Implements the flow settled in the CIS-09 design sessions:

    save draft → snapshot → submit header →
    parse response → link identity → outcome-by-error-type

and, on the outcome branches:

- Daysmart down / auth dead   → 'sync_failed'; the draft survives (that IS
  the outbox guarantee) and the next trigger retries it.
- Timeout                      → NEVER a blind resend. Snapshot-diff:
  exactly one plausible new order → link it; provably nothing landed →
  one resend; anything ambiguous → a human (OPS resolve endpoint).
- Unreadable create response   → quarantine: the order MAY exist in
  Daysmart, so the issues are persisted (payload_context =
  'create_response') and no resend happens without a human.

Header-only is an API boundary, not merely a DaySmart wire detail. Lines are
created later through ``POST /purchase-orders/{po_uuid}/lines`` so their
individual outcomes and DaySmart line ids can be reported honestly.

Every step lands in ``po_sync_attempts``. This module only STAGES database
changes; the caller owns the commit (repo convention). Nothing here raises
for Daysmart-side problems — the outcome is always a CreateFlowResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import models
from schemas.purchase_orders import (
    CreatePurchaseOrderCommandV1,
    build_daysmart_create_payload,
)
from services.daysmart_service import (
    DaysmartAuthFailure,
    DaysmartError,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_snapshot_diff import resolve_snapshot_diff
from services.po_sync_state import assign_daysmart_identity

#: Clock slack around the submission window used by the snapshot-diff —
#: covers clock skew between us and Daysmart's create_at stamping.
_WINDOW_MARGIN = timedelta(seconds=60)


@dataclass(frozen=True)
class CreateFlowResult:
    po: "models.PurchaseOrder"
    #: 'synced' | 'sync_failed' | 'needs_review'
    outcome: str


def create_purchase_order(
    db,
    command: CreatePurchaseOrderCommandV1,
    daysmart,
    *,
    default_ship_to_id: int,
    default_bill_to_id: int,
    created_by_user_id: int | None = None,
) -> CreateFlowResult:
    now = _now_iso()
    po = _stage_draft(db, command, created_by_user_id=created_by_user_id,
                      default_ship_to_id=default_ship_to_id,
                      default_bill_to_id=default_bill_to_id, now=now)
    db.flush()  # PO id needed for attempt/issue rows

    # --- snapshot: without it the timeout probe is impossible, so a failed
    # snapshot degrades to a retryable draft — we never submit blind.
    started = _now_iso()
    try:
        snapshot_page = daysmart.fetch_orders(page=1)
    except DaysmartError as exc:
        _attempt(db, po, step="fetch", outcome=_outcome_for(exc),
                 error=exc, started_at=started)
        po.sync_status = "sync_failed"
        return CreateFlowResult(po=po, outcome="sync_failed")
    snapshot_max = max((o.daysmart_id for o in snapshot_page.orders), default=0)
    window_start = datetime.now(timezone.utc) - _WINDOW_MARGIN

    payload = build_daysmart_create_payload(
        command, default_ship_to_id=default_ship_to_id,
        default_bill_to_id=default_bill_to_id)
    po.sync_status = "submitting"

    outcome = _submit(db, po, daysmart, payload, allow_diff=True,
                      snapshot_max=snapshot_max, window_start=window_start)
    return CreateFlowResult(po=po, outcome=outcome)


#: Last-attempt outcomes after which the first attempt MAY have taken
#: effect in Daysmart — resubmission is not provably safe, so the engine
#: refuses and a human decides (link / retry / cancel).
_MAY_HAVE_LANDED = frozenset({"timeout", "contract_mismatch", "needs_review"})


def resubmit_purchase_order(db, po, daysmart) -> CreateFlowResult:
    """Re-drive the sync of an EXISTING PO — the lifecycle engine.

    Never creates a new local row or a second Daysmart order. Shares the
    create saga's machinery, so a retry behaves exactly like a first
    attempt (snapshot first, diff on timeout, everything logged).

    Callers: the explicit /retry endpoint, and the outcome-aware
    idempotency replay (only for provably-never-landed first attempts).
    Automatic resubmission of old drafts is deliberately NOT a thing —
    a stale draft may duplicate an order an admin already created
    manually, and no machinery can prove otherwise.
    """
    if po.sync_status == "synced":
        raise ValueError(f"PO {po.po_uuid} is already synced — nothing to retry")
    if po.sync_status == "cancelled":
        raise ValueError(f"PO {po.po_uuid} is cancelled — retry is not allowed")

    last = max(po.sync_attempts, key=lambda a: a.id, default=None)
    if last is not None and last.outcome in _MAY_HAVE_LANDED:
        # The order may exist in Daysmart unreadably/unmatched. Touching
        # Daysmart here risks a duplicate; only a human can rule it out.
        return CreateFlowResult(po=po, outcome="needs_review")

    if po.daysmart_id is not None:
        # Header already landed. Lines have their own explicit endpoint and
        # lifecycle; a header retry must never send them as a side effect.
        po.sync_status = "synced"
        return CreateFlowResult(po=po, outcome="synced")

    started = _now_iso()
    try:
        snapshot_page = daysmart.fetch_orders(page=1)
    except DaysmartError as exc:
        _attempt(db, po, step="fetch", outcome=_outcome_for(exc),
                 error=exc, started_at=started)
        po.sync_status = "sync_failed"
        return CreateFlowResult(po=po, outcome="sync_failed")
    snapshot_max = max((o.daysmart_id for o in snapshot_page.orders), default=0)
    window_start = datetime.now(timezone.utc) - _WINDOW_MARGIN

    po.sync_status = "submitting"
    outcome = _submit(db, po, daysmart, _payload_from_po(po), allow_diff=True,
                      snapshot_max=snapshot_max, window_start=window_start)
    return CreateFlowResult(po=po, outcome=outcome)


# ---------------------------------------------------------------------------


def _submit(db, po, daysmart, payload, *, allow_diff, snapshot_max,
            window_start) -> str:
    started = _now_iso()
    try:
        result = daysmart.create_order(payload)
    except DaysmartTimeout as exc:
        _attempt(db, po, step="create_header", outcome="timeout",
                 error=exc, started_at=started)
        if not allow_diff:
            po.sync_status = "sync_failed"
            return "sync_failed"
        return _diff_after_timeout(
            db, po, daysmart, payload,
            snapshot_max=snapshot_max, window_start=window_start)
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, step="create_header", outcome=_outcome_for(exc),
                 error=exc, started_at=started)
        po.sync_status = "sync_failed"
        return "sync_failed"
    except DaysmartError as exc:
        # Malformed response AFTER a POST: the order may exist, unreadably.
        _attempt(db, po, step="create_header", outcome="contract_mismatch",
                 error=exc, started_at=started)
        po.sync_status = "sync_failed"
        return "needs_review"

    if result.order is None:
        # Daysmart answered; we cannot read it. Quarantine with evidence —
        # the order MAY exist, so no resend without a human.
        _attempt(db, po, step="create_header", outcome="contract_mismatch",
                 error_detail="create response failed the contract",
                 started_at=started)
        _persist_issues(db, po, result.issues, context="create_response")
        po.sync_status = "sync_failed"
        return "needs_review"

    _attempt(db, po, step="create_header", outcome="success", started_at=started)
    if result.issues:  # non-blocking drift (e.g. unknown status) — record it
        _persist_issues(db, po, result.issues, context="create_response")
    _link(db, po, result.order.daysmart_id, result.order.index)
    return "synced"


def _diff_after_timeout(db, po, daysmart, payload, *, snapshot_max,
                        window_start) -> str:
    started = _now_iso()
    try:
        diff_page = daysmart.fetch_orders(page=1)
    except DaysmartError as exc:
        _attempt(db, po, step="snapshot_diff", outcome=_outcome_for(exc),
                 error=exc, started_at=started)
        po.sync_status = "sync_failed"
        return "sync_failed"

    verdict = resolve_snapshot_diff(
        snapshot_max_daysmart_id=snapshot_max,
        supplier_daysmart_id=po.supplier_daysmart_id,
        submitted_between=(window_start, datetime.now(timezone.utc) + _WINDOW_MARGIN),
        # Header creation never sends lines. A matched timeout candidate must
        # therefore still be an empty header.
        expected_line_count=0,
        candidates=diff_page.orders,
        page_had_issues=bool(diff_page.issues),
    )

    if verdict.kind == "matched":
        _attempt(db, po, step="snapshot_diff", outcome="success",
                 error_detail=verdict.reason, started_at=started)
        _link(db, po, verdict.order.daysmart_id, verdict.order.index)
        return "synced"

    if verdict.kind == "not_landed":
        _attempt(db, po, step="snapshot_diff", outcome="success",
                 error_detail=verdict.reason, started_at=started)
        # Provably nothing landed → the one safe resend, diff NOT re-armed:
        # a second timeout parks the PO for the next trigger instead of
        # looping fetch/submit against a struggling Daysmart.
        return _submit(db, po, daysmart, payload, allow_diff=False,
                       snapshot_max=snapshot_max, window_start=window_start)

    candidate_ids = ", ".join(str(o.daysmart_id) for o in verdict.candidates) or "none"
    _attempt(db, po, step="snapshot_diff", outcome="needs_review",
             error_detail=f"{verdict.reason} (candidates: {candidate_ids})",
             started_at=started)
    po.sync_status = "sync_failed"
    return "needs_review"


def _stage_draft(db, command, *, created_by_user_id, default_ship_to_id,
                 default_bill_to_id, now) -> "models.PurchaseOrder":
    po = models.PurchaseOrder(
        sync_status="draft",
        supplier_daysmart_id=command.supplier_daysmart_id,
        supplier_name=command.supplier_name,
        account_no=command.account_no,
        order_date=command.order_date.isoformat(),
        ship_to_id=command.ship_to_id if command.ship_to_id is not None else default_ship_to_id,
        bill_to_id=command.bill_to_id if command.bill_to_id is not None else default_bill_to_id,
        notes=command.notes,
        created_by_user_id=created_by_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(po)
    return po


def _payload_from_po(po) -> dict:
    """The header-create payload rebuilt from the stored PO row — what a
    RESUBMIT sends (the original command is long gone)."""
    return {
        "supplier_id": po.supplier_daysmart_id,
        "supplier_name": po.supplier_name,
        "account_no": po.account_no,
        "order_date": int(datetime.fromisoformat(po.order_date).timestamp()),
        "ship_to_id": po.ship_to_id,
        "bill_to_id": po.bill_to_id,
        "notes": po.notes or None,
        "template_id": None,
    }


def _link(db, po, daysmart_id: int, daysmart_index: int) -> None:
    assign_daysmart_identity(po, daysmart_id=daysmart_id, daysmart_index=daysmart_index)
    po.sync_status = "synced"
    po.updated_at = _now_iso()


def _persist_issues(db, po, issues, *, context: str) -> None:
    now = _now_iso()
    for issue in issues:
        db.add(models.PoValidationIssueRecord(
            code=issue.code.value,
            field_path=issue.field_path,
            raw_value=None if issue.raw_value is None else str(issue.raw_value),
            expected=issue.expected,
            daysmart_id=issue.daysmart_id,
            purchase_order_id=po.id,
            payload_context=context,
            created_at=now,
        ))


def _attempt(db, po, *, step: str, outcome: str, started_at: str,
             error: Exception | None = None, error_detail: str | None = None) -> None:
    db.add(models.PoSyncAttempt(
        purchase_order_id=po.id,
        step=step,
        outcome=outcome,
        error_code=type(error).__name__ if error is not None else None,
        error_detail=error_detail if error_detail is not None else (str(error) if error else None),
        started_at=started_at,
        finished_at=_now_iso(),
    ))


def _outcome_for(exc: DaysmartError) -> str:
    if isinstance(exc, DaysmartAuthFailure):
        return "auth_failure"
    if isinstance(exc, DaysmartTimeout):
        return "timeout"
    return "daysmart_down"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
