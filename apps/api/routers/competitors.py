"""Competitor price links + scraping.

BOs link competitor product URLs to a SKU; a scrape fetches each one's current selling price
(Shopify JSON where possible, HTML otherwise). Endpoints:
  GET    /competitors/by-sku/{sku}   list a SKU's competitors + latest prices
  POST   /competitors/by-sku/{sku}   add a competitor URL (auto-names + scrapes it once)
  DELETE /competitors/{id}           remove a competitor link
  POST   /competitors/{id}/refresh   re-scrape one link
  POST   /competitors/refresh        re-scrape one product's links (body: {product_id}) — the per-SKU button
  POST   /competitors/refresh-all    re-scrape EVERYTHING in the background — the global button
"""
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from dependencies import require_user
from permissions import require_capability
from services import competitor_scraper as scraper, audit_log

router = APIRouter(prefix="/competitors", tags=["competitors"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(cp: models.CompetitorPrice) -> dict:
    return {
        "id": cp.id, "product_id": cp.product_id, "competitor_name": cp.competitor_name,
        "url": cp.url, "platform": cp.platform, "price": cp.price, "in_stock": cp.in_stock,
        "title": cp.title, "last_checked": cp.last_checked, "last_status": cp.last_status,
        "notes": cp.notes,
    }


def _product_by_sku(db: Session, sku: str) -> models.Product:
    p = db.query(models.Product).filter(models.Product.sku_code == sku).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@router.get("/by-sku/{sku:path}")
def list_for_sku(sku: str, db: Session = Depends(database.get_db),
                 _user: models.User = Depends(require_user)):
    p = _product_by_sku(db, sku)
    rows = (db.query(models.CompetitorPrice)
              .filter(models.CompetitorPrice.product_id == p.id).all())
    prices = [r.price for r in rows if r.price is not None]
    rows.sort(key=lambda r: (r.price is None, r.price or 0))
    return {
        "sku_code": sku,
        "cheapest": min(prices) if prices else None,
        "competitors": [_to_dict(r) for r in rows],
    }


class AddBody(BaseModel):
    url: str
    competitor_name: str | None = None
    notes: str | None = None


@router.post("/by-sku/{sku:path}")
def add_for_sku(sku: str, body: AddBody, request: Request, db: Session = Depends(database.get_db),
                _user: models.User = Depends(require_capability("product_edit"))):
    p = _product_by_sku(db, sku)
    url = (body.url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="A valid http(s) competitor URL is required.")
    name = (body.competitor_name or "").strip() or scraper.guess_name(url)
    now = _now()
    cp = models.CompetitorPrice(
        product_id=p.id, competitor_name=name, url=url,
        platform=scraper.detect_platform(url), notes=(body.notes or None),
        created_at=now, updated_at=now)
    db.add(cp)
    db.flush()
    audit_log.record(db, action="competitor.add", actor=_user, entity_type="competitor_price",
                     entity_id=cp.id, entity_label=f"{name} · {p.sku_code}",
                     details={"sku": p.sku_code, "competitor": name, "url": url,
                              "platform": cp.platform}, request=request)
    scraper.scrape_rows(db, [cp])   # fetch its price right away (also commits the audit row)
    db.refresh(cp)
    return _to_dict(cp)


@router.delete("/{cid}")
def delete_one(cid: int, request: Request, db: Session = Depends(database.get_db),
               _user: models.User = Depends(require_capability("product_edit"))):
    cp = db.query(models.CompetitorPrice).filter(models.CompetitorPrice.id == cid).first()
    if not cp:
        raise HTTPException(status_code=404, detail="Competitor link not found")
    sku = db.query(models.Product.sku_code).filter(models.Product.id == cp.product_id).scalar()
    audit_log.record(db, action="competitor.delete", actor=_user, entity_type="competitor_price",
                     entity_id=cp.id, entity_label=f"{cp.competitor_name} · {sku or cp.product_id}",
                     details={"sku": sku, "competitor": cp.competitor_name, "url": cp.url,
                              "price": cp.price, "last_status": cp.last_status}, request=request)
    db.delete(cp)
    db.commit()
    return {"deleted": cid}


@router.post("/{cid}/refresh")
def refresh_one(cid: int, request: Request, db: Session = Depends(database.get_db),
                _user: models.User = Depends(require_capability("product_edit"))):
    cp = db.query(models.CompetitorPrice).filter(models.CompetitorPrice.id == cid).first()
    if not cp:
        raise HTTPException(status_code=404, detail="Competitor link not found")
    old_price = cp.price
    scraper.scrape_rows(db, [cp])
    db.refresh(cp)
    audit_log.record(db, action="competitor.refresh", actor=_user, entity_type="competitor_price",
                     entity_id=cp.id, entity_label=cp.competitor_name,
                     details={"url": cp.url, "price": {"from": old_price, "to": cp.price},
                              "status": cp.last_status}, request=request, commit=True)
    return _to_dict(cp)


class RefreshBody(BaseModel):
    product_id: int


@router.post("/refresh")
def refresh_product(body: RefreshBody, request: Request, db: Session = Depends(database.get_db),
                    _user: models.User = Depends(require_capability("product_edit"))):
    """Re-scrape ONE product's competitor links synchronously (few URLs, fast) — the per-SKU
    'Refresh prices' button."""
    result = scraper.scrape_product(db, body.product_id)
    sku = db.query(models.Product.sku_code).filter(models.Product.id == body.product_id).scalar()
    audit_log.record(db, action="competitor.refresh_product", actor=_user, entity_type="product",
                     entity_id=body.product_id, entity_label=sku, details=result,
                     request=request, commit=True)
    return result


def _scrape_all_bg(actor_id=None, actor_name=None) -> None:
    db = database.SessionLocal()
    try:
        result = scraper.scrape_all(db)
        # The scrape touches many rows; audit the RUN (summary counts) rather than each price,
        # attributed to whoever pressed the button.
        audit_log.record(db, action="competitor.scrape_run", entity_type="competitor_price",
                         entity_label="refresh-all",
                         details={"trigger": "refresh_all", "by": actor_name,
                                  "actor_id": actor_id, **(result or {})}, commit=True)
    finally:
        db.close()


@router.post("/refresh-all")
def refresh_all(background: BackgroundTasks, request: Request, db: Session = Depends(database.get_db),
                _user: models.User = Depends(require_capability("product_edit"))):
    """Kick off a scrape of ALL linked competitor prices in the background (can be hundreds of
    URLs) and return immediately — the global 'fetch all competitor prices' button."""
    count = db.query(models.CompetitorPrice).count()
    audit_log.record(db, action="competitor.refresh_all", actor=_user, entity_type="competitor_price",
                     entity_label=f"{count} links", details={"count": count, "mode": "background"},
                     request=request, commit=True)
    background.add_task(lambda: _scrape_all_bg(actor_id=_user.id, actor_name=_user.username))
    return {"started": True, "count": count}
