"""Push the ops-DB view of the catalogue to the BizOps Google Sheet."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from permissions import require_capability
from services import audit_log
from services.ops_db_sheet import OpsSheetError, push_published_rows

router = APIRouter(prefix="/ops-db", tags=["ops-db"])


class SheetPushResponse(BaseModel):
    rows_written: int
    columns: int
    tab: str
    sheet_url: str


@router.post("/push-to-sheet", response_model=SheetPushResponse)
def push_to_sheet(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("stock_import")),
) -> SheetPushResponse:
    """Replace the ops sheet's contents with every published catalogue row.

    Published only, by decision: the sheet is read as fact by people costing
    orders, and a row still waiting on review has not been agreed by anyone.
    """
    try:
        result = push_published_rows(db)
    except OpsSheetError as exc:
        # These are conditions a person can act on (no credential, no access,
        # missing tab), so the message travels rather than a bare 500.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    audit_log.record(
        db,
        action="ops_db.sheet_push",
        actor=user,
        entity_type="ops_db",
        entity_id=result.sheet_id,
        entity_label=result.tab,
        details={"rows_written": result.rows_written, "columns": result.columns,
                 "tab": result.tab, "sheet_url": result.sheet_url},
    )
    db.commit()
    return SheetPushResponse(
        rows_written=result.rows_written,
        columns=result.columns,
        tab=result.tab,
        sheet_url=result.sheet_url,
    )
