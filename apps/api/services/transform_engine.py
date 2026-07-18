"""Configuration-driven transformation engine — Phase A (behaviour-neutral foundation).

Moves the hard-coded margin / cost / WOC transformations out of `pricing_service.py` and into
DB-backed configuration. Phase A introduces the engine + registry + seed and re-routes the
`pricing_service` call-sites onto it; the **seeded config reproduces the previous formulas
exactly** (proven by `tests/test_transform_engine.py`). Editing the config from a UI arrives in
Phases B/C — see `_bmad-output/planning-artifacts/architecture-config-transformation-engine.md`.

Design guarantees:
  • **Behaviour-neutral** — the DB is seeded with `_DEFAULTS`, and `_DEFAULTS` is also the
    in-process fallback when no active config exists (or the DB is unreachable). Either way the
    numbers match the pre-config code.
  • **Sandboxed** — formulas evaluate under an AST allow-list: arithmetic, comparisons, boolean
    ops, the ternary form, and the functions round/abs/min/max, over the transformation's
    declared input variables only. No attribute access, no other calls, no builtins. This is a
    hard security boundary; see `_validate()`.
  • **Never raises in the hot path** — `evaluate()` returns None on any error, matching the
    defensive guards the hard-coded formulas already had (a bad/edge input yields "n/a").
"""
import ast
import json
from datetime import datetime

from database import SessionLocal
from models import Transformation, ConfigVersion, TransformationValue

# ── Safe expression sandbox ─────────────────────────────────────────────────────────────
_ALLOWED_FUNCS = {"round": round, "abs": abs, "min": min, "max": max}
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.And, ast.Or,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.Is, ast.IsNot,
)


def _validate(tree: ast.AST, allowed_names: set) -> None:
    """Raise ValueError unless every node is on the allow-list and every referenced name is a
    declared input variable or an allowed function. This is the security boundary — anything not
    explicitly permitted (attribute access, subscripts, lambdas, comprehensions, other calls,
    dunders) is rejected before the expression is ever compiled."""
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise ValueError("only round/abs/min/max may be called")
            if node.keywords:
                raise ValueError("keyword arguments are not allowed")
        if isinstance(node, ast.Name) and node.id not in allowed_names and node.id not in _ALLOWED_FUNCS:
            raise ValueError(f"unknown name: {node.id}")


def compile_formula(expr: str, inputs) -> object:
    """Validate `expr` (names limited to `inputs` + the allowed functions) and compile it."""
    tree = ast.parse(expr, mode="eval")
    _validate(tree, set(inputs) | set(_ALLOWED_FUNCS))
    return compile(tree, "<formula>", "eval")


def _run(code, inputs: dict):
    # No builtins in globals; only the allow-listed funcs + the bound inputs are visible.
    return eval(code, {"__builtins__": {}}, {**_ALLOWED_FUNCS, **inputs})


def eval_formula(expr: str, inputs: dict):
    """Validate + compile + evaluate `expr` with `inputs` bound. Raises on a bad expression —
    used by config validation (Phase B/C) and tests. The runtime path uses `evaluate()`."""
    return _run(compile_formula(expr, inputs.keys()), inputs)


# ── Default configuration = the exact formulas/values hard-coded before Phase A ───────────
# Seeded verbatim into the DB, and used as the in-process fallback → behaviour-neutral.
_DEFAULTS = [
    dict(key="unit_cost", name="Unit cost (per sell-unit)", category="cost", output_field="unit_cost",
         inputs=["basic_cost", "units_per_pack"], kind="formula",
         formula="None if basic_cost is None else (basic_cost / units_per_pack if (units_per_pack or 0) > 1 else basic_cost)",
         description="Per-sell-unit supplier cost: whole-pack basic_cost divided by pack size when pack > 1."),
    dict(key="gross_gp", name="Gross GP%", category="margin", output_field="gp_pct",
         inputs=["price", "cost"], kind="formula",
         formula="None if (not price or price <= 0 or cost is None or cost <= 0) else round((price - cost) / price, 4)",
         description="Gross margin as a fraction of selling price."),
    dict(key="net_margin", name="Net margin% (after channel charges)", category="margin", output_field="margin",
         inputs=["price", "cost", "fee_pct", "delivery"], kind="formula",
         formula="None if (not price or price <= 0 or cost is None or cost <= 0) else round((price - cost - (fee_pct or 0) * price - delivery) / price, 4)",
         description="Margin after channel fee (fee_pct*price) and delivery cost, as a fraction of price."),
    dict(key="woc", name="Weeks of cover", category="inventory", output_field="woc",
         inputs=["total_qty", "weekly_demand"], kind="formula",
         formula="round(total_qty / weekly_demand, 1) if weekly_demand > 0 else None",
         description="Total stock divided by weekly demand (None when demand is 0)."),
    dict(key="sales_120d", name="120-day sales", category="inventory", output_field="sales_120d",
         inputs=["weekly_demand"], kind="formula",
         formula="round(weekly_demand * 120 / 7) if weekly_demand > 0 else 0",
         description="Weekly demand annualised over 120 days."),
    dict(key="mbb_buy_x_get_y", name="MBB buy-x-get-y unit cost", category="cost", output_field="mbb_unit_cost",
         inputs=["base", "min_qty", "free_qty"], kind="formula",
         formula="round(base * min_qty / (min_qty + free_qty), 4) if (base and min_qty and free_qty) else None",
         description="Effective per-sell-unit cost of a buy-x-get-y bulk term."),
    dict(key="mbb_spend_discount", name="MBB spend-discount unit cost", category="cost", output_field="mbb_unit_cost",
         inputs=["base", "discount_pct"], kind="formula",
         formula="round(base * (1 - discount_pct), 4) if (base and discount_pct) else None",
         description="Effective per-sell-unit cost of a spend-discount bulk term."),
    dict(key="sf_logistics", name="Shopify logistics (SF Express)", category="margin", output_field="delivery_cost",
         inputs=["weight_g"], kind="table",
         table={"tiers": [[500, 20.0], [1000, 30.0], [1500, 37.0], [2000, 44.0], [2500, 51.0], [5000, 58.0],
                          [10000, 68.0], [15000, 78.0], [20000, 88.0], [21000, 294.0], [22000, 307.0],
                          [23000, 320.0], [24000, 333.0], [25000, 346.0]],
                "over": 346.0, "unknown": 58.0},
         description="SF Express (Speedy Express, HK domestic Limited-Time-Offer, eff. 2024-06-01) delivery cost per sell-unit by shipping weight in grams; weight billed rounded up to 0.5kg (<=5kg) then 1kg."),
    dict(key="hktv_fee", name="HKTV default platform fee", category="margin", output_field="channel_fee_pct",
         inputs=[], kind="parameter", value=0.18,
         description="HKTV Mall commission used when a SKU has no explicit channel fee."),
    dict(key="cross_channel_threshold", name="Cross-channel GP spread threshold", category="margin",
         output_field="cross_channel_flag", inputs=[], kind="parameter", value=0.05,
         description="Flag a SKU when its max minus min channel GP exceeds this."),
    dict(key="staleness_days", name="Cost staleness window (days)", category="cost", output_field="cost_is_stale",
         inputs=[], kind="parameter", value=90,
         description="A cost older than this many days is considered stale."),
]
DEFAULTS_BY_KEY = {d["key"]: d for d in _DEFAULTS}


def _default_entry(d: dict) -> dict:
    return dict(kind=d["kind"], inputs=d.get("inputs", []),
                formula=d.get("formula"), value=d.get("value"), table=d.get("table"))


# ── In-process config cache (loaded once; call invalidate() after a config write) ─────────
_STATE = {"loaded": False, "config": {}, "compiled": {}}


def invalidate() -> None:
    """Drop the cached config + compiled formulas. Call after any config write (Phase B/C) or in
    tests that change the active version."""
    _STATE.update(loaded=False, config={}, compiled={})


def _load() -> None:
    """Populate the cache from the active DB config version, overlaying `_DEFAULTS` for any key
    the DB doesn't set. Falls back entirely to `_DEFAULTS` when there's no active version or the
    DB is unreachable — so the engine is always behaviour-neutral, never broken."""
    if _STATE["loaded"]:
        return
    cfg = {k: _default_entry(d) for k, d in DEFAULTS_BY_KEY.items()}
    try:
        db = SessionLocal()
        try:
            cv = db.query(ConfigVersion).filter_by(is_active=1).first()
            if cv is not None:
                for v in db.query(TransformationValue).filter_by(config_version_id=cv.id).all():
                    base = cfg.get(v.transformation_key, {"inputs": []})
                    if v.value_kind == "formula":
                        cfg[v.transformation_key] = {**base, "kind": "formula", "formula": v.formula_text}
                    elif v.value_kind == "scalar":
                        cfg[v.transformation_key] = {**base, "kind": "parameter", "value": v.scalar_value}
                    elif v.value_kind == "table":
                        cfg[v.transformation_key] = {**base, "kind": "table", "table": json.loads(v.table_json)}
        finally:
            db.close()
    except Exception:
        pass  # DB unreachable → defaults (behaviour-neutral)
    _STATE.update(loaded=True, config=cfg, compiled={})


def _entry(key: str) -> dict:
    _load()
    e = _STATE["config"].get(key)
    if e is None:
        raise KeyError(f"unknown transformation: {key}")
    return e


def _compiled(key: str):
    c = _STATE["compiled"].get(key)
    if c is None:
        e = _entry(key)
        c = compile_formula(e["formula"], e.get("inputs") or [])
        _STATE["compiled"][key] = c
    return c


# ── Public API ────────────────────────────────────────────────────────────────────────────
def evaluate(key: str, inputs: dict):
    """Evaluate a formula transformation. Returns None on any evaluation error — matching the
    defensive guards the hard-coded formulas had (a bad/edge input yields 'n/a', never a crash)."""
    try:
        return _run(_compiled(key), inputs)
    except Exception:
        return None


def get_param(key: str):
    """Return the scalar value of a parameter transformation (e.g. hktv_fee, staleness_days)."""
    return _entry(key).get("value")


def lookup_table(key: str, x):
    """Tiered lookup, e.g. SF logistics by weight. Table shape: {tiers:[[limit,val]...], over, unknown}.
    x falsy or <= 0 → 'unknown'; else the first tier whose limit x does not exceed; else 'over'."""
    t = _entry(key)["table"]
    if not x or x <= 0:
        return t["unknown"]
    for limit, val in t["tiers"]:
        if x <= limit:
            return val
    return t["over"]


# ── Seed ────────────────────────────────────────────────────────────────────────────────────
def seed_defaults(engine=None) -> None:
    """Idempotently seed the transformation registry + a default active config version whose
    values reproduce the pre-Phase-A formulas. Safe to call on every startup."""
    db = SessionLocal()
    try:
        existing = {t.key for t in db.query(Transformation).all()}
        for i, d in enumerate(_DEFAULTS):
            if d["key"] not in existing:
                db.add(Transformation(
                    key=d["key"], name=d["name"], description=d.get("description"),
                    category=d["category"], output_field=d.get("output_field"),
                    input_vars=json.dumps(d.get("inputs", [])), kind=d["kind"], sort_order=i))
        if db.query(ConfigVersion).filter_by(is_active=1).first() is None:
            cv = ConfigVersion(created_at=datetime.utcnow().isoformat(), created_by="system:seed",
                               note="Phase A seed — reproduces the pre-config formulas", is_active=1)
            db.add(cv)
            db.flush()
            for d in _DEFAULTS:
                kind = d["kind"]
                db.add(TransformationValue(
                    config_version_id=cv.id, transformation_key=d["key"],
                    value_kind=("formula" if kind == "formula" else "scalar" if kind == "parameter" else "table"),
                    formula_text=d.get("formula"),
                    scalar_value=(float(d["value"]) if kind == "parameter" else None),
                    table_json=(json.dumps(d["table"]) if kind == "table" else None)))
        db.commit()
    finally:
        db.close()
    invalidate()


# ── Config editing (Phase B) ──────────────────────────────────────────────────────────────
# Parameters and tables are editable live; every edit creates a NEW active config version
# (the previous one is preserved as history) so rollback is a single action. Formula editing
# arrives in Phase C. The router owns the DB session, commit, audit, and cache invalidation.
_PARAM_RANGES = {
    "hktv_fee":                (0.0, 1.0),    # commission fraction
    "cross_channel_threshold": (0.0, 1.0),    # GP-spread fraction
    "staleness_days":          (1, 3650),     # days
}


def validate_param(key: str, value) -> float:
    """Range/type-check a scalar parameter edit. Raises ValueError on bad input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a number")
    lo, hi = _PARAM_RANGES.get(key, (None, None))
    if lo is not None and not (lo <= value <= hi):
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return float(value)


def validate_table(key: str, table: dict) -> dict:
    """Validate a tiered lookup table (strictly-ascending positive limits, non-negative values,
    non-negative over/unknown). Raises ValueError on bad input; returns the normalised table."""
    if not isinstance(table, dict):
        raise ValueError("table must be an object")
    tiers = table.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("tiers must be a non-empty list")
    prev = None
    for row in tiers:
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            raise ValueError("each tier must be [limit, value]")
        limit, val = row
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
            raise ValueError("tier limit must be a number > 0")
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
            raise ValueError("tier value must be a number >= 0")
        if prev is not None and limit <= prev:
            raise ValueError("tier limits must strictly ascend")
        prev = limit
    for f in ("over", "unknown"):
        v = table.get(f)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"'{f}' must be a number >= 0")
    return {"tiers": [[float(l), float(v)] for l, v in tiers],
            "over": float(table["over"]), "unknown": float(table["unknown"])}


def _clone_values(db, from_vid: int, to_vid: int) -> None:
    for v in db.query(TransformationValue).filter_by(config_version_id=from_vid).all():
        db.add(TransformationValue(config_version_id=to_vid, transformation_key=v.transformation_key,
                                   value_kind=v.value_kind, formula_text=v.formula_text,
                                   scalar_value=v.scalar_value, table_json=v.table_json))


def _seed_values(db, vid: int) -> None:
    for d in _DEFAULTS:
        kind = d["kind"]
        db.add(TransformationValue(
            config_version_id=vid, transformation_key=d["key"],
            value_kind=("formula" if kind == "formula" else "scalar" if kind == "parameter" else "table"),
            formula_text=d.get("formula"),
            scalar_value=(float(d["value"]) if kind == "parameter" else None),
            table_json=(json.dumps(d["table"]) if kind == "table" else None)))


def _create_active(db, editor: str, note: str | None, parent_id: int | None):
    """Deactivate the current active version and create a fresh active one. Returns (new, prev_active)."""
    prev = db.query(ConfigVersion).filter_by(is_active=1).first()
    new = ConfigVersion(created_at=datetime.utcnow().isoformat(), created_by=editor, note=note,
                        parent_version_id=parent_id, is_active=1)
    db.add(new)
    db.flush()
    if prev:
        prev.is_active = 0
    return new, prev


# Representative "happy-path" inputs per formula transformation — a candidate formula must
# evaluate to a number/None on these without raising. The sandbox already guarantees a formula
# can do nothing dangerous; this additionally catches one that would just break the number.
_SAMPLE_INPUTS = {
    "unit_cost":          {"basic_cost": 100.0, "units_per_pack": 10},
    "gross_gp":           {"price": 100.0, "cost": 40.0},
    "net_margin":         {"price": 100.0, "cost": 40.0, "fee_pct": 0.18, "delivery": 34.0},
    "woc":                {"total_qty": 50.0, "weekly_demand": 5.0},
    "sales_120d":         {"weekly_demand": 5.0},
    "mbb_buy_x_get_y":    {"base": 10.0, "min_qty": 6, "free_qty": 1},
    "mbb_spend_discount": {"base": 10.0, "discount_pct": 0.1},
}


def validate_formula(key: str, formula_text: str) -> str:
    """Validate a candidate formula edit (Phase C). Raises ValueError unless it (a) passes the
    sandbox allow-list using ONLY this transformation's declared input variables, and (b)
    evaluates to a number/None on representative inputs. Returns the trimmed formula."""
    if not formula_text or not str(formula_text).strip():
        raise ValueError("formula cannot be empty")
    d = DEFAULTS_BY_KEY.get(key)
    inputs = d.get("inputs", []) if d else []
    # (a) parse + allow-list; names limited to the declared inputs (raises on anything else)
    compile_formula(formula_text, inputs)
    # (b) must compute cleanly to a number/None on representative inputs
    sample = _SAMPLE_INPUTS.get(key) or {name: 1.0 for name in inputs}
    try:
        result = eval_formula(formula_text, sample)
    except Exception as e:
        raise ValueError(f"formula failed on sample inputs: {e}")
    if isinstance(result, bool) or (result is not None and not isinstance(result, (int, float))):
        raise ValueError("formula must produce a number (or None)")
    return str(formula_text).strip()


def edit_value(db, key: str, *, value=None, table=None, formula=None, editor: str, note: str | None = None):
    """Live-edit a parameter (value=), table (table=), or formula (formula=). Validates, then
    writes a NEW active config version carrying the change (unchanged keys cloned from the prior
    active). Returns (new_version, before, after). Raises ValueError for an unknown key or bad
    input. The caller commits + invalidates() + audits."""
    t = db.query(Transformation).filter_by(key=key).first()
    if t is None:
        raise ValueError(f"unknown transformation: {key}")
    if t.kind == "formula":
        if formula is None:
            raise ValueError("a 'formula' string is required")
        after = validate_formula(key, formula)
    elif t.kind == "parameter":
        if value is None:
            raise ValueError("a numeric 'value' is required")
        after = validate_param(key, value)
    elif t.kind == "table":
        if table is None:
            raise ValueError("a 'table' object is required")
        after = validate_table(key, table)
    else:
        raise ValueError(f"{key} is not editable")

    _load()
    _e = _STATE["config"].get(key, {})
    before = (_e.get("formula") if t.kind == "formula"
              else _e.get("value") if t.kind == "parameter"
              else _e.get("table"))

    new, prev = _create_active(db, editor, note, prev_id_for(db))
    if prev:
        _clone_values(db, prev.id, new.id)
    else:
        _seed_values(db, new.id)
    db.flush()
    row = db.query(TransformationValue).filter_by(config_version_id=new.id, transformation_key=key).first()
    if row is None:
        row = TransformationValue(config_version_id=new.id, transformation_key=key)
        db.add(row)
    if t.kind == "parameter":
        row.value_kind, row.scalar_value, row.formula_text, row.table_json = "scalar", after, None, None
    elif t.kind == "table":
        row.value_kind, row.table_json, row.scalar_value, row.formula_text = "table", json.dumps(after), None, None
    else:  # formula
        row.value_kind, row.formula_text, row.scalar_value, row.table_json = "formula", after, None, None
    db.flush()
    return new, before, after


def prev_id_for(db):
    a = db.query(ConfigVersion).filter_by(is_active=1).first()
    return a.id if a else None


def restore_version(db, version_id: int, *, editor: str):
    """Roll back by cloning a prior version's values into a NEW active version (history kept).
    Returns the new version. Raises ValueError if the version doesn't exist."""
    target = db.get(ConfigVersion, version_id)
    if target is None:
        raise ValueError(f"version {version_id} not found")
    new, _prev = _create_active(db, editor, f"restore of version {version_id}", version_id)
    _clone_values(db, version_id, new.id)
    db.flush()
    return new


def list_config(db) -> list:
    """Registry + the active config's value/formula/table per transformation, for the API/UI."""
    _load()
    out = []
    for t in db.query(Transformation).order_by(Transformation.sort_order).all():
        e = _STATE["config"].get(t.key, {})
        out.append({"key": t.key, "name": t.name, "description": t.description, "category": t.category,
                    "output_field": t.output_field, "inputs": json.loads(t.input_vars or "[]"),
                    "kind": t.kind, "editable": t.kind in ("parameter", "table"),
                    "value": e.get("value"), "formula": e.get("formula"), "table": e.get("table")})
    return out


def list_versions(db) -> list:
    return [{"id": v.id, "created_at": v.created_at, "created_by": v.created_by, "note": v.note,
             "is_active": bool(v.is_active), "parent_version_id": v.parent_version_id}
            for v in db.query(ConfigVersion).order_by(ConfigVersion.id.desc()).all()]
