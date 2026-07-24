from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, selectinload
from typing import Optional

import models
import database
from services.pricing_service import product_to_dict

router = APIRouter(prefix="/pricing", tags=["pricing"])


def _base_query(db: Session):
    return db.query(models.Product).options(
        selectinload(models.Product.channels),
        selectinload(models.Product.stock_levels),
        selectinload(models.Product.product_suppliers).selectinload(models.ProductSupplier.supplier),
        selectinload(models.Product.sales_velocity),
    )


def _load_cat_rules(db: Session) -> dict:
    rules = db.query(models.CategoryRule).all()
    return {r.category: r for r in rules}


@router.get("")
def get_pricing_matrix(
    category: Optional[str] = Query(None),
    search:   Optional[str] = Query(None),
    db: Session = Depends(database.get_db),
):
    q = _base_query(db).filter(models.Product.status == 'ACTIVE')

    if category:
        q = q.filter(models.Product.category == category)
    if search:
        term = f"%{search}%"
        q = q.filter(
            models.Product.name.ilike(term) |
            models.Product.sku_code.ilike(term)
        )

    products = q.order_by(models.Product.category, models.Product.name).all()
    cat_rules = _load_cat_rules(db)

    rows = [product_to_dict(p, cat_rules) for p in products]

    # Sort: products needing price review first, by largest gap
    def sort_key(r):
        gaps = [c["gap_pct"] for c in r["channels"] if c["gap_pct"] is not None]
        return -(max(gaps) if gaps else 0)

    rows.sort(key=sort_key)

    price_alert_count = sum(
        1 for r in rows
        if any(c["recommendation"] == "Raise price ⚠" for c in r["channels"])
    )

    # Direct Response: skip jsonable_encoder re-walking the full product list (same
    # optimisation as GET /products).
    return JSONResponse({
        "price_alert_count": price_alert_count,
        "total": len(rows),
        "items": rows,
    })


@router.get("/{sku}")
def get_product_pricing(sku: str, db: Session = Depends(database.get_db)):
    product = _base_query(db).filter(models.Product.sku_code == sku).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    cat_rules = _load_cat_rules(db)
    return product_to_dict(product, cat_rules)
