"""units_per_pack SIZE-MIS-READ cleanup (Phase 0) — DRY-RUN by default, opt-in --apply, --rollback-from-csv.

Generalises upp_weight_misread_cleanup.py from weight-only to WEIGHT **and** VOLUME, and resolves its
target set dynamically from live data (the population is ~100, not 14). Each target is a LIVE
product_supplier whose units_per_pack equals a weight (grams) or volume (ml) parsed from the product
name / catalogue pack_size — i.e. the pack SIZE was written into the cost-basis field. Because
effective_unit_cost = basic_cost / units_per_pack, a 5 L antiseptic jug reads as HK$690 / 5000 = HK$0.14
per "unit". These are sold as one container, so the only correction is units_per_pack -> 1.

Confidence:
  HIGH   — uom is NOT a per-sell count unit (tablet/can/pouch/…). Safe to set upp=1.
  REVIEW — uom IS a sell-count unit, so the size-match might be coincidental (e.g. FortiFlora
           "1.06OZ" == 30 vs a 30-sachet count). NEVER auto-applied; listed for a human.

Guards (a row is written only when ALL still hold at write time, else the whole run aborts with ZERO
writes): the manifest row's units_per_pack is unchanged; the size token still parses to exactly that
weight/volume from the current name+pack_size; the description contains no COUNT token equal to it; and
(HIGH only) the uom is not a sell-count unit. Sets units_per_pack=1 ONLY — never touches basic_cost,
cost_source, order_increment_*, minimum_order_*, or Product.min_purchase_qty. Appends a documenting
pricing_note; records an AuditLog `supplier_cost.units_per_pack_correction`; reversible.

Two-step, manifest-driven (the reviewed dry-run CSV IS the target set):
    docker exec backend-api-1 python scripts/upp_size_misread_cleanup.py --out /tmp/phase0_preview.csv   # dry-run
    # (review /tmp/phase0_preview.csv — HIGH rows only get applied)
    docker exec backend-api-1 python scripts/upp_size_misread_cleanup.py \
        --apply --from-csv /tmp/phase0_preview.csv --operator "Desmond" --expected-fix-count <N> --out /tmp/phase0_APPLIED.csv
    docker exec backend-api-1 python scripts/upp_size_misread_cleanup.py \
        --rollback-from-csv /tmp/phase0_APPLIED.csv --operator "Desmond"
"""
from __future__ import annotations

import argparse, csv, os, re, sqlite3, sys
from datetime import datetime, timezone

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _p in (_HERE, os.path.dirname(_HERE)):
        if _p not in sys.path:
            sys.path.insert(0, _p)
except NameError:
    pass

ACTION = "supplier_cost.units_per_pack_correction"
ROLLBACK_ACTION = "supplier_cost.units_per_pack_correction_rollback"

_WEIGHT = re.compile(r'(\d+(?:\.\d+)?)\s*(kgs?|g|gm|gms|grams?|lbs?|pounds?|oz)\b', re.I)
_VOLUME = re.compile(r'(\d+(?:\.\d+)?)\s*(l|litres?|liters?|ml|mls)\b', re.I)
_COUNT  = re.compile(r'(\d+)\s*(?:x\s*)?(tabs?|tablets?|caps?|capsules?|pcs?|pieces?|cans?|pouch(?:es)?|'
                     r'sachets?|tests?|strips?|vials?|sticks?|wipes?|doses?|servings?)\b', re.I)
_COUNT_UOMS = {'tablet','tab','capsule','cap','can','pouch','sachet','piece','pc','pcs','strip','vial','test','dose'}
# already corrected by the earlier weight cleanup (they are upp=1 now, so they won't match anyway)
_ALREADY = {239,497,498,499,500,502,503,504,505,513,515,527,1062,2970}


def _now(): return datetime.now(timezone.utc).isoformat()
def _abort(m): sys.exit(f"ABORT (no writes): {m}")
def db_path():
    url = os.environ.get("DATABASE_URL", "sqlite:////data/ims.db")
    return (url.split("sqlite:///")[-1] or "/data/ims.db") if url.startswith("sqlite") else "/data/ims.db"
def connect_ro(p):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True); con.row_factory = sqlite3.Row; return con


def _to_g(v, u):
    u = u.lower()
    if u.startswith('kg'): return v * 1000
    if u in ('g','gm','gms','gram','grams'): return v
    if u.startswith('lb') or u.startswith('pound'): return v * 453.592
    if u == 'oz': return v * 28.3495
    return None
def _to_ml(v, u):
    u = u.lower()
    if u in ('ml','mls'): return v
    if u.startswith('l'): return v * 1000
    return None
def size_match(text, target):
    """('weight'|'volume', token, grams_or_ml) if a weight/volume in text == target, else None."""
    for m in _WEIGHT.finditer(text or ''):
        g = _to_g(float(m.group(1)), m.group(2))
        if g and round(g) == target: return ('weight', m.group(0).strip(), round(g))
    for m in _VOLUME.finditer(text or ''):
        ml = _to_ml(float(m.group(1)), m.group(2))
        if ml and round(ml) == target: return ('volume', m.group(0).strip(), round(ml))
    return None
def count_match(text, target):
    for m in _COUNT.finditer(text or ''):
        if int(m.group(1)) == target: return m.group(0).strip()
    return None


def _row_ctx(cur, psid):
    """Live evidence for one product_supplier: cost/upp + name + latest catalogue pack_size + uom."""
    return cur.execute("""
        SELECT ps.id, ps.units_per_pack upp, ps.basic_cost bc, p.id pid, p.sku_code, p.name, p.uom,
               p.category, s.code scode,
               (SELECT ci.pack_size FROM catalogue_items ci
                  WHERE ci.matched_product_id=p.id AND ci.supplier_id=ps.supplier_id
                    AND ci.pack_size IS NOT NULL ORDER BY ci.id DESC LIMIT 1) pack_size
        FROM product_suppliers ps JOIN products p ON p.id=ps.product_id
        LEFT JOIN suppliers s ON s.id=ps.supplier_id WHERE ps.id=?""", (psid,)).fetchone()


def classify_row(r):
    """Return (confidence|None, size_tuple). confidence in {HIGH, REVIEW}; None if not a size-misread."""
    if r is None or r["upp"] is None or r["upp"] <= 1 or r["id"] in _ALREADY:
        return (None, None)
    blob = f"{r['name'] or ''} {r['pack_size'] or ''}"
    sz = size_match(blob, r["upp"])
    if not sz or count_match(blob, r["upp"]):
        return (None, sz)
    uom = (r["uom"] or "").strip().lower()
    return (("REVIEW" if uom in _COUNT_UOMS else "HIGH"), sz)


def resolve(cur):
    rows = cur.execute("SELECT ps.id FROM product_suppliers ps WHERE ps.units_per_pack>1").fetchall()
    out = []
    for pr in rows:
        r = _row_ctx(cur, pr["id"])
        conf, sz = classify_row(r)
        if conf:
            out.append((r, sz, conf))
    out.sort(key=lambda h: (h[2] != "HIGH", -h[0]["upp"]))
    return out


_COLS = ["confidence","kind","id","sku_code","supplier","name","uom","category","basic_cost",
         "old_units_per_pack","size_token","parsed_size","eff_now","new_units_per_pack"]


def _rowdict(r, sz, conf):
    eff = round(r["bc"]/r["upp"], 4) if r["bc"] else None
    return {"confidence": conf, "kind": sz[0], "id": r["id"], "sku_code": r["sku_code"], "supplier": r["scode"],
            "name": r["name"], "uom": r["uom"], "category": r["category"], "basic_cost": r["bc"],
            "old_units_per_pack": r["upp"], "size_token": sz[1], "parsed_size": sz[2], "eff_now": eff,
            "new_units_per_pack": 1}


def preview(out=None):
    con = connect_ro(db_path()); hits = resolve(con.cursor()); con.close()
    hi = [h for h in hits if h[2] == "HIGH"]; rv = [h for h in hits if h[2] == "REVIEW"]
    print("=" * 100)
    print("units_per_pack SIZE-MIS-READ CLEANUP (Phase 0) — DRY-RUN PREVIEW (READ-ONLY, NO WRITES)")
    print("Sets units_per_pack=1 ONLY on HIGH rows; never touches basic_cost / order fields.")
    print("=" * 100)
    for r, sz, _ in hi[:30]:
        eff = r["bc"]/r["upp"] if r["bc"] else None
        print(f"  HIGH   ps={r['id']:<5} {str(r['scode'] or '?'):<8} upp={r['upp']:<5} '{sz[1]}'={sz[2]}{sz[0][:3]} "
              f"cost={r['bc']} eff {round(eff,3) if eff else '-'}->{r['bc']} | {(r['name'] or '')[:36]}")
    for r, sz, _ in rv:
        print(f"  REVIEW ps={r['id']:<5} uom={r['uom']} upp={r['upp']} '{sz[1]}'={sz[2]} | {(r['name'] or '')[:40]}  (NOT auto-applied)")
    print(f"\nSUMMARY: candidates={len(hits)}  HIGH(apply)={len(hi)}  REVIEW(hold)={len(rv)}")
    if out:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_COLS); w.writeheader()
            for r, sz, conf in hits:
                w.writerow(_rowdict(r, sz, conf))
        print(f"manifest CSV: {out}  (HIGH rows are the apply set)")
    return hits


def apply(from_csv, operator, expected_fix_count, out=None, include_review=False):
    if not from_csv: _abort("--apply requires --from-csv <preview manifest>")
    if not operator: _abort("--apply requires --operator")
    if expected_fix_count is None: _abort("--apply requires --expected-fix-count")
    with open(from_csv, encoding="utf-8-sig") as f:
        manifest = [r for r in csv.DictReader(f)]
    want = [m for m in manifest if m["confidence"] == "HIGH" or (include_review and m["confidence"] == "REVIEW")]
    if not want: _abort("manifest has no applicable rows")

    con = connect_ro(db_path()); cur = con.cursor()
    if "pricing_note" not in {c[1] for c in cur.execute("PRAGMA table_info(product_suppliers)")}:
        con.close(); _abort("pricing_note column missing")
    # Re-verify EVERY row against live state before touching anything.
    ready, blocked = [], []
    for m in want:
        r = _row_ctx(cur, int(m["id"]))
        conf, sz = classify_row(r)
        if r is None: blocked.append((m["id"], "row gone")); continue
        if r["upp"] != int(m["old_units_per_pack"]): blocked.append((m["id"], f"upp changed {m['old_units_per_pack']}->{r['upp']}")); continue
        if conf is None or not sz: blocked.append((m["id"], "no longer a size-misread")); continue
        if not include_review and conf != "HIGH": blocked.append((m["id"], f"confidence={conf}")); continue
        ready.append((r, sz))
    con.close()
    if blocked:
        _abort("rows drifted since preview — refusing to write any:\n  " +
               "\n  ".join(f"ps {i}: {why}" for i, why in blocked))
    if len(ready) != expected_fix_count:
        _abort(f"--expected-fix-count {expected_fix_count} != {len(ready)} ready rows")

    import database, models
    from services import audit_log
    when = _now(); applied = []
    db = database.SessionLocal()
    try:
        for r, sz in ready:
            ps = db.get(models.ProductSupplier, r["id"])
            if ps is None or ps.units_per_pack != r["upp"]:
                raise RuntimeError(f"ps {r['id']} changed at write time")
            old_upp = ps.units_per_pack; old_note = ps.pricing_note
            unit = 'g' if sz[0] == 'weight' else 'ml'
            note = (f"units_per_pack cleanup {when[:10]} by {operator}: units_per_pack {old_upp} -> 1. "
                    f"Previous value equalled the pack {sz[0]} ('{sz[1]}' = {sz[2]}{unit}) parsed from the "
                    f"description — an import/OCR mis-read; this item is sold as one container, not per "
                    f"{unit}. basic_cost {ps.basic_cost} unchanged; order fields left NULL.")
            ps.pricing_note = (ps.pricing_note + " | " if ps.pricing_note else "") + note
            ps.units_per_pack = 1
            ps.updated_at = when
            audit_log.record(db, action=ACTION, actor=None, entity_type="product_supplier",
                             entity_id=ps.id, entity_label=r["sku_code"],
                             details={"operator": operator, "phase": "0-size-misread", "kind": sz[0],
                                      "size_token": sz[1], "parsed_size": sz[2],
                                      "basic_cost_unchanged": ps.basic_cost,
                                      "old_units_per_pack": old_upp, "new_units_per_pack": 1})
            applied.append({"product_supplier_id": ps.id, "sku_code": r["sku_code"], "supplier": r["scode"],
                            "name": r["name"], "kind": sz[0], "size_token": sz[1], "operator": operator,
                            "applied_at": when, "basic_cost": ps.basic_cost, "old_units_per_pack": old_upp,
                            "new_units_per_pack": 1, "old_pricing_note": old_note or "",
                            "new_pricing_note": ps.pricing_note})
        db.commit()
    except Exception as exc:
        db.rollback(); _abort(f"apply failed, rolled back: {type(exc).__name__}: {exc}")
    finally:
        db.close()
    print(f"APPLIED: {len(applied)} size-misread rows corrected by {operator} at {when} (units_per_pack->1).")
    if out and applied:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(applied[0].keys())); w.writeheader(); w.writerows(applied)
        print(f"APPLIED CSV: {out}")
    return {"changed": len(applied)}


def rollback_from_csv(csv_path, operator, out=None):
    if not operator: _abort("--rollback-from-csv requires --operator")
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows: _abort(f"{csv_path} has no rows")
    import database, models
    from services import audit_log
    when = _now(); restored = []
    db = database.SessionLocal()
    try:
        for r in rows:
            ps = db.get(models.ProductSupplier, int(r["product_supplier_id"]))
            if ps is None: raise RuntimeError(f"ps {r['product_supplier_id']} not found")
            before = {"units_per_pack": ps.units_per_pack, "pricing_note": ps.pricing_note}
            ps.units_per_pack = int(r["old_units_per_pack"]); ps.pricing_note = (r.get("old_pricing_note") or None)
            ps.updated_at = when
            audit_log.record(db, action=ROLLBACK_ACTION, actor=None, entity_type="product_supplier",
                             entity_id=ps.id, entity_label=r.get("sku_code"),
                             details={"operator": operator, "restored_from": csv_path, "reverted": before})
            restored.append({"product_supplier_id": ps.id, "restored_units_per_pack": ps.units_per_pack})
        db.commit()
    except Exception as exc:
        db.rollback(); _abort(f"rollback failed, rolled back: {type(exc).__name__}: {exc}")
    finally:
        db.close()
    print(f"ROLLED BACK: {len(restored)} rows restored by {operator} at {when}.")
    return {"restored": len(restored)}


def main(argv):
    ap = argparse.ArgumentParser(description="units_per_pack size-mis-read cleanup (Phase 0, read-only default).")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--from-csv", default=None, help="preview manifest to apply (HIGH rows)")
    ap.add_argument("--include-review", action="store_true", help="also apply REVIEW rows (default: skip)")
    ap.add_argument("--operator", default=None)
    ap.add_argument("--expected-fix-count", type=int, default=None)
    ap.add_argument("--rollback-from-csv", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.rollback_from_csv:
        rollback_from_csv(a.rollback_from_csv, a.operator, a.out)
    elif a.apply:
        apply(a.from_csv, a.operator, a.expected_fix_count, a.out, a.include_review)
    else:
        preview(a.out)


if __name__ == "__main__":
    main(sys.argv[1:])
