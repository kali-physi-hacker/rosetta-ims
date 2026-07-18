"""Deterministic pack / cost guard for catalogue parsing (ingestion-spec Phase 1; used by re-parse).

Pure functions, no I/O, no DB — so they are trivially testable and safe to run at ingest and re-parse.
The core rule: units_per_pack is the COST-BASIS count (sellable units the price covers). It must never be
a weight (grams), a volume (ml), or a coincidental order multiple. This module parses the printed
pack-size / name and applies that rule.
"""
from __future__ import annotations

import re

# ── Placeholder scrub (shared vocabulary with routers/products.py::_clean_str) ──
_PLACEHOLDERS = {"", "#n/a", "n/a", "na", "nan", "none", "null", "-", "—"}


def clean_str(v):
    """Trim + drop placeholder junk. None for empty/#N/A-style values, else the trimmed string."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in _PLACEHOLDERS else s


# ── Pack-size grammar ──────────────────────────────────────────────────────────
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kgs?|g|gm|gms|grams?|lbs?|pounds?|oz)\b", re.I)
_VOLUME = re.compile(r"(\d+(?:\.\d+)?)\s*(l|litres?|liters?|ml|mls)\b", re.I)
_COUNT = re.compile(
    r"(\d+)\s*(?:x\s*)?(tabs?|tablets?|caps?|capsules?|pcs?|pieces?|cans?|pouch(?:es)?|sachets?|"
    r"tests?|strips?|vials?|sticks?|wipes?|doses?|servings?)\b", re.I)
# uoms that are a per-sell count — a size-match on these is ambiguous (e.g. FortiFlora "1.06OZ" == 30
# sachets), so we do NOT auto-propose the fix; it's held for a human (REVIEW).
COUNT_UOMS = {"tablet", "tab", "capsule", "cap", "can", "pouch", "sachet", "piece", "pc", "pcs",
              "strip", "vial", "test", "dose"}


def _to_grams(val: float, unit: str):
    unit = unit.lower()
    if unit.startswith("kg"):
        return val * 1000
    if unit in ("g", "gm", "gms", "gram", "grams"):
        return val
    if unit.startswith("lb") or unit.startswith("pound"):
        return val * 453.592
    if unit == "oz":
        return val * 28.3495
    return None


def _to_ml(val: float, unit: str):
    unit = unit.lower()
    if unit in ("ml", "mls"):
        return val
    if unit.startswith("l"):
        return val * 1000
    return None


def parse_weight_grams(text: str):
    """First weight token in text, in grams (rounded), else None."""
    for m in _WEIGHT.finditer(text or ""):
        g = _to_grams(float(m.group(1)), m.group(2))
        if g is not None:
            return round(g)
    return None


def parse_volume_ml(text: str):
    for m in _VOLUME.finditer(text or ""):
        ml = _to_ml(float(m.group(1)), m.group(2))
        if ml is not None:
            return round(ml)
    return None


def parse_count(text: str):
    """First explicit sell-unit count ('100 tabs', '24 cans/ctn'), else None."""
    m = _COUNT.search(text or "")
    return int(m.group(1)) if m else None


def size_misread(name: str, pack_size: str, uom, current_upp):
    """Is `current_upp` actually the pack SIZE (weight g / volume ml) — a mis-read that should be 1?

    Returns (kind, token_value) e.g. ('weight', 4000) or ('volume', 5000), else None. Never fires when a
    real sell-unit count token equals upp (that's a legit basis, e.g. "100 tabs/bot" → 100).
    """
    if not current_upp or current_upp <= 1:
        return None
    blob = f"{name or ''} {pack_size or ''}"
    if parse_count(blob) == current_upp:      # a genuine sell-unit count → legit basis, not a misread
        return None
    if parse_weight_grams(blob) == current_upp:
        return ("weight", current_upp)
    if parse_volume_ml(blob) == current_upp:
        return ("volume", current_upp)
    return None


def size_misread_confidence(uom) -> str:
    """HIGH (safe to auto-fix to 1) unless the uom is itself a sell-count unit (then REVIEW)."""
    return "REVIEW" if (uom or "").strip().lower() in COUNT_UOMS else "HIGH"


def corrected_units_per_pack(name: str, pack_size: str, uom, current_upp):
    """The cost-basis units this row SHOULD have. If current_upp is a HIGH-confidence size mis-read → 1;
    else unchanged (a count-uom size-match is held for human review, not auto-proposed).
    Returns (new_upp, reason|None). reason is set only when a correction is proposed."""
    hit = size_misread(name, pack_size, uom, current_upp)
    if hit and size_misread_confidence(uom) == "HIGH":
        kind, val = hit
        return 1, f"units_per_pack {current_upp} was the pack {kind} ({val}{'g' if kind == 'weight' else 'ml'}) — mis-read; item sold as one unit"
    return current_upp, None
