"""Purchase-order RECEIPT endpoints — every route lives under its PO.

Receipts are read with the PO (``GET /purchase-orders/{po_uuid}`` lists
them with their rows), pulled with the PO (``/sync``, ``/{po_uuid}/refresh``),
and changed here, always through the PO's uuid:

    POST /purchase-orders/{po_uuid}/receipts                               create
    PUT  /purchase-orders/{po_uuid}/receipts/{receipt_uuid}                edit the header
    PUT  /purchase-orders/{po_uuid}/receipts/{receipt_uuid}/items/{item_uuid}  edit one row
    POST /purchase-orders/{po_uuid}/receipts/{receipt_uuid}/post           post (commit stock)

Create works the way Daysmart's own screen does: the header alone is a
complete receipt (Daysmart generates one row per PO line, received as
ordered, and we mirror them); optional ``items`` are ADJUSTMENTS naming
one of this PO's lines by ``line_uuid``. The two edits are the review of
received against ordered: a full replacement of the fields we own, pushed
to Daysmart, then the receipt is pulled so our copy shows what Daysmart
stored. Same conventions as the line router: the local edit stands, the
outcome and the error code come back, nothing is retried in the
background; what Rosetta refuses to attempt is a 409 with a named code
(a posted receipt is closed in Daysmart). Mutations audit in-transaction.
"""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

import database
import models
from dependencies import require_user
from routers.purchase_orders import (
    _ERROR_CODES,
    _get_po,
    _idempotency_record,
    _idempotency_store,
    _receipt_json,
    get_po_daysmart,
)
from services import audit_log
from services.daysmart_service import DaysmartError, DaysmartService
from services.po_receipts_inbound import refresh_receipt
from services.po_receipt_flow import (
    ReceiptDraft,
    ReceiptHeaderDraft,
    ReceiptItemChanges,
    ReceiptItemDraft,
    ReceiptOperationRefused,
    ReceiptValidationError,
    create_receipt_for_po,
    post_receipt_flow,
    retry_receipt,
    update_receipt_header,
    update_receipt_row,
)

router = APIRouter(prefix="/purchase-orders/{po_uuid}/receipts",
                   tags=["purchase-order-receipts"])


@router.post("", status_code=201)
def create_receipt(
    po_uuid: str,
    body: dict,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Create a receipt: header alone is complete; ``items`` are adjustments."""
    record = _idempotency_record(db, idempotency_key, f"create_receipt:{po_uuid}", body)
    if record is not None:
        return _replay_or_redrive(db, record, daysmart, user)

    po = _get_po(db, po_uuid)
    raw_items = body.get("items") or []
    draft = ReceiptDraft(
        invoice_number=str(body.get("invoice_number", "")),
        receipt_date=str(body.get("receipt_date", "")),
        shipping=body.get("shipping"),
        tax=body.get("tax"),
        tax_type=body.get("tax_type"),
        notes=str(body.get("notes", "")),
        items=[ReceiptItemDraft(
            quantity_received=item.get("quantity_received"),
            quantity_in_stock=item.get("quantity_in_stock"),
            amount=str(item.get("amount", "")),
            line_uuid=item.get("line_uuid"),
            post_to_item_id=item.get("post_to_item_id"),
            daysmart_po_item_id=item.get("daysmart_po_item_id"),
            purchase_order_line_id=item.get("purchase_order_line_id"),
            expiration_date=item.get("expiration_date"),
            lot_number=str(item.get("lot_number", "")),
            manufacturer=item.get("manufacturer"),
            ndc_code=item.get("ndc_code"),
            tax=item.get("tax"),
        ) for item in raw_items],
    )
    try:
        result = create_receipt_for_po(db, po, draft, daysmart,
                                       created_by_user_id=user.id)
    except ReceiptValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "RECEIPT_VALIDATION_FAILED", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "PO_NOT_SYNCED", "message": str(exc)}) from exc
    audit_log.record(db, action="purchase_order.receipt_create", actor=user,
                     entity_type="po_receipt", entity_id=result.receipt.receipt_uuid,
                     details={"outcome": result.outcome,
                              "invoice_number": draft.invoice_number})
    db.flush()
    response = _receipt_response(result.receipt, result)
    _idempotency_store(db, idempotency_key, f"create_receipt:{po_uuid}", body, response)
    db.commit()
    return response


#: First-attempt outcomes that provably NEVER took effect in Daysmart —
#: the only classes a same-key repeat (or a retry) may safely re-drive.
_NEVER_LANDED = {"daysmart_down", "auth_failure"}


def _receipt_response(receipt, result) -> dict:
    response = {"outcome": result.outcome, "receipt": _receipt_json(receipt),
                "generated_rows": result.generated_rows,
                "adjustments_applied": result.adjustments_applied,
                "mirror_error": result.mirror_error}
    if result.outcome != "synced":
        failure = _latest_receipt_failure(receipt)
        if failure is not None:
            response["failure"] = failure
    return response


def _latest_receipt_failure(receipt) -> dict | None:
    failures = [a for a in receipt.purchase_order.sync_attempts
                if a.receipt_id == receipt.id and a.outcome != "success"]
    if not failures:
        return None
    a = max(failures, key=lambda a: a.id)
    return {"step": a.step, "outcome": a.outcome,
            "error_code": _ERROR_CODES.get(a.error_code, a.error_code),
            "error_detail": a.error_detail, "started_at": a.started_at, "finished_at": a.finished_at}


def _replay_or_redrive(db: Session, record, daysmart, user) -> dict:
    """Outcome-aware idempotent replay for receipt creation, like POs:
    synced → current truth; provably-never-landed → re-drive the SAME
    receipt; may-have-landed or refused → the stored answer, and a human
    decides (retry / delete)."""
    stored = _json.loads(record.response_json)
    receipt_uuid = (stored.get("receipt") or {}).get("receipt_uuid")
    receipt = (db.query(models.PoReceipt).filter_by(receipt_uuid=receipt_uuid).first()
               if receipt_uuid else None)
    if receipt is None:
        return stored
    if receipt.sync_status == "synced" and receipt.daysmart_receipt_id is not None:
        fresh = {**stored, "outcome": "synced", "receipt": _receipt_json(receipt)}
        fresh.pop("failure", None)
        record.response_json = _json.dumps(fresh, ensure_ascii=False)
        db.commit()
        return fresh
    failure = _latest_receipt_failure(receipt)
    if (receipt.daysmart_receipt_id is None and failure is not None
            and failure["outcome"] in _NEVER_LANDED):
        result = retry_receipt(db, receipt, daysmart)
        db.flush()
        response = _receipt_response(receipt, result)
        record.response_json = _json.dumps(response, ensure_ascii=False)
        audit_log.record(db, action="purchase_order.receipt_retry", actor=user,
                         entity_type="po_receipt", entity_id=receipt.receipt_uuid,
                         details={"trigger": "idempotency_replay", "outcome": result.outcome})
        db.commit()
        return response
    return stored


@router.post("/{receipt_uuid}/retry")
def retry_receipt_endpoint(
    po_uuid: str,
    receipt_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Re-drive a receipt whose create or follow-up failed. A first attempt
    that MAY have landed (timeout, refusal) is not re-sent — it answers
    needs_review for a human to check Daysmart."""
    po = _get_po(db, po_uuid)
    receipt = _get_receipt(db, po, receipt_uuid)
    try:
        result = retry_receipt(db, receipt, daysmart)
    except ReceiptOperationRefused as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "PO_NOT_SYNCED", "message": str(exc)}) from exc
    db.flush()
    audit_log.record(db, action="purchase_order.receipt_retry", actor=user,
                     entity_type="po_receipt", entity_id=receipt.receipt_uuid,
                     details={"outcome": result.outcome})
    db.commit()
    return _receipt_response(receipt, result)


@router.put("/{receipt_uuid}")
def edit_receipt_header(
    po_uuid: str,
    receipt_uuid: str,
    body: dict,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Edit the header fields we own (full replacement): invoice number,
    date, shipping, tax, notes."""
    po = _get_po(db, po_uuid)
    receipt = _get_receipt(db, po, receipt_uuid)
    draft = ReceiptHeaderDraft(
        invoice_number=str(body.get("invoice_number", "")),
        receipt_date=str(body.get("receipt_date", "")),
        shipping=body.get("shipping"),
        tax=body.get("tax"),
        notes=str(body.get("notes", "")),
    )
    try:
        result = update_receipt_header(db, receipt, draft, daysmart)
    except ReceiptValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "RECEIPT_VALIDATION_FAILED", "message": str(exc)}) from exc
    except ReceiptOperationRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc)}) from exc
    audit_log.record(db, action="purchase_order.receipt_edit", actor=user,
                     entity_type="po_receipt", entity_id=receipt.receipt_uuid,
                     details={"outcome": result.outcome})
    db.commit()
    return _op_json(receipt, result)


@router.put("/{receipt_uuid}/items/{item_uuid}")
def edit_receipt_row(
    po_uuid: str,
    receipt_uuid: str,
    item_uuid: str,
    body: dict,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Edit one received row (full replacement of the fields we own): the
    review of received against ordered."""
    po = _get_po(db, po_uuid)
    receipt = _get_receipt(db, po, receipt_uuid)
    item = next((i for i in receipt.items if i.item_uuid == item_uuid), None)
    if item is None:
        raise HTTPException(status_code=404, detail={
            "code": "RECEIPT_ITEM_NOT_FOUND", "message": item_uuid})
    changes = ReceiptItemChanges(
        quantity_received=body.get("quantity_received"),
        quantity_in_stock=body.get("quantity_in_stock"),
        amount=str(body.get("amount", "")),
        expiration_date=body.get("expiration_date"),
        lot_number=str(body.get("lot_number", "")),
        manufacturer=body.get("manufacturer"),
        ndc_code=body.get("ndc_code"),
        tax=body.get("tax"),
        post_to_item_id=body.get("post_to_item_id"),
    )
    if not isinstance(changes.quantity_received, int) or not isinstance(changes.quantity_in_stock, int):
        raise HTTPException(status_code=422, detail={
            "code": "RECEIPT_VALIDATION_FAILED",
            "message": "quantity_received and quantity_in_stock must be integers"})
    try:
        result = update_receipt_row(db, receipt, item, changes, daysmart)
    except ReceiptValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "RECEIPT_VALIDATION_FAILED", "message": str(exc)}) from exc
    except ReceiptOperationRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc)}) from exc
    audit_log.record(db, action="purchase_order.receipt_row_edit", actor=user,
                     entity_type="po_receipt", entity_id=receipt.receipt_uuid,
                     details={"item_uuid": item_uuid, "outcome": result.outcome})
    db.commit()
    return _op_json(receipt, result)


@router.post("/{receipt_uuid}/post")
def post_receipt(
    po_uuid: str,
    receipt_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    po = _get_po(db, po_uuid)
    receipt = _get_receipt(db, po, receipt_uuid)
    try:
        outcome = post_receipt_flow(db, receipt, daysmart)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "RECEIPT_NOT_SYNCED", "message": str(exc)}) from exc
    if outcome == "refused":
        db.commit()  # the attempt row is still worth keeping
        raise HTTPException(status_code=409, detail={
            "code": "DAYSMART_REFUSED",
            "message": "Daysmart refused to post the receipt (pending items?)"})
    mirror_error = None
    if outcome == "posted":
        try:
            refresh_receipt(db, receipt, daysmart)      # Daysmart's own 'Posted' lands locally
        except DaysmartError as exc:
            mirror_error = f"{type(exc).__name__}: {exc}"
    audit_log.record(db, action="purchase_order.receipt_post", actor=user,
                     entity_type="po_receipt", entity_id=receipt.receipt_uuid,
                     details={"outcome": outcome, "mirror_error": mirror_error})
    db.commit()
    return {"status": outcome, "mirror_error": mirror_error, "receipt": _receipt_json(receipt)}


# ---------------------------------------------------------------------------


def _get_receipt(db: Session, po: "models.PurchaseOrder", receipt_uuid: str) -> "models.PoReceipt":
    receipt = (db.query(models.PoReceipt)
               .filter_by(receipt_uuid=receipt_uuid, purchase_order_id=po.id).first())
    if receipt is None:
        raise HTTPException(status_code=404, detail={
            "code": "RECEIPT_NOT_FOUND", "message": receipt_uuid})
    return receipt


def _op_json(receipt: "models.PoReceipt", result) -> dict:
    return {
        "outcome": result.outcome,
        "error_code": (_ERROR_CODES.get(result.error_code, result.error_code)
                       if result.error_code else None),
        "message": result.message,
        "mirror_error": result.mirror_error,
        "receipt": _receipt_json(receipt),
    }
