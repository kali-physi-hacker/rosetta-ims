"""Name similarity between a supplier's parsed product name and our catalogue.

The pipeline matches a supplier row to a canonical product by exact
``supplier_sku`` or ``barcode``, scoped to the supplier. That is correct but
narrow: the first time a supplier sends us a product we already stock, there is
no supplier identity to match through, so the row lands on PROPOSED_CREATE even
though the product has been in the catalogue for years.

This module is the second opinion. It is used in two places:

  * ranking the reviewer's match suggestions, so the real answer surfaces
  * the duplicate radar, which must fire *before* anyone creates a product

Scoring is IDF-weighted token coverage rather than a plain string ratio. In a
pet-food catalogue, "wet", "dog" and "food" appear in thousands of names while
"biome" or "onc" appear in a handful — a scorer that treats them alike will rank
"Hill's Prescription Diet - Wet Dog Food - anything" above the one product that
actually shares the rare words. Weighting by inverse document frequency puts the
distinguishing words in charge.
"""

from __future__ import annotations

import math
import re
import threading
from typing import Any, Iterable

from sqlalchemy.orm import Session

import models

# Words that carry no distinguishing power in this catalogue. Kept small on
# purpose — IDF already discounts common words; this list only removes noise
# that would otherwise inflate the token count and dilute coverage.
_STOP = {
    "the", "and", "for", "with", "per", "pack", "packs", "size", "sizes",
    "new", "pcs", "pc", "ea", "each", "box", "case", "ctn", "carton",
}

_SPLIT = re.compile(r"[^a-z0-9]+")
# Prescription-diet codes — i/d, k/d, c/d, z/d — are the whole distinguishing
# signal on those products, and both halves are single characters that the
# tokenizer would otherwise drop. Fuse them before splitting so "i/d" survives
# as one token, in the supplier's string and in ours alike.
_DIET_CODE = re.compile(r"\b([a-z])\s*/\s*([a-z])\b")


def normalize(text: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    lowered = _DIET_CODE.sub(r"\1\2", (text or "").lower())
    return " ".join(_SPLIT.split(lowered)).strip()


def tokens(text: str | None) -> list[str]:
    """Distinguishing tokens, order preserved, duplicates dropped.

    Single characters go — they match half the catalogue. Diet codes survive
    that rule because normalize() fuses them first: "i/d Adult 1+" tokenizes to
    ["id", "adult"], not ["i", "d", "adult"].
    """
    out: list[str] = []
    seen: set[str] = set()
    for tok in normalize(text).split():
        if len(tok) < 2 or tok in _STOP or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


class _IdfIndex:
    """Token -> inverse document frequency over every variant name we hold.

    Built once per process and rebuilt when the variant count moves, which is
    cheap insurance against a long-lived worker scoring against a stale vocabulary
    after an ingestion run creates products.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idf: dict[str, float] = {}
        self._default = 1.0
        self._built_at_count = -1

    def _build(self, db: Session) -> None:
        rows = db.query(models.ProductVariant.name, models.ProductVariant.brand).all()
        total = max(1, len(rows))
        df: dict[str, int] = {}
        for name, brand in rows:
            for tok in set(tokens(name)) | set(tokens(brand)):
                df[tok] = df.get(tok, 0) + 1
        self._idf = {tok: math.log(total / (1 + n)) + 1.0 for tok, n in df.items()}
        # An unseen token is maximally distinguishing — it appears nowhere else.
        self._default = math.log(total) + 1.0
        self._built_at_count = total

    def weights(self, db: Session) -> tuple[dict[str, float], float]:
        count = db.query(models.ProductVariant.id).count()
        if self._built_at_count != count:
            with self._lock:
                if self._built_at_count != count:
                    self._build(db)
        return self._idf, self._default


_INDEX = _IdfIndex()


def reset_index() -> None:
    """Drop the cached vocabulary. Tests that create variants call this."""
    _INDEX._built_at_count = -1


def score_tokens(
    query_tokens: Iterable[str],
    candidate_tokens: Iterable[str],
    idf: dict[str, float],
    default: float,
) -> float:
    """How much of the query's information the candidate accounts for, 0..1.

    Coverage of the *query* leads, because our canonical names are long by
    convention ("Hill's Prescription Diet - Wet Cat Food - GI Biome Chicken")
    and a supplier's short name should still score highly against one. A modest
    reverse term stops a 40-word name matching everything.
    """
    q = list(dict.fromkeys(query_tokens))
    c = set(candidate_tokens)
    if not q or not c:
        return 0.0
    w = lambda t: idf.get(t, default)  # noqa: E731
    shared = [t for t in q if t in c]
    if not shared:
        return 0.0
    q_mass = sum(w(t) for t in q)
    c_mass = sum(w(t) for t in c)
    shared_mass = sum(w(t) for t in shared)
    cover_q = shared_mass / q_mass if q_mass else 0.0
    cover_c = shared_mass / c_mass if c_mass else 0.0
    return round(0.75 * cover_q + 0.25 * cover_c, 4)


def _candidate_pool(db: Session, toks: list[str], cap: int) -> list[models.ProductVariant]:
    """Variants sharing at least one query token.

    An OR over LIKE terms is crude but it is index-free work the database does
    well, and it keeps the Python scorer off the full 11k-row table on every
    keystroke. The cap bounds the worst case (a query whose tokens are all
    common); the scorer then sorts what survives.
    """
    from sqlalchemy import or_, func

    if not toks:
        return []
    clauses = []
    for tok in toks[:8]:
        like = f"%{tok}%"
        clauses.append(func.lower(models.ProductVariant.name).like(like))
    return (
        db.query(models.ProductVariant)
        .filter(or_(*clauses))
        .limit(cap)
        .all()
    )


def rank_variants(
    db: Session,
    query: str,
    *,
    limit: int = 8,
    pool_cap: int = 900,
    min_score: float = 0.0,
    exclude_skus: set[str] | None = None,
) -> list[tuple[models.ProductVariant, float]]:
    """Best-matching variants for a free-text product name, highest first."""
    toks = tokens(query)
    if not toks:
        return []
    idf, default = _INDEX.weights(db)
    scored: list[tuple[models.ProductVariant, float]] = []
    for variant in _candidate_pool(db, toks, pool_cap):
        if exclude_skus and variant.sku_code in exclude_skus:
            continue
        name_tokens = tokens(variant.name) + tokens(variant.brand)
        s = score_tokens(toks, name_tokens, idf, default)
        if s >= min_score:
            scored.append((variant, s))
    # Exact SKU typed into the box beats any name score.
    needle = normalize(query)
    scored.sort(key=lambda pair: (normalize(pair[0].sku_code) == needle, pair[1]), reverse=True)
    return scored[:limit]


# ── duplicate radar ───────────────────────────────────────────────────────────
# Rungs, hardest first. A barcode is a fact about a physical object, so a
# collision is a hard block with no override; an identical normalized name is
# treated the same way. Similarity above the threshold needs a typed reason.

# Set from the real queue, not a guess: across the four known duplicate pairs in
# the pending runs the correct product scores 0.72-0.90, while unrelated
# products in the same brand sit at 0.36-0.40. 0.62 catches every true pair with
# margin on both sides.
DUPLICATE_REASON_THRESHOLD = 0.62


def duplicate_check(
    db: Session,
    *,
    name: str | None,
    barcode: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """What stands between this draft and a new SKU."""
    blockers: list[dict[str, Any]] = []

    if barcode and barcode.strip():
        owner = (
            db.query(models.ProductVariant)
            .join(models.ProductSupplier, models.ProductSupplier.product_id == models.ProductVariant.id)
            .filter(models.ProductSupplier.barcode == barcode.strip())
            .first()
        )
        if owner is None:
            offering = (
                db.query(models.SupplierOffering)
                .filter(models.SupplierOffering.barcode == barcode.strip())
                .filter(models.SupplierOffering.product_variant_id.isnot(None))
                .first()
            )
            if offering is not None:
                owner = db.get(models.ProductVariant, offering.product_variant_id)
        if owner is not None:
            blockers.append({
                "kind": "barcode",
                "sku_code": owner.sku_code,
                "name": owner.name,
                "detail": f"Barcode {barcode.strip()} already belongs to this product.",
            })

    normalized = normalize(name)
    if normalized:
        for variant in db.query(models.ProductVariant).filter(
            models.ProductVariant.name.isnot(None)
        ).all():
            if normalize(variant.name) == normalized:
                blockers.append({
                    "kind": "name",
                    "sku_code": variant.sku_code,
                    "name": variant.name,
                    "detail": "A product with this exact name already exists.",
                })
                break

    similar = [
        {
            "sku_code": v.sku_code,
            "name": v.name,
            "brand": v.brand,
            "category": v.category,
            "status": v.status,
            "score": s,
            # Some historical rows carry a whole product name in the SKU column.
            # They are still the right thing to match to, but the reviewer
            # should see that they are inheriting a broken code.
            "sku_valid": bool(re.fullmatch(r"\d{6,}", (v.sku_code or "").strip())),
        }
        for v, s in rank_variants(db, name or "", limit=limit, min_score=0.2)
    ]
    top = similar[0]["score"] if similar else 0.0
    return {
        "blockers": blockers,
        "similar": similar,
        "top_score": top,
        "reason_required": not blockers and top >= DUPLICATE_REASON_THRESHOLD,
        "threshold": DUPLICATE_REASON_THRESHOLD,
    }
