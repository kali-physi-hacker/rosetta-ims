"""
Stock level import endpoints.

POST /stock/import        Upload clinic or warehouse stock snapshot (CSV/Excel)
GET  /stock/status        Last import dates + product coverage counts
"""

import io
import re
import csv
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
import openpyxl

import models
import database
from permissions import require_capability
from services import audit_log

router = APIRouter(prefix="/stock", tags=["stock"])


# ── Parsers ───────────────────────────────────────────────────────────────────

def _find_col(headers: list[str], *keywords: str) -> Optional[int]:
    for kw in keywords:
        for i, h in enumerate(headers):
            if kw in h:
                return i
    return None


def _parse_rows(rows: list[tuple]) -> list[dict]:
    """Auto-detect header row and SKU/name/qty columns, return list of row dicts."""
    if not rows:
        return []

    # Find header row — first with 2+ non-empty cells
    header_idx = 0
    for i, row in enumerate(rows[:15]):
        if sum(1 for c in row if c is not None and str(c).strip()) >= 2:
            header_idx = i
            break

    headers = [str(c).strip().lower() if c else "" for c in rows[header_idx]]

    col_id   = _find_col(headers, 'sku', 'item code', 'item id', 'item no',
                          'item#', 'code', 'product id', 'ref', 'id')
    col_name = _find_col(headers, 'description', 'product name', 'item name',
                          'name', 'product', 'item', 'title')
    col_qty  = _find_col(headers, 'qty', 'quantity', 'on hand', 'available',
                          'stock', 'soh', 'base quantity', 'inventory')

    results = []
    for row in rows[header_idx + 1:]:
        if all(c is None or str(c).strip() == "" for c in row):
            continue

        def cell(idx: Optional[int]) -> Optional[str]:
            if idx is None or idx >= len(row):
                return None
            v = row[idx]
            return str(v).strip() if v is not None else None

        raw_qty = cell(col_qty)
        if raw_qty is None:
            continue
        try:
            qty = float(re.sub(r'[,\s]', '', raw_qty))
        except (ValueError, TypeError):
            continue

        results.append({
            "raw_id":   cell(col_id),
            "raw_name": cell(col_name),
            "qty":      qty,
        })

    return results


def _parse_excel(content: bytes) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    return _parse_rows(rows)


def _parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")  # utf-8-sig strips BOM
    reader = csv.reader(io.StringIO(text))
    rows = [tuple(r) for r in reader]
    return _parse_rows(rows)


# ── Matching ──────────────────────────────────────────────────────────────────

def _match_product(
    raw_id: Optional[str],
    raw_name: Optional[str],
    db: Session,
) -> tuple[Optional[models.Product], Optional[str]]:
    """Return (product, match_type) or (None, None)."""

    if raw_id:
        # 1. Internal SKU exact match
        p = db.query(models.Product).filter(models.Product.sku_code == raw_id).first()
        if p:
            return p, "sku"

        # 2. Supplier SKU match
        ps = db.query(models.ProductSupplier).filter(
            models.ProductSupplier.supplier_sku == raw_id
        ).first()
        if ps:
            p = db.query(models.Product).filter(models.Product.id == ps.product_id).first()
            if p:
                return p, "supplier_sku"

    # 3. Fuzzy name match (word overlap ≥ 0.7)
    if raw_name and len(raw_name) > 3:
        words = {w for w in raw_name.lower().split() if len(w) > 3}
        if words:
            candidates = (
                db.query(models.Product)
                .filter(models.Product.status == "ACTIVE")
                .limit(300)
                .all()
            )
            best: Optional[models.Product] = None
            best_score = 0.0
            for p in candidates:
                p_words = set(p.name.lower().split())
                overlap = len(words & p_words) / max(len(words), 1)
                if overlap > best_score:
                    best_score = overlap
                    best = p
            if best_score >= 0.7 and best:
                return best, "name_fuzzy"

    return None, None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/import")
async def import_stock(
    request: Request,
    file: UploadFile = File(...),
    location: str = Form(...),            # 'clinic' | 'warehouse'
    as_of_date: Optional[str] = Form(None),  # YYYY-MM-DD; defaults to today
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("stock_import")),
):
    """
    Upload a stock snapshot CSV or Excel from DaySmart (clinic) or Warehouse.
    Upserts StockLevel rows — one row per product per location.
    """
    if location not in ("clinic", "warehouse"):
        raise HTTPException(status_code=400, detail="location must be 'clinic' or 'warehouse'")

    content = await file.read()
    filename = (file.filename or "").lower()
    ct = (file.content_type or "").lower()

    if filename.endswith((".xlsx", ".xls")) or "spreadsheet" in ct or "excel" in ct:
        rows = _parse_excel(content)
    elif filename.endswith(".csv") or "csv" in ct:
        rows = _parse_csv(content)
    else:
        try:
            rows = _parse_csv(content)
        except Exception:
            raise HTTPException(status_code=400, detail="Unsupported format. Use CSV or Excel.")

    as_of = as_of_date or date.today().isoformat()
    now   = datetime.utcnow().isoformat()

    updated   = 0
    skipped   = 0  # qty <= 0 and no existing record — nothing to write
    unmatched: list[dict] = []

    for row in rows:
        product, _ = _match_product(row["raw_id"], row["raw_name"], db)

        if not product:
            unmatched.append({"raw_id": row["raw_id"], "raw_name": row["raw_name"], "qty": row["qty"]})
            continue

        sl = db.query(models.StockLevel).filter(
            models.StockLevel.product_id == product.id,
            models.StockLevel.location   == location,
        ).first()

        if sl:
            sl.qty        = row["qty"]
            sl.as_of_date = as_of
            sl.source     = "import"
            sl.updated_at = now
        else:
            db.add(models.StockLevel(
                product_id=product.id,
                location=location,
                qty=row["qty"],
                as_of_date=as_of,
                source="import",
                updated_at=now,
            ))

        updated += 1

    audit_log.record(db, action="stock.import", actor=_user, entity_type="stock",
                     entity_label=f"{location} · {as_of}",
                     details={"location": location, "as_of_date": as_of, "rows_parsed": len(rows),
                              "updated": updated, "unmatched_count": len(unmatched)}, request=request)
    db.commit()

    return {
        "location":        location,
        "as_of_date":      as_of,
        "rows_parsed":     len(rows),
        "updated":         updated,
        "unmatched_count": len(unmatched),
        "unmatched":       unmatched[:25],
    }


@router.get("/status")
def stock_status(db: Session = Depends(database.get_db)):
    """Current stock coverage: how many products have stock data and when it was last imported."""
    clinic_count = (
        db.query(func.count(models.StockLevel.id))
        .filter(models.StockLevel.location == "clinic")
        .scalar() or 0
    )
    warehouse_count = (
        db.query(func.count(models.StockLevel.id))
        .filter(models.StockLevel.location == "warehouse")
        .scalar() or 0
    )
    latest_clinic = (
        db.query(func.max(models.StockLevel.as_of_date))
        .filter(models.StockLevel.location == "clinic")
        .scalar()
    )
    latest_warehouse = (
        db.query(func.max(models.StockLevel.as_of_date))
        .filter(models.StockLevel.location == "warehouse")
        .scalar()
    )
    total_products = db.query(func.count(models.Product.id)).filter(
        models.Product.status == "ACTIVE"
    ).scalar() or 0

    return {
        "total_active_products": total_products,
        "clinic":    {"count": clinic_count,    "latest_as_of": latest_clinic},
        "warehouse": {"count": warehouse_count, "latest_as_of": latest_warehouse},
    }
