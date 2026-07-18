"""units_per_pack WEIGHT-MIS-READ cleanup — DRY-RUN by default, opt-in --apply, --rollback-from-csv.

Sibling of hills_dry_upp_cleanup.py, same rigor, wider (cross-supplier) target set. Each of these
rows carries a `units_per_pack` that is exactly the pack WEIGHT IN GRAMS parsed from the product name
(e.g. "…4KG" -> 4000, "…454G" -> 454, "…4G" -> 4). That is an import/OCR mis-read: the item is sold
as ONE unit (a bag / tub / tube), so `units_per_pack` should be 1. Left uncorrected, the effective
unit cost is basic_cost / grams — e.g. Ziwipeak HK$1592 / 4000 = HK$0.40 — which wrecks the margin.
The only correction is `units_per_pack -> 1`. We must NOT copy the old value into
order_increment_qty / minimum_order_qty (it was never an order multiple).

Reviewed target set (ProductSupplier.id -> expected CURRENT units_per_pack == grams-in-name).
Hardcoded on purpose — a one-off, auditable remediation for exactly these rows, not a general tool.
Purina FortiFlora ("1.06OZ" ≈ 30g vs upp 30) is DELIBERATELY EXCLUDED: 30 could be the sachet count,
so it needs a human call, not this script.

Guards (a row is only written when ALL hold): current units_per_pack == the expected value below;
the product `uom` is NOT a weight unit (so it isn't genuinely sold by weight); and the product name
still contains a kg/g token that converts to exactly that many grams (the evidence is re-verified at
classify AND at write time). If any target is in an unexpected state (and is not already corrected),
--apply aborts with ZERO writes. Mutation key is ProductSupplier.id only. basic_cost, cost_source,
order_increment_qty, minimum_order_qty and Product.min_purchase_qty are NEVER touched. Each fix
appends a pricing_note explaining the mis-read and records an AuditLog
`supplier_cost.units_per_pack_correction` row. Reversible via --rollback-from-csv.

Usage (runs standalone in the api container — NO PYTHONPATH):
    docker exec backend-api-1 python scripts/upp_weight_misread_cleanup.py                  # read-only preview
    docker exec backend-api-1 python scripts/upp_weight_misread_cleanup.py \
        --apply --operator "Desmond" --expected-fix-count 14 --out /tmp/upp_weight_APPLIED.csv
    docker exec backend-api-1 python scripts/upp_weight_misread_cleanup.py \
        --rollback-from-csv /tmp/upp_weight_APPLIED.csv --operator "Desmond"
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

# Standalone-import bootstrap (mirrors hills_dry_upp_cleanup.py): backend root for
# database/models/services, this scripts/ dir for completeness. No PYTHONPATH needed. Guarded so the
# read-only preview still runs when piped via stdin (`python -`), where __file__ is undefined.
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _BACKEND_ROOT = os.path.dirname(_HERE)
    for _p in (_HERE, _BACKEND_ROOT):
        if _p not in sys.path:
            sys.path.insert(0, _p)
except NameError:
    pass

# ProductSupplier.id -> expected CURRENT units_per_pack (== pack weight in grams from the name).
TARGETS = {
    239: 75,      # Beaphar Dental Powder 75g
    497: 1000,    # Ziwipeak Air-Dried Cat Beef 1KG
    498: 400,     # Ziwipeak Air-Dried Cat Beef 400G
    499: 1000,    # Ziwipeak Air-Dried Cat Free-Range Chicken 1KG
    500: 400,     # Ziwipeak Air-Dried Cat Free-Range Chicken 400G
    502: 400,     # Ziwipeak Air-Dried Cat Lamb 400G
    503: 1000,    # Ziwipeak Air-Dried Cat Mackerel & Lamb 1KG
    504: 400,     # Ziwipeak Air-Dried Cat Mackerel & Lamb 400G
    505: 400,     # Ziwipeak Air-Dried Cat Venison 400G
    513: 4000,    # Ziwipeak Air-Dried Dog Free-Range Chicken 4KG
    515: 2500,    # Ziwipeak Air-Dried Dog Lamb 2.5KG
    527: 454,     # Ziwipeak Air-Dried Dog Venison 454G
    1062: 4,      # Amacin Eye & Ear Ointment 4G
    2970: 4,      # Tricin Eye & Ear Ointment 4G
}
ACTION = "supplier_cost.units_per_pack_correction"
ROLLBACK_ACTION = "supplier_cost.units_per_pack_correction_rollback"

_WEIGHT_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s?(kg|g)\b", re.I)   # kg/g only (oz/lb excluded on purpose)
_WEIGHT_UOM = {"g", "gram", "grams", "kg", "ml", "l", "litre", "liter"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _abort(msg: str):
    sys.exit(f"ABORT (no writes): {msg}")


def db_path() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:////data/ims.db")
    return (url.split("sqlite:///")[-1] or "/data/ims.db") if url.startswith("sqlite") else "/data/ims.db"


def connect_ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def grams_token_matching(name: str, expected: int):
    """Return the literal weight token (e.g. '4KG') whose gram value == expected, else None.
    This is the per-row EVIDENCE the units_per_pack value is a weight mis-read."""
    for val, unit in _WEIGHT_TOKEN.findall(name or ""):
        grams = float(val) * (1000 if unit.lower() == "kg" else 1)
        if round(grams) == expected:
            return f"{val}{unit}"
    return None


def _note(operator: str, old_upp: int, token: str, basic_cost, when: str) -> str:
    return (f"units_per_pack cleanup {when[:10]} by {operator}: units_per_pack {old_upp} -> 1. "
            f"Previous value equalled the pack weight in grams parsed from the product name "
            f"('{token}' = {old_upp}g) — an import/OCR mis-read, NOT a sellable-unit count or order "
            f"multiple. This item is sold as one unit. basic_cost {basic_cost} unchanged; "
            f"order_increment_qty and minimum_order_qty deliberately left NULL.")


def classify_targets(cur) -> list[dict]:
    """Read-only: for each target compute its state — READY | DONE | BLOCKED(reason)."""
    out = []
    for psid, expected in TARGETS.items():
        r = cur.execute(
            """SELECT ps.id, ps.supplier_id, ps.supplier_sku, ps.basic_cost, ps.units_per_pack,
                      ps.order_increment_qty, ps.minimum_order_qty, ps.pricing_note,
                      p.sku_code, p.name, p.uom, p.min_purchase_qty, s.code AS scode
                 FROM product_suppliers ps JOIN products p ON p.id = ps.product_id
                 LEFT JOIN suppliers s ON s.id = ps.supplier_id
                WHERE ps.id = ?""", (psid,)).fetchone()
        if r is None:
            out.append({"product_supplier_id": psid, "expected_old_upp": expected,
                        "state": "BLOCKED", "reason": "ProductSupplier not found"})
            continue
        cur_upp = r["units_per_pack"]
        uom = (r["uom"] or "").strip().lower()
        token = grams_token_matching(r["name"], expected)
        bc = r["basic_cost"]
        if cur_upp == 1:
            state, reason = "DONE", "already corrected (units_per_pack=1)"
        elif uom in _WEIGHT_UOM:
            state, reason = "BLOCKED", f"uom={uom!r} is a weight unit (may be sold by weight)"
        elif cur_upp != expected:
            state, reason = "BLOCKED", f"current units_per_pack={cur_upp} != expected {expected}"
        elif token is None:
            state, reason = "BLOCKED", f"no kg/g token == {expected}g in name (evidence gone)"
        else:
            state, reason = "READY", f"weight mis-read '{token}'={expected}g; units_per_pack {expected} -> 1"
        out.append({
            "product_supplier_id": psid, "internal_sku": r["sku_code"], "supplier_code": r["scode"],
            "supplier_sku": r["supplier_sku"], "product_name": (r["name"] or "")[:52], "uom": r["uom"],
            "basic_cost": bc, "weight_token": token or "", "expected_old_upp": expected,
            "old_units_per_pack": cur_upp, "new_units_per_pack": (1 if state in ("READY", "DONE") else cur_upp),
            "current_unit_cost": round(bc / cur_upp, 4) if (bc and cur_upp and cur_upp > 1) else bc,
            "future_unit_cost": bc if state in ("READY", "DONE") else (round(bc / cur_upp, 4) if (bc and cur_upp and cur_upp > 1) else bc),
            "order_increment_qty": r["order_increment_qty"], "minimum_order_qty": r["minimum_order_qty"],
            "product_min_purchase_qty": r["min_purchase_qty"], "old_pricing_note": r["pricing_note"],
            "state": state, "reason": reason})
    return out


def _write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


_PREVIEW_COLS = ["product_supplier_id", "internal_sku", "supplier_code", "supplier_sku", "product_name",
                 "uom", "basic_cost", "weight_token", "old_units_per_pack", "new_units_per_pack",
                 "current_unit_cost", "future_unit_cost", "order_increment_qty", "minimum_order_qty",
                 "product_min_purchase_qty", "old_pricing_note", "state", "reason"]


def preview(out: str | None = None) -> list[dict]:
    con = connect_ro(db_path())
    rows = classify_targets(con.cursor())
    con.close()
    print("=" * 100)
    print("units_per_pack WEIGHT-MIS-READ CLEANUP — DRY-RUN PREVIEW (READ-ONLY, NO WRITES)")
    print("Sets units_per_pack=1 ONLY; never sets order_increment_qty / minimum_order_qty / touches basic_cost.")
    print("=" * 100)
    for r in rows:
        print(f"  ps={r['product_supplier_id']:>5} {str(r.get('internal_sku','?')):9} "
              f"{str(r.get('supplier_code','') or ''):7} upp {r.get('old_units_per_pack','?')}->{r.get('new_units_per_pack','?'):<4} "
              f"cost={str(r.get('basic_cost','?')):>7} eff {r.get('current_unit_cost','?')}->{r.get('future_unit_cost','?'):<7} "
              f"{r['state']:8} {r['reason']}")
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    print("SUMMARY:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if out:
        _write_csv(out, rows, _PREVIEW_COLS)
        print(f"preview CSV: {out}")
    return rows


def apply(operator: str | None, expected_fix_count: int | None, out: str | None = None) -> dict:
    if not operator:
        _abort("--apply requires --operator")
    if expected_fix_count is None:
        _abort("--apply requires --expected-fix-count")
    con = connect_ro(db_path())
    cur = con.cursor()
    if "pricing_note" not in {c[1] for c in cur.execute("PRAGMA table_info(product_suppliers)")}:
        con.close(); _abort("pricing_note column missing — run the migration first")
    rows = classify_targets(cur)
    con.close()

    blocked = [r for r in rows if r["state"] == "BLOCKED"]
    if blocked:
        _abort("targets in an unexpected state — refusing to write any row:\n  " +
               "\n  ".join(f"ps {r['product_supplier_id']}: {r['reason']}" for r in blocked))
    ready = [r for r in rows if r["state"] == "READY"]
    if not ready:
        print("Nothing to apply (all targets already corrected). No writes.")
        return {"changed": 0}
    if len(ready) != expected_fix_count:
        _abort(f"--expected-fix-count {expected_fix_count} != {len(ready)} ready rows "
               f"({[r['product_supplier_id'] for r in ready]})")

    import database, models
    from services import audit_log
    when = _now()
    applied = []
    db = database.SessionLocal()
    try:
        for r in ready:
            ps = db.get(models.ProductSupplier, r["product_supplier_id"])
            expected = TARGETS[ps.id]
            token = grams_token_matching(ps.product.name, expected)
            # Re-verify EVERY guard at write time (state could have drifted since the read-only pass).
            if ps is None or ps.units_per_pack != expected or token is None \
                    or (ps.product.uom or "").strip().lower() in _WEIGHT_UOM:
                raise RuntimeError(f"ps {r['product_supplier_id']} failed re-check at write time")
            old_upp = ps.units_per_pack
            old_note = ps.pricing_note
            note = _note(operator, old_upp, token, ps.basic_cost, when)
            ps.pricing_note = (ps.pricing_note + " | " if ps.pricing_note else "") + note
            ps.units_per_pack = 1
            ps.updated_at = when
            # NEVER touched: basic_cost, cost_source(_ref), order_increment_qty, minimum_order_qty,
            #                Product.min_purchase_qty.
            audit_log.record(db, action=ACTION, actor=None, entity_type="product_supplier",
                             entity_id=ps.id, entity_label=r["internal_sku"],
                             details={"operator": operator,
                                      "reason": "units_per_pack == pack weight in grams (import/OCR mis-read)",
                                      "weight_token": token, "basic_cost_unchanged": ps.basic_cost,
                                      "old_units_per_pack": old_upp, "new_units_per_pack": 1})
            applied.append({
                "product_supplier_id": ps.id, "internal_sku": r["internal_sku"],
                "supplier_code": r["supplier_code"], "supplier_sku": r["supplier_sku"],
                "product_name": r["product_name"], "weight_token": token, "operator": operator,
                "applied_at": when, "basic_cost": ps.basic_cost, "old_units_per_pack": old_upp,
                "new_units_per_pack": 1, "order_increment_qty": ps.order_increment_qty,
                "minimum_order_qty": ps.minimum_order_qty, "product_min_purchase_qty": r["product_min_purchase_qty"],
                "old_pricing_note": old_note or "", "new_pricing_note": ps.pricing_note})
        db.commit()
    except Exception as exc:
        db.rollback(); _abort(f"apply failed, rolled back: {type(exc).__name__}: {exc}")
    finally:
        db.close()

    print(f"APPLIED: {len(applied)} weight-mis-read rows corrected by {operator} at {when} "
          f"(units_per_pack->1; basic_cost unchanged; order fields left NULL).")
    if out and applied:
        _write_csv(out, applied, list(applied[0].keys()))
        print(f"APPLIED CSV: {out}")
    return {"changed": len(applied), "ps_ids": [a["product_supplier_id"] for a in applied], "csv": out}


def _to_int(v):
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def rollback_from_csv(csv_path: str, operator: str | None, out: str | None = None) -> dict:
    if not operator:
        _abort("--rollback-from-csv requires --operator")
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        _abort(f"{csv_path} has no rows")
    import database, models
    from services import audit_log
    when = _now()
    restored = []
    db = database.SessionLocal()
    try:
        for r in rows:
            ps = db.get(models.ProductSupplier, int(r["product_supplier_id"]))
            if ps is None:
                raise RuntimeError(f"ps {r['product_supplier_id']} not found")
            before = {"units_per_pack": ps.units_per_pack, "pricing_note": ps.pricing_note}
            ps.units_per_pack = _to_int(r.get("old_units_per_pack"))
            ps.pricing_note = (r.get("old_pricing_note") or None)
            ps.updated_at = when
            audit_log.record(db, action=ROLLBACK_ACTION, actor=None, entity_type="product_supplier",
                             entity_id=ps.id, entity_label=r.get("internal_sku"),
                             details={"operator": operator, "restored_from": csv_path, "reverted": before})
            restored.append({"product_supplier_id": ps.id, "internal_sku": r.get("internal_sku"),
                             "restored_units_per_pack": ps.units_per_pack, "rolled_back_at": when})
        db.commit()
    except Exception as exc:
        db.rollback(); _abort(f"rollback failed, rolled back: {type(exc).__name__}: {exc}")
    finally:
        db.close()
    print(f"ROLLED BACK: {len(restored)} rows restored by {operator} at {when}.")
    if out and restored:
        _write_csv(out, restored, list(restored[0].keys()))
        print(f"ROLLBACK CSV: {out}")
    return {"restored": len(restored), "csv": out}


def main(argv):
    ap = argparse.ArgumentParser(description="units_per_pack weight-mis-read cleanup (read-only default).")
    ap.add_argument("--apply", action="store_true", help="OPT-IN: set units_per_pack=1 on the ready target rows")
    ap.add_argument("--operator", default=None)
    ap.add_argument("--expected-fix-count", type=int, default=None)
    ap.add_argument("--rollback-from-csv", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.rollback_from_csv:
        rollback_from_csv(args.rollback_from_csv, args.operator, args.out)
    elif args.apply:
        apply(args.operator, args.expected_fix_count, args.out)
    else:
        preview(args.out)


if __name__ == "__main__":
    main(sys.argv[1:])
