"""Receiving flow — receipts the way Daysmart's own screen makes them.

In Daysmart you pick the PO, press create, and the receipt ROWS appear by
themselves, one per PO line, filled in as "received as ordered". Then you
review each row against the order. Rosetta now does the same:

1. stage the receipt header locally (and any ADJUSTMENTS the caller sent,
   each naming one of OUR PO lines — nobody types Daysmart ids);
2. create the header in Daysmart (``POST inventory/receipt`` with the
   PO's id);
3. pull the rows Daysmart generated (the receipt inbox mirrors them; a
   staged adjustment is ADOPTED by its generated row through the exact
   PO-line identity, never by position);
4. PUT each adjustment onto its generated row, then pull once more so the
   mirror shows what Daysmart stored.

A header-only request is therefore complete on its own: every generated
row is accepted as generated. Rows the caller did not mention are simply
received as ordered — that is Daysmart's default, not a problem to
review. POSTING remains a separate, explicit stock commitment.

Failure policy (one attempt, never a blind resend, always reported):
- unavailable/auth → 'sync_failed', local record survives, the same
  request can be re-run; timeout on the receipt CREATE → 'needs_review',
  never resent (the receipt may exist);
- a rows pull that fails after the header landed keeps the link
  ('sync_failed'); the next PO sync/refresh mirrors the rows;
- an adjustment whose line got no generated row → that item
  'needs_review' (the receipt too), the others proceed;
- an adjustment PUT: timeout → 'needs_review'; unavailable → 'sync_failed'
  (item stays pending); refused → item 'needs_review', siblings proceed.

Attempts are logged to ``po_sync_attempts`` against the PARENT PO (steps
'create_receipt' / 'fetch_receipt_rows' / 'update_receipt_item' /
'post_receipt'). Stages only; the caller owns the commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import models
from services.daysmart_service import (
    DaysmartAuthFailure,
    DaysmartError,
    DaysmartRefused,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_receipts_inbound import refresh_receipt, refresh_receipt_rows


@dataclass(frozen=True)
class ReceiptItemDraft:
    """One ADJUSTMENT to a row Daysmart will generate: names OUR PO line
    (``line_uuid`` is the way; the ids are accepted for older callers)."""
    quantity_received: int
    quantity_in_stock: int
    amount: str
    line_uuid: str | None = None
    #: Daysmart inventory-item id override — a "DIT-…" STRING on the wire.
    post_to_item_id: str | None = None
    daysmart_po_item_id: int | None = None
    purchase_order_line_id: int | None = None
    expiration_date: str | int | None = None
    lot_number: str = ""
    manufacturer: str | None = None
    ndc_code: str | None = None
    tax: str | None = None

    @property
    def sendable(self) -> bool:
        return self.post_to_item_id is not None and self.daysmart_po_item_id is not None


@dataclass(frozen=True)
class ReceiptDraft:
    invoice_number: str
    receipt_date: str | int
    shipping: str | None = None
    tax: str | None = None
    tax_type: int | None = None
    notes: str = ""
    items: list[ReceiptItemDraft] = field(default_factory=list)


@dataclass(frozen=True)
class ReceiptHeaderDraft:
    """A full replacement of the header fields WE own (wire: ``PUT
    receipt/{id}`` takes date, tax, shipping, invoice_number, notes)."""
    invoice_number: str
    receipt_date: str | int
    shipping: str | None = None
    tax: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class ReceiptItemChanges:
    """A full replacement of the row fields WE own (wire: ``PUT
    receipt/items/{id}`` takes every one of these)."""
    quantity_received: int
    quantity_in_stock: int
    amount: str
    expiration_date: str | int | None = None
    lot_number: str = ""
    manufacturer: str | None = None
    ndc_code: str | None = None
    tax: str | None = None
    post_to_item_id: str | None = None


@dataclass(frozen=True)
class ReceiptOpResult:
    #: 'synced' | 'sync_failed' | 'needs_review'
    outcome: str
    error_code: str | None = None
    message: str | None = None
    #: The pull after a successful write failed — the write stands.
    mirror_error: str | None = None


class ReceiptOperationRefused(ValueError):
    """A receipt write Rosetta refuses to attempt at all (409 at the API)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReceiptFlowResult:
    receipt: "models.PoReceipt"
    #: 'synced' | 'sync_failed' | 'needs_review'
    outcome: str
    #: Rows Daysmart generated (mirrored locally), adjustments applied.
    generated_rows: int = 0
    adjustments_applied: int = 0
    #: The pull after the adjustments failed — the writes stand, the
    #: mirror may lag until the next sync/refresh.
    mirror_error: str | None = None


class ReceiptValidationError(ValueError):
    """The requested receipt cannot be represented on DaySmart's wire."""


def create_receipt_for_po(db, po, draft: ReceiptDraft, daysmart, *,
                          created_by_user_id: int | None = None) -> ReceiptFlowResult:
    if po.daysmart_id is None:
        raise ValueError(
            f"PO {po.po_uuid} is not synced to Daysmart — a receipt needs the "
            "Daysmart order id; sync the PO first")

    # Validate everything that can be validated BEFORE staging: a malformed
    # date or an adjustment naming no line of this PO is a caller error, not
    # a failed sync, and must not leave a local receipt behind.
    receipt_epoch = _validated_epoch(draft.receipt_date, "receipt_date")
    for item in draft.items:
        if item.expiration_date not in (None, ""):
            _validated_epoch(item.expiration_date, "expiration_date")
    targets = [_resolve_adjustment(db, po, item) for item in draft.items]
    seen: set[int] = set()
    for _, po_item_id, _ in targets:
        if po_item_id in seen:
            raise ReceiptValidationError(
                f"two adjustments name the same PO line (Daysmart line {po_item_id})")
        seen.add(po_item_id)

    now = _now_iso()
    receipt = models.PoReceipt(
        purchase_order_id=po.id,
        invoice_number=draft.invoice_number,
        receipt_date=draft.receipt_date,
        shipping=draft.shipping,
        tax=draft.tax,
        tax_type=draft.tax_type,
        notes=draft.notes,
        created_by_user_id=created_by_user_id,
        created_at=now,
        updated_at=now,
    )
    receipt.items = [
        models.PoReceiptItem(
            purchase_order_line_id=line_id,
            daysmart_po_item_id=po_item_id,
            post_to_item_id=post_to,
            quantity_received=str(item.quantity_received),
            quantity_in_stock=str(item.quantity_in_stock),
            amount=item.amount,
            expiration_date=None if item.expiration_date in (None, "") else str(item.expiration_date),
            lot_number=item.lot_number,
            manufacturer=item.manufacturer,
            ndc_code=item.ndc_code,
            tax=item.tax,
            sync_status="pending",
        )
        for item, (line_id, po_item_id, post_to) in zip(draft.items, targets)
    ]
    db.add(receipt)
    db.flush()  # ids needed for attempt rows
    return _drive(db, receipt, po, daysmart, receipt_epoch)


#: First-attempt outcomes after which the receipt MAY exist in Daysmart —
#: a second create is not provably safe, so a human decides.
_MAY_HAVE_LANDED = frozenset({"timeout", "contract_mismatch", "refused", "needs_review"})


def retry_receipt(db, receipt, daysmart) -> ReceiptFlowResult:
    """Re-drive an EXISTING receipt — the lifecycle engine. Never a second
    local row; a second Daysmart create only when the first provably never
    landed. Header already there → finish what is left (rows, adjustments)."""
    po = receipt.purchase_order
    if receipt.post_status == "posted":
        raise ReceiptOperationRefused("RECEIPT_POSTED", "a posted receipt has nothing to retry")
    if receipt.daysmart_receipt_id is None:
        last = max((a for a in po.sync_attempts
                    if a.receipt_id == receipt.id and a.step == "create_receipt"),
                   key=lambda a: a.id, default=None)
        if last is not None and last.outcome in _MAY_HAVE_LANDED:
            return ReceiptFlowResult(receipt=receipt, outcome="needs_review")
        if po.daysmart_id is None:
            raise ValueError(f"PO {po.po_uuid} is not synced to Daysmart — sync the PO first")
        return _drive(db, receipt, po, daysmart, _validated_epoch(receipt.receipt_date, "receipt_date"))
    if receipt.sync_status == "synced" and all(i.sync_status == "synced" for i in receipt.items):
        return ReceiptFlowResult(receipt=receipt, outcome="synced")
    return _finish(db, receipt, po, daysmart)


def _drive(db, receipt, po, daysmart, receipt_epoch: int) -> ReceiptFlowResult:
    # --- 2. create the receipt header in Daysmart ---------------------------
    receipt.sync_status = "submitting"
    started = _now_iso()
    try:
        daysmart_receipt_id = daysmart.create_receipt(
            _receipt_payload(po, receipt, receipt_epoch))
    except DaysmartTimeout as exc:
        _attempt(db, po, "create_receipt", "timeout", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return ReceiptFlowResult(receipt=receipt, outcome="needs_review")
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "create_receipt", _outcome_for(exc), exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return ReceiptFlowResult(receipt=receipt, outcome="sync_failed")
    except DaysmartRefused as exc:
        _attempt(db, po, "create_receipt", "refused", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return ReceiptFlowResult(receipt=receipt, outcome="needs_review")
    except DaysmartError as exc:
        _attempt(db, po, "create_receipt", "contract_mismatch", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return ReceiptFlowResult(receipt=receipt, outcome="needs_review")

    _attempt(db, po, "create_receipt", "success", None, started, receipt=receipt)
    receipt.daysmart_receipt_id = daysmart_receipt_id
    receipt.sync_status = "synced"
    receipt.updated_at = _now_iso()
    return _finish(db, receipt, po, daysmart)


def _finish(db, receipt, po, daysmart) -> ReceiptFlowResult:
    """From a landed header: pull the generated rows (adopting staged
    adjustments), apply pending adjustments, pull once more."""
    # --- 3. pull the rows Daysmart generated; adopt staged adjustments -------
    started = _now_iso()
    try:
        pulled = refresh_receipt(db, receipt, daysmart).rows
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "fetch_receipt_rows", _outcome_for(exc), exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return ReceiptFlowResult(receipt=receipt, outcome="sync_failed")
    except DaysmartError as exc:
        _attempt(db, po, "fetch_receipt_rows", "contract_mismatch", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return ReceiptFlowResult(receipt=receipt, outcome="needs_review")
    _attempt(db, po, "fetch_receipt_rows", "success", None, started, receipt=receipt)
    generated_rows = pulled.created + pulled.adopted

    # --- 4. apply the adjustments onto their generated rows ------------------
    outcome = "synced"
    applied = 0
    for item in list(receipt.items):
        if item.sync_status != "pending":
            continue                                   # generated as-is: nothing to send
        if item.daysmart_receipt_item_id is None:
            item.sync_status = "needs_review"
            _attempt(db, po, "update_receipt_item", "needs_review", DaysmartRefused(
                f"Daysmart generated no receipt row for PO line {item.daysmart_po_item_id}; "
                "the adjustment cannot be applied"), _now_iso(), receipt=receipt)
            outcome = "needs_review"
            continue
        result = apply_receipt_adjustment(db, receipt, item, daysmart)
        if result == "synced":
            applied += 1
        elif result == "sync_failed":
            receipt.sync_status = "sync_failed"
            receipt.updated_at = _now_iso()
            return ReceiptFlowResult(receipt=receipt, outcome="sync_failed",
                                     generated_rows=generated_rows, adjustments_applied=applied)
        else:
            outcome = "needs_review"

    mirror_error = None
    if applied:
        # Show what Daysmart stored — the rows, and the header's amount,
        # which Daysmart computes from them.
        mirror_error = _mirror_after_write(db, receipt, daysmart)

    if outcome == "synced" and all(row.sync_status == "synced" for row in receipt.items):
        receipt.sync_status = "synced"
    receipt.updated_at = _now_iso()
    return ReceiptFlowResult(receipt=receipt, outcome=outcome, generated_rows=generated_rows,
                             adjustments_applied=applied, mirror_error=mirror_error)


def apply_receipt_adjustment(db, receipt, item, daysmart) -> str:
    """PUT one local item's OWN values (what we want) onto its generated
    Daysmart row. Returns 'synced' | 'sync_failed' | 'needs_review'; the
    item's ``sync_status`` follows. Shared by create and the adjust step."""
    if item.daysmart_receipt_item_id is None:
        raise ValueError("the item has no generated Daysmart row to adjust")
    po = receipt.purchase_order
    started = _now_iso()
    try:
        daysmart.update_receipt_item(item.daysmart_receipt_item_id, _adjustment_payload(item))
    except DaysmartTimeout as exc:
        _attempt(db, po, "update_receipt_item", "timeout", exc, started, receipt=receipt)
        item.sync_status = "needs_review"
        return "needs_review"
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "update_receipt_item", _outcome_for(exc), exc, started, receipt=receipt)
        item.sync_status = "pending"
        return "sync_failed"
    except DaysmartRefused as exc:
        _attempt(db, po, "update_receipt_item", "refused", exc, started, receipt=receipt)
        item.sync_status = "needs_review"
        return "needs_review"
    except DaysmartError as exc:
        _attempt(db, po, "update_receipt_item", "contract_mismatch", exc, started, receipt=receipt)
        item.sync_status = "needs_review"
        return "needs_review"
    _attempt(db, po, "update_receipt_item", "success", None, started, receipt=receipt)
    item.sync_status = "synced"
    return "synced"


def post_receipt_flow(db, receipt, daysmart) -> str:
    """Commit a receipt to stock — explicit, never automatic.

    Returns 'posted' | 'refused' | 'sync_failed' | 'needs_review'.
    """
    if receipt.daysmart_receipt_id is None:
        raise ValueError(
            f"receipt {receipt.receipt_uuid} is not synced to Daysmart — "
            "nothing to post")
    if receipt.post_status == "posted":
        return "posted"

    po = receipt.purchase_order
    started = _now_iso()
    try:
        remote_items = daysmart.fetch_receipt_items(receipt.daysmart_receipt_id)
    except DaysmartTimeout as exc:
        _attempt(db, po, "fetch_receipt_items", "timeout", exc, started, receipt=receipt)
        return "needs_review"
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "fetch_receipt_items", _outcome_for(exc), exc, started, receipt=receipt)
        return "sync_failed"
    except DaysmartError as exc:
        _attempt(db, po, "fetch_receipt_items", "contract_mismatch", exc, started, receipt=receipt)
        return "needs_review"
    pending = [item for item in remote_items if not item.post_to_item_id]
    if pending or not remote_items or any(
            item.sync_status != "synced" for item in receipt.items):
        detail = (
            "receipt cannot be posted until every generated item is mapped "
            "and its local adjustment is synced")
        _attempt(
            db, po, "fetch_receipt_items", "refused",
            DaysmartRefused(detail), started)
        return "refused"
    _attempt(db, po, "fetch_receipt_items", "success", None, started, receipt=receipt)

    started = _now_iso()
    try:
        daysmart.post_receipt(receipt.daysmart_receipt_id)
    except DaysmartRefused as exc:
        # e.g. their "Action Required" pending-items case — stays a draft.
        _attempt(db, po, "post_receipt", "refused", exc, started, receipt=receipt)
        return "refused"
    except DaysmartTimeout as exc:
        _attempt(db, po, "post_receipt", "timeout", exc, started, receipt=receipt)
        return "needs_review"
    except DaysmartError as exc:
        _attempt(db, po, "post_receipt", _outcome_for(exc), exc, started, receipt=receipt)
        return "sync_failed"

    _attempt(db, po, "post_receipt", "success", None, started, receipt=receipt)
    receipt.post_status = "posted"
    receipt.updated_at = _now_iso()
    return "posted"


def update_receipt_header(db, receipt, draft: ReceiptHeaderDraft, daysmart) -> ReceiptOpResult:
    """Edit the header fields WE own and push them (``PUT receipt/{id}``),
    then pull the receipt so the mirror shows what Daysmart stored. The
    local edit stands whatever Daysmart answers; the outcome says what
    happened. A posted receipt is closed in Daysmart — refused locally."""
    _require_editable(receipt)
    receipt_epoch = _validated_epoch(draft.receipt_date, "receipt_date")
    receipt.invoice_number = draft.invoice_number
    receipt.receipt_date = draft.receipt_date
    receipt.shipping = draft.shipping
    receipt.tax = draft.tax
    receipt.notes = draft.notes
    receipt.updated_at = _now_iso()

    po = receipt.purchase_order
    started = _now_iso()
    try:
        daysmart.update_receipt(receipt.daysmart_receipt_id, {
            "date": receipt_epoch,
            "tax": receipt.tax,
            "shipping": receipt.shipping,
            "invoice_number": receipt.invoice_number,
            "notes": receipt.notes,
        })
    except DaysmartTimeout as exc:
        # A full-replacement PUT is safe to resend — retryable.
        _attempt(db, po, "update_receipt", "timeout", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return _failed("sync_failed", exc)
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "update_receipt", _outcome_for(exc), exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return _failed("sync_failed", exc)
    except DaysmartRefused as exc:
        _attempt(db, po, "update_receipt", "refused", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return _failed("needs_review", exc)
    except DaysmartError as exc:
        _attempt(db, po, "update_receipt", "contract_mismatch", exc, started, receipt=receipt)
        receipt.sync_status = "sync_failed"
        return _failed("needs_review", exc)
    _attempt(db, po, "update_receipt", "success", None, started, receipt=receipt)
    receipt.sync_status = "synced"
    return ReceiptOpResult(outcome="synced", mirror_error=_mirror_after_write(db, receipt, daysmart))


def update_receipt_row(db, receipt, item, changes: ReceiptItemChanges, daysmart) -> ReceiptOpResult:
    """Edit one received row — the review of received against ordered —
    and push it onto its generated Daysmart row, then pull the receipt."""
    _require_editable(receipt)
    if item.po_receipt_id != receipt.id:
        raise ReceiptOperationRefused("RECEIPT_ITEM_NOT_ON_RECEIPT", "the row belongs to another receipt")
    if item.daysmart_receipt_item_id is None:
        raise ReceiptOperationRefused(
            "RECEIPT_ITEM_NOT_GENERATED",
            "Daysmart has not generated this row yet — refresh the purchase order first")
    if changes.expiration_date not in (None, ""):
        _validated_epoch(changes.expiration_date, "expiration_date")
    item.quantity_received = str(changes.quantity_received)
    item.quantity_in_stock = str(changes.quantity_in_stock)
    item.amount = changes.amount
    item.expiration_date = None if changes.expiration_date in (None, "") else str(changes.expiration_date)
    item.lot_number = changes.lot_number
    item.manufacturer = changes.manufacturer
    item.ndc_code = changes.ndc_code
    item.tax = changes.tax
    if changes.post_to_item_id is not None:
        item.post_to_item_id = changes.post_to_item_id
    receipt.updated_at = _now_iso()

    outcome = apply_receipt_adjustment(db, receipt, item, daysmart)
    if outcome != "synced":
        receipt.sync_status = "sync_failed"
        last = max((a for a in receipt.purchase_order.sync_attempts if a.step == "update_receipt_item"),
                   key=lambda a: a.id, default=None)
        return ReceiptOpResult(outcome=outcome,
                               error_code=last.error_code if last else None,
                               message=last.error_detail if last else None)
    receipt.sync_status = "synced"
    return ReceiptOpResult(outcome="synced", mirror_error=_mirror_after_write(db, receipt, daysmart))


def _require_editable(receipt) -> None:
    if receipt.daysmart_receipt_id is None:
        raise ReceiptOperationRefused(
            "RECEIPT_NOT_SYNCED", "the receipt has no Daysmart receipt yet — nothing to edit there")
    if receipt.post_status == "posted" or receipt.daysmart_status == "posted":
        raise ReceiptOperationRefused(
            "RECEIPT_POSTED", "a posted receipt is closed in Daysmart and cannot be edited")


def _mirror_after_write(db, receipt, daysmart) -> str | None:
    started = _now_iso()
    try:
        refresh_receipt(db, receipt, daysmart)
    except DaysmartError as exc:
        _attempt(db, receipt.purchase_order, "fetch_receipt_rows", _outcome_for(exc), exc, started, receipt=receipt)
        return f"{type(exc).__name__}: {exc}"
    return None


def _failed(outcome: str, exc: Exception) -> ReceiptOpResult:
    return ReceiptOpResult(outcome=outcome, error_code=type(exc).__name__, message=str(exc))


def _receipt_payload(po, receipt, receipt_epoch: int) -> dict:
    """Wire create shape (PO-end-to-end.har) — amount is null on create
    (server-computed from the items). Built from the STAGED receipt so a
    retry sends exactly what the first attempt sent."""
    return {
        "supplier_id": po.supplier_daysmart_id,
        "supplier_name": po.supplier_name,
        "date": receipt_epoch,
        "order_id": [str(po.daysmart_id)],
        "invoice_number": receipt.invoice_number,
        "amount": None,
        "shipping": receipt.shipping,
        "notes": receipt.notes,
        "tax": receipt.tax,
        "tax_type": receipt.tax_type,
    }


def _resolve_adjustment(db, po, item: ReceiptItemDraft) -> tuple[int | None, int, str | None]:
    """(our line id, Daysmart's line id, post-to) for one adjustment —
    from ``line_uuid`` (the way), ``purchase_order_line_id``, or a bare
    ``daysmart_po_item_id`` for older callers. Never guessed."""
    line = None
    if item.line_uuid:
        line = next((l for l in po.lines if l.line_uuid == item.line_uuid), None)
        if line is None:
            raise ReceiptValidationError(f"line {item.line_uuid} is not on this purchase order")
    elif item.purchase_order_line_id is not None:
        line = db.get(models.PurchaseOrderLine, item.purchase_order_line_id)
        if line is None or line.purchase_order_id != po.id:
            raise ReceiptValidationError(
                f"line {item.purchase_order_line_id} is not on this purchase order")
    if line is not None:
        if line.daysmart_item_id is None:
            raise ReceiptValidationError(
                f"line {line.line_uuid} was never sent to Daysmart — no receipt row can exist for it")
        return line.id, line.daysmart_item_id, item.post_to_item_id or line.daysmart_inventory_item_id
    if item.daysmart_po_item_id is not None:
        local = next((l for l in po.lines if l.daysmart_item_id == item.daysmart_po_item_id), None)
        post_to = item.post_to_item_id or (local.daysmart_inventory_item_id if local else None)
        return (local.id if local else None), item.daysmart_po_item_id, post_to
    raise ReceiptValidationError("an adjustment must name a PO line (line_uuid)")


def _adjustment_payload(item) -> dict:
    """The row-update body (wire: ``PUT receipt/items/{id}``), from what WE
    want on the local item. ``item_id`` rides only when our post-to differs
    from the one Daysmart shows for the row."""
    payload = {
        "quantity_received": int(item.quantity_received),
        "quantity_in_stock": int(item.quantity_in_stock),
        "amount": item.amount,
        "expiration_date": (None if item.expiration_date in (None, "")
                            else _to_epoch(item.expiration_date, "expiration_date")),
        "lot_number": item.lot_number,
        "manufacturer": item.manufacturer,
        "ndc_code": item.ndc_code,
    }
    if item.post_to_item_id is not None and item.post_to_item_id != item.daysmart_post_to_item_id:
        payload["item_id"] = item.post_to_item_id
    if item.tax is not None:  # their UI includes tax only for custom-tax items
        payload["tax"] = item.tax
    return payload


def _validated_epoch(value: str | int, field: str) -> int:
    try:
        return _to_epoch(value, field)
    except (TypeError, ValueError) as exc:
        raise ReceiptValidationError(str(exc)) from exc


def _to_epoch(value: str | int, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    if text.isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an epoch or ISO-8601 date/time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _attempt(db, po, step: str, outcome: str, error, started_at: str, *, receipt=None) -> None:
    db.add(models.PoSyncAttempt(
        purchase_order_id=po.id,
        receipt_id=receipt.id if receipt is not None else None,
        step=step,
        outcome=outcome,
        error_code=type(error).__name__ if error is not None else None,
        error_detail=str(error) if error is not None else None,
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
