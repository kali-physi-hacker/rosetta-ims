"""
Outbound sync endpoints.

POST /sync/algo         Pull real sales + inventory expiry from the algo-dashboard Postgres
POST /sync/push-sheet   Push IMS-owned columns TO the reporting Google Sheet (dry-run by default)

The inbound Google Sheet ingestion was retired — the sheet is no longer a
source of product, cost or stock data. Cost lives in supplier offerings and
arrives through catalogue review; stock is adjusted in the app.
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

import database
import models
from services import sheet_push, audit_log, algo_sync
from permissions import require_capability

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/algo")
def sync_from_algo_dashboard(request: Request, db: Session = Depends(database.get_db),
                             user: models.User = Depends(require_capability("sheet"))):
    """Pull real sales (Shopify) + inventory expiry from the algo-dashboard Postgres into IMS."""
    from fastapi import HTTPException
    if not algo_sync.is_configured():
        raise HTTPException(status_code=400,
                            detail="algo-dashboard sync is not configured (ALGO_DASHBOARD_DATABASE_URL unset).")
    audit_log.record(db, action="sync.algo", actor=user, entity_type="algo_dashboard",
                     entity_label="algo-dashboard", request=request, commit=True)
    try:
        return algo_sync.run_algo_sync(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Algo sync failed: {e}")


@router.post("/push-sheet")
def push_to_sheet(
    request: Request,
    dry_run: bool = Query(True, description="Preview only; no cells written"),
    gid: int | None = Query(None, description="Target worksheet gid (default: Operational Database)"),
    start_row: int | None = Query(None, ge=2, description="First data row (default: 5)"),
    limit: int | None = Query(None, ge=1, description="Cap products written (testing)"),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("sheet")),
):
    if not dry_run:
        audit_log.record(db, action="sheet.push", actor=user, entity_type="sheet",
                         entity_label="SSOT sheet", details={"gid": gid, "limit": limit},
                         request=request, commit=True)
    """Push IMS-owned columns into the SSOT sheet. Dry-run by default — returns a
    preview (target tab, columns it would write, sample row). Pass dry_run=false to
    write. TECH columns are never touched."""
    from fastapi import HTTPException
    try:
        return sheet_push.run_push(db, gid=gid, start_row=start_row, dry_run=dry_run, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
