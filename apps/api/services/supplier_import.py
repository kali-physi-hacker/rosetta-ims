"""Supplier-master import: build the supplier reference (for catalogue→supplier matching)
from the three Google Sheets.

Sources (layered):
  * CONSOLIDATED ("SUPPLIER INFO") — the spine: the authoritative supplier *list* and the
    authoritative value for any OVERLAPPING field (MOQ, credit term, order/delivery schedule,
    cut-off). Per business rule: on a duplicate, consolidated wins.
  * VET / NON-VET (detailed) — enrich matched suppliers with Brands, contacts, FMCG split,
    bulk-buy, delivery, bank details, and set the `segment` marker (vet / non_vet).

Anything in the detailed sheets that doesn't line up with the consolidated, or a consolidated
row whose segment can't be determined, is reported under `flagged` for a quick manual pass
rather than silently guessed.

Upsert reconciles with existing suppliers by (normalized_name, segment) so codes + product
links survive. Builds supplier_aliases (codes/parentheticals/name variants) and supplier_brands
(the strongest matching signal). Idempotent; supports dry_run.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

NOW = lambda: datetime.utcnow().isoformat()

# (sheet_id, gid, label)
CONSOLIDATED = ("14AmlnPcaqDRbUjXpp0CYo9EGGzNsPCK5IhQFBgzDEwA", 563039445, "consolidated")
NON_VET      = ("1XMqa9kvGLR5iHIsvfG3e7evImNM2VEgRPudym1vmAl8", 1534528050, "non_vet")
VET          = ("1aU9EfcNVCR3UORpBzLfJY9gUydYXbFZj9tlKNx2FjFI", 1534528050, "vet")

_SUFFIXES = ["int'l", "international", "limited", "ltd.", "ltd", "company", "co.,", "co.", "co",
             "(hk)", "(h.k.)", "(reseller)", "trading", "pharmaceutical", "pharma"]


def _norm(s: str) -> str:
    """Normalize a name/brand for matching: lowercase, drop segment+suffix noise, alnum only."""
    s = (s or "").lower().strip()
    s = re.sub(r"\(vet\)|\(non[ -]?vet\)", "", s)
    for suf in _SUFFIXES:
        s = s.replace(suf, " ")
    s = re.sub(r"\(.*?\)", " ", s)        # drop any remaining parentheticals
    return re.sub(r"[^a-z0-9]", "", s)


def _segment_from_name(name: str):
    low = (name or "").lower()
    if re.search(r"non[ -]?vet", low):
        return "non_vet"
    if "(vet)" in low or re.search(r"\bvet\b", low):
        return "vet"
    return None


def _parens(name: str) -> list[str]:
    return [m.strip() for m in re.findall(r"\((.*?)\)", name or "") if m.strip()]


def _split_list(s: str) -> list[str]:
    parts = re.split(r"[,\n;/]+", s or "")
    return [re.sub(r"\(.*?\)", "", p).strip() for p in parts if p and p.strip()]


def _client(credentials_file: str | None):
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    sa_json = os.environ.get("GOOGLE_SA_KEY_JSON", "")
    path = credentials_file or os.environ.get("GOOGLE_SA_KEY_PATH", "")
    if sa_json:
        creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=scopes)
    elif path and os.path.exists(path):
        creds = Credentials.from_service_account_file(path, scopes=scopes)
    else:
        raise RuntimeError("No service-account credential (GOOGLE_SA_KEY_JSON / GOOGLE_SA_KEY_PATH / credentials_file).")
    return gspread.authorize(creds)


def _read(gc, sheet_id, gid):
    sh = gc.open_by_key(sheet_id)
    ws = next((w for w in sh.worksheets() if w.id == gid), sh.sheet1)
    return ws.get_all_values()


def _find_header_row(values, key="supplier"):
    for i, row in enumerate(values[:8]):
        if any(key in (c or "").lower() for c in row):
            return i
    return 0


def _col(headers, *keywords, exact=None):
    for i, h in enumerate(headers):
        hl = (h or "").lower().replace("\n", " ").strip()
        if exact and hl == exact:
            return i
        if not exact and all(k in hl for k in keywords):
            return i
    return None


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _truthy(v):
    return (v or "").strip().upper() in ("TRUE", "YES", "Y", "1")


def _parse_consolidated(values) -> list[dict]:
    """List of all consolidated supplier rows (the authoritative supplier list)."""
    hrow = _find_header_row(values, "supplier")
    headers = values[hrow]
    c_sup = _col(headers, "supplier")
    c_cut = _col(headers, "cut-off") or _col(headers, "cut off") or _col(headers, "cut")
    c_moq = _col(headers, "moq")
    c_cred = _col(headers, "credit")
    out = []
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for row in values[hrow + 1:]:
        name = _cell(row, c_sup)
        if not name or name.lower() == "supplier":
            continue
        order_cols = range((c_sup or 0) + 1, c_cut) if c_cut else range(0, 0)
        deliv_cols = range((c_cut or 0) + 1, c_moq) if (c_cut and c_moq) else range(0, 0)
        order_days = [DAYS[i] for i, col in enumerate(order_cols) if i < len(DAYS) and _truthy(_cell(row, col))]
        deliv_days = [DAYS[i] for i, col in enumerate(deliv_cols) if i < len(DAYS) and _truthy(_cell(row, col))]
        out.append({
            "name": name,
            "segment": _segment_from_name(name),
            "order_days": ",".join(order_days) or None,
            "delivery_days": ",".join(deliv_days) or None,
            "cut_off_time": _cell(row, c_cut) or None,
            "moq_value": _cell(row, c_moq) or None,
            "credit_term": _cell(row, c_cred) or None,
        })
    return out


def _parse_detailed(values, segment) -> list[dict]:
    hrow = _find_header_row(values, "supplier")
    headers = values[hrow]
    c_sup = _col(headers, "supplier name") or _col(headers, "supplier")
    cols = {
        "type_of_brand": _col(headers, "type of brand"),
        "brands": _col(headers, "brands"),
        "fmcg": _col(headers, "fmcg") if _col(headers, "non-fmcg") is None else None,
        "nonfmcg": _col(headers, "non-fmcg") or _col(headers, "non fmcg"),
        "monthly_rebate": _col(headers, "monthly rebate"),
        "bulk_buy_structure": _col(headers, "bulk"),
        "moq_value": _col(headers, "moq", "value") or _col(headers, "moq"),
        "moq_specific": _col(headers, "moq", "specific"),
        "credit_term": _col(headers, "credit"),
        "delivery_time": _col(headers, "time to deliver") or _col(headers, "deliver"),
        "key_contact": _col(headers, "key contact"),
        "contact_phone": _col(headers, "phone"),
        "contact_mobile": _col(headers, "mobile"),
        "contact_email": _col(headers, "email"),
        "delivery_charges": _col(headers, "delivery charge"),
        "warehouse_pickup": _col(headers, "warehouse"),
        "supply_agreement": _col(headers, "supply agreement"),
        "bank_details": _col(headers, "bank"),
        "other_details": _col(headers, "other"),
    }
    # plain FMCG col (non-vet has it; guard against grabbing 'non-fmcg')
    c_fmcg = None
    for i, h in enumerate(headers):
        hl = (h or "").lower().strip()
        if hl == "fmcg":
            c_fmcg = i
            break
    out = []
    for row in values[hrow + 1:]:
        name = _cell(row, c_sup)
        if not name or "supplier" in name.lower():
            continue
        rec = {"name": name, "segment": segment}
        for key, idx in cols.items():
            rec[key] = _cell(row, idx) or None
        rec["fmcg_list"] = _split_list(_cell(row, c_fmcg))
        rec["nonfmcg_list"] = _split_list(_cell(row, cols["nonfmcg"]))
        rec["brand_list"] = _split_list(rec.get("brands") or "")
        out.append(rec)
    return out


def _gen_code(name: str, used: set) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", (name or "X").upper())[:6] or "SUP"
    code = base
    n = 1
    while code in used:
        n += 1
        code = f"{base[:5]}{n}"
    used.add(code)
    return code


def run_import(db, dry_run: bool = True, credentials_file: str | None = None) -> dict:
    import models

    gc = _client(credentials_file)
    cons = _parse_consolidated(_read(gc, *CONSOLIDATED[:2]))
    detailed = _parse_detailed(_read(gc, *NON_VET[:2]), "non_vet") + _parse_detailed(_read(gc, *VET[:2]), "vet")

    # ── Spine = CONSOLIDATED (the authoritative supplier list — the 61 rows). The detailed
    #    sheets only ENRICH a matching consolidated supplier (brands, contacts, segment); a
    #    detailed name NOT in the consolidated is NOT created (it was dropped in consolidation).
    #    Vet/Non-Vet rows of the same company collapse to one record (segment 'both') so an
    #    existing SKU-linked supplier is never duplicated. ──
    OVERLAP = ("order_days", "delivery_days", "cut_off_time", "moq_value", "credit_term")
    detailed_by_nrm: dict[str, list] = {}
    for d in detailed:
        detailed_by_nrm.setdefault(_norm(d["name"]), []).append(d)

    merged: dict[str, dict] = {}
    segs: dict[str, set] = {}
    flagged = {"consolidated_unknown_segment": [], "collapsed_vet_nonvet": [],
               "detailed_not_in_consolidated": []}

    for c in cons:                               # cons = the list of 61 consolidated rows
        nrm = _norm(c["name"])
        segs.setdefault(nrm, set())
        if c["segment"] in ("vet", "non_vet"):
            segs[nrm].add(c["segment"])
        if nrm in merged:                        # 2nd consolidated row for same company (Vet/Non-Vet)
            flagged["collapsed_vet_nonvet"].append(c["name"])
            for f in OVERLAP:                    # gap-fill only; the first row's value stands
                if merged[nrm].get(f) is None and c.get(f) is not None:
                    merged[nrm][f] = c[f]
            continue
        rec = {"name": c["name"], "brand_list": [], "fmcg_list": [], "nonfmcg_list": []}
        for f in OVERLAP:
            rec[f] = c.get(f)                    # consolidated authoritative for these
        for d in detailed_by_nrm.get(nrm, []):   # enrich from detailed
            if d.get("segment") in ("vet", "non_vet"):
                segs[nrm].add(d["segment"])
            for k, v in d.items():
                if k in ("brand_list", "fmcg_list", "nonfmcg_list"):
                    rec[k] = list(dict.fromkeys(rec[k] + (v or [])))
                elif k == "segment":
                    continue
                elif k in OVERLAP:
                    if rec.get(k) is None and v not in (None, ""):
                        rec[k] = v               # gap-fill (consolidated still wins on conflict)
                elif v not in (None, "") and not rec.get(k):
                    rec[k] = v
        merged[nrm] = rec

    def _final_seg(nrm):
        s = segs.get(nrm, set())
        if "vet" in s and "non_vet" in s:
            return "both"
        return next(iter(s)) if s else "unknown"

    for nrm, rec in merged.items():
        rec["segment"] = _final_seg(nrm)
        if rec["segment"] == "unknown":
            flagged["consolidated_unknown_segment"].append(rec["name"])

    flagged["detailed_not_in_consolidated"] = sorted({
        d["name"] for nrm, ds in detailed_by_nrm.items() if nrm not in merged for d in ds})

    # ── Upsert — reuse an existing supplier by normalized name (ANY segment), preferring the
    #    SKU-linked record, so links are never stranded. Never duplicate an existing supplier. ──
    existing = db.query(models.Supplier).all()
    used_codes = {s.code for s in existing if s.code}
    linked_ids = {r[0] for r in db.query(models.ProductSupplier.supplier_id).distinct() if r[0]}
    by_norm: dict[str, list] = {}
    for s in existing:
        by_norm.setdefault(s.normalized_name or _norm(s.name), []).append(s)
    for k in by_norm:                            # SKU-linked existing record wins the match
        by_norm[k].sort(key=lambda s: (s.id not in linked_ids, s.id))

    report = {"dry_run": dry_run, "consolidated_rows": len(cons),
              "suppliers_in_merge": len(merged),
              "created": 0, "updated": 0, "aliases": 0, "brands": 0,
              "by_segment": {}, "flagged": flagged,
              "duplicate_of_existing_linked": 0, "samples": []}

    for nrm, rec in sorted(merged.items()):
        seg = rec["segment"]
        report["by_segment"][seg] = report["by_segment"].get(seg, 0) + 1
        cands = by_norm.get(nrm, [])
        sup = cands[0] if cands else None
        created = sup is None
        if created:
            # invariant check: a create must never collide with an existing SKU-linked supplier
            if any(s.id in linked_ids for s in cands):
                report["duplicate_of_existing_linked"] += 1
            sup = models.Supplier(code=_gen_code(rec["name"], used_codes), name=rec["name"],
                                  created_at=NOW())
            if not dry_run:
                db.add(sup)
        # field assignment
        fields = ("type_of_brand", "moq_value", "moq_specific", "credit_term", "monthly_rebate",
                  "bulk_buy_structure", "delivery_time", "delivery_charges", "warehouse_pickup",
                  "order_days", "delivery_days", "cut_off_time", "holidays", "key_contact",
                  "contact_phone", "contact_mobile", "contact_email", "bank_details",
                  "supply_agreement", "other_details")
        # ── projected aliases + brands (counted in BOTH dry-run and write) ──
        parens = _parens(rec["name"])
        alias_vals = {rec["name"], re.sub(r"\(.*?\)", "", rec["name"]).strip(), *parens, sup.code}
        alias_rows, seen_a = [], set()
        for a in alias_vals:
            na = (a or "").lower() if re.fullmatch(r"[A-Z0-9]{2,8}", a or "") else _norm(a)
            if not a or not na or na in seen_a:
                continue
            seen_a.add(na)
            alias_rows.append((a, na, "parenthetical" if a in parens else "name"))
        fmcg = {_norm(x) for x in rec.get("fmcg_list", [])}
        nonf = {_norm(x) for x in rec.get("nonfmcg_list", [])}
        brand_rows, seen_b = [], set()
        for b in rec.get("brand_list", []):
            nb = _norm(b)
            if not nb or nb in seen_b:
                continue
            seen_b.add(nb)
            brand_rows.append((b, nb, 1 if nb in fmcg else (0 if nb in nonf else None)))
        report["aliases"] += len(alias_rows)
        report["brands"] += len(brand_rows)

        if not dry_run:
            sup.name = rec["name"]
            sup.normalized_name = nrm
            sup.segment = seg
            for f in fields:
                if rec.get(f) is not None:
                    setattr(sup, f, rec[f])
            sup.is_active = 1
            sup.source = "sheet_import"
            sup.updated_at = NOW()
            sup.raw_json = json.dumps({k: v for k, v in rec.items() if not isinstance(v, list)}, default=str)[:4000]
            db.flush()
            db.query(models.SupplierAlias).filter(
                models.SupplierAlias.supplier_id == sup.id,
                models.SupplierAlias.source != "manual").delete()
            for a, na, src in alias_rows:
                db.add(models.SupplierAlias(supplier_id=sup.id, alias=a, normalized_alias=na,
                                            source=src, created_at=NOW()))
            db.query(models.SupplierBrand).filter(models.SupplierBrand.supplier_id == sup.id).delete()
            for b, nb, isf in brand_rows:
                db.add(models.SupplierBrand(supplier_id=sup.id, brand_name=b, normalized_brand=nb,
                                            is_fmcg=isf, created_at=NOW()))
        report["created" if created else "updated"] += 1
        if len(report["samples"]) < 12:
            report["samples"].append({"name": rec["name"], "segment": seg,
                                      "brands": len(rec.get("brand_list", [])),
                                      "moq": rec.get("moq_value"), "credit": rec.get("credit_term")})

    if not dry_run:
        db.commit()
    return report
