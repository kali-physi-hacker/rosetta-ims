"""
Google Sheet sync endpoints.

POST /sync/sheet        Re-sync all products FROM the SSOT Google Sheet (pull)
GET  /sync/status       When was the last pull and what changed
POST /sync/push-sheet   Push IMS-owned columns TO the SSOT sheet (write; dry-run by default)
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

import database
import models
from services.sheet_sync import run_sync, read_last_sync
from services import sheet_push, audit_log, algo_sync
from permissions import require_capability

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/sheet")
def sync_from_sheet(request: Request, db: Session = Depends(database.get_db),
                    user: models.User = Depends(require_capability("sheet"))):
    """Fetch the Google Sheet SKU master and upsert all products, costs, stock, and sales velocity."""
    audit_log.record(db, action="sheet.pull", actor=user, entity_type="sheet",
                     entity_label="SSOT sheet", request=request, commit=True)
    result = run_sync()
    if "error" in result:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=result["error"])
    return result


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


@router.get("/status")
def sync_status():
    last = read_last_sync()
    if not last:
        return {"synced": False, "synced_at": None}
    return {"synced": True, **last}


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
