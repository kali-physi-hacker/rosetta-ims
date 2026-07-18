"""One-off backfill: flat mbb_* scalars + free text  ->  relational mbb_terms rows.

Idempotent (skips a supplier that already has term rows). Purely additive — it does NOT touch
the old columns, so it is safe to re-run and easy to reverse (just DELETE FROM mbb_terms).

Priority when reading the old data:
  1. mbb_tiers JSON            -> one `tier` term per {min_qty, unit_cost}
  2. free text "buy N get M"   -> `buy_x_get_y` (fixes the rows mis-typed as unit_cost)
  3. mbb_type='spend_discount' -> `spend_discount`
  4. mbb_type='buy_x_get_y'    -> `buy_x_get_y`
  5. mbb_type='unit_cost'      -> `flat_unit_cost` (converted per-box -> per-unit when pack known;
                                  flagged in `note` for human review when pack is unknown)

Run:  python scripts/backfill_mbb_terms.py <db_path>
"""
import sqlite3, json, re, sys
from datetime import datetime

DB = sys.argv[1] if len(sys.argv) > 1 else "ims.db"
BUYGET = re.compile(r'buy\s*(\d+)\s*(?:units?\s*)?(?:,|and|-)?\s*get\s*(\d+)', re.I)

con = sqlite3.connect(DB)
cur = con.cursor()
now = datetime.utcnow().isoformat()

# ensure the table exists (same DDL as database.py run_migrations), so this runs standalone
cur.execute(
    "CREATE TABLE IF NOT EXISTS mbb_terms ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, product_supplier_id INTEGER NOT NULL, kind TEXT NOT NULL,"
    " min_qty INTEGER, min_spend REAL, free_qty INTEGER, discount_pct REAL, unit_cost REAL,"
    " note TEXT, sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT)")
cur.execute("CREATE INDEX IF NOT EXISTS ix_mbb_terms_ps_id ON mbb_terms(product_supplier_id)")

have_terms = {r[0] for r in cur.execute("SELECT DISTINCT product_supplier_id FROM mbb_terms")}

rows = cur.execute("""
    SELECT id, mbb_type, bulk_buy_cost, bulk_buy_min_qty, mbb_min_amount,
           mbb_free_qty, mbb_discount_pct, mbb_terms, mbb_tiers, units_per_pack
    FROM product_suppliers
""").fetchall()

def per_unit(cost, pack):
    if cost is None:
        return None
    return round(cost / pack, 4) if (pack and pack > 1) else cost

created = {'buy_x_get_y': 0, 'spend_discount': 0, 'tier': 0, 'flat_unit_cost': 0}
suppliers_touched = flagged = 0

for (psid, mtype, bbc, bbmq, mma, mfq, mdp, terms_txt, tiers_json, pack) in rows:
    if psid in have_terms:
        continue
    terms_txt = (terms_txt or "").strip()
    has_any = any(v not in (None, "", 0) for v in (mtype, bbc, mma, mfq, mdp)) or terms_txt or (tiers_json or "").strip()
    if not has_any:
        continue

    made = []  # tuples: (kind, min_qty, min_spend, free_qty, discount_pct, unit_cost, note)

    if (tiers_json or "").strip():
        try:
            for t in json.loads(tiers_json):
                uc = t.get('unit_cost')
                if uc is not None:
                    made.append(('tier', t.get('min_qty'), None, None, None, per_unit(uc, pack), terms_txt or None))
        except Exception:
            pass

    if not made and terms_txt:
        m = BUYGET.search(terms_txt)
        if m:
            made.append(('buy_x_get_y', int(m.group(1)), None, int(m.group(2)), None, None, terms_txt))

    if not made:
        if mtype == 'spend_discount' and mdp is not None:
            made.append(('spend_discount', None, mma, None, mdp, None, terms_txt or None))
        elif mtype == 'buy_x_get_y' and mfq is not None:
            made.append(('buy_x_get_y', bbmq, None, mfq, None, None, terms_txt or None))
        elif mtype == 'unit_cost' and bbc is not None:
            note = terms_txt
            if not (pack and pack > 1):
                note = ("verify basis (per-box vs per-unit): " + note).strip(); flagged += 1
            made.append(('flat_unit_cost', bbmq, mma, None, None, per_unit(bbc, pack), note or None))

    for i, (kind, mq, ms, fq, dp, uc, note) in enumerate(made):
        cur.execute(
            "INSERT INTO mbb_terms (product_supplier_id, kind, min_qty, min_spend, free_qty, "
            "discount_pct, unit_cost, note, sort_order, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (psid, kind, mq, ms, fq, dp, uc, note, i, now))
        created[kind] += 1
    if made:
        suppliers_touched += 1

con.commit()
con.close()
print(f"suppliers given terms: {suppliers_touched}")
print(f"terms created by kind:  {created}")
print(f"flagged for basis review: {flagged}")
