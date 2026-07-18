"""Smart collections — saved rules that dynamically select products."""
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

import models
import database
from services import collection_service, audit_log
from dependencies import require_user
from permissions import require_capability

router = APIRouter(prefix="/collections", tags=["collections"])

NOW = lambda: datetime.utcnow().isoformat()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "collection"


def _unique_slug(db, name: str, exclude_id: int | None = None) -> str:
    base = _slugify(name)
    slug, n = base, 2
    while True:
        q = db.query(models.Collection).filter(models.Collection.slug == slug)
        if exclude_id is not None:
            q = q.filter(models.Collection.id != exclude_id)
        if not q.first():
            return slug
        slug = f"{base}-{n}"; n += 1


def _to_dict(c: models.Collection, count: int | None = None) -> dict:
    try: rule = json.loads(c.rule_json)
    except Exception: rule = {"match": "all", "conditions": []}
    d = {"id": c.id, "name": c.name, "slug": c.slug, "description": c.description,
         "rule": rule, "is_smart": bool(c.is_smart), "ai_generated": bool(c.ai_generated),
         "created_by": c.created_by, "created_at": c.created_at, "updated_at": c.updated_at}
    if count is not None:
        d["count"] = count
    return d


class CollectionBody(BaseModel):
    name: str
    description: Optional[str] = None
    rule: dict
    ai_generated: Optional[bool] = False


class RuleBody(BaseModel):
    rule: dict


@router.get("")
def list_collections(db: Session = Depends(database.get_db)):
    cols = db.query(models.Collection).order_by(models.Collection.name).all()
    if not cols:
        return []
    dicts, tmap = collection_service.load_products(db)   # load once, count each
    out = []
    for c in cols:
        try: rule = json.loads(c.rule_json)
        except Exception: rule = {}
        out.append(_to_dict(c, count=len(collection_service.evaluate(rule, dicts, tmap))))
    return out


@router.get("/fields")
def rule_fields():
    """Field/operator vocabulary for the rule builder."""
    return {
        "fields": collection_service.FIELDS,
        "numeric_fields": sorted(collection_service.NUMERIC_FIELDS),
        "string_ops": sorted(collection_service.STRING_OPS),
        "numeric_ops": sorted(collection_service.NUMERIC_OPS),
        "tag_ops": sorted(collection_service.TAG_OPS),
    }


@router.post("/preview")
def preview(body: RuleBody, db: Session = Depends(database.get_db)):
    """Evaluate a rule without saving — returns the live count + a small sample."""
    try:
        rule = collection_service.validate_rule(body.rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dicts, tmap = collection_service.load_products(db)
    members = collection_service.evaluate(rule, dicts, tmap)
    sample = [{"sku_code": m["sku_code"], "name": m.get("name"), "category": m.get("category"),
               "brand": m.get("brand")} for m in members[:8]]
    return {"count": len(members), "sample": sample, "rule": rule}


@router.post("/suggest")
def suggest(db: Session = Depends(database.get_db), user: models.User = Depends(require_capability("reference_admin"))):
    """AI-proposed draft collections (not saved). Humans review and POST the keepers."""
    return {"drafts": collection_service.suggest_collections(db)}


@router.post("")
def create_collection(body: CollectionBody, request: Request, db: Session = Depends(database.get_db),
                      user: models.User = Depends(require_capability("reference_admin"))):
    try:
        rule = collection_service.validate_rule(body.rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    now = NOW()
    c = models.Collection(
        name=body.name.strip(), slug=_unique_slug(db, body.name),
        description=body.description, rule_json=json.dumps(rule),
        is_smart=1, ai_generated=1 if body.ai_generated else 0,
        created_by=user.display_name, created_at=now, updated_at=now)
    db.add(c); db.flush()
    audit_log.record(db, action="collection.create", actor=user, entity_type="collection",
                     entity_id=c.id, entity_label=c.name,
                     details={"name": c.name, "slug": c.slug, "rule": rule,
                              "ai_generated": bool(c.ai_generated)}, request=request)
    db.commit(); db.refresh(c)
    return _to_dict(c)


@router.post("/import-shopify")
def import_shopify(request: Request, replace: bool = Query(True), db: Session = Depends(database.get_db),
                   user: models.User = Depends(require_capability("reference_admin"))):
    """Import the store's real Shopify smart collections (from backend/seed_collections.json)
    as IMS smart collections, translating each Shopify ruleSet into our rule format.
    replace=true (default) clears existing collections first so the set mirrors Shopify exactly.
    Collections whose rules are purely VARIANT_PRICE catch-alls (e.g. 'ALL') are skipped."""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_collections.json")
    try:
        with open(path) as f:
            shop_cols = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="seed_collections.json not found on server")

    now = NOW()
    if replace:
        db.query(models.Collection).delete(synchronize_session=False)
        db.flush()

    imported, skipped, by_slug = 0, [], {}
    for sc in shop_cols:
        title = (sc.get("title") or "").strip()
        handle = (sc.get("handle") or "").strip() or _slugify(title)
        ims_rule = collection_service.shopify_ruleset_to_ims(sc.get("ruleSet"))
        if not title or not ims_rule:
            skipped.append(title or handle)
            continue
        try:
            rule = collection_service.validate_rule(ims_rule)
        except ValueError:
            skipped.append(title)
            continue
        # de-dupe by handle within this run (Shopify has a few duplicate-title collections)
        if handle in by_slug:
            handle = f"{handle}-{len(by_slug)}"
        by_slug[handle] = True
        db.add(models.Collection(
            name=title, slug=handle, description="Imported from Shopify",
            rule_json=json.dumps(rule), is_smart=1, ai_generated=0,
            created_by="shopify-import", created_at=now, updated_at=now))
        imported += 1
    audit_log.record(db, action="collection.import", actor=user, entity_type="collection",
                     entity_label="shopify-import",
                     details={"imported": imported, "skipped": len(skipped),
                              "replaced_existing": replace}, request=request)
    db.commit()
    return {"imported": imported, "skipped": len(skipped), "skipped_titles": skipped[:20],
            "replaced_existing": replace}


@router.get("/{collection_id}")
def get_collection(collection_id: int, db: Session = Depends(database.get_db)):
    c = db.query(models.Collection).filter(models.Collection.id == collection_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Collection not found")
    return _to_dict(c)


@router.get("/{collection_id}/products")
def collection_products(collection_id: int, db: Session = Depends(database.get_db)):
    c = db.query(models.Collection).filter(models.Collection.id == collection_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Collection not found")
    try: rule = json.loads(c.rule_json)
    except Exception: rule = {}
    dicts, tmap = collection_service.load_products(db)
    members = collection_service.evaluate(rule, dicts, tmap)
    return {"collection": _to_dict(c, count=len(members)), "products": members}


@router.patch("/{collection_id}")
def update_collection(collection_id: int, body: CollectionBody, request: Request,
                      db: Session = Depends(database.get_db),
                      user: models.User = Depends(require_capability("reference_admin"))):
    c = db.query(models.Collection).filter(models.Collection.id == collection_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        rule = collection_service.validate_rule(body.rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try: _old_rule = json.loads(c.rule_json)
    except Exception: _old_rule = None
    before = {"name": c.name, "description": c.description, "rule": _old_rule}
    if body.name and body.name.strip() != c.name:
        c.name = body.name.strip()
        c.slug = _unique_slug(db, body.name, exclude_id=c.id)
    c.description = body.description
    c.rule_json = json.dumps(rule)
    c.updated_at = NOW()
    changes = audit_log.diff(before, {"name": c.name, "description": c.description, "rule": rule})
    if changes:
        audit_log.record(db, action="collection.update", actor=user, entity_type="collection",
                         entity_id=c.id, entity_label=c.name, details={"changes": changes},
                         request=request)
    db.commit(); db.refresh(c)
    return _to_dict(c)


@router.delete("/{collection_id}")
def delete_collection(collection_id: int, request: Request, db: Session = Depends(database.get_db),
                      user: models.User = Depends(require_capability("reference_admin"))):
    c = db.query(models.Collection).filter(models.Collection.id == collection_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Collection not found")
    audit_log.record(db, action="collection.delete", actor=user, entity_type="collection",
                     entity_id=c.id, entity_label=c.name,
                     details={"name": c.name, "slug": c.slug}, request=request)
    db.delete(c); db.commit()
    return {"deleted": collection_id}
