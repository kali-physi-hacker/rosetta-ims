"""Catalogue data contracts — a per-supplier schema for the parser.

A contract (`backend/catalogue_contracts/<slug>.yaml`) states, for ONE supplier, which source column is
cost vs RRP, the pricing basis, and where the order multiple / species / segment / category live — instead
of the generic prompt re-guessing on every import. Extraction stays model-assisted but contract-GUIDED
(`prompt_section`); this module then deterministically ENFORCES the contract's invariants and VALIDATES
each row (`apply`).

Pure + deterministic: reads the contract file, no model calls, no DB. Additive + opt-in — a supplier with
no contract → `load_contract` returns None → the caller runs today's generic path unchanged.

Design: PRD/architecture in `_bmad-output/planning-artifacts/*-catalogue-contracts.md`.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import yaml

_CONTRACT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "catalogue_contracts")

# Canonical catalogue_item / ordering fields a contract may bind. Binding anything else is a typo → fail loud.
_CANONICAL = {
    "supplier_sku", "description", "pack_size", "brand", "barcode", "variant", "uom",
    "cost_price", "rrp", "units_per_pack", "order_increment_qty", "order_increment_uom",
    "species", "segment", "category", "min_purchase_qty", "min_sellable_qty", "weight_grams",
}
_BINDABLE = _CANONICAL | {"bulk_tiers"}                 # keys allowed under columns:/ordering:
_VALIDATE_RE = re.compile(r"^\s*([a-z_]+)\s*(<=|>=|==|<|>)\s*([a-z_]+|-?\d+(?:\.\d+)?)\s*$")


def _as_col(spec) -> Optional[str]:
    """A field spec resolves to a SOURCE COLUMN name (for the guided prompt) — accepts a bare string, or
    {column|from: X}. Returns None for a const/parse/join/none spec (handled elsewhere)."""
    if isinstance(spec, str):
        return None if spec.strip().lower() == "none" else spec
    if isinstance(spec, dict):
        return spec.get("column") or spec.get("from")
    return None


def _const_of(spec):
    return spec.get("const") if isinstance(spec, dict) and "const" in spec else None


def _parse_col(spec) -> Optional[str]:
    return spec.get("parse") if isinstance(spec, dict) and "parse" in spec else None


def _leading_int(text) -> Optional[int]:
    """First integer in a packing string: '10 pcs/box' → 10, '500pcs/bag' → 500, '1 pc' → 1."""
    if text is None:
        return None
    m = re.search(r"\d+", str(text))
    return int(m.group()) if m else None


_UNIT_G = {"kg": 1000.0, "g": 1.0, "lb": 453.592, "oz": 28.3495}


def _grams_from_size(text) -> Optional[float]:
    """Per-unit net weight in grams from a Size string. Drops a leading 'N/' case-pack prefix so the
    SELL-UNIT weight is read ('24/2.9 oz' → 2.9 oz, not 24), and converts oz/lb/kg/g. Returns None when no
    weight unit is present ('24' alone, or blank) — so a good weight is never nulled by an unparseable size."""
    if not text:
        return None
    s = str(text).strip().lower().rsplit("/", 1)[-1]      # keep the unit-weight part, not the case count
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|lb|oz|g)\b", s)
    if not m:
        return None
    return float(round(float(m.group(1)) * _UNIT_G[m.group(2)]))   # whole grams (weight is stored/applied as int)


class Contract:
    """A parsed, validated supplier contract."""

    def __init__(self, raw: dict, slug: str):
        self.slug = slug
        self.supplier = raw.get("supplier")
        self.supplier_id = raw.get("supplier_id")
        self.version = raw.get("version", 1)
        self.fmt = raw.get("format")
        self.document = raw.get("document", {}) or {}
        self.columns = raw.get("columns", {}) or {}
        self.pricing = raw.get("pricing", {}) or {}
        self.ordering = raw.get("ordering", {}) or {}
        self.weight = raw.get("weight", {}) or {}
        self.normalize = raw.get("normalize", {}) or {}
        self.validate_rules = raw.get("validate", []) or []
        self._check()

    # ── load-time integrity (fail loud, never mis-map silently) ──────────────
    def _check(self):
        if not self.supplier_id:
            raise ValueError(f"contract '{self.slug}': missing supplier_id")
        strayed = {f for f in (set(self.columns) | set(self.ordering)) if f not in _BINDABLE}
        if strayed:
            raise ValueError(f"contract '{self.slug}': binds unknown field(s) {sorted(strayed)}")
        for rule in self.validate_rules:
            head = re.split(r"\s+or\s+", str(rule), flags=re.I)[0]
            if not _VALIDATE_RE.match(head):
                raise ValueError(f"contract '{self.slug}': unparseable validate rule {rule!r}")

    def _cost_col(self) -> Optional[str]:
        return _as_col(self.pricing.get("basic_cost"))

    def _rrp_col(self) -> Optional[str]:
        return _as_col(self.pricing.get("rrp"))

    def expected_columns(self) -> set:
        """Columns the catalogue is expected to have — for drift detection (DC-4)."""
        cols = set()
        for spec in list(self.columns.values()) + [self.pricing.get("basic_cost"), self.pricing.get("rrp")]:
            c = _as_col(spec)
            if c:
                cols.add(c)
            if isinstance(spec, dict) and spec.get("join"):
                cols.update(spec["join"])
        return cols

    # ── (1) guided prompt ────────────────────────────────────────────────────
    def prompt_section(self) -> str:
        """A supplier-specific instruction block appended to EXTRACTION_PROMPT so the model extracts the
        NAMED columns for this supplier (not a guess). The deterministic `apply` still has final say on the
        invariants below, so a model slip can't override them."""
        L = [f"\n=== SUPPLIER CONTRACT — {self.supplier} (do EXACTLY this) ==="]
        cost, rrp = self._cost_col(), self._rrp_col()
        basis = self.pricing.get("basis", "per_unit")
        if cost:
            L.append(f'- cost_price = the "{cost}" column — OUR wholesale/buying cost (strip any currency symbol). Prices are {basis}.')
        if rrp:
            L.append(f'- rrp = the "{rrp}" column — the retail / suggested selling price.')
            if cost:
                L.append('- cost_price (wholesale) is the LOWER of the two prices; rrp (retail) is the HIGHER — do NOT swap them.')
        else:
            L.append("- rrp = null (this catalogue has no RRP column).")
        upp = self.pricing.get("units_per_pack")
        if _const_of(upp) is not None:
            L.append(f"- units_per_pack = {_const_of(upp)} ALWAYS (the price is per sellable unit — never divide it).")
        oiq = self.ordering.get("order_increment_qty")
        if _parse_col(oiq):
            L.append(f'- order_increment_qty = the number of pieces stated in "{_parse_col(oiq)}" '
                     f'(e.g. "10 pcs/box" → 10). This is the ORDER MULTIPLE, NOT units_per_pack.')
        elif _as_col(oiq):
            L.append(f'- order_increment_qty = the "{_as_col(oiq)}" column — the order multiple / carton, '
                     f"NOT the pack size, and it must never be put into units_per_pack.")
        for f in ("supplier_sku", "brand", "pack_size", "barcode"):
            spec = self.columns.get(f)
            if _as_col(spec):
                L.append(f'- {f} = the "{_as_col(spec)}" column.')
            elif _const_of(spec) is not None:
                L.append(f'- {f} = "{_const_of(spec)}" (constant for this supplier).')
        desc = self.columns.get("description")
        if isinstance(desc, dict) and desc.get("join"):
            L.append("- description = join of: " + ", ".join(f'"{c}"' for c in desc["join"]))
        elif _as_col(desc):
            L.append(f'- description = the "{_as_col(desc)}" column.')
        L += self._species_prompt() + self._const_facts_prompt()
        L.append("Never swap cost and RRP. Never put an order multiple / carton size into units_per_pack.")
        return "\n".join(L)

    def _species_prompt(self) -> list:
        sp = self.document.get("species")
        if not isinstance(sp, dict):
            return []
        if sp.get("from") == "section_header" and sp.get("map"):
            m = ", ".join(f"{k}→{v}" for k, v in sp["map"].items())
            return [f"- species = from the page/section banner ({m})."]
        if sp.get("from") in ("product_name", "name"):
            return [f'- species = from the product name (e.g. "(Canine)"→dog, "(Feline)"→cat); '
                    f'else "{sp.get("default", "both")}".']
        return []

    def _const_facts_prompt(self) -> list:
        out = []
        for key in ("segment", "category"):
            spec = self.document.get(key)
            if _const_of(spec) is not None:
                out.append(f'- {key} = "{_const_of(spec)}" (constant for this supplier).')
            elif isinstance(spec, dict) and spec.get("map"):
                m = ", ".join(f'"{k}"→{v}' for k, v in spec["map"].items())
                out.append(f"- {key} = {m}.")
            elif isinstance(spec, dict) and spec.get("from") == "section_header":
                out.append(f'- {key} = from the therapeutic-class/section header (default "{spec.get("default","")}").')
        return out

    # ── (2) enforce invariants + (3) validate ────────────────────────────────
    def apply(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """Enforce the contract's deterministic invariants on the (guided) model output, then validate.
        Returns (items, flags) where flags = [{index, sku, rule, detail}] for rows that fail validation —
        the caller marks those needs_review, never drops them."""
        flags = []
        for i, it in enumerate(items):
            self._enforce(it)
            for rule in self.validate_rules:
                ok, detail = _eval_rule(rule, it)
                if not ok:
                    flags.append({"index": i, "sku": it.get("supplier_sku"), "rule": rule, "detail": detail})
        return items, flags

    def _enforce(self, it: dict):
        # const columns (e.g. brand on a single-brand catalogue) — authoritative for this supplier
        for f, spec in self.columns.items():
            if _const_of(spec) is not None:
                it[f] = _const_of(spec)
        # document facts: a const wins; else fill a declared default only where the model left it blank
        for f in ("species", "segment", "category"):
            spec = self.document.get(f)
            if _const_of(spec) is not None:
                it[f] = _const_of(spec)
            elif isinstance(spec, dict) and spec.get("default") is not None and it.get(f) in (None, ""):
                it[f] = spec["default"]
        # units_per_pack invariant (per-unit supplier ⇒ 1; a per-unit price is never divided)
        if _const_of(self.pricing.get("units_per_pack")) is not None:
            it["units_per_pack"] = _const_of(self.pricing.get("units_per_pack"))
        # wholesale-always-below-RRP supplier: auto-correct a swapped extraction (the model occasionally
        # flips the two price columns on a dense page). Deterministic, opt-in via pricing.autoswap_cost_rrp.
        if self.pricing.get("autoswap_cost_rrp"):
            c, r = _num(it.get("cost_price")), _num(it.get("rrp"))
            if c is not None and r is not None and r > 0 and c > r:
                it["cost_price"], it["rrp"] = r, c
        # a contract that declares no RRP column must not carry a (spurious) rrp — null it so nothing
        # downstream proposes/keeps a wrong retail price for this supplier
        _rrp = self.pricing.get("rrp")
        if _rrp is not None and _as_col(_rrp) is None and _const_of(_rrp) is None:
            it["rrp"] = None
        # order multiple: parse from the packing string when the contract says so (Alfamedic)
        if _parse_col(self.ordering.get("order_increment_qty")):
            n = _leading_int(it.get("pack_size") or it.get("units_per_pack"))
            if n:
                it["order_increment_qty"] = n
        # net weight (grams): parse the per-unit weight from the Size string when the contract says so
        # (Hill's — the Size column is the sell-unit weight). Only sets when parseable; never nulls.
        wsrc = self.weight.get("parse_from")
        if wsrc:
            g = _grams_from_size(it.get(wsrc))
            if g is not None:
                it["weight_grams"] = g
        # "By Quote" price text → null cost (a manual quote is needed; not a validation error)
        if self.normalize.get("by_quote") is not None and isinstance(it.get("cost_price"), str) \
                and "quote" in it["cost_price"].lower():
            it["cost_price"] = None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _eval_rule(rule: str, it: dict) -> tuple[bool, str]:
    """Tiny allow-listed comparator (no eval): 'field <op> field|number'. A missing operand (e.g. a
    By-Quote null cost, or a row with no RRP) SKIPS the rule rather than failing it."""
    m = _VALIDATE_RE.match(re.split(r"\s+or\s+", str(rule), flags=re.I)[0])
    if not m:
        return True, ""
    left, op, right = m.groups()
    lv = _num(it.get(left))
    if lv is None:
        return True, ""                              # left operand absent (incl. By-Quote null cost) → skip
    is_field = right in _CANONICAL
    if is_field and it.get(right) is None:
        return True, ""                              # comparison field absent → skip
    rv = _num(it.get(right)) if is_field else _num(right)
    if rv is None:
        return True, ""
    ok = {"<": lv < rv, "<=": lv <= rv, ">": lv > rv, ">=": lv >= rv, "==": lv == rv}[op]
    shown = f"{right}({it.get(right)})" if is_field else right
    return ok, ("" if ok else f"{left}({it.get(left)}) not {op} {shown} — check the columns")


# ── loader (cache-on-first-use) ──────────────────────────────────────────────
_CACHE: dict[int, Optional[Contract]] = {}
_LOADED = False


def _load_all():
    global _LOADED
    _CACHE.clear()
    if os.path.isdir(_CONTRACT_DIR):
        for name in os.listdir(_CONTRACT_DIR):
            if not name.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(_CONTRACT_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if not isinstance(raw, dict) or "supplier_id" not in raw:
                continue
            c = Contract(raw, slug=os.path.splitext(name)[0])
            _CACHE[int(c.supplier_id)] = c
    _LOADED = True


def load_contract(supplier_id: Optional[int]) -> Optional[Contract]:
    """The contract for this supplier, or None (→ caller uses the generic path). Contracts with load
    errors raise here, deliberately, so a broken contract can't silently mis-map."""
    if supplier_id is None:
        return None
    if not _LOADED:
        _load_all()
    return _CACHE.get(int(supplier_id))


def reload_contracts():
    """Test/ops hook — re-read the contract directory."""
    global _LOADED
    _LOADED = False
    _load_all()
