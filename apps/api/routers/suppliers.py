import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

import models
import database
from permissions import require_capability
from services import audit_log

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(segment: str = Query(None), include_inactive: bool = Query(False),
                   db: Session = Depends(database.get_db)):
    q = db.query(models.Supplier).order_by(models.Supplier.name)
    if not include_inactive:
        q = q.filter(models.Supplier.is_active != 0)   # deactivated legacy hidden by default
    if segment:
        q = q.filter(models.Supplier.segment == segment)
    suppliers = q.all()
    return [
        {"id": s.id, "code": s.code, "name": s.name, "segment": s.segment,
         "contact_name": s.contact_name, "contact_email": s.contact_email,
         "lead_time_days": s.lead_time_days,
         "moq_value": s.moq_value, "credit_term": s.credit_term,
         "order_days": s.order_days, "cut_off_time": s.cut_off_time, "delivery_days": s.delivery_days,
         "brand_count": len(s.brand_links), "alias_count": len(s.aliases),
         "is_active": s.is_active}
        for s in suppliers
    ]


@router.post("/import-master")
def import_supplier_master(
    request: Request,
    dry_run: bool = Query(True),
    credentials_file: str = Query(None),
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("reference_admin")),
):
    """Import/refresh the supplier master from the vet / non-vet / consolidated sheets.
    dry_run=True (default) reports what would change without writing."""
    from services import supplier_import
    try:
        result = supplier_import.run_import(db, dry_run=dry_run, credentials_file=credentials_file)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    if not dry_run:
        audit_log.record(db, action="supplier.import_master", actor=_user, entity_type="supplier",
                         entity_label="master-import",
                         details=(result if isinstance(result, dict) else {"result": str(result)[:500]}),
                         request=request, commit=True)
    return result


@router.post("/reconcile-legacy")
def reconcile_legacy_suppliers(request: Request, dry_run: bool = Query(True),
                               db: Session = Depends(database.get_db),
                               _user: models.User = Depends(require_capability("reference_admin"))):
    """Merge legacy (pre-import) suppliers into the consolidated master: reassign their SKU links
    to the matched master supplier and deactivate the emptied legacy row. dry_run reports the plan."""
    from services import supplier_reconcile
    try:
        result = supplier_reconcile.reconcile_legacy(db, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    if not dry_run:
        audit_log.record(db, action="supplier.reconcile_legacy", actor=_user, entity_type="supplier",
                         entity_label="reconcile-legacy",
                         details=(result if isinstance(result, dict) else {"result": str(result)[:500]}),
                         request=request, commit=True)
    return result


@router.get("/{code}")
def get_supplier(code: str, db: Session = Depends(database.get_db)):
    s = db.query(models.Supplier).filter(models.Supplier.code == code.upper()).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return {"id": s.id, "code": s.code, "name": s.name,
            "contact_name": s.contact_name, "contact_email": s.contact_email,
            "lead_time_days": s.lead_time_days}


@router.get("/category-rules/all")
def list_category_rules(db: Session = Depends(database.get_db)):
    rules = db.query(models.CategoryRule).all()
    return [
        {"category": r.category, "gp_floor": r.gp_floor,
         "storage_rule": r.storage_rule, "channel_restriction": r.channel_restriction}
        for r in rules
    ]


# ── Admin supplier management (audited) ──────────────────────────────────────

_EDITABLE = ["name", "segment", "contact_name", "contact_email", "contact_phone",
             "key_contact", "lead_time_days", "moq_value", "credit_term",
             "order_days", "cut_off_time", "delivery_time", "delivery_days"]


def _gen_code(name: str, used: set) -> str:
    """Auto-assign a supplier code (mirrors services.supplier_import._gen_code):
    first 6 alphanumerics of the name, uppercased; numeric suffix on collision."""
    base = re.sub(r"[^A-Za-z0-9]", "", (name or "X").upper())[:6] or "SUP"
    code, n = base, 1
    while code in used:
        n += 1
        code = f"{base[:5]}{n}"
    return code


class SupplierBody(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    segment: Optional[str] = None            # vet | non_vet | unknown
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    key_contact: Optional[str] = None
    lead_time_days: Optional[int] = None
    moq_value: Optional[str] = None
    credit_term: Optional[str] = None
    order_days: Optional[str] = None
    cut_off_time: Optional[str] = None
    delivery_time: Optional[str] = None
    delivery_days: Optional[str] = None
    is_active: Optional[bool] = None


@router.post("")
def create_supplier(body: SupplierBody, request: Request,
                    db: Session = Depends(database.get_db),
                    admin: models.User = Depends(require_capability("reference_admin"))):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Supplier name is required")
    code = (body.code or "").strip().upper()
    if code:
        # explicit code (API / importer) — must be unique
        if db.query(models.Supplier).filter(models.Supplier.code == code).first():
            raise HTTPException(status_code=409, detail=f"Supplier code {code} already exists")
    else:
        # auto-assign from the name (the UI no longer asks for a code)
        used = {c for (c,) in db.query(models.Supplier.code).all() if c}
        code = _gen_code(name, used)
    now = datetime.utcnow().isoformat()
    sup = models.Supplier(code=code, name=name, created_at=now, updated_at=now,
                          is_active=1, source="manual")
    for f in _EDITABLE:
        v = getattr(body, f)
        if v is not None and f != "name":
            setattr(sup, f, v)
    db.add(sup)
    db.flush()
    audit_log.record(db, action="supplier.create", actor=admin, entity_type="supplier",
                     entity_id=sup.id, entity_label=f"{code} {name}", request=request)
    db.commit()
    return {"id": sup.id, "code": sup.code, "name": sup.name}


@router.patch("/{supplier_id}")
def update_supplier(supplier_id: int, body: SupplierBody, request: Request,
                    db: Session = Depends(database.get_db),
                    admin: models.User = Depends(require_capability("reference_admin"))):
    sup = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found")
    changes = {}
    for f in _EDITABLE:
        v = getattr(body, f)
        if v is not None and getattr(sup, f) != v:
            changes[f] = {"from": getattr(sup, f), "to": v}
            setattr(sup, f, v)
    if body.is_active is not None and bool(sup.is_active) != body.is_active:
        changes["is_active"] = {"from": bool(sup.is_active), "to": body.is_active}
        sup.is_active = 1 if body.is_active else 0
    if changes:
        sup.updated_at = datetime.utcnow().isoformat()
        audit_log.record(db, action="supplier.update", actor=admin, entity_type="supplier",
                         entity_id=sup.id, entity_label=f"{sup.code} {sup.name}",
                         details={"changes": changes}, request=request)
        db.commit()
    return {"id": sup.id, "code": sup.code, "name": sup.name, "changed": list(changes)}
