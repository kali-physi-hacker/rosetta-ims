"""Purchase-order endpoints (DEV-304) — the front door over the CIS-09
service layer.

Design rules carried from the flow work:

- READS serve LOCAL data only — fast, and alive when Daysmart is down.
  Freshness comes from the sync endpoint / create-time resolution, not
  from per-request Daysmart calls.
- The create endpoint runs the full saga and always answers 201 when the
  PO was persisted — the ``outcome`` field says what Daysmart did
  ('synced' | 'sync_failed' | 'needs_review'). A Daysmart outage is not a
  client error: the draft is saved and retryable.
- Validation failures are 422 with the NAMED issues (all of them at once).
- Submit is the EXPLICIT step (never called by create); posting a receipt
  likewise. Business refusals from Daysmart map to 409; transport
  failures to 502/504.
- Mutations stage audit rows in the same transaction and commit once.
- Fixed paths (/review, /sync) are declared before /{po_uuid} — FastAPI
  matches in declaration order.
- PULLS (Daysmart → Rosetta) are /sync (every order) and /{po_uuid}/refresh
  (one order); both always take header, lines AND receipts, and both
  report a failure right away — nothing is queued for a later cycle.
  Line WRITES live in routers/purchase_order_lines.py (/{po_uuid}/lines),
  receipt WRITES in routers/purchase_order_receipts.py (/{po_uuid}/receipts).

The Daysmart service arrives via the ``get_po_daysmart`` dependency so
tests inject a fake and production wires ``DaysmartService.from_env()``.
"""

from __future__ import annotations

import os
from typing import Any

import hashlib
import json as _json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from dependencies import require_user
from schemas.purchase_orders import parse_create_command, parse_update_command
from services import audit_log
from services.daysmart_service import (
    DaysmartError,
    DaysmartRefused,
    DaysmartService,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_create_flow import create_purchase_order, resubmit_purchase_order
from services.po_header_flow import (
    HeaderOperationRefused,
    delete_purchase_order,
    update_purchase_order,
)
from services.po_inbound_sync import (
    DaysmartOrderUnreadable,
    refresh_purchase_order,
    run_full_inbound_sync,
)
from services.po_sync_state import DaysmartIdentityConflict, assign_daysmart_identity

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


def get_po_daysmart() -> DaysmartService:
    """Dependency seam: overridden with a fake in tests."""
    return DaysmartService.from_env()


def _default_ship_to() -> int:
    return int(os.environ.get("DAYSMART_DEFAULT_SHIP_TO_ID", "861040"))


def _default_bill_to() -> int:
    return int(os.environ.get("DAYSMART_DEFAULT_BILL_TO_ID", "861040"))


def _issue_json(issue) -> dict:
    return {
        "code": issue.code.value,
        "field_path": issue.field_path,
        "raw_value": None if issue.raw_value is None else str(issue.raw_value),
        "expected": issue.expected,
    }


def _line_json(line: "models.PurchaseOrderLine") -> dict:
    return {
        "line_uuid": line.line_uuid,
        "description": line.description,
        "quantity": line.quantity,
        "quantity_uom": line.quantity_uom,
        "unit_price_raw": line.unit_price_raw,
        "display_cost": line.display_cost,
        "notes": line.notes,
        "sync_status": line.sync_status,
        "daysmart_item_id": line.daysmart_item_id,
        "daysmart_inventory_item_id": line.daysmart_inventory_item_id,
        "daysmart_quantity_ordered": line.daysmart_quantity_ordered,
        "daysmart_quantity_received": line.daysmart_quantity_received,
        "daysmart_back_ordered": line.daysmart_back_ordered,
        "daysmart_price_raw": line.daysmart_price_raw,
        "daysmart_is_active": line.daysmart_is_active,
        "last_seen_at": line.last_seen_at,
    }


def _po_json(po: "models.PurchaseOrder", *, include_lines: bool = True) -> dict:
    data = {
        "po_uuid": po.po_uuid,
        "daysmart_id": po.daysmart_id,
        "daysmart_index": po.daysmart_index,
        "sync_status": po.sync_status,
        "supplier_daysmart_id": po.supplier_daysmart_id,
        "supplier_name": po.supplier_name,
        "account_no": po.account_no,
        "order_date": po.order_date,
        "ship_to_id": po.ship_to_id,
        "bill_to_id": po.bill_to_id,
        "notes": po.notes,
        "is_active": po.is_active,
        "sub_total": po.sub_total,
        "total": po.total,
        "daysmart_status": po.daysmart_status,
        "item_count": po.item_count,
        "created_at": po.created_at,
        "updated_at": po.updated_at,
    }
    if include_lines:
        data["lines"] = [_line_json(line) for line in po.lines]
        data["receipts"] = [_receipt_json(receipt) for receipt in po.receipts]
    return data


#: Stable client-facing error codes (exception class name → API vocabulary),
#: consistent with the repo's _detail("CODE", …) convention.
_ERROR_CODES = {
    "DaysmartUnavailable": "DAYSMART_UNAVAILABLE",
    "DaysmartTimeout": "DAYSMART_TIMEOUT",
    "DaysmartAuthFailure": "DAYSMART_AUTH_FAILURE",
    "DaysmartRefused": "DAYSMART_REFUSED",
    "DaysmartMalformedResponse": "DAYSMART_MALFORMED_RESPONSE",
}


def _attempt_json(attempt: "models.PoSyncAttempt") -> dict:
    return {
        "step": attempt.step,
        "outcome": attempt.outcome,
        "error_code": _ERROR_CODES.get(attempt.error_code, attempt.error_code),
        "error_detail": attempt.error_detail,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
    }


def _latest_failure(po: "models.PurchaseOrder") -> dict | None:
    """The newest non-success attempt — the immediate 'why did it fail?'.

    Rides any non-synced creation response so the caller can act without
    database access; the FULL history lives on the detail endpoint. A
    replayed (idempotent) response keeps its original failure — a replay is
    a receipt of what happened, not a status check."""
    failures = [a for a in po.sync_attempts if a.outcome != "success"]
    if not failures:
        return None
    return _attempt_json(max(failures, key=lambda a: a.id))


def _receipt_json(receipt: "models.PoReceipt") -> dict:
    return {
        "receipt_uuid": receipt.receipt_uuid,
        "daysmart_receipt_id": receipt.daysmart_receipt_id,
        "sync_status": receipt.sync_status,
        "post_status": receipt.post_status,
        "invoice_number": receipt.invoice_number,
        "receipt_date": receipt.receipt_date,
        "amount": receipt.amount,
        "notes": receipt.notes,
        # Daysmart's own view (the receipt pull):
        "daysmart_status": receipt.daysmart_status,
        "daysmart_status_label": receipt.daysmart_status_label,
        "daysmart_is_active": receipt.daysmart_is_active,
        "daysmart_amount": receipt.daysmart_amount,
        "daysmart_invoice_number": receipt.daysmart_invoice_number,
        "daysmart_purchase_order_id": receipt.daysmart_purchase_order_id,
        "daysmart_post_at": receipt.daysmart_post_at,
        "last_seen_at": receipt.last_seen_at,
        "items": [{
            "item_uuid": item.item_uuid,
            "daysmart_receipt_item_id": item.daysmart_receipt_item_id,
            "daysmart_po_item_id": item.daysmart_po_item_id,
            "purchase_order_line_id": item.purchase_order_line_id,
            "post_to_item_id": item.post_to_item_id,
            "quantity_received": item.quantity_received,
            "quantity_in_stock": item.quantity_in_stock,
            "amount": item.amount,
            "lot_number": item.lot_number,
            "expiration_date": item.expiration_date,
            "sync_status": item.sync_status,
            "daysmart_quantity_received": item.daysmart_quantity_received,
            "daysmart_quantity_in_stock": item.daysmart_quantity_in_stock,
            "daysmart_quantity_rejected": item.daysmart_quantity_rejected,
            "daysmart_is_active": item.daysmart_is_active,
            "last_seen_at": item.last_seen_at,
        } for item in receipt.items],
    }


def _receipt_pull_json(summary) -> dict | None:
    if summary is None:
        return None
    return {
        "completed": summary.completed, "pages_applied": summary.pages_applied,
        "created": summary.created, "updated": summary.updated,
        "unchanged": summary.unchanged, "unlinked": summary.unlinked,
        "issues_recorded": summary.issues_recorded,
        "rows_synced": summary.rows_synced, "rows_failed": summary.rows_failed,
        "failures": [{"daysmart_receipt_id": f.daysmart_receipt_id,
                      "error_code": _ERROR_CODES.get(f.error_code, f.error_code),
                      "message": f.message} for f in summary.failures],
        "error": summary.error,
    }


def _fingerprint(endpoint: str, body: dict) -> str:
    material = endpoint + "\n" + _json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _idempotency_record(db: Session, key: str | None, endpoint: str, body: dict):
    """The stored record for this key (fingerprint-checked), or None.

    Same key + different material → 409, mirroring the catalogue
    submission convention. What a matching record MEANS is decided by the
    endpoint — see create_po's outcome-aware replay.
    """
    if not key:
        return None
    record = (db.query(models.PoRequestIdempotency)
              .filter_by(idempotency_key=key).first())
    if record is None:
        return None
    if record.material_fingerprint != _fingerprint(endpoint, body):
        raise HTTPException(status_code=409, detail={
            "code": "IDEMPOTENCY_CONFLICT",
            "message": "this Idempotency-Key was already used for a different request"})
    return record


def _idempotency_store(db: Session, key: str | None, endpoint: str,
                       body: dict, response: dict) -> None:
    if not key:
        return
    db.add(models.PoRequestIdempotency(
        idempotency_key=key,
        material_fingerprint=_fingerprint(endpoint, body),
        endpoint=endpoint,
        response_json=_json.dumps(response, ensure_ascii=False),
        created_at=datetime.now(timezone.utc).isoformat(),
    ))


def _pull_after_write(db: Session, po: "models.PurchaseOrder", daysmart) -> str | None:
    """Every write that reached Daysmart ends with a pull of the whole PO —
    header, lines, receipts — so the local copy is current without a
    separate refresh (Moses's rule). Returns the failure text when the
    pull failed; the write itself stands."""
    try:
        refresh_purchase_order(db, po, daysmart)
    except (DaysmartError, DaysmartOrderUnreadable) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _get_po(db: Session, po_uuid: str) -> "models.PurchaseOrder":
    po = db.query(models.PurchaseOrder).filter_by(po_uuid=po_uuid).first()
    if po is None:
        raise HTTPException(status_code=404,
                            detail={"code": "PO_NOT_FOUND", "message": po_uuid})
    return po


# --------------------------------------------------------------------- create


@router.post("", status_code=201)
def create_po(
    body: dict,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    record = _idempotency_record(db, idempotency_key, "create_po", body)
    if record is not None:
        return _replay_or_redrive(db, record, daysmart, user)

    if body.get("lines"):
        # Lines are a separate resource with their own endpoint and their
        # own outcomes — a header create never carries them.
        raise HTTPException(status_code=422, detail={
            "code": "PO_LINES_NOT_ACCEPTED",
            "message": "create the header first, then add lines through "
                       "POST /purchase-orders/{po_uuid}/lines"})
    parsed = parse_create_command(body)
    if parsed.command is None:
        raise HTTPException(status_code=422, detail={
            "code": "PO_VALIDATION_FAILED",
            "issues": [_issue_json(issue) for issue in parsed.issues],
        })

    result = create_purchase_order(
        db, parsed.command, daysmart,
        default_ship_to_id=_default_ship_to(),
        default_bill_to_id=_default_bill_to(),
        created_by_user_id=user.id,
    )
    audit_log.record(db, action="purchase_order.create", actor=user,
                     entity_type="purchase_order", entity_id=result.po.po_uuid,
                     entity_label=result.po.supplier_name,
                     details={"outcome": result.outcome,
                              "daysmart_id": result.po.daysmart_id})
    response = {"outcome": result.outcome, "po": _po_json(result.po)}
    if result.outcome != "synced":
        db.flush()  # attempt rows need their ids for newest-first ordering
        failure = _latest_failure(result.po)
        if failure is not None:
            response["failure"] = failure
    _idempotency_store(db, idempotency_key, "create_po", body, response)
    db.commit()
    return response


#: First-attempt outcomes that provably NEVER took effect in Daysmart —
#: the only classes a same-key repeat may safely re-drive (Stripe
#: semantics: the key exists to make retrying safe, not to forbid it).
_NEVER_LANDED = {"daysmart_down", "auth_failure"}


def _replay_or_redrive(db: Session, record, daysmart, user) -> dict:
    """Outcome-aware idempotent replay for creation.

    synced → replay/report success; provably-never-landed failure →
    re-drive the SAME PO via the lifecycle engine; may-have-landed
    (timeout/quarantine) or refused → replay the stored response and leave
    it to the human verbs (retry / resolve / cancel).
    """
    stored = _json.loads(record.response_json)
    po_uuid = (stored.get("po") or {}).get("po_uuid")
    po = (db.query(models.PurchaseOrder).filter_by(po_uuid=po_uuid).first()
          if po_uuid else None)
    if po is None:
        return stored

    if po.sync_status == "synced":
        # Current truth beats the frozen past (it may have been resolved).
        fresh = {"outcome": "synced", "po": _po_json(po)}
        record.response_json = _json.dumps(fresh, ensure_ascii=False)
        db.commit()
        return fresh

    failure = _latest_failure(po)
    if (po.sync_status in ("draft", "sync_failed")
            and failure is not None and failure["outcome"] in _NEVER_LANDED):
        result = resubmit_purchase_order(db, po, daysmart)
        response = {"outcome": result.outcome, "po": _po_json(result.po)}
        if result.outcome != "synced":
            db.flush()
            fresh_failure = _latest_failure(result.po)
            if fresh_failure is not None:
                response["failure"] = fresh_failure
        record.response_json = _json.dumps(response, ensure_ascii=False)
        audit_log.record(db, action="purchase_order.retry", actor=user,
                         entity_type="purchase_order", entity_id=po.po_uuid,
                         details={"trigger": "idempotency_replay",
                                  "outcome": result.outcome})
        db.commit()
        return response

    return stored


# ------------------------------------------------- fixed paths before uuid


@router.get("")
def list_pos(
    sync_status: str | None = None,
    supplier_daysmart_id: str | None = None,
    page: int = 1,
    per_page: int = 50,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
):
    query = db.query(models.PurchaseOrder)
    if sync_status:
        query = query.filter_by(sync_status=sync_status)
    if supplier_daysmart_id:
        query = query.filter_by(supplier_daysmart_id=supplier_daysmart_id)
    total = query.count()
    items = (query.order_by(models.PurchaseOrder.id.desc())
             .offset((max(page, 1) - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page, "per_page": per_page,
            "items": [_po_json(po, include_lines=False) for po in items]}


@router.get("/suppliers")
def list_suppliers(
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Supplier picker — the source of a PO's supplier_daysmart_id."""
    try:
        return {"suppliers": daysmart.get_suppliers()}
    except DaysmartError as exc:
        raise HTTPException(status_code=502, detail={
            "code": "DAYSMART_UNAVAILABLE", "message": str(exc)}) from exc


@router.get("/item-search")
def item_search(
    q: str,
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Item picker — the ONLY source of a line's DIT-… inventory item id.
    Search by name or SKU; a human (or dropdown) chooses the match."""
    if len(q.strip()) < 2:
        return {"items": []}   # too short to be useful; don't hammer Daysmart
    try:
        return {"items": daysmart.search_inventory_items(q.strip())}
    except DaysmartError as exc:
        raise HTTPException(status_code=502, detail={
            "code": "DAYSMART_UNAVAILABLE", "message": str(exc)}) from exc


@router.get("/review")
def review_buckets(
    limit: int = 50,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
):
    """The three OPS buckets: unsent drafts, failed syncs, open issues."""
    def bucket(query, serialize):
        return {"total": query.count(),
                "items": [serialize(row) for row in query.limit(limit).all()]}

    pos = db.query(models.PurchaseOrder)
    issues = (db.query(models.PoValidationIssueRecord)
              .filter_by(resolution_status="open")
              .order_by(models.PoValidationIssueRecord.id.desc()))
    return {
        "drafts": bucket(pos.filter_by(sync_status="draft"),
                         lambda po: _po_json(po, include_lines=False)),
        "sync_failed": bucket(pos.filter_by(sync_status="sync_failed"),
                              lambda po: _po_json(po, include_lines=False)),
        "open_issues": bucket(issues, lambda rec: {
            "id": rec.id, "code": rec.code, "field_path": rec.field_path,
            "raw_value": rec.raw_value, "expected": rec.expected,
            "daysmart_id": rec.daysmart_id,
            "payload_context": rec.payload_context,
            "created_at": rec.created_at,
        }),
    }


@router.post("/sync")
def run_sync(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """One full PULL (also the initial import): every page of headers, the
    lines of every order on them, then every receipt of every local order
    with its rows — no trigger, no flag. The watermark advances only if the
    whole header cycle completed. A line pull that failed is REPORTED here
    per order (``line_failures``), a receipt-row pull per receipt
    (``receipts.failures``), a receipt-list failure in ``receipts.error`` —
    for a person to re-run, never queued for a later cycle."""
    summary = run_full_inbound_sync(db, daysmart)
    line_failures = [{"daysmart_id": f.daysmart_id,
                      "error_code": _ERROR_CODES.get(f.error_code, f.error_code),
                      "message": f.message} for f in summary.line_failures]
    audit_log.record(db, action="purchase_order.sync", actor=user,
                     entity_type="purchase_orders",
                     details={"completed": summary.completed,
                              "pages": summary.pages_applied,
                              "created": summary.created,
                              "updated": summary.updated,
                              "lines_synced": summary.lines_synced,
                              "lines_failed": summary.lines_failed,
                              "receipts_completed": summary.receipts.completed
                              if summary.receipts else None})
    db.commit()
    return {"completed": summary.completed, "pages_applied": summary.pages_applied,
            "created": summary.created, "updated": summary.updated,
            "unchanged": summary.unchanged,
            "issues_recorded": summary.issues_recorded,
            "watermark": summary.watermark,
            "lines_synced": summary.lines_synced, "lines_failed": summary.lines_failed,
            "line_failures": line_failures,
            "receipts": _receipt_pull_json(summary.receipts)}


# ----------------------------------------------------------- per-PO actions


@router.get("/{po_uuid}")
def get_po_detail(
    po_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
):
    po = _get_po(db, po_uuid)
    attempts = sorted(po.sync_attempts, key=lambda a: a.id, reverse=True)
    return {"po": _po_json(po),
            "sync_attempts": [_attempt_json(a) for a in attempts]}


@router.post("/{po_uuid}/refresh")
def refresh_po(
    po_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Pull THIS purchase order from Daysmart now — header (detail surface),
    lines, and its receipts with their rows, together, always. Every read
    happens before anything is applied: it fails loudly (409 / 502 / 504)
    with nothing applied; nothing is queued for later."""
    po = _get_po(db, po_uuid)
    if po.daysmart_id is None:
        raise HTTPException(status_code=409, detail={
            "code": "PO_NOT_SYNCED",
            "message": "the PO has no Daysmart order yet — nothing to pull"})
    try:
        summary = refresh_purchase_order(db, po, daysmart)
    except DaysmartOrderUnreadable as exc:
        raise HTTPException(status_code=409, detail={
            "code": "DAYSMART_ORDER_UNREADABLE",
            "issues": [_issue_json(issue) for issue in exc.issues]}) from exc
    except DaysmartTimeout as exc:
        raise HTTPException(status_code=504, detail={
            "code": "DAYSMART_TIMEOUT", "message": str(exc)}) from exc
    except DaysmartError as exc:
        raise HTTPException(status_code=502, detail={
            "code": _ERROR_CODES.get(type(exc).__name__, "DAYSMART_UNAVAILABLE"),
            "message": str(exc)}) from exc
    lines = {"created": summary.lines.created, "updated": summary.lines.updated,
             "unchanged": summary.lines.unchanged}
    audit_log.record(db, action="purchase_order.refresh", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"header_changed": summary.header_changed, **lines,
                              "issues_recorded": summary.issues_recorded,
                              "receipts": summary.receipts.rows_synced if summary.receipts else None})
    db.commit()
    return {"header_changed": summary.header_changed, "lines": lines,
            "receipts": _receipt_pull_json(summary.receipts),
            "issues_recorded": summary.issues_recorded, "po": _po_json(po)}


@router.post("/{po_uuid}/submit")
def submit_po(
    po_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    po = _get_po(db, po_uuid)
    if po.daysmart_id is None:
        raise HTTPException(status_code=409, detail={
            "code": "PO_NOT_SYNCED",
            "message": "the PO has no Daysmart order yet — sync or resolve it first"})
    try:
        daysmart.submit_order(po.daysmart_id)
    except DaysmartRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": "DAYSMART_REFUSED", "message": str(exc)}) from exc
    except DaysmartTimeout as exc:
        raise HTTPException(status_code=504, detail={
            "code": "DAYSMART_TIMEOUT",
            "message": "submit timed out — check the order in Daysmart before "
                       "retrying"}) from exc
    except DaysmartError as exc:
        raise HTTPException(status_code=502, detail={
            "code": "DAYSMART_UNAVAILABLE", "message": str(exc)}) from exc

    mirror_error = _pull_after_write(db, po, daysmart)     # status → submitted, locally
    audit_log.record(db, action="purchase_order.submit", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"daysmart_id": po.daysmart_id, "mirror_error": mirror_error})
    db.commit()
    return {"status": "submitted", "mirror_error": mirror_error, "po": _po_json(po)}


@router.post("/{po_uuid}/retry")
def retry_po(
    po_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """The explicit human retry — 'I checked Daysmart, it's not there,
    send it.' Refuses synced/cancelled POs; may-have-landed histories come
    back as needs_review without touching Daysmart."""
    po = _get_po(db, po_uuid)
    try:
        result = resubmit_purchase_order(db, po, daysmart)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "PO_NOT_RETRYABLE", "message": str(exc)}) from exc
    mirror_error = _pull_after_write(db, po, daysmart) if result.outcome == "synced" else None
    audit_log.record(db, action="purchase_order.retry", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"trigger": "manual", "outcome": result.outcome})
    response = {"outcome": result.outcome, "mirror_error": mirror_error, "po": _po_json(result.po)}
    if result.outcome != "synced":
        db.flush()
        failure = _latest_failure(result.po)
        if failure is not None:
            response["failure"] = failure
    db.commit()
    return response


class CancelBody(BaseModel):
    reason: str | None = None


@router.post("/{po_uuid}/cancel")
def cancel_po(
    po_uuid: str,
    body: CancelBody | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
):
    """Discard a failed/unsent draft — LOCAL ONLY, never touches Daysmart.
    The human verb for 'this draft is obsolete (e.g. I already created it
    manually)'. Cancelled POs leave the review buckets and refuse retry."""
    po = _get_po(db, po_uuid)
    if po.sync_status not in ("draft", "sync_failed"):
        raise HTTPException(status_code=409, detail={
            "code": "PO_NOT_CANCELLABLE",
            "message": f"only unsent drafts can be cancelled (status: {po.sync_status})"})
    po.sync_status = "cancelled"
    po.updated_at = datetime.now(timezone.utc).isoformat()
    audit_log.record(db, action="purchase_order.cancel", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"reason": body.reason if body else None})
    db.commit()
    return {"po": _po_json(po, include_lines=False)}


@router.put("/{po_uuid}")
def update_po(
    po_uuid: str,
    body: dict,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Edit the header (account number, order date, ship-to, bill-to,
    notes). A PO in Daysmart is written THROUGH: Daysmart first, then its
    echo is mirrored; a draft is edited locally. Submitted/closed orders
    are refused (409 PO_NOT_OPEN)."""
    parsed = parse_update_command(body)
    if parsed.command is None:
        raise HTTPException(status_code=422, detail={
            "code": "PO_VALIDATION_FAILED",
            "issues": [_issue_json(issue) for issue in parsed.issues]})
    po = _get_po(db, po_uuid)
    try:
        result = update_purchase_order(db, po, parsed.command, daysmart)
    except HeaderOperationRefused as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    audit_log.record(db, action="purchase_order.update", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"outcome": result.outcome})
    db.commit()
    return _header_op_json(po, result)


@router.delete("/{po_uuid}")
def delete_po(
    po_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Delete the order in Daysmart (a SOFT delete there: inactive, notes
    rewritten to "[removed]"); the local row stays as 'deleted'. A draft
    that was never sent is cancelled instead (409 PO_NOT_SYNCED)."""
    po = _get_po(db, po_uuid)
    try:
        result = delete_purchase_order(db, po, daysmart)
    except HeaderOperationRefused as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    audit_log.record(db, action="purchase_order.delete", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"outcome": result.outcome})
    db.commit()
    return _header_op_json(po, result)


def _header_op_json(po: "models.PurchaseOrder", result) -> dict:
    return {
        "outcome": result.outcome,
        "error_code": (_ERROR_CODES.get(result.error_code, result.error_code)
                       if result.error_code else None),
        "message": result.message,
        "po": _po_json(po),
    }


class ResolveBody(BaseModel):
    daysmart_id: int


@router.post("/{po_uuid}/resolve")
def resolve_po(
    po_uuid: str,
    body: ResolveBody,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """OPS supplies the Daysmart order id; we fetch it, verify, and link."""
    po = _get_po(db, po_uuid)
    already = (db.query(models.PurchaseOrder)
               .filter(models.PurchaseOrder.daysmart_id == body.daysmart_id,
                       models.PurchaseOrder.id != po.id).first())
    if already is not None:
        raise HTTPException(status_code=409, detail={
            "code": "DAYSMART_ID_ALREADY_LINKED",
            "message": f"Daysmart order {body.daysmart_id} is already linked "
                       f"to PO {already.po_uuid}"})
    try:
        result = daysmart.get_order(body.daysmart_id)
    except DaysmartError as exc:
        raise HTTPException(status_code=502, detail={
            "code": "DAYSMART_UNAVAILABLE", "message": str(exc)}) from exc
    if result.order is None:
        raise HTTPException(status_code=409, detail={
            "code": "DAYSMART_ORDER_UNREADABLE",
            "issues": [_issue_json(issue) for issue in result.issues]})

    try:
        assign_daysmart_identity(po, daysmart_id=result.order.daysmart_id,
                                 daysmart_index=result.order.index)
    except DaysmartIdentityConflict as exc:
        raise HTTPException(status_code=409, detail={
            "code": "PO_ALREADY_LINKED", "message": str(exc)}) from exc
    po.sync_status = "synced"
    mirror_error = _pull_after_write(db, po, daysmart)     # header, lines, receipts of the linked order
    audit_log.record(db, action="purchase_order.resolve", actor=user,
                     entity_type="purchase_order", entity_id=po.po_uuid,
                     details={"daysmart_id": body.daysmart_id, "mirror_error": mirror_error})
    db.commit()
    return {"mirror_error": mirror_error, "po": _po_json(po)}
