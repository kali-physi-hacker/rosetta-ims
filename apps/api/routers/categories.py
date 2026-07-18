"""Item-category management — the single IMS category list (GP floor, storage,
SKU leading digit). Editable so categories can be added without a code change."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

import models
import database
from dependencies import require_user
from permissions import require_capability
from services import audit_log

router = APIRouter(prefix="/category-rules", tags=["categories"])


def _to_dict(r: models.CategoryRule) -> dict:
    return {"category": r.category, "gp_floor": r.gp_floor, "storage_rule": r.storage_rule,
            "channel_restriction": r.channel_restriction, "sku_digit": r.sku_digit}


def _valid_digit(d: str) -> bool:
    return isinstance(d, str) and len(d) == 1 and d in "123456789"


@router.get("")
def list_rules(db: Session = Depends(database.get_db)):
    rules = db.query(models.CategoryRule).order_by(models.CategoryRule.category).all()
    return [_to_dict(r) for r in rules]


class RuleCreate(BaseModel):
    category: str
    gp_floor: float
    storage_rule: str = "any"
    channel_restriction: Optional[str] = None
    sku_digit: str


@router.post("")
def create_rule(body: RuleCreate, request: Request, db: Session = Depends(database.get_db),
                user: models.User = Depends(require_capability("reference_admin"))):
    cat = body.category.strip()
    if not cat:
        raise HTTPException(status_code=400, detail="Category name required")
    if not _valid_digit(body.sku_digit):
        raise HTTPException(status_code=400, detail="sku_digit must be a single digit 1-9")
    if db.query(models.CategoryRule).filter(models.CategoryRule.category == cat).first():
        raise HTTPException(status_code=409, detail=f"Category '{cat}' already exists")
    r = models.CategoryRule(
        category=cat, gp_floor=body.gp_floor,
        storage_rule=(body.storage_rule or "any"),
        channel_restriction=body.channel_restriction or None,
        sku_digit=body.sku_digit)
    db.add(r)
    audit_log.record(db, action="category.create", actor=user, entity_type="category",
                     entity_id=cat, entity_label=cat, details=_to_dict(r), request=request)
    db.commit()
    return _to_dict(r)


class RuleUpdate(BaseModel):
    gp_floor: Optional[float] = None
    storage_rule: Optional[str] = None
    channel_restriction: Optional[str] = None
    sku_digit: Optional[str] = None


@router.patch("/{category}")
def update_rule(category: str, body: RuleUpdate, request: Request, db: Session = Depends(database.get_db),
                user: models.User = Depends(require_capability("reference_admin"))):
    r = db.query(models.CategoryRule).filter(models.CategoryRule.category == category).first()
    if not r:
        raise HTTPException(status_code=404, detail="Category not found")
    before = _to_dict(r)
    if body.gp_floor is not None:            r.gp_floor = body.gp_floor
    if body.storage_rule is not None:        r.storage_rule = body.storage_rule or "any"
    if body.channel_restriction is not None: r.channel_restriction = body.channel_restriction or None
    if body.sku_digit is not None:
        if not _valid_digit(body.sku_digit):
            raise HTTPException(status_code=400, detail="sku_digit must be a single digit 1-9")
        r.sku_digit = body.sku_digit
    changes = audit_log.diff(before, _to_dict(r))
    if changes:
        audit_log.record(db, action="category.update", actor=user, entity_type="category",
                         entity_id=category, entity_label=category, details={"changes": changes},
                         request=request)
    db.commit()
    return _to_dict(r)


@router.delete("/{category}")
def delete_rule(category: str, request: Request, db: Session = Depends(database.get_db),
                user: models.User = Depends(require_capability("reference_admin"))):
    r = db.query(models.CategoryRule).filter(models.CategoryRule.category == category).first()
    if not r:
        raise HTTPException(status_code=404, detail="Category not found")
    in_use = db.query(models.Product).filter(models.Product.category == category).count()
    audit_log.record(db, action="category.delete", actor=user, entity_type="category",
                     entity_id=category, entity_label=category,
                     details={**_to_dict(r), "products_still_tagged": in_use}, request=request)
    db.delete(r); db.commit()
    return {"deleted": category, "products_still_tagged": in_use}
