"""Hill's cost-basis remediation — READ-ONLY DRY-RUN PREVIEW (no writes, ever).

Hill's catalogue lists Gross Wholesale Price PER SELLABLE UNIT (per can/bag), while "Order
Multiple" (12/24) is an ordering constraint. During ingestion the order multiple landed in
`ProductSupplier.units_per_pack`, and because `unit_cost = basic_cost / units_per_pack`, the
per-unit cost is being divided by 12/24 — understated. This script CLASSIFIES the affected rows
and PREVIEWS the intended fix.

The six ordering-term columns NOW EXIST on product_suppliers (added by the landed PR-A migration):
order_increment_qty, order_increment_uom, minimum_order_qty, minimum_order_uom,
minimum_order_source, pricing_note. THIS SCRIPT STILL DOES NOT WRITE TO THEM — it opens the DB
mode=ro and only previews the values a future apply would set:

    order_increment_qty_after   = old units_per_pack
    order_increment_uom_after   = product.uom if present else "sellable_unit"
    minimum_order_qty_after     = old units_per_pack
    minimum_order_uom_after     = product.uom if present else "sellable_unit"
    minimum_order_source_after  = "inferred_from_order_multiple"
    pricing_note_after          = Hill's basis-fix explanation
    units_per_pack_after        = 1        (basic_cost is NEVER changed)

Mutation key (for the future apply) is ProductSupplier.id. `supplier_sku` is catalogue EVIDENCE
only (not unique, even within Hill's) and is never a write key.

AUTO_FIX gate (all must hold): supplier is the confirmed Hill's; units_per_pack>1;
cost_source=='catalogue'; segment==WET; basic_cost present; supplier_sku non-empty and maps to
exactly one Hill's ProductSupplier; catalogue pack evidence agrees on a count == units_per_pack;
catalogue cost_price does NOT conflict with basic_cost; Product.min_purchase_qty is NULL.
Everything else -> REVIEW.

--approve-ids may promote ONLY a MANUAL WET row (human-entered cost), and only when it also clears
every gate: confirmed Hill's, cost_source=='manual', segment==WET, basic_cost present, non-empty
supplier_sku, units_per_pack>1, Product.min_purchase_qty is NULL, supplier_sku is not dual-mapped,
AND catalogue_cost_matches_basic_cost is True. Catalogue-source rows, DRY rows, dual-mapped,
min-purchase-conflict, catalogue-cost-conflict, or missing-cost-evidence ids passed to --approve-ids
are IGNORED and stay REVIEW with a reason. Non-manual wet rows are classified normally (AUTO_FIX or
REVIEW) — approval never overrides that.

This script is strictly read-only: mode=ro connection, no ORM/session, no commit, no migration,
no apply mode. Safe to run repeatedly; it never changes the database (mtime unchanged).

Usage:
    python scripts/hills_cost_basis_dryrun.py                                  # preview (supplier 14)
    python scripts/hills_cost_basis_dryrun.py --supplier-id 14 --out preview.csv
    python scripts/hills_cost_basis_dryrun.py --approve-ids "362,1675,1678"    # promote manual wet
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys

DEFAULT_SUPPLIER_ID = 14


def _abort(msg: str):
    sys.exit(f"ABORT: {msg}")


def db_path() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:////data/ims.db")
    return (url.split("sqlite:///")[-1] or "/data/ims.db") if url.startswith("sqlite") else "/data/ims.db"


def connect_ro(path: str) -> sqlite3.Connection:
    """Open the DB strictly READ-ONLY so this tool physically cannot write."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def resolve_hills(cur, supplier_id: int) -> sqlite3.Row:
    """Load the supplier by the EXPLICIT id (primary input), then assert it looks like Hill's
    via name / normalized_name / code (safety check — we never resolve Hill's by name search)."""
    r = cur.execute("SELECT id, code, name, normalized_name FROM suppliers WHERE id = ?",
                    (supplier_id,)).fetchone()
    if r is None:
        _abort(f"supplier id {supplier_id} not found")
    name = (r["name"] or "").lower()
    norm = (r["normalized_name"] or "").lower()
    code = (r["code"] or "")
    if not ("hill" in name or norm == "hills" or code == "10369HG"):
        _abort(f"supplier id {supplier_id} (name={r['name']!r} code={code!r} normalized={norm!r}) "
               f"does not look like Hill's — refusing to run")
    return r


# ── catalogue pack-count parsing (order multiple), unchanged ─────────────────
_COUNT_UNIT = r"(?:cans?|pcs?|pouch(?:es)?|ct|pk|packs?|tabs?|caps?|bottles?|units?)"


def catalogue_multiple(pack_size, ci_upp) -> int | None:
    if ci_upp and ci_upp > 1:
        return int(ci_upp)
    if not pack_size:
        return None
    s = str(pack_size).strip().lower()
    m = re.match(r"^\s*(\d{1,3})\s*[x/]", s) \
        or re.match(rf"^\s*(\d{{1,3}})\s*{_COUNT_UNIT}\b", s) \
        or re.match(r"^\s*(?:box|case|carton)\s*(?:of|/)?\s*(\d{1,3})\b", s)
    return int(m.group(1)) if m else None   # bare weight (kg/lbs/oz) -> no count


def segment_of(name: str) -> str:
    n = (name or "").lower()
    return "WET" if "wet" in n else ("DRY" if "dry" in n else "UNKNOWN")


def _approval_ok(seg, cost_source, basic_cost, supplier_sku, upp, min_purchase_qty, n_db_same_sku,
                 cost_matches) -> tuple[bool, str]:
    """--approve-ids may ONLY promote a MANUAL WET Hill's row that clears EVERY gate below,
    INCLUDING catalogue cost agreement (catalogue_cost_matches_basic_cost is True). DRY rows,
    catalogue-source rows, dual-mapped supplier_sku, min_purchase_qty conflicts, and catalogue
    cost conflicts/missing cost evidence are never promotable — the classifier decides those."""
    if seg != "WET":
        return False, "approval ignored: dry/suspicious row cannot be approved by this script"
    if cost_source != "manual":
        return False, "approval ignored: only manual-wet rows are promotable (catalogue rows classify normally)"
    if basic_cost is None:
        return False, "approval ignored: no basic_cost"
    if not supplier_sku:
        return False, "approval ignored: empty supplier_sku"
    if not upp or upp <= 1:
        return False, "approval ignored: units_per_pack<=1"
    if min_purchase_qty is not None:
        return False, f"approval ignored: Product.min_purchase_qty={min_purchase_qty} conflict"
    if n_db_same_sku != 1:
        return False, f"approval ignored: supplier_sku dual-mapped to {n_db_same_sku} DB rows"
    if cost_matches is False:
        return False, "approval ignored: catalogue cost_price conflicts with basic_cost"
    if cost_matches is None:
        return False, "approval ignored: no catalogue cost evidence to confirm basic_cost"
    return True, ""


def _fix_note(old_upp: int, basic_cost) -> str:
    return (f"Hill's basis-fix (preview): listed price is per sellable unit; order multiple "
            f"{old_upp} would move to order_increment_qty/minimum_order_qty; units_per_pack -> 1; "
            f"basic_cost {basic_cost} unchanged.")


def classify(cur, hill_id: int, approved: set[int]) -> list[dict]:
    rows = cur.execute(
        """SELECT ps.id AS ps_id, ps.supplier_sku, ps.basic_cost, ps.units_per_pack AS upp,
                  ps.cost_source, ps.order_increment_qty, ps.minimum_order_qty,
                  p.id AS product_id, p.sku_code, p.name, p.uom, p.min_purchase_qty
             FROM product_suppliers ps JOIN products p ON p.id = ps.product_id
            WHERE ps.supplier_id = ? AND ps.units_per_pack > 1
            ORDER BY ps.units_per_pack DESC, p.name""", (hill_id,)).fetchall()
    out = []
    for r in rows:
        upp, ssku, bc, seg = r["upp"], r["supplier_sku"], r["basic_cost"], segment_of(r["name"])
        uom_after = r["uom"] or "sellable_unit"
        n_db = cur.execute("SELECT count(*) FROM product_suppliers WHERE supplier_id=? AND supplier_sku=?",
                           (hill_id, ssku)).fetchone()[0] if ssku else 0
        cat = cur.execute("SELECT pack_size, units_per_pack, cost_price FROM catalogue_items "
                          "WHERE supplier_id=? AND supplier_sku=?", (hill_id, ssku)).fetchall() if ssku else []
        # pack (order-multiple) evidence
        pack_mults = [catalogue_multiple(c["pack_size"], c["units_per_pack"]) for c in cat]
        pack_consensus = bool(pack_mults) and all(m is not None and m == pack_mults[0] for m in pack_mults)
        pack_ok = pack_consensus and pack_mults[0] == upp
        # cost evidence
        costs = sorted({c["cost_price"] for c in cat if c["cost_price"] is not None})
        if not costs:
            cost_consensus, cost_matches = None, None
        elif len(costs) > 1:
            cost_consensus, cost_matches = False, False           # conflicting catalogue costs
        else:
            cost_consensus = True
            cost_matches = (bc is not None and abs(costs[0] - bc) <= max(0.01, 0.001 * abs(bc)))

        # AUTO_FIX gate
        reasons = []
        if r["cost_source"] != "catalogue":   reasons.append(f"cost_source={r['cost_source']}")
        if seg != "WET":                       reasons.append(f"segment={seg}")
        if bc is None:                         reasons.append("no basic_cost")
        if not ssku:                           reasons.append("empty supplier_sku")
        elif n_db != 1:                        reasons.append(f"supplier_sku maps to {n_db} DB rows")
        if r["min_purchase_qty"] is not None:  reasons.append(f"Product.min_purchase_qty={r['min_purchase_qty']} (kept)")
        if not cat:                            reasons.append("no catalogue evidence")
        elif not pack_ok:                      reasons.append(f"pack multiples {pack_mults} not consensus==upp({upp})")
        if cost_matches is False:              reasons.append("catalogue cost_price conflicts with basic_cost")

        if not reasons:
            bucket, reason = "AUTO_FIX", f"catalogue+WET; pack consensus={upp}; cost matches basic_cost"
        elif r["ps_id"] in approved:
            ok, why = _approval_ok(seg, r["cost_source"], bc, ssku, upp, r["min_purchase_qty"], n_db, cost_matches)
            bucket, reason = ("APPROVED", "explicit --approve-ids sign-off (manual wet)") if ok else ("REVIEW", why)
        else:
            bucket, reason = "REVIEW", "; ".join(reasons)

        will_fix = bucket in ("AUTO_FIX", "APPROVED")
        cur_uc = round(bc / upp, 4) if (bc and upp) else bc
        out.append({
            "product_supplier_id": r["ps_id"], "internal_sku": r["sku_code"], "supplier_sku": ssku or "",
            "segment": seg, "cost_source": r["cost_source"],
            "basic_cost_before": bc, "basic_cost_after": bc,                 # NEVER changes
            "units_per_pack_before": upp, "units_per_pack_after": (1 if will_fix else upp),
            "current_computed_unit_cost": cur_uc,
            "future_computed_unit_cost": (bc if will_fix else cur_uc),
            "order_increment_qty_before": r["order_increment_qty"],
            "order_increment_qty_after": (upp if will_fix else r["order_increment_qty"]),
            "order_increment_uom_after": (uom_after if will_fix else None),
            "minimum_order_qty_before": r["minimum_order_qty"],
            "minimum_order_qty_after": (upp if will_fix else r["minimum_order_qty"]),
            "minimum_order_uom_after": (uom_after if will_fix else None),
            "minimum_order_source_after": ("inferred_from_order_multiple" if will_fix else None),
            "pricing_note_after": (_fix_note(upp, bc) if will_fix else None),
            "product_min_purchase_qty": r["min_purchase_qty"],
            "catalogue_match_count": len(cat),
            "catalogue_pack_evidence": " | ".join(str(c["pack_size"]) for c in cat),
            "catalogue_multiples": ",".join(str(m) for m in pack_mults) if pack_mults else "",
            "catalogue_cost_prices": ",".join(str(x) for x in costs) if costs else "",
            "catalogue_cost_consensus": cost_consensus,
            "catalogue_cost_matches_basic_cost": cost_matches,
            "bucket": bucket, "reason": reason,
        })
    return out


CSV_COLS = [
    "product_supplier_id", "internal_sku", "supplier_sku", "segment", "cost_source",
    "basic_cost_before", "basic_cost_after", "units_per_pack_before", "units_per_pack_after",
    "current_computed_unit_cost", "future_computed_unit_cost",
    "order_increment_qty_before", "order_increment_qty_after", "order_increment_uom_after",
    "minimum_order_qty_before", "minimum_order_qty_after", "minimum_order_uom_after",
    "minimum_order_source_after", "pricing_note_after", "product_min_purchase_qty",
    "catalogue_match_count", "catalogue_pack_evidence", "catalogue_multiples",
    "catalogue_cost_prices", "catalogue_cost_consensus", "catalogue_cost_matches_basic_cost",
    "bucket", "reason",
]


def preview(supplier_id: int = DEFAULT_SUPPLIER_ID, approve_ids: set[int] | None = None,
            out: str | None = None) -> list[dict]:
    approve_ids = approve_ids or set()
    con = connect_ro(db_path())
    hill = resolve_hills(con.cursor(), supplier_id)
    rows = classify(con.cursor(), hill["id"], approve_ids)
    con.close()

    print("=" * 96)
    print("HILL'S COST-BASIS — DRY-RUN PREVIEW (READ-ONLY, NO WRITES)")
    print(f"supplier: id={hill['id']} code={hill['code']} name={hill['name']!r}  (verified Hill's)")
    if approve_ids:
        print(f"--approve-ids: {sorted(approve_ids)}")
    print("=" * 96)
    for r in rows:
        cm = {True: "cost=ok", False: "cost=CONFLICT", None: "cost=?"}[r["catalogue_cost_matches_basic_cost"]]
        print(f"  ps={r['product_supplier_id']:>5} {r['internal_sku']:9} {r['supplier_sku'][:10]:10} "
              f"{r['segment']:4} {r['cost_source']:9} upp={r['units_per_pack_before']:>2} "
              f"basic={r['basic_cost_before']} uc {r['current_computed_unit_cost']}->{r['future_computed_unit_cost']} "
              f"{cm:13} {r['bucket']:9} {r['reason']}")
    counts = {}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    print("-" * 96)
    print("SUMMARY:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no upp>1 rows",
          f"(total {len(rows)})")
    print("READ-ONLY: no rows were modified; the six ordering columns exist but this script never "
          "writes them. basic_cost is never changed.")
    if out:
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLS)
            w.writeheader()
            w.writerows(rows)
        print(f"preview CSV: {out} ({len(rows)} rows)")
    return rows


def main(argv):
    ap = argparse.ArgumentParser(description="Hill's cost-basis DRY-RUN preview (read-only).")
    ap.add_argument("--supplier-id", type=int, default=DEFAULT_SUPPLIER_ID,
                    help="Hill's supplier id (primary input; name/code is a safety assertion)")
    ap.add_argument("--approve-ids", default="",
                    help="comma-separated ProductSupplier.id to promote (gated — dry/suspicious ids stay REVIEW)")
    ap.add_argument("--out", default=None, help="write the preview CSV to this path")
    args = ap.parse_args(argv)
    approved = {int(x) for x in re.split(r"[,\s]+", args.approve_ids) if x.strip().isdigit()}
    preview(args.supplier_id, approved, args.out)


if __name__ == "__main__":
    main(sys.argv[1:])
