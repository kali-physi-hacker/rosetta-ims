from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

import database
from services.sku_service import next_sku, CATEGORY_PREFIX

router = APIRouter(prefix="/sku", tags=["sku"])


@router.get("/next")
def preview_next_sku(
    category: str = Query(..., description="Product category"),
    db: Session = Depends(database.get_db),
):
    import models
    rule = db.query(models.CategoryRule).filter(models.CategoryRule.category == category).first()
    if not rule and category not in CATEGORY_PREFIX:
        known = [r.category for r in db.query(models.CategoryRule.category).all()] or list(CATEGORY_PREFIX)
        return {"error": f"Unknown category '{category}'", "known_categories": known}
    sku = next_sku(category, db)
    return {"category": category, "next_sku": sku}
