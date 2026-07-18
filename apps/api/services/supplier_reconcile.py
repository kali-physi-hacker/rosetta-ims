"""Reconcile legacy (pre-import) suppliers into the consolidated master.

After the master import, prod carries the consolidated suppliers (segment-tagged) PLUS legacy
rows from the original product-data seed — mostly near-duplicates ("Asia Vet Medical Limited"
vs master "Asia Vet Medical Limited (AVM)") or compound junk ("Alfamedic / Maxipro").

Each legacy supplier is matched to a master one (exact alias/normalized-name, else fuzzy;
compound "A / B" names use the first part). Its product_supplier (SKU) links are reassigned to
the master, and the emptied legacy row is DEACTIVATED (is_active=0, kept for audit). SKU links
are preserved — reassigned, and if the product already links to the master, the duplicate legacy
link is dropped. dry_run reports the plan without moving anything.
"""
from __future__ import annotations

import difflib
import re

from services.supplier_import import _norm


def reconcile_legacy(db, dry_run: bool = True, fuzzy_min: float = 0.82) -> dict:
    import models
    from sqlalchemy import func

    master = db.query(models.Supplier).filter(
        models.Supplier.segment.isnot(None), models.Supplier.is_active == 1).all()
    master_ids = {m.id for m in master}
    mby_id = {m.id: m for m in master}
    legacy = db.query(models.Supplier).filter(
        models.Supplier.segment.is_(None), models.Supplier.is_active == 1).all()
    link_counts = dict(
        db.query(models.ProductSupplier.supplier_id, func.count())
        .group_by(models.ProductSupplier.supplier_id).all())

    # master match index: alias + normalized name
    malias: dict[str, int] = {}
    for a in db.query(models.SupplierAlias).all():
        if a.supplier_id in master_ids:
            malias.setdefault(a.normalized_alias, a.supplier_id)
    for m in master:
        malias.setdefault(m.normalized_name or _norm(m.name), m.id)

    def match(name: str):
        primary = re.split(r"\s*/\s*|\s*&\s*", name)[0].strip()  # compound -> first part
        nn = _norm(primary)
        if nn and nn in malias:
            return malias[nn], 0.95
        best, bestr = None, 0.0
        for m in master:
            r = difflib.SequenceMatcher(None, nn, m.normalized_name or _norm(m.name)).ratio()
            if r > bestr:
                bestr, best = r, m
        return (best.id, round(bestr, 2)) if (best and bestr >= fuzzy_min) else (None, round(bestr, 2))

    merges, kept = [], []
    for L in legacy:
        mid, conf = match(L.name)
        nlinks = link_counts.get(L.id, 0)
        if mid:
            merges.append({"legacy_id": L.id, "legacy": L.name, "legacy_code": L.code,
                           "links": nlinks, "master_id": mid, "master": mby_id[mid].name, "conf": conf})
        else:
            kept.append({"legacy_id": L.id, "legacy": L.name, "links": nlinks, "best_conf": conf})

    reassigned = dropped = deactivated = 0
    if not dry_run:
        for m in merges:
            pss = db.query(models.ProductSupplier).filter(
                models.ProductSupplier.supplier_id == m["legacy_id"]).all()
            for ps in pss:
                clash = db.query(models.ProductSupplier).filter(
                    models.ProductSupplier.product_id == ps.product_id,
                    models.ProductSupplier.supplier_id == m["master_id"]).first()
                if clash:
                    db.delete(ps)
                    dropped += 1
                else:
                    ps.supplier_id = m["master_id"]
                    reassigned += 1
            leg = db.query(models.Supplier).filter(models.Supplier.id == m["legacy_id"]).first()
            leg.is_active = 0
            deactivated += 1
        db.commit()

    return {"dry_run": dry_run,
            "legacy_total": len(legacy),
            "merge_count": len(merges),
            "keep_count": len(kept),
            "total_legacy_links": sum(link_counts.get(L.id, 0) for L in legacy),
            "links_reassigned": reassigned,
            "links_dropped_as_dup": dropped,
            "deactivated": deactivated,
            "merges": sorted(merges, key=lambda x: -x["links"]),
            "kept": sorted(kept, key=lambda x: -x["links"])}
