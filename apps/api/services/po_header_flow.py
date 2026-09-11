"""Purchase-order HEADER writes after creation: update and delete.

- UPDATE (``PUT order/{id}``, wire): the editable header is account
  number, order date, ship-to and bill-to, notes. For a PO that lives in
  Daysmart the write is WRITE-THROUGH: Daysmart is asked first, and on
  success the echoed order — what Daysmart actually stored — is mirrored
  onto the row. The header columns are Daysmart-owned mirrors once a PO
  is synced (the next pull overwrites them), so a local edit that Daysmart
  never took would only be lost; instead nothing changes locally and the
  outcome says why. A local DRAFT (never sent) is simply edited: its
  header is what will be sent. Daysmart blocks header edits once an order
  is submitted or closed — refused locally, with a named code.
- DELETE (``DELETE order/{id}``, wire, SOFT): Daysmart marks the order
  inactive and rewrites its notes to "[removed]"; the echo is mirrored
  and the local row is kept as ``sync_status='deleted'`` (audit). A draft
  is not deleted but CANCELLED (its own verb) — refused here.

Failure policy (one attempt, always reported, never a background retry):
unavailable/auth → 'sync_failed' (nothing reached Daysmart; re-run);
refused → 'needs_review'; an update timeout → 'sync_failed' (a full
replacement is safe to resend); a delete timeout → 'needs_review'
(refresh the PO to see). The PO's ``sync_status`` is left alone on
failure — it still means "does Daysmart have this order".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import models
from schemas.purchase_orders import UpdatePurchaseOrderCommandV1, build_daysmart_update_payload
from services.daysmart_service import (
    DaysmartAuthFailure,
    DaysmartError,
    DaysmartRefused,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_inbound_sync import apply_order_header_from_detail


@dataclass(frozen=True)
class HeaderOpResult:
    #: 'synced' | 'draft' | 'deleted' | 'sync_failed' | 'needs_review'
    outcome: str
    error_code: str | None = None
    message: str | None = None


class HeaderOperationRefused(ValueError):
    """A header write Rosetta refuses to attempt at all (409 at the API)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: Daysmart's UI hides header edits once an order left 'open'.
_NOT_EDITABLE = frozenset({"submitted", "closed"})


def update_purchase_order(db, po, command: UpdatePurchaseOrderCommandV1, daysmart) -> HeaderOpResult:
    if po.sync_status in ("cancelled", "deleted"):
        raise HeaderOperationRefused("PO_NOT_EDITABLE", f"a {po.sync_status} purchase order cannot be edited")
    now = _now_iso()
    if po.daysmart_id is None:
        # Never sent: the local header IS what will be sent — edit it.
        _apply_locally(po, command)
        po.updated_at = now
        return HeaderOpResult(outcome="draft")
    if po.daysmart_status in _NOT_EDITABLE:
        raise HeaderOperationRefused(
            "PO_NOT_OPEN", f"Daysmart does not allow header edits on a {po.daysmart_status} order")

    payload = build_daysmart_update_payload(
        command,
        ship_to_id=command.ship_to_id if command.ship_to_id is not None else po.ship_to_id,
        bill_to_id=command.bill_to_id if command.bill_to_id is not None else po.bill_to_id)
    started = _now_iso()
    try:
        result = daysmart.update_order(po.daysmart_id, payload)
    except DaysmartTimeout as exc:
        # A full-replacement PUT is safe to resend — retryable.
        _attempt(db, po, "update_order", "timeout", exc, started)
        return _failed("sync_failed", exc)
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "update_order", _outcome_for(exc), exc, started)
        return _failed("sync_failed", exc)
    except DaysmartRefused as exc:
        _attempt(db, po, "update_order", "refused", exc, started)
        return _failed("needs_review", exc)
    except DaysmartError as exc:
        _attempt(db, po, "update_order", "contract_mismatch", exc, started)
        return _failed("needs_review", exc)
    _attempt(db, po, "update_order", "success", None, started)

    if result.order is not None and result.order.daysmart_id == po.daysmart_id:
        apply_order_header_from_detail(po, result.order, now)   # what Daysmart stored
    else:
        _apply_locally(po, command)                              # acked, echo unreadable
    po.updated_at = now
    return HeaderOpResult(outcome="synced")


def delete_purchase_order(db, po, daysmart) -> HeaderOpResult:
    if po.sync_status == "deleted":
        return HeaderOpResult(outcome="deleted", message="already deleted")
    if po.daysmart_id is None:
        raise HeaderOperationRefused(
            "PO_NOT_SYNCED", "this purchase order was never sent to Daysmart — cancel it instead")
    started = _now_iso()
    try:
        result = daysmart.delete_order(po.daysmart_id)
    except DaysmartTimeout as exc:
        _attempt(db, po, "delete_order", "timeout", exc, started)
        return _failed("needs_review", exc, message=(
            "Daysmart did not answer — refresh the purchase order to see whether it is gone"))
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        _attempt(db, po, "delete_order", _outcome_for(exc), exc, started)
        return _failed("sync_failed", exc)
    except DaysmartRefused as exc:
        _attempt(db, po, "delete_order", "refused", exc, started)
        return _failed("needs_review", exc)
    except DaysmartError as exc:
        _attempt(db, po, "delete_order", "contract_mismatch", exc, started)
        return _failed("needs_review", exc)
    _attempt(db, po, "delete_order", "success", None, started)

    now = _now_iso()
    if result.order is not None and result.order.daysmart_id == po.daysmart_id:
        apply_order_header_from_detail(po, result.order, now)   # is_active false, "[removed]"
    else:
        po.is_active = False
    po.sync_status = "deleted"
    po.updated_at = now
    return HeaderOpResult(outcome="deleted")


# ---------------------------------------------------------------------------


def _apply_locally(po, command: UpdatePurchaseOrderCommandV1) -> None:
    po.account_no = command.account_no
    po.order_date = command.order_date.isoformat()
    if command.ship_to_id is not None:
        po.ship_to_id = command.ship_to_id
    if command.bill_to_id is not None:
        po.bill_to_id = command.bill_to_id
    po.notes = command.notes


def _failed(outcome: str, exc: Exception, *, message: str | None = None) -> HeaderOpResult:
    return HeaderOpResult(outcome=outcome, error_code=type(exc).__name__,
                          message=message if message is not None else str(exc))


def _attempt(db, po, step: str, outcome: str, error, started_at: str) -> None:
    db.add(models.PoSyncAttempt(
        purchase_order_id=po.id,
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
