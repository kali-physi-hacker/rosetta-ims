"""Resolve a *detected* supplier (name + brands read from a catalogue) to a master supplier.

Signals, against the supplier master + alias + brand indexes (built once per call):
  1. exact supplier CODE          -> 0.99
  2. exact normalized NAME/ALIAS  -> 0.95
  3. BRAND match (strong signal)  -> 0.85   (detected brand -> supplier_brands)
  4. fuzzy NAME (difflib)         -> ratio * 0.80   (only above `fuzzy_min`)

A supplier's score is the MAX of its signals, with a small synergy bump when NAME and
BRAND independently point at it (that's the high-confidence case). Returns the best
candidate + alternates, and flags `ambiguous` when two candidates are close (e.g. Royal
Canin Vet vs Non-Vet, or a brand carried by several suppliers) so the UI forces a manual
pick instead of guessing — which is exactly the failure we're fixing.
"""
from __future__ import annotations

import difflib

from services.supplier_import import _norm


def _index(db):
    import models
    sups = db.query(models.Supplier).filter(models.Supplier.is_active == 1).all()
    by_id = {s.id: s for s in sups}
    code_map = {}
    for s in sups:
        if s.code:
            code_map[s.code.upper()] = s.id
    alias_map: dict[str, set] = {}
    for a in db.query(models.SupplierAlias).all():
        alias_map.setdefault(a.normalized_alias, set()).add(a.supplier_id)
    # also index normalized names directly
    for s in sups:
        if s.normalized_name:
            alias_map.setdefault(s.normalized_name, set()).add(s.id)
    brand_map: dict[str, set] = {}
    for b in db.query(models.SupplierBrand).all():
        brand_map.setdefault(b.normalized_brand, set()).add(b.supplier_id)
    return by_id, code_map, alias_map, brand_map


def resolve(db, detected_name: str = None, detected_brands: list[str] = None,
            fuzzy_min: float = 0.72, ambiguity_gap: float = 0.08, top_n: int = 4) -> dict:
    by_id, code_map, alias_map, brand_map = _index(db)
    name = (detected_name or "").strip()
    nn = _norm(name)
    brands = [b for b in (detected_brands or []) if b and b.strip()]

    name_hits: dict[int, float] = {}   # supplier_id -> name-signal score
    brand_hits: dict[int, float] = {}  # supplier_id -> brand-signal score

    # 1. exact code (the detected text, or a token of it, equal to a code)
    for tok in {name.upper(), *[t.strip().upper() for t in name.replace("(", " ").replace(")", " ").split()]}:
        if tok in code_map:
            name_hits[code_map[tok]] = max(name_hits.get(code_map[tok], 0), 0.99)

    # 2. exact normalized name / alias
    if nn:
        for sid in alias_map.get(nn, set()):
            name_hits[sid] = max(name_hits.get(sid, 0), 0.95)

    # 3. brand match
    for b in brands:
        nb = _norm(b)
        for sid in brand_map.get(nb, set()):
            brand_hits[sid] = max(brand_hits.get(sid, 0), 0.85)

    # 4. fuzzy name (only if we don't already have a strong exact name hit)
    if nn and max(name_hits.values(), default=0) < 0.95:
        for sid, s in by_id.items():
            if not s.normalized_name:
                continue
            r = difflib.SequenceMatcher(None, nn, s.normalized_name).ratio()
            if r >= fuzzy_min:
                name_hits[sid] = max(name_hits.get(sid, 0), round(r * 0.80, 3))

    # combine: max of signals + synergy when name AND brand both point here
    scores: dict[int, float] = {}
    for sid in set(name_hits) | set(brand_hits):
        base = max(name_hits.get(sid, 0), brand_hits.get(sid, 0))
        if name_hits.get(sid, 0) >= 0.7 and brand_hits.get(sid, 0) >= 0.7:
            base = min(0.99, base + 0.10)   # name + brand agree -> confident
        scores[sid] = round(base, 3)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    def _meth(sid):
        m = []
        if name_hits.get(sid, 0) >= 0.95: m.append("name/code")
        elif name_hits.get(sid, 0): m.append("fuzzy")
        if brand_hits.get(sid, 0): m.append("brand")
        return "+".join(m) or "none"

    candidates = [{
        "supplier_id": sid, "name": by_id[sid].name, "code": by_id[sid].code,
        "segment": by_id[sid].segment, "confidence": sc, "method": _meth(sid),
    } for sid, sc in ranked[:top_n]]

    best = candidates[0] if candidates else None
    ambiguous = bool(
        best and (
            len(ranked) > 1 and ranked[0][1] - ranked[1][1] < ambiguity_gap
            or best["confidence"] < 0.70
        )
    )
    return {
        "detected_name": name or None,
        "detected_brands": brands,
        "resolved": None if (not best or ambiguous) else best,
        "best_guess": best,          # shown even when ambiguous, as a pre-select hint
        "ambiguous": ambiguous,
        "candidates": candidates,
    }
