"""Purchase-order LINE endpoints — every route lives under its PO.

Lines are not a resource of their own. They are read with the PO
(``GET /purchase-orders/{po_uuid}``), pulled with the PO (``/sync`` and
``/{po_uuid}/refresh`` — header and lines, always together), and changed
here, always through the PO's uuid:

    POST   /purchase-orders/{po_uuid}/lines                add lines
    PUT    /purchase-orders/{po_uuid}/lines/{line_uuid}    edit one line
    DELETE /purchase-orders/{po_uuid}/lines/{line_uuid}    delete one line

Conventions shared with the PO router: a request that reached the flow
answers 200/201 with each line's ACTUAL outcome ('synced' | 'pending' |
'sync_failed' | 'needs_review' | 'deleted') plus the error code when
Daysmart said no or could not be reached; the local edit stays in place
for a deliberate re-run — nothing is retried in the background. What
Rosetta refuses to attempt at all is a 409 with a named code. Mutations
audit in the same transaction and commit once.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import database
import models
from dependencies import require_user
from routers.purchase_orders import (
    _ERROR_CODES,
    _get_po,
    _issue_json,
    _line_json,
    get_po_daysmart,
)
from schemas.purchase_orders import parse_line_commands
from services import audit_log
from services.daysmart_service import DaysmartService
from services.po_line_flow import (
    LineOperationRefused,
    add_purchase_order_lines,
    delete_purchase_order_line,
    update_purchase_order_line,
)

router = APIRouter(prefix="/purchase-orders/{po_uuid}/lines",
                   tags=["purchase-order-lines"])


@router.post("", status_code=201)
def add_po_lines(
    po_uuid: str,
    body: list[dict],
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Stage and send PO lines explicitly, with one outcome per line."""
    parsed = parse_line_commands(body)
    if parsed.lines is None:
        raise HTTPException(status_code=422, detail={
            "code": "PO_LINE_VALIDATION_FAILED",
            "issues": [_issue_json(issue) for issue in parsed.issues],
        })
    po = _get_po(db, po_uuid)
    try:
        results = add_purchase_order_lines(db, po, parsed.lines, daysmart)
    except LineOperationRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc)}) from exc

    counts: dict[str, int] = {}
    serialized = []
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
        item = {"line": _line_json(result.line), "outcome": result.outcome}
        if result.error_code:
            item["error_code"] = _ERROR_CODES.get(result.error_code, result.error_code)
        if result.message:
            item["message"] = result.message
        if result.mirror_error:
            item["mirror_error"] = result.mirror_error
        serialized.append(item)

    audit_log.record(
        db, action="purchase_order.lines_add", actor=user,
        entity_type="purchase_order", entity_id=po.po_uuid,
        details={"counts": counts})
    db.commit()
    return {"po_uuid": po.po_uuid, "results": serialized, "summary": counts}


@router.put("/{line_uuid}")
def edit_po_line(
    po_uuid: str,
    line_uuid: str,
    body: dict,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Edit one line (full line body). Local first; pushed as an UPDATE
    when the line lives in Daysmart, then the PO's lines are pulled."""
    parsed = parse_line_commands([body])
    if parsed.lines is None:
        raise HTTPException(status_code=422, detail={
            "code": "PO_LINE_VALIDATION_FAILED",
            "issues": [_single_line_issue(issue) for issue in parsed.issues],
        })
    po = _get_po(db, po_uuid)
    line = _get_line(db, po, line_uuid)
    try:
        result = update_purchase_order_line(db, po, line, parsed.lines[0], daysmart)
    except LineOperationRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc)}) from exc
    audit_log.record(
        db, action="purchase_order.line_edit", actor=user,
        entity_type="purchase_order", entity_id=po.po_uuid,
        details={"line_uuid": line_uuid, "outcome": result.outcome})
    db.commit()
    return _op_json(po, result)


@router.delete("/{line_uuid}")
def delete_po_line(
    po_uuid: str,
    line_uuid: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_user),
    daysmart: DaysmartService = Depends(get_po_daysmart),
):
    """Delete one line: in Daysmart too when it lives there (the local row
    stays as 'deleted' for audit); a never-sent line is simply removed."""
    po = _get_po(db, po_uuid)
    line = _get_line(db, po, line_uuid)
    try:
        result = delete_purchase_order_line(db, po, line, daysmart)
    except LineOperationRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc)}) from exc
    audit_log.record(
        db, action="purchase_order.line_delete", actor=user,
        entity_type="purchase_order", entity_id=po.po_uuid,
        details={"line_uuid": line_uuid, "outcome": result.outcome})
    db.commit()
    return _op_json(po, result)


# ---------------------------------------------------------------------------


def _get_line(db: Session, po: "models.PurchaseOrder", line_uuid: str) -> "models.PurchaseOrderLine":
    line = (db.query(models.PurchaseOrderLine)
            .filter_by(purchase_order_id=po.id, line_uuid=line_uuid).first())
    if line is None:
        raise HTTPException(status_code=404,
                            detail={"code": "LINE_NOT_FOUND", "message": line_uuid})
    return line


def _single_line_issue(issue) -> dict:
    """The parser reports array positions ("[0].quantity"); a single-line
    body has none."""
    data = _issue_json(issue)
    data["field_path"] = data["field_path"].removeprefix("[0].")
    return data


def _op_json(po: "models.PurchaseOrder", result) -> dict:
    return {
        "po_uuid": po.po_uuid,
        "line": _line_json(result.line) if result.line is not None else None,
        "outcome": result.outcome,
        "error_code": (_ERROR_CODES.get(result.error_code, result.error_code)
                       if result.error_code else None),
        "message": result.message,
        "mirror_error": result.mirror_error,
    }
