"""IMS → SSOT Google Sheet push (WRITE).

Opposite direction from sheet_sync (which PULLS via the public CSV export). IMS is
the single writer of the SSOT sheet: it writes its own columns AND the TECH columns,
fetching the latter at assembly time from the algo-dashboard server's read API
(GET /api/commerce/tech-fields/) instead of that server writing to the sheet.

If the TECH API isn't configured or the fetch fails, the push degrades safely —
it writes only the IMS columns and leaves the TECH columns untouched (so a transient
outage never blanks good data).

Config (env):
  GOOGLE_SA_KEY_JSON     service-account key JSON content (preferred on hosts; e.g. a Fly secret)
  GOOGLE_SA_KEY_PATH     service-account JSON file path (dev fallback)
  SSOT_SHEET_ID          spreadsheet id   (default: the v7 SSOT sheet)
  SSOT_SHEET_GID         worksheet gid    (default: 'Operational Database' tab)
  SSOT_DATA_START_ROW    first data row   (default: 5)
  ROSETTA_TECH_API_URL   algo-dashboard base url (e.g. https://<host>); enables TECH merge
  ROSETTA_TECH_API_KEY   X-API-Key for that endpoint

Columns are matched by header NAME in row 1, so reordering columns won't break it.
"""
import json
import os
import urllib.request

# v7 SSOT sheet — header keys in row 1, per-column spec notes in rows 2-4, data from row 5.
# `... or "default"` so an env var set to an EMPTY string falls back to the default
# (a blank value would otherwise override it and crash int() on startup).
SHEET_ID        = os.environ.get("SSOT_SHEET_ID") or "1-Hn7BKcKWJKmWr4SnWZGafJxkTrtDOBorb9DgSeolyA"
DEFAULT_GID     = int(os.environ.get("SSOT_SHEET_GID") or "87357125")
HEADER_ROW      = 1
DATA_START_ROW  = int(os.environ.get("SSOT_DATA_START_ROW") or "5")
SA_KEY_PATH     = os.environ.get("GOOGLE_SA_KEY_PATH", "")
SA_KEY_JSON     = os.environ.get("GOOGLE_SA_KEY_JSON", "")  # the key's JSON content (host secret)
SCOPES          = ["https://www.googleapis.com/auth/spreadsheets"]

TECH_API_URL    = os.environ.get("ROSETTA_TECH_API_URL", "")
TECH_API_KEY    = os.environ.get("ROSETTA_TECH_API_KEY", "")

# IMS-owned sheet columns (header name → derived from product/primary-supplier).
# Resolved against the sheet's header row.
IMS_FIELDS = [
    "sku_id", "sku_name", "brand", "category", "sub_category", "species", "storage_rule", "status", "hero_sku",
    "primary_supplier_id", "alternative_supplier_ids", "supplier_sku_code", "supplier_barcode",
    "basic_unit_cost", "cost_source", "cost_last_reviewed_at",
    "mbb_cost_per_unit", "mbb_min_qty", "mbb_tier_structure",
    "units_per_pack", "weight_grams", "minimum_operating_unit", "minimum_purchase_qty", "rrp",
    "supplier_moq_cached",
    "last_updated_by", "last_updated_at",
    # Human-in-the-loop verification (from the catalogue-onboarding audit trail)
    "hitl_verified", "verified_by", "hitl_timestamp",
]

# Tolerant header aliases for newer columns whose row-1 key text may vary
# (case-insensitive). Lets the sheet header read "HITL Verified (Human in The Loop)"
# or "hitl_verified" and still map to the same field.
FIELD_ALIASES = {
    "hitl_verified": ["hitl_verified", "hitl verified", "hitl",
                      "hitl verified (human in the loop)", "human verified", "hitl_verified (human in the loop)"],
    "verified_by":   ["verified_by", "verified by", "verifier", "verified_by_user",
                      "verified user", "verified person", "verified by (human in the loop)",
                      "hitl_verified_by", "hitl verified by", "verified_by (human in the loop)"],
    "hitl_timestamp": ["hitl_timestamp", "hitl timestamp", "hitl verified at", "verified_at",
                       "verified at", "hitl_verified_at", "hitl time", "verification timestamp"],
}

# TECH columns sourced from the algo-dashboard tech-fields API (keyed by sku_id).
# IMS writes these too — the dashboard server no longer touches the sheet.
TECH_FIELDS = [
    "stock_clinic", "stock_warehouse",
    "demand_120d_daysmart", "demand_120d_shopify", "demand_120d_hktv",
    "sell_price_clinic", "sell_price_shopify", "sell_price_hktv",
    "daysmart_last_invoice_cost", "shopify_last_invoice_cost",
    "listed_on_shopify", "listed_on_hktv",
    "unfulfilled_jit", "upcoming_14d_autoship",
    "uom_sell_unit", "dispensing_fee_clinic", "dispensing_fee_ecommerce",
    "channel_fee_pct_hktv", "competitor_lowest_price", "sf_express_fee",
]


def _fmt(v):
    """Sheet cells want str/number; None → blank."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else ""
    return v


def _client():
    import gspread
    from google.oauth2.service_account import Credentials
    # Prefer the JSON content from an env var (Fly/host secret — env vars aren't files),
    # fall back to a local file path for dev.
    if SA_KEY_JSON:
        creds = Credentials.from_service_account_info(json.loads(SA_KEY_JSON), scopes=SCOPES)
    elif SA_KEY_PATH and os.path.exists(SA_KEY_PATH):
        creds = Credentials.from_service_account_file(SA_KEY_PATH, scopes=SCOPES)
    else:
        raise RuntimeError(
            "No service-account credential — set GOOGLE_SA_KEY_JSON (the key's JSON, e.g. a Fly secret) "
            "or GOOGLE_SA_KEY_PATH (a local file)."
        )
    return gspread.authorize(creds)


def _open_ws(gid: int):
    gc = _client()
    sh = gc.open_by_key(SHEET_ID)
    ws = next((w for w in sh.worksheets() if w.id == gid), None)
    if ws is None:
        raise RuntimeError(f"Worksheet gid={gid} not found in spreadsheet {SHEET_ID}")
    return sh, ws


def _rows_for_push(db) -> list[dict]:
    """One dict per product, keyed by sheet field name. Sorted by sku_id."""
    import models
    products = db.query(models.Product).all()
    all_ps = db.query(models.ProductSupplier).all()
    primary, alts = {}, {}
    for r in all_ps:
        alts.setdefault(r.product_id, []).append(r.supplier_id)
        if r.is_primary == 1:
            primary[r.product_id] = r

    # Human-in-the-loop verification: a human confirmed-match or created this SKU during
    # catalogue review. Keep the MOST RECENT such reviewer + timestamp per sku_id (rows
    # scanned oldest→newest so the last write wins).
    # Latest event per SKU wins: a confirm-match/assign-new verifies it; an
    # hitl_unverify removes that status (so the SKU drops out of the push again).
    verified_by, verified_at = {}, {}
    ver_rows = (db.query(models.CatalogueAuditEvent.sku_code, models.CatalogueAuditEvent.action,
                         models.CatalogueAuditEvent.display_name, models.CatalogueAuditEvent.created_at)
                .filter(models.CatalogueAuditEvent.action.in_(["confirm_match", "assign_new", "hitl_unverify"]),
                        models.CatalogueAuditEvent.sku_code.isnot(None))
                .order_by(models.CatalogueAuditEvent.created_at).all())   # oldest -> newest
    for sku, action, name, ts in ver_rows:
        s = str(sku)
        if action == "hitl_unverify":
            verified_by.pop(s, None); verified_at.pop(s, None)
        else:
            verified_by[s] = name; verified_at[s] = ts

    # Cached supplier MOQ (AUTO: from the primary supplier's moq).
    sup_moq = dict(db.query(models.Supplier.id, models.Supplier.moq_value).all())

    rows = []
    for p in products:
        if str(p.sku_code) not in verified_by:   # only push HITL-verified products
            continue
        ps = primary.get(p.id)
        unit_cost = None
        if ps and ps.basic_cost:
            unit_cost = round(ps.basic_cost / ps.units_per_pack, 4) if ps.units_per_pack else ps.basic_cost
        prim_sup = ps.supplier_id if ps else None
        alt_ids = [s for s in dict.fromkeys(alts.get(p.id, [])) if s and s != prim_sup]
        rows.append({
            "sku_id":                   p.sku_code,
            "sku_name":                 p.name,
            "brand":                    p.brand,
            "category":                 p.category,
            "sub_category":             p.subcategory,
            "species":                  p.species,
            "storage_rule":             p.storage_rule,
            "status":                   p.status,
            "hero_sku":                 "Yes" if p.hero_sku else "",
            "primary_supplier_id":      prim_sup,
            "alternative_supplier_ids": ",".join(str(s) for s in alt_ids),
            "supplier_sku_code":        ps.supplier_sku if ps else None,
            "supplier_barcode":         ps.barcode if ps else None,
            "basic_unit_cost":          unit_cost,
            "cost_source":              ps.cost_source if ps else None,
            "cost_last_reviewed_at":    ps.cost_updated_at if ps else None,
            "mbb_cost_per_unit":        None,   # MBB is relational now (mbb_terms) — not pushed as a flat scalar
            "mbb_min_qty":              None,
            "mbb_tier_structure":       ("; ".join(t.note or t.kind for t in ps.mbb_term_list) if (ps and ps.mbb_term_list) else None),
            "units_per_pack":           ps.units_per_pack if ps else None,
            "weight_grams":             p.weight_g,
            "minimum_operating_unit":   p.uom,
            "minimum_purchase_qty":     p.min_purchase_qty,
            "rrp":                      p.rrp,
            "last_updated_by":          p.last_manual_edit_by,
            "last_updated_at":          p.last_manual_edit_at or p.updated_at,
            "supplier_moq_cached":      sup_moq.get(prim_sup),
            "hitl_verified":            "Yes" if str(p.sku_code) in verified_by else "",
            "verified_by":              verified_by.get(str(p.sku_code)),
            "hitl_timestamp":           verified_at.get(str(p.sku_code)),
        })
    rows.sort(key=lambda r: str(r["sku_id"]))
    return rows


def _fetch_tech_fields(sku_ids) -> tuple[dict, dict]:
    """Fetch {sku_id: {tech_field: value}} from the algo-dashboard tech-fields API.

    Pages through the whole result set (the endpoint can't filter on sku_ids that
    contain commas). On no-config or ANY failure returns ({}, meta) so the push
    degrades to IMS-columns-only rather than blanking TECH cells.
    """
    if not TECH_API_URL:
        return {}, {"enabled": False, "reason": "ROSETTA_TECH_API_URL not set"}
    base = TECH_API_URL.rstrip("/")
    # Set a real User-Agent: urllib's default ("Python-urllib/x.y") is flagged as a
    # bot by Cloudflare's Browser Integrity Check and 403'd before reaching the
    # origin — relevant when the dashboard sits behind a Cloudflare-proxied tunnel.
    headers = {
        "Accept": "application/json",
        "User-Agent": "rosetta-ims/1.0 (+https://rosetta-ims-api.fly.dev)",
    }
    if TECH_API_KEY:
        headers["X-API-Key"] = TECH_API_KEY
    wanted = {str(s) for s in sku_ids}
    data: dict = {}
    try:
        page = 1
        while True:
            req = urllib.request.Request(
                f"{base}/api/commerce/tech-fields/?page={page}&page_size=500", headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:   # fail fast if the dashboard is down
                payload = json.load(resp)
            for row in payload.get("results", []):
                sid = str(row.get("sku_id") or "")
                if sid in wanted:
                    data[sid] = row
            if not payload.get("next"):
                break
            page += 1
        return data, {"enabled": True, "matched": len(data)}
    except Exception as e:  # noqa: BLE001
        return {}, {"enabled": True, "error": f"{type(e).__name__}: {e}"}


def run_push(db, gid: int = None, start_row: int = None, dry_run: bool = True, limit: int = None) -> dict:
    """Push IMS columns + (fetched) TECH columns into the sheet's data region.

    dry_run=True (default) previews without writing. Each mapped column is written
    as its own contiguous range. If the TECH fetch is unconfigured/fails, only IMS
    columns are written and TECH columns are left untouched.
    """
    gid = DEFAULT_GID if gid is None else gid
    start_row = DATA_START_ROW if start_row is None else start_row

    sh, ws = _open_ws(gid)
    header = ws.row_values(HEADER_ROW)
    want = set(IMS_FIELDS) | set(TECH_FIELDS)
    alias_of = {a.lower(): f for f, aliases in FIELD_ALIASES.items() for a in aliases}
    col_of = {}
    for idx, h in enumerate(header, start=1):
        key = (h or "").strip()
        if key in want and key not in col_of:
            col_of[key] = idx
        else:                                   # tolerant alias match (case-insensitive)
            f = alias_of.get(key.lower())
            if f and f not in col_of:
                col_of[f] = idx

    rows = _rows_for_push(db)
    if limit:
        rows = rows[:limit]

    # All DB reads are done and materialised into `rows` (plain dicts). Release the
    # DB connection back to the pool NOW so the slow remainder (TECH fetch + the
    # ~60s of Google Sheets writes) doesn't hold a connection and starve other
    # requests — this was the cause of the production stalls.
    db.commit()

    # Fetch TECH fields and merge them into each row by sku_id.
    tech_map, tech_meta = _fetch_tech_fields([r["sku_id"] for r in rows])
    if tech_map:
        for r in rows:
            t = tech_map.get(str(r["sku_id"]))
            if t:
                for f in TECH_FIELDS:
                    if f in t:
                        r[f] = t[f]

    ims_cols  = [f for f in IMS_FIELDS if f in col_of]
    tech_cols = [f for f in TECH_FIELDS if f in col_of] if tech_map else []
    mapped    = ims_cols + tech_cols
    unmapped  = [f for f in IMS_FIELDS if f not in col_of]

    result = {
        "target": {
            "spreadsheet_id": SHEET_ID, "tab": ws.title, "gid": gid,
            "header_row": HEADER_ROW, "data_start_row": start_row,
            "grid": {"rows": ws.row_count, "cols": ws.col_count},
        },
        "products":          len(rows),
        "columns_written":   mapped,
        "ims_columns":       ims_cols,
        "tech_columns":      tech_cols,
        "tech_fetch":        tech_meta,
        "tech_cols_left_untouched": not bool(tech_cols),
        "columns_unmapped":  unmapped,
        "sheet_headers":     [h for h in header if (h or "").strip()],   # diagnostic: exact row-1 keys
        "sheet_cols_total":  len([h for h in header if h.strip()]),
        "sample": {f: _fmt(rows[0].get(f)) for f in mapped} if rows else {},
        "dry_run":           dry_run,
    }
    if dry_run:
        return result

    from gspread.utils import rowcol_to_a1
    end_row = start_row - 1   # one before the data region when there are no verified rows
    if rows:
        end_row = start_row + len(rows) - 1
        if end_row > ws.row_count:
            ws.add_rows(end_row - ws.row_count)
        updates = []
        for f in mapped:
            c = col_of[f]
            rng = f"{rowcol_to_a1(start_row, c)}:{rowcol_to_a1(end_row, c)}"
            updates.append({"range": rng, "values": [[_fmt(r.get(f))] for r in rows]})
        ws.batch_update(updates, value_input_option="RAW")

    # Only HITL-verified rows are pushed — clear the IMS columns BELOW the verified
    # data so a previous (larger) push doesn't leave stale, unverified rows behind.
    cleared = 0
    clear_from = end_row + 1
    if ws.row_count >= clear_from and ims_cols:
        ws.batch_clear([f"{rowcol_to_a1(clear_from, col_of[f])}:{rowcol_to_a1(ws.row_count, col_of[f])}" for f in ims_cols])
        cleared = ws.row_count - clear_from + 1
    result["cleared_unverified_rows"] = cleared

    result["written_rows"]  = len(rows)
    result["written_cells"] = len(rows) * len(mapped)
    result["row_range"]     = f"{start_row}..{end_row}"
    return result
