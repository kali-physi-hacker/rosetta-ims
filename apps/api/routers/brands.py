"""Brand list (from the supplier sheets) + add-brand for catalogue onboarding.

The known brands come from the SupplierBrand table (built from the vet / non-vet
supplier sheets). Onboarding matches an item's brand against this list; a brand
that isn't found can be added here (linked to the catalogue's supplier)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional

import models
import database
from dependencies import require_user
from permissions import require_capability
from services import audit_log

router = APIRouter(prefix="/brands", tags=["brands"])


def _norm(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


@router.get("")
def list_brands(db: Session = Depends(database.get_db)):
    """Distinct known brands with a supplier count and FMCG flag, A→Z."""
    rows = (db.query(models.SupplierBrand.normalized_brand,
                     func.min(models.SupplierBrand.brand_name),
                     func.count(func.distinct(models.SupplierBrand.supplier_id)),
                     func.max(models.SupplierBrand.is_fmcg))
            .group_by(models.SupplierBrand.normalized_brand)
            .order_by(func.min(models.SupplierBrand.brand_name)).all())
    return [{"normalized": n, "name": name, "supplier_count": c,
             "is_fmcg": (bool(f) if f is not None else None)}
            for n, name, c, f in rows]


class BrandCreate(BaseModel):
    name: str
    supplier_id: Optional[int] = None
    is_fmcg: Optional[bool] = None


@router.post("")
def add_brand(body: BrandCreate, request: Request, db: Session = Depends(database.get_db),
              user: models.User = Depends(require_capability("reference_admin"))):
    """Add a brand that isn't in the list. Idempotent on normalized name. Links to
    the given supplier (e.g. the catalogue's supplier); supplier_id is required
    because SupplierBrand rows belong to a supplier."""
    name = body.name.strip()
    norm = _norm(name)
    if not norm:
        raise HTTPException(status_code=400, detail="Brand name required")
    existing = db.query(models.SupplierBrand).filter(
        models.SupplierBrand.normalized_brand == norm).first()
    if existing:
        return {"created": False, "name": existing.brand_name, "normalized": norm}
    if body.supplier_id is None:
        raise HTTPException(status_code=400, detail="supplier_id required to add a brand")
    if not db.query(models.Supplier).filter(models.Supplier.id == body.supplier_id).first():
        raise HTTPException(status_code=404, detail=f"Supplier {body.supplier_id} not found")
    sb = models.SupplierBrand(
        supplier_id=body.supplier_id, brand_name=name, normalized_brand=norm,
        is_fmcg=(1 if body.is_fmcg else 0) if body.is_fmcg is not None else None,
        created_at=datetime.utcnow().isoformat())
    db.add(sb); db.flush()
    audit_log.record(db, action="brand.create", actor=user, entity_type="brand",
                     entity_id=sb.id, entity_label=name,
                     details={"name": name, "normalized": norm, "supplier_id": body.supplier_id},
                     request=request)
    db.commit()
    return {"created": True, "name": name, "normalized": norm}


# ── Admin brand management (audited) ─────────────────────────────────────────

@router.get("/detail")
def brand_detail(db: Session = Depends(database.get_db),
                 _: models.User = Depends(require_capability("reference_admin"))):
    """Brands with their supplier links, for the admin manager."""
    sups = {s.id: s for s in db.query(models.Supplier).all()}
    out: dict = {}
    for b in db.query(models.SupplierBrand).all():
        e = out.setdefault(b.normalized_brand, {"normalized": b.normalized_brand,
                                                "name": b.brand_name,
                                                "is_fmcg": bool(b.is_fmcg) if b.is_fmcg is not None else None,
                                                "links": []})
        sup = sups.get(b.supplier_id)
        e["links"].append({"id": b.id, "supplier_id": b.supplier_id,
                           "supplier_name": sup.name if sup else f"#{b.supplier_id}",
                           "supplier_code": sup.code if sup else None})
    return {"brands": sorted(out.values(), key=lambda x: x["name"].lower())}


class BrandRename(BaseModel):
    from_name: str
    to_name: str


@router.patch("/rename")
def rename_brand(body: BrandRename, request: Request,
                 db: Session = Depends(database.get_db),
                 admin: models.User = Depends(require_capability("reference_admin"))):
    """Rename a brand across all its supplier links (and product records that carry it)."""
    frm, to = _norm(body.from_name), body.to_name.strip()
    if not frm or not to:
        raise HTTPException(status_code=400, detail="Both names are required")
    rows = db.query(models.SupplierBrand).filter(models.SupplierBrand.normalized_brand == frm).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Brand not found")
    for r in rows:
        r.brand_name, r.normalized_brand = to, _norm(to)
    n_prod = (db.query(models.Product)
              .filter(models.Product.brand.isnot(None))
              .filter(func.lower(models.Product.brand) == frm)
              .update({models.Product.brand: to}, synchronize_session=False))
    audit_log.record(db, action="brand.rename", actor=admin, entity_type="brand",
                     entity_label=to, details={"from": body.from_name, "to": to,
                                               "links": len(rows), "products": n_prod},
                     request=request)
    db.commit()
    return {"renamed": True, "links": len(rows), "products_updated": n_prod}


class BrandLink(BaseModel):
    name: str
    supplier_id: int


@router.post("/link")
def add_brand_link(body: BrandLink, request: Request,
                   db: Session = Depends(database.get_db),
                   admin: models.User = Depends(require_capability("reference_admin"))):
    """Link an (existing or new) brand to a supplier."""
    name = body.name.strip()
    norm = _norm(name)
    if not norm:
        raise HTTPException(status_code=400, detail="Brand name required")
    sup = db.query(models.Supplier).filter(models.Supplier.id == body.supplier_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found")
    if db.query(models.SupplierBrand).filter(models.SupplierBrand.normalized_brand == norm,
                                             models.SupplierBrand.supplier_id == sup.id).first():
        return {"created": False, "detail": "Link already exists"}
    db.add(models.SupplierBrand(supplier_id=sup.id, brand_name=name, normalized_brand=norm,
                                created_at=datetime.utcnow().isoformat()))
    audit_log.record(db, action="brand.link", actor=admin, entity_type="brand",
                     entity_label=name, details={"supplier": sup.name}, request=request)
    db.commit()
    return {"created": True}


@router.delete("/link/{link_id}")
def remove_brand_link(link_id: int, request: Request,
                      db: Session = Depends(database.get_db),
                      admin: models.User = Depends(require_capability("reference_admin"))):
    row = db.query(models.SupplierBrand).filter(models.SupplierBrand.id == link_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Link not found")
    sup = db.query(models.Supplier).filter(models.Supplier.id == row.supplier_id).first()
    audit_log.record(db, action="brand.unlink", actor=admin, entity_type="brand",
                     entity_label=row.brand_name,
                     details={"supplier": sup.name if sup else row.supplier_id}, request=request)
    db.delete(row)
    db.commit()
    return {"deleted": True}
