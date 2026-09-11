"""Purchase-order LINE writes (Rosetta → Daysmart): add, edit, delete.

The header saga never sends lines. Every line write here is explicit,
per line, and answers with the line's actual outcome:

- ADD stages each requested line locally, sends the sendable ones one at
  a time (``item/save``), then re-fetches the order's lines and identifies
  the new Daysmart line by set difference + the stable ``DIT-…`` inventory
  id. Zero or multiple matches are never guessed → ``needs_review``.
- EDIT always takes the edit locally (Rosetta-owned fields: what WE want).
  A line that lives in Daysmart (has a line id) is pushed as an UPDATE —
  ``item/save`` with ``purchase_order_item_id`` (save = upsert) — then the
  PO's lines are pulled so the mirror shows what Daysmart stored. A line
  that was never sent (no line id) is edited locally only: sending is the
  add endpoint's job, never a side effect of an edit. The inventory item
  is the line's identity in Daysmart and is never changed by an edit.
- DELETE of a line that lives in Daysmart calls ``DELETE order/item/{id}``
  and keeps the local row as ``deleted`` (audit); a never-sent line is
  simply removed. A line Daysmart already shows inactive is deleted
  locally without a call — that is the recovery path after a timeout:
  refresh the PO, delete again.

Every line write that reached Daysmart ends with a PULL OF THE WHOLE
PURCHASE ORDER — header, lines, receipts — the same pull the refresh
endpoint does (Moses's rule: everything attached to a PO comes with the
PO). So after an add, an edit or a delete, ``item_count`` and every mirror
column are current without anyone calling refresh. A pull that fails does
not undo the write; it is reported as ``mirror_error``.

Failure policy — one attempt, never a blind resend, always reported:
unavailable/auth → ``sync_failed`` (nothing reached Daysmart; re-run the
same request); refused → ``needs_review`` (Daysmart said no — e.g. the
order was already submitted); an ADD timeout → ``needs_review`` (a resend
may duplicate); an UPDATE timeout → ``sync_failed`` (keyed by line id, a
resend is safe); a DELETE timeout → ``needs_review`` (refresh to see).
Every attempt is logged on the PO. The pull after a successful write
failing does not undo the write — it is reported as ``mirror_error``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import models
from services.daysmart_service import (
    DaysmartAuthFailure,
    DaysmartError,
    DaysmartRefused,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_inbound_sync import DaysmartOrderUnreadable, refresh_purchase_order


@dataclass(frozen=True)
class LineOpResult:
    #: None only after a never-sent line was removed outright.
    line: "models.PurchaseOrderLine | None"
    #: 'synced' | 'pending' | 'sync_failed' | 'needs_review' | 'deleted'
    outcome: str
    error_code: str | None = None
    message: str | None = None
    #: The write succeeded but the follow-up pull of the PO's lines did
    #: not — the mirror may lag until the next sync/refresh.
    mirror_error: str | None = None


LineAddResult = LineOpResult  # the add flow's historical name


class LineOperationRefused(ValueError):
    """A line write Rosetta refuses to attempt at all (409 at the API)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def add_purchase_order_lines(db, po, commands, daysmart) -> list[LineOpResult]:
    _require_synced(po, "adding lines")
    lines = [_stage_line(db, po, command) for command in commands]
    db.flush()

    try:
        known = _known_line_ids(daysmart, po)
    except DaysmartError as exc:
        results = []
        for line in lines:
            if line.daysmart_inventory_item_id is None:
                results.append(_pending(line))
                continue
            line.sync_status = "sync_failed"
            _attempt(db, po, line, "fetch_line_ids", _outcome_for(exc), exc)
            results.append(_failed(line, "sync_failed", exc))
        po.updated_at = _now_iso()
        return results

    results = [_send_new_line(db, po, line, known, daysmart) for line in lines]
    po.updated_at = _now_iso()
    if any(r.outcome == "synced" for r in results):
        error = _pull_purchase_order(db, po, lines[0], daysmart)
        if error is not None:
            results = [LineOpResult(line=r.line, outcome=r.outcome, error_code=r.error_code,
                                    message=r.message, mirror_error=error)
                       if r.outcome == "synced" else r for r in results]
    return results


def update_purchase_order_line(db, po, line, command, daysmart) -> LineOpResult:
    if line.purchase_order_id != po.id:
        raise LineOperationRefused("LINE_NOT_ON_PO", "the line belongs to another purchase order")
    if line.sync_status == "deleted":
        raise LineOperationRefused("LINE_DELETED", "a deleted line cannot be edited")
    in_daysmart = line.daysmart_item_id is not None
    if in_daysmart and command.daysmart_inventory_item_id != line.daysmart_inventory_item_id:
        raise LineOperationRefused(
            "LINE_IDENTITY_IMMUTABLE",
            "the inventory item is the line's identity in Daysmart — "
            "delete this line and add a new one instead")
    if in_daysmart:
        _require_synced(po, "editing a line that lives in Daysmart")

    _apply_command(line, command, allow_identity=not in_daysmart)
    po.updated_at = _now_iso()
    if not in_daysmart:
        # Never sent. Edited locally only — sending is the add endpoint's job.
        line.sync_status = "pending"
        return _pending(line)

    try:
        daysmart.save_order_items([_payload(po, line, existing_id=line.daysmart_item_id)])
    except DaysmartTimeout as exc:
        # Keyed by purchase_order_item_id, a resend cannot duplicate — retryable.
        line.sync_status = "sync_failed"
        _attempt(db, po, line, "update_line", "timeout", exc)
        return _failed(line, "sync_failed", exc)
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        line.sync_status = "sync_failed"
        _attempt(db, po, line, "update_line", _outcome_for(exc), exc)
        return _failed(line, "sync_failed", exc)
    except DaysmartRefused as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "update_line", "refused", exc)
        return _failed(line, "needs_review", exc)
    except DaysmartError as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "update_line", "contract_mismatch", exc)
        return _failed(line, "needs_review", exc)

    _attempt(db, po, line, "update_line", "success", None)
    line.sync_status = "synced"
    return _mirror_after_write(db, po, line, daysmart, outcome="synced")


def delete_purchase_order_line(db, po, line, daysmart) -> LineOpResult:
    if line.purchase_order_id != po.id:
        raise LineOperationRefused("LINE_NOT_ON_PO", "the line belongs to another purchase order")
    if line.sync_status == "deleted":
        return LineOpResult(line=line, outcome="deleted", message="already deleted")
    now = _now_iso()
    if line.daysmart_item_id is None:
        # Never reached Daysmart — nothing to tell them; the draft row goes.
        db.query(models.PoSyncAttempt).filter_by(line_id=line.id).update({"line_id": None})
        po.lines.remove(line)
        db.delete(line)
        po.updated_at = now
        return LineOpResult(line=None, outcome="deleted")
    if line.daysmart_is_active is False:
        # Daysmart already shows it gone (a previous delete's timeout, or
        # removed in their UI): finish locally, no call.
        line.sync_status = "deleted"
        po.updated_at = now
        return LineOpResult(line=line, outcome="deleted",
                            message="already inactive in Daysmart")
    _require_synced(po, "deleting a line that lives in Daysmart")

    try:
        daysmart.delete_order_item(line.daysmart_item_id)
    except DaysmartTimeout as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "delete_line", "timeout", exc)
        return _failed(line, "needs_review", exc, message=(
            "Daysmart did not answer — refresh the PO to see whether the "
            "line is gone, then delete again"))
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        line.sync_status = "sync_failed"
        _attempt(db, po, line, "delete_line", _outcome_for(exc), exc)
        return _failed(line, "sync_failed", exc)
    except DaysmartRefused as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "delete_line", "refused", exc)
        return _failed(line, "needs_review", exc)
    except DaysmartError as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "delete_line", "contract_mismatch", exc)
        return _failed(line, "needs_review", exc)

    _attempt(db, po, line, "delete_line", "success", None)
    line.sync_status = "deleted"
    line.daysmart_is_active = False
    po.updated_at = now
    return _mirror_after_write(db, po, line, daysmart, outcome="deleted")


# ---------------------------------------------------------------------------


def _send_new_line(db, po, line, known: set, daysmart) -> LineOpResult:
    """Send one staged line and learn its Daysmart id by re-fetching."""
    if line.daysmart_inventory_item_id is None:
        return _pending(line)

    try:
        daysmart.save_order_items([_payload(po, line)])
    except DaysmartTimeout as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "add_line", "timeout", exc)
        return _failed(line, "needs_review", exc)
    except (DaysmartUnavailable, DaysmartAuthFailure) as exc:
        line.sync_status = "sync_failed"
        _attempt(db, po, line, "add_line", _outcome_for(exc), exc)
        return _failed(line, "sync_failed", exc)
    except DaysmartRefused as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "add_line", "refused", exc)
        return _failed(line, "needs_review", exc)
    except DaysmartError as exc:
        line.sync_status = "needs_review"
        _attempt(db, po, line, "add_line", "contract_mismatch", exc)
        return _failed(line, "needs_review", exc)

    _attempt(db, po, line, "add_line", "success", None)
    try:
        current = daysmart.fetch_order_items(po.daysmart_id)
    except DaysmartError as exc:
        # The save acked, so the line exists, but its identity is unknown.
        # A resend would duplicate it.
        line.sync_status = "needs_review"
        _attempt(db, po, line, "fetch_line_ids", _outcome_for(exc), exc)
        return _failed(line, "needs_review", exc)

    new_items = [item for item in current if item.daysmart_item_id not in known]
    matches = [item for item in new_items
               if item.inventory_item_id == line.daysmart_inventory_item_id]
    known.update(item.daysmart_item_id for item in current)
    if len(matches) != 1:
        line.sync_status = "needs_review"
        message = ("DaySmart acknowledged the line but id backfill found "
                   f"{len(matches)} new matching rows; refusing to guess")
        _attempt(db, po, line, "fetch_line_ids", "needs_review", None,
                 error_detail=message)
        return LineOpResult(line=line, outcome="needs_review", message=message)

    line.daysmart_item_id = matches[0].daysmart_item_id
    line.sync_status = "synced"
    _attempt(db, po, line, "fetch_line_ids", "success", None)
    return LineOpResult(line=line, outcome="synced")


def _mirror_after_write(db, po, line, daysmart, *, outcome: str) -> LineOpResult:
    return LineOpResult(line=line, outcome=outcome,
                        mirror_error=_pull_purchase_order(db, po, line, daysmart))


def _pull_purchase_order(db, po, line, daysmart) -> str | None:
    """The whole PO from Daysmart — header, lines, receipts — after a line
    write. Returns the failure text when the pull failed (the write stands)."""
    try:
        refresh_purchase_order(db, po, daysmart)
    except (DaysmartError, DaysmartOrderUnreadable) as exc:
        if isinstance(exc, DaysmartError):
            _attempt(db, po, line, "fetch_lines", _outcome_for(exc), exc)
        else:
            _attempt(db, po, line, "fetch_lines", "contract_mismatch", exc)
        return f"{type(exc).__name__}: {exc}"
    return None


def _require_synced(po, what: str) -> None:
    if po.daysmart_id is None or po.sync_status != "synced":
        raise LineOperationRefused(
            "PO_NOT_SYNCED",
            f"PO {po.po_uuid} is not synced to DaySmart; create or resolve "
            f"the header before {what}")


def _known_line_ids(daysmart, po) -> set:
    return {item.daysmart_item_id for item in daysmart.fetch_order_items(po.daysmart_id)}


def _stage_line(db, po, command):
    line = models.PurchaseOrderLine(sync_status="pending")
    _apply_command(line, command, allow_identity=True)
    po.lines.append(line)
    db.add(line)
    return line


def _apply_command(line, command, *, allow_identity: bool) -> None:
    line.description = command.description
    line.quantity = str(command.quantity)
    line.quantity_uom = command.quantity_uom
    line.unit_price_raw = str(command.unit_price)
    line.notes = command.notes
    line.supplier_offering_id = command.supplier_offering_id
    if allow_identity:
        line.daysmart_inventory_item_id = command.daysmart_inventory_item_id


def _payload(po, line, *, existing_id: int | None = None) -> dict:
    quantity = int(line.quantity)
    line_total = Decimal(line.unit_price_raw) * quantity
    payload = {
        # Wire capture used a string id on line writes.
        "purchase_order_id": str(po.daysmart_id),
        "item_id": line.daysmart_inventory_item_id,
        "item_name": line.description,
        "quantity_ordered": quantity,
        "price": line.unit_price_raw,
        "line_total": str(line_total),
        "notes": line.notes,
    }
    if existing_id is not None:
        payload["purchase_order_item_id"] = existing_id   # save = upsert
    return payload


def _pending(line) -> LineOpResult:
    if line.daysmart_inventory_item_id is None:
        message = "line has no DaySmart inventory item id"
    else:
        message = ("line was never sent to DaySmart — an edit stays local; "
                   "send lines through the add endpoint")
    return LineOpResult(line=line, outcome="pending", message=message)


def _failed(line, outcome: str, exc: Exception, *, message: str | None = None) -> LineOpResult:
    return LineOpResult(line=line, outcome=outcome,
                        error_code=type(exc).__name__,
                        message=message if message is not None else str(exc))


def _attempt(db, po, line, step: str, outcome: str,
             error: Exception | None, *, error_detail: str | None = None) -> None:
    now = _now_iso()
    db.add(models.PoSyncAttempt(
        purchase_order_id=po.id,
        line_id=line.id,
        step=step,
        outcome=outcome,
        error_code=type(error).__name__ if error is not None else None,
        error_detail=error_detail if error_detail is not None else (
            str(error) if error is not None else None),
        started_at=now,
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
