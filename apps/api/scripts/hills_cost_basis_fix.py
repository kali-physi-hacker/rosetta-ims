"""Hill's cost-basis APPLY + ROLLBACK — reuses the CANONICAL classifier (no duplicated logic).

Classification, supplier verification, `--supplier-id`, evidence and the (manual-wet-only,
cost-conflict-rejecting) `--approve-ids` gate all come from the merged, reviewed dry-run script
`hills_cost_basis_dryrun.py`. This module only adds the write layer:

  * DEFAULT (no flags)  -> the canonical read-only preview (mode=ro).
  * --apply             -> mutate ONLY the rows the canonical classifier returns as AUTO_FIX or
                           APPROVED; nothing else can ever be touched.
  * --rollback-from-csv -> restore literal pre-apply values by ProductSupplier.id.

Because the fix set is taken verbatim from the canonical classifier, the apply can NEVER mutate a
REVIEW / DRY / non-Hill's / cost-conflict / min-purchase-conflict / dual-mapped / blindly-approved
row — those are excluded upstream by the same gates the dry-run enforces. Mutation key is always
ProductSupplier.id; `supplier_sku` is evidence only. `basic_cost` and `get_unit_cost()` are never
touched.

Per fixed row (order matters): snapshot old values, copy old units_per_pack ->
order_increment_qty + minimum_order_qty, set uom (product.uom else 'sellable_unit') +
minimum_order_source='inferred_from_order_multiple', append pricing_note (operator + old multiple;
approval noted for --approve-ids rows), THEN units_per_pack -> 1.

Usage (runs standalone in the api container — NO PYTHONPATH needed):
    docker exec backend-api-1 python scripts/hills_cost_basis_fix.py                     # canonical preview
    docker exec backend-api-1 python scripts/hills_cost_basis_fix.py \
        --apply --operator "Desmond" --expected-fix-count 3 --out /tmp/hills_cost_fix_APPLIED.csv
    docker exec backend-api-1 python scripts/hills_cost_basis_fix.py \
        --apply --operator "Desmond" --approve-ids "1675,1678" --expected-fix-count 5
    docker exec backend-api-1 python scripts/hills_cost_basis_fix.py \
        --rollback-from-csv /tmp/hills_cost_fix_APPLIED.csv --operator "Desmond"
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone

# Runnable standalone (`python scripts/hills_cost_basis_fix.py …`) with NO PYTHONPATH: put this
# scripts/ dir (for the sibling dry-run import) AND the backend root (for database/models/services,
# imported only by --apply/--rollback) on sys.path before importing them.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _BACKEND_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── single source of truth: the merged, reviewed classifier + supplier verification ──
from hills_cost_basis_dryrun import (
    DEFAULT_SUPPLIER_ID, db_path, connect_ro, resolve_hills, classify, preview,
)

NEW_COLS = ["order_increment_qty", "order_increment_uom", "minimum_order_qty",
            "minimum_order_uom", "minimum_order_source", "pricing_note"]
FIX_ACTION = "supplier_cost.basis_fix"
ROLLBACK_ACTION = "supplier_cost.basis_fix_rollback"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _abort(msg: str):
    sys.exit(f"ABORT (no writes): {msg}")


def _apply_note(operator: str, old_upp: int, basic_cost, bucket: str, when: str) -> str:
    note = (f"Hill's basis-fix {when[:10]} by {operator}: listed price is per sellable unit "
            f"(basic_cost {basic_cost} unchanged); order multiple {old_upp} moved off units_per_pack "
            f"to order_increment_qty/minimum_order_qty; units_per_pack set to 1.")
    if bucket == "APPROVED":
        note += " Row approved for fix by ProductSupplier.id via --approve-ids."
    return note


def _columns_present(cur, cols):
    have = {r[1] for r in cur.execute("PRAGMA table_info(product_suppliers)")}
    return [c for c in cols if c not in have]


def _write_csv(path: str, rows: list[dict], cols: list[str]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _to_int(v):
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


# ── APPLY (opt-in) — writes ONLY canonical AUTO_FIX + APPROVED rows ──────────
def apply(supplier_id: int, operator: str | None, expected_fix_count: int | None,
          approve_ids: set[int] | None = None, out: str | None = None) -> dict:
    approve_ids = approve_ids or set()
    if not operator:
        _abort("--apply requires --operator")
    if expected_fix_count is None:
        _abort("--apply requires --expected-fix-count")

    con = connect_ro(db_path())
    cur = con.cursor()
    missing = _columns_present(cur, NEW_COLS)
    if missing:
        con.close(); _abort(f"PR-A columns missing on product_suppliers: {missing} — run migration first")
    hill = resolve_hills(cur, supplier_id)                 # canonical supplier verification
    rows = classify(cur, hill["id"], approve_ids)          # canonical classifier (all safety gates)
    con.close()

    fix_set = [r for r in rows if r["bucket"] in ("AUTO_FIX", "APPROVED")]
    if not fix_set:
        print("Nothing to apply (already fixed / no eligible rows). No writes.")
        return {"changed": 0}
    if len(fix_set) != expected_fix_count:
        _abort(f"--expected-fix-count {expected_fix_count} != {len(fix_set)} eligible rows "
               f"({[r['product_supplier_id'] for r in fix_set]})")

    import database, models
    from services import audit_log
    when = _now()
    applied = []
    db = database.SessionLocal()
    try:
        for r in fix_set:
            ps = db.get(models.ProductSupplier, r["product_supplier_id"])
            if ps is None or ps.supplier_id != hill["id"]:
                raise RuntimeError(f"ps {r['product_supplier_id']} vanished or not Hill's — aborting")
            if not (ps.units_per_pack and ps.units_per_pack > 1) or ps.order_increment_qty is not None:
                continue                                   # idempotency guard
            old = {"units_per_pack": ps.units_per_pack, **{c: getattr(ps, c) for c in NEW_COLS}}
            old_upp = ps.units_per_pack
            uom = r["order_increment_uom_after"]           # product.uom else 'sellable_unit' (canonical)
            ps.order_increment_qty = old_upp
            ps.order_increment_uom = uom
            ps.minimum_order_qty = old_upp
            ps.minimum_order_uom = uom
            ps.minimum_order_source = r["minimum_order_source_after"]   # 'inferred_from_order_multiple'
            note = _apply_note(operator, old_upp, ps.basic_cost, r["bucket"], when)
            ps.pricing_note = (ps.pricing_note + " | " if ps.pricing_note else "") + note
            ps.units_per_pack = 1                           # AFTER copying the old value away
            ps.updated_at = when
            # basic_cost / cost_source / cost_source_ref / Product.min_purchase_qty: UNTOUCHED
            new = {"units_per_pack": 1, "order_increment_qty": old_upp, "order_increment_uom": uom,
                   "minimum_order_qty": old_upp, "minimum_order_uom": uom,
                   "minimum_order_source": ps.minimum_order_source, "pricing_note": ps.pricing_note}
            audit_log.record(db, action=FIX_ACTION, actor=None, entity_type="product_supplier",
                             entity_id=ps.id, entity_label=r["internal_sku"],
                             details={"operator": operator, "bucket": r["bucket"],
                                      "basic_cost_unchanged": ps.basic_cost,
                                      "catalogue_evidence": r["catalogue_pack_evidence"],
                                      "catalogue_cost_matches_basic_cost": r["catalogue_cost_matches_basic_cost"],
                                      "old": old, "new": new})
            applied.append({
                "product_supplier_id": ps.id, "internal_sku": r["internal_sku"],
                "supplier_sku": r["supplier_sku"], "segment": r["segment"], "cost_source": r["cost_source"],
                "bucket": r["bucket"], "operator": operator, "applied_at": when, "basic_cost": ps.basic_cost,
                "catalogue_cost_matches_basic_cost": r["catalogue_cost_matches_basic_cost"],
                **{f"old_{k}": v for k, v in old.items()}, **{f"new_{k}": v for k, v in new.items()}})
        db.commit()
    except Exception as exc:
        db.rollback(); _abort(f"apply failed, rolled back: {type(exc).__name__}: {exc}")
    finally:
        db.close()

    print(f"APPLIED: {len(applied)} rows changed by {operator} at {when} "
          f"(basic_cost unchanged; units_per_pack->1; order multiple recorded).")
    if out and applied:
        _write_csv(out, applied, list(applied[0].keys()))
        print(f"APPLIED CSV: {out}")
    return {"changed": len(applied), "ps_ids": [a["product_supplier_id"] for a in applied], "csv": out}


# ── ROLLBACK (literal restore from an APPLIED CSV) ───────────────────────────
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
            before = {c: getattr(ps, c) for c in ["units_per_pack", *NEW_COLS]}
            ps.units_per_pack = _to_int(r.get("old_units_per_pack"))
            ps.order_increment_qty = _to_int(r.get("old_order_increment_qty"))
            ps.order_increment_uom = (r.get("old_order_increment_uom") or None)
            ps.minimum_order_qty = _to_int(r.get("old_minimum_order_qty"))
            ps.minimum_order_uom = (r.get("old_minimum_order_uom") or None)
            ps.minimum_order_source = (r.get("old_minimum_order_source") or None)
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
    ap = argparse.ArgumentParser(description="Hill's cost-basis apply/rollback (canonical classifier).")
    ap.add_argument("--supplier-id", type=int, default=DEFAULT_SUPPLIER_ID)
    ap.add_argument("--apply", action="store_true", help="OPT-IN: mutate the canonical AUTO_FIX+APPROVED rows")
    ap.add_argument("--operator", default=None)
    ap.add_argument("--expected-fix-count", type=int, default=None)
    ap.add_argument("--approve-ids", default="")
    ap.add_argument("--rollback-from-csv", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    approved = {int(x) for x in re.split(r"[,\s]+", args.approve_ids) if x.strip().isdigit()}

    if args.rollback_from_csv:
        rollback_from_csv(args.rollback_from_csv, args.operator, args.out)
    elif args.apply:
        apply(args.supplier_id, args.operator, args.expected_fix_count, approved, args.out)
    else:
        preview(args.supplier_id, approved, args.out)      # canonical preview (read-only)


if __name__ == "__main__":
    main(sys.argv[1:])
