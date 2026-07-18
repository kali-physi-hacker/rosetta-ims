"""
Seed products from the Google Sheets SKU master list.
Run AFTER seed.py (which creates category rules and suppliers).
Usage: python seed_from_sheet.py

Sheet: https://docs.google.com/spreadsheets/d/18WUxJQZ9srms7S1oga6mrAdeH1QCA2pBsaJBUfwFRTQ
Tab gid: 8428031 (RECORD | SKU MASTER LIST)
"""
import csv
import io
import re
import urllib.request
from datetime import datetime

from database import SessionLocal, engine
import models

models.Base.metadata.create_all(bind=engine)

SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "18WUxJQZ9srms7S1oga6mrAdeH1QCA2pBsaJBUfwFRTQ"
    "/export?format=csv&gid=8428031"
)

# Exact column names from the CSV (contain newlines)
COL_STATUS      = 'status'                      # lowercase in sheet
COL_CATEGORY    = 'Item Category\n(medicince, preventative, supplement, shampoo, food etc.)\nFrom Shopify Tags'
COL_SELL_CLINIC = 'Selling Price\nFrom DaySmart'
COL_SELL_SHOP   = 'Selling Price\nFrom Shopify'
COL_COST        = 'Wholesale Cost \n(basic)'     # most populated cost column (1364 rows)
COL_COST_ALT    = 'Last known cost per\nDaySmart\nfrom last invoice'  # fallback
COL_BULK_COST   = ' Wholesale Cost\n(Max Bulk Buy) '
COL_STOCK_CLI   = 'Base Quantity\nFrom DaySmart'
COL_STOCK_SHOP  = 'STP SOH'
COL_DISP_FEE    = 'Dispensing Fee\n(Yes / No)\n\nFrom DaySmart'
COL_HERO        = 'Hero SKU?\n(Yes ? No)'
COL_COMP_PRICE  = 'Lowest Competitor Price'
COL_COMP_NAME   = 'Competitor Name'
COL_COMP_LINK   = 'Competitor Link'
COL_HKTV_PRICE  = 'Competitor Price\n(From HKTV)'
COL_HKTV_LINK   = 'Competitor Link\n(from HKTV)'

# Map sheet Status values to IMS status values
STATUS_MAP = {
    'ONLINE':  'ACTIVE',
    'OFFLINE': 'INACTIVE',
    '':        'ACTIVE',   # blank = incomplete data, not inactive
}

# Map sheet category names to canonical IMS categories
CATEGORY_MAP = {
    'Medicine':     'Medicine',
    'Preventative': 'Preventative',
    'Supplement':   'Supplement',
    'Food':         'Food',
    'Pet Hygiene':  'Pet Hygiene',
    'Toys':         'Toys',
    'Cat Litter':   'Cat Litter',
    'Shampoo':      'Shampoo',
    'Not-For-Sale': 'Not-For-Sale',
}


def clean_price(val: str) -> float | None:
    if not val or val.strip() == '':
        return None
    cleaned = re.sub(r'[HKD$,\s]', '', val.strip())
    try:
        f = float(cleaned)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def clean_qty(val: str) -> float:
    if not val or val.strip() == '':
        return 0.0
    cleaned = re.sub(r'[,\s]', '', val.strip())
    try:
        return max(0.0, float(cleaned))
    except (ValueError, TypeError):
        return 0.0


def fetch_csv() -> list[dict]:
    print(f"Fetching sheet CSV...")
    req = urllib.request.Request(SHEET_CSV_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(raw))
    return list(reader)


def find_supplier(db, name: str) -> models.Supplier | None:
    if not name or not name.strip():
        return None
    return db.query(models.Supplier).filter(
        models.Supplier.name.ilike(f"%{name.strip()}%")
    ).first()


def seed():
    rows = fetch_csv()
    print(f"  {len(rows)} rows fetched.")

    db = SessionLocal()
    now = datetime.utcnow().isoformat()
    today = datetime.utcnow().strftime('%Y-%m-%d')

    seeded = 0
    updated = 0
    skipped = 0
    skip_reasons: list[str] = []

    try:
        for i, row in enumerate(rows, start=2):  # row 2 = first data row
            sku_code = (row.get('SKU ID') or '').strip()
            name     = (row.get('SKU NAME') or '').strip()

            # Skip completely blank rows
            if not sku_code and not name:
                skipped += 1
                skip_reasons.append(f"Row {i}: blank SKU and name")
                continue

            # Skip rows without a SKU ID
            if not sku_code:
                skipped += 1
                skip_reasons.append(f"Row {i}: '{name}' — missing SKU ID")
                continue

            # Map category
            raw_cat  = (row.get(COL_CATEGORY) or '').strip()
            category = CATEGORY_MAP.get(raw_cat, raw_cat or 'Not-For-Sale')
            storage_rule = 'clinic_only' if category == 'Medicine' else 'any'

            # Map status
            raw_status = (row.get(COL_STATUS) or '').strip().upper()
            status = STATUS_MAP.get(raw_status, 'ACTIVE')

            brand    = (row.get('Brand') or '').strip() or None
            hero_raw = (row.get(COL_HERO) or '').strip().upper()
            hero_sku = 1 if hero_raw in ('YES', 'Y', '1', 'TRUE') else 0

            # Upsert product
            product = db.query(models.Product).filter(
                models.Product.sku_code == sku_code
            ).first()

            if not product:
                product = models.Product(
                    sku_code=sku_code,
                    name=name or f"SKU {sku_code}",
                    brand=brand,
                    category=category,
                    uom=(row.get('UOM') or '').strip() or None,
                    storage_rule=storage_rule,
                    status=status,
                    hero_sku=hero_sku,
                    created_at=now,
                    updated_at=now,
                )
                db.add(product)
                db.flush()  # get product.id
                seeded += 1
            else:
                product.name = name or product.name
                product.brand = brand or product.brand
                product.category = category
                product.storage_rule = storage_rule
                product.status = status
                product.hero_sku = hero_sku
                product.updated_at = now
                updated += 1

            pid = product.id

            # Supplier link
            supplier_name = (row.get('Supplier') or '').strip()
            supplier = find_supplier(db, supplier_name)
            supplier_sku = (row.get('Supplier Code') or '').strip() or None
            barcode      = (row.get('Supplier Barcode') or '').strip() or None
            basic_cost   = clean_price(row.get(COL_COST) or '') or clean_price(row.get(COL_COST_ALT) or '')

            if supplier or basic_cost or supplier_sku:
                ps = db.query(models.ProductSupplier).filter(
                    models.ProductSupplier.product_id == pid,
                    models.ProductSupplier.supplier_id == (supplier.id if supplier else None),
                ).first()
                if not ps:
                    # Try to find any existing PS for this product
                    ps = db.query(models.ProductSupplier).filter(
                        models.ProductSupplier.product_id == pid
                    ).first()
                if ps:
                    if basic_cost is not None: ps.basic_cost = basic_cost
                    if supplier_sku:           ps.supplier_sku = supplier_sku
                    if barcode:                ps.barcode = barcode
                    if supplier:               ps.supplier_id = supplier.id
                    ps.is_primary = 1
                    ps.updated_at = now
                else:
                    db.add(models.ProductSupplier(
                        product_id=pid,
                        supplier_id=supplier.id if supplier else None,
                        supplier_sku=supplier_sku,
                        barcode=barcode,
                        basic_cost=basic_cost,
                        is_primary=1,
                        updated_at=now,
                    ))

            # Channels — clinic
            clinic_price = clean_price(row.get(COL_SELL_CLINIC) or '')
            disp_raw = (row.get(COL_DISP_FEE) or '').strip().upper()
            has_dispensing = 1 if disp_raw in ('YES', 'Y', '1') else 0

            if clinic_price is not None:
                _upsert_channel(db, pid, 'clinic', clinic_price, has_dispensing, now)

            # Channels — shopify
            shopify_price = clean_price(row.get(COL_SELL_SHOP) or '')
            if shopify_price is not None:
                _upsert_channel(db, pid, 'shopify', shopify_price, 0, now)

            # Channels — hktv
            hktv_raw = (row.get('HKTV?') or '').strip().upper()
            if hktv_raw in ('YES', 'Y', '1'):
                _upsert_channel(db, pid, 'hktv', None, 0, now)

            # Stock levels
            clinic_qty    = clean_qty(row.get(COL_STOCK_CLI) or '')
            warehouse_qty = clean_qty(row.get(COL_STOCK_SHOP) or '')
            _upsert_stock(db, pid, 'clinic', clinic_qty, today, now)
            _upsert_stock(db, pid, 'warehouse', warehouse_qty, today, now)

            # Competitor prices
            comp_price = clean_price(row.get(COL_COMP_PRICE) or '')
            comp_name  = (row.get(COL_COMP_NAME) or '').strip()
            comp_link  = (row.get(COL_COMP_LINK) or '').strip() or None
            if comp_price and comp_name:
                existing_comp = db.query(models.CompetitorPrice).filter(
                    models.CompetitorPrice.product_id == pid,
                    models.CompetitorPrice.competitor_name == comp_name,
                    models.CompetitorPrice.channel == 'general',
                ).first()
                if not existing_comp:
                    db.add(models.CompetitorPrice(
                        product_id=pid,
                        competitor_name=comp_name,
                        channel='general',
                        price=comp_price,
                        url=comp_link,
                        last_checked=today,
                        created_at=now,
                        updated_at=now,
                    ))

            hktv_comp_price = clean_price(row.get(COL_HKTV_PRICE) or '')
            hktv_comp_link  = (row.get(COL_HKTV_LINK) or '').strip() or None
            if hktv_comp_price:
                existing_hktv = db.query(models.CompetitorPrice).filter(
                    models.CompetitorPrice.product_id == pid,
                    models.CompetitorPrice.channel == 'hktv',
                ).first()
                if not existing_hktv:
                    db.add(models.CompetitorPrice(
                        product_id=pid,
                        competitor_name='HKTVMall competitor',
                        channel='hktv',
                        price=hktv_comp_price,
                        url=hktv_comp_link,
                        last_checked=today,
                        created_at=now,
                        updated_at=now,
                    ))

        db.commit()
        print(f"Done. Seeded: {seeded} | Updated: {updated} | Skipped: {skipped}")
        if skip_reasons[:10]:
            print("First skipped rows:")
            for r in skip_reasons[:10]:
                print(f"  {r}")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def _upsert_channel(db, product_id: int, channel: str, selling_price, has_dispensing: int, now: str):
    pc = db.query(models.ProductChannel).filter(
        models.ProductChannel.product_id == product_id,
        models.ProductChannel.channel == channel,
    ).first()
    if pc:
        if selling_price is not None: pc.selling_price = selling_price
        pc.has_dispensing_fee = has_dispensing
        pc.updated_at = now
    else:
        db.add(models.ProductChannel(
            product_id=product_id,
            channel=channel,
            is_active=1,
            selling_price=selling_price,
            has_dispensing_fee=has_dispensing,
            updated_at=now,
        ))


def _upsert_stock(db, product_id: int, location: str, qty: float, as_of_date: str, now: str):
    sl = db.query(models.StockLevel).filter(
        models.StockLevel.product_id == product_id,
        models.StockLevel.location == location,
    ).first()
    if sl:
        sl.qty = qty
        sl.as_of_date = as_of_date
        sl.updated_at = now
    else:
        db.add(models.StockLevel(
            product_id=product_id,
            location=location,
            qty=qty,
            as_of_date=as_of_date,
            source='import',
            updated_at=now,
        ))


if __name__ == "__main__":
    seed()
