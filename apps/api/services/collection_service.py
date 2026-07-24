"""Smart-collection rule evaluation + AI suggestion.

A rule is JSON: {"match": "all"|"any", "conditions": [{field, op, value}, ...]}.
Membership is evaluated in-memory over the product list (small dataset) so a
collection is always live — no stored membership to drift.
"""
import json
import os

# field -> key in product_to_dict (string fields unless listed numeric/special)
FIELD_MAP = {
    "category": "category", "brand": "brand", "supplier": "supplier_name",
    "name": "name", "status": "status", "data_grade": "data_grade",
    "cost": "primary_cost", "sales_120d": "sales_120d", "stock": "total_qty", "woc": "woc",
}
NUMERIC_FIELDS = {"cost", "sales_120d", "stock", "woc"}
STRING_OPS  = {"equals", "not_equals", "contains", "not_contains", "in"}
NUMERIC_OPS = {"gt", "gte", "lt", "lte", "equals"}
TAG_OPS     = {"has", "not_has"}

# Exposed to the frontend rule builder.
FIELDS = (["tag"] + list(FIELD_MAP.keys()))


# Singular/plural (and close-variant) tag aliases — the Shopify store filters some
# collections on a plural the products never use (e.g. rule "Tablets" vs tag "Tablet").
# Canonicalising both the rule value AND the product tags at match time fixes those
# collections without touching stored data.
_TAG_ALIASES = {
    "tablets": "tablet", "capsules": "capsule", "sprays": "spray",
    "heartworms": "heartworm", "fleas & ticks": "flea & tick", "fleas": "flea & tick",
}


def _canon_tag(label) -> str:
    t = " ".join(str(label or "").strip().lower().split())
    return _TAG_ALIASES.get(t, t)


def _eval_condition(d: dict, tags: set, cond: dict) -> bool:
    field = cond.get("field")
    op = cond.get("op")
    value = cond.get("value")

    if field == "tag":
        t = _canon_tag(value)
        present = t in tags
        return present if op == "has" else (not present) if op == "not_has" else False

    if field in NUMERIC_FIELDS:
        v = d.get(FIELD_MAP[field])
        try:
            x, y = float(v), float(value)
        except (TypeError, ValueError):
            return False
        return {"gt": x > y, "gte": x >= y, "lt": x < y, "lte": x <= y,
                "equals": x == y}.get(op, False)

    key = FIELD_MAP.get(field)
    if not key:
        return False
    raw = d.get(key)
    sv = ("" if raw is None else str(raw)).strip().lower()
    if op == "equals":       return sv == str(value or "").strip().lower()
    if op == "not_equals":   return sv != str(value or "").strip().lower()
    if op == "contains":     return str(value or "").strip().lower() in sv
    if op == "not_contains": return str(value or "").strip().lower() not in sv
    if op == "in":           return sv in [str(x).strip().lower() for x in (value or [])]
    return False


def matches(d: dict, tags: set, rule: dict) -> bool:
    conds = (rule or {}).get("conditions") or []
    if not conds:
        return False
    results = [_eval_condition(d, tags, c) for c in conds]
    return all(results) if (rule.get("match") != "any") else any(results)


def validate_rule(rule: dict) -> dict:
    """Drop malformed conditions; coerce match. Raises ValueError if nothing valid."""
    out = {"match": "any" if (rule or {}).get("match") == "any" else "all", "conditions": []}
    for c in (rule or {}).get("conditions") or []:
        field, op = c.get("field"), c.get("op")
        if field == "tag" and op in TAG_OPS:
            out["conditions"].append({"field": "tag", "op": op, "value": str(c.get("value") or "").strip().lower()})
        elif field in NUMERIC_FIELDS and op in NUMERIC_OPS:
            out["conditions"].append({"field": field, "op": op, "value": c.get("value")})
        elif field in FIELD_MAP and op in STRING_OPS:
            out["conditions"].append({"field": field, "op": op, "value": c.get("value")})
    if not out["conditions"]:
        raise ValueError("Rule has no valid conditions")
    return out


# ── Shopify smart-collection import ─────────────────────────────────────────────
# Map a Shopify automated-collection ruleSet to our rule format. Shopify columns:
#   TAG -> tag (has/not_has), TITLE -> name (contains/not_contains/equals),
#   VENDOR -> brand (equals/not_equals). VARIANT_PRICE rules are storefront catch-alls
#   (e.g. price>0 = whole store) and are ignored.
_SHOPIFY_TAG_OP   = {"EQUALS": "has", "NOT_EQUALS": "not_has"}
_SHOPIFY_STR_OP   = {"CONTAINS": "contains", "NOT_CONTAINS": "not_contains",
                     "EQUALS": "equals", "NOT_EQUALS": "not_equals"}


def shopify_ruleset_to_ims(ruleset: dict) -> dict | None:
    """Translate one Shopify ruleSet -> {"match","conditions"}; None if nothing usable."""
    if not ruleset:
        return None
    match = "any" if ruleset.get("appliedDisjunctively") else "all"
    conds = []
    for r in ruleset.get("rules") or []:
        col, rel, val = r.get("column"), r.get("relation"), r.get("condition")
        if col == "TAG" and rel in _SHOPIFY_TAG_OP:
            conds.append({"field": "tag", "op": _SHOPIFY_TAG_OP[rel], "value": val})
        elif col == "TITLE" and rel in _SHOPIFY_STR_OP:
            conds.append({"field": "name", "op": _SHOPIFY_STR_OP[rel], "value": val})
        elif col == "VENDOR" and rel in ("EQUALS", "NOT_EQUALS"):
            conds.append({"field": "brand", "op": _SHOPIFY_STR_OP[rel], "value": val})
        # VARIANT_PRICE / unknown columns: skipped
    if not conds:
        return None
    return {"match": match, "conditions": conds}


def shopify_tag_vocabulary(collections: list) -> list:
    """Sorted distinct TAG condition values across all Shopify collections — the
    controlled tag vocabulary the AI tagger must draw from."""
    vocab = set()
    for c in collections:
        for r in (c.get("ruleSet") or {}).get("rules") or []:
            if r.get("column") == "TAG" and r.get("condition"):
                vocab.add(r["condition"].strip())
    return sorted(vocab)


def tags_map(db) -> dict:
    """{product_id: set(tag_label)} for all tagged products."""
    import models
    out: dict[int, set] = {}
    rows = (db.query(models.ProductTag.product_id, models.Tag.label)
            .join(models.Tag, models.Tag.id == models.ProductTag.tag_id).all())
    for pid, label in rows:
        # canonicalised (lowercased + singular/plural aliases) so matching is consistent
        # on both sides, while tags still display in their original casing.
        out.setdefault(pid, set()).add(_canon_tag(label))
    return out


def load_products(db):
    """[(id, product_dict)], tags_map — loaded once, reused across collections."""
    from routers.products import _base_query, _load_cat_rules
    from services.pricing_service import product_to_dict
    cat_rules = _load_cat_rules(db)
    prods = _base_query(db).all()
    dicts = [(p.id, product_to_dict(p, cat_rules)) for p in prods]
    return dicts, tags_map(db)


def evaluate(rule: dict, dicts, tmap) -> list:
    """Return the product dicts matching `rule`."""
    return [d for (pid, d) in dicts if matches(d, tmap.get(pid, set()), rule)]


# ── AI suggestion ──────────────────────────────────────────────────────────────

def _ai_suggest(tag_counts, categories, brands, max_n):
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    from services.extraction_service import _loads_json_array
    tags_str = ", ".join(f"{t} ({c})" for t, c in tag_counts[:60])
    prompt = f"""You are organising a Hong Kong vet/pet inventory into Shopify-style smart collections.

Available product TAGS (with counts): {tags_str}
CATEGORIES: {", ".join(categories)}
TOP BRANDS: {", ".join(brands[:25])}

Propose up to {max_n} useful smart collections. For EACH return:
  {{"name": "...", "description": "...", "rule": {{"match": "all"|"any", "conditions": [{{"field": "...", "op": "...", "value": "..."}}]}}}}

Allowed fields: tag (op has|not_has), category|brand|supplier|name|status|data_grade (op equals|contains|in),
cost|sales_120d|stock|woc (op gt|gte|lt|lte). Build rules ONLY from the tags/categories/brands listed above.
Prefer merchandising groupings buyers care about (e.g. "Senior Cat Food", "Grain-Free Dog", "Prescription Diets").
Return a JSON array only. No prose."""
    msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=4096,
                                 messages=[{"role": "user", "content": prompt}])
    return _loads_json_array(msg.content[0].text)


def suggest_collections(db, max_n: int = 8) -> list:
    """Propose draft smart collections (NOT saved) from the current tag/category mix.
    Uses Claude when ANTHROPIC_API_KEY is set; otherwise a rule-based fallback."""
    import models
    from sqlalchemy import func
    tag_counts = (db.query(models.Tag.label, func.count(models.ProductTag.id))
                  .join(models.ProductTag, models.ProductTag.tag_id == models.Tag.id)
                  .group_by(models.Tag.id).order_by(func.count(models.ProductTag.id).desc()).all())
    tag_counts = [(t, c) for t, c in tag_counts]
    categories = [r[0] for r in db.query(models.Product.category).distinct().all() if r[0]]
    brand_rows = (db.query(models.Product.brand, func.count(models.Product.id))
                  .filter(models.Product.brand.isnot(None))
                  .group_by(models.Product.brand).order_by(func.count(models.Product.id).desc()).all())
    brands = [b for b, _ in brand_rows]

    drafts = []
    if os.environ.get("ANTHROPIC_API_KEY") and tag_counts:
        try:
            for obj in _ai_suggest(tag_counts, categories, brands, max_n):
                name = (obj.get("name") or "").strip()
                rule = obj.get("rule") or {}
                if not name:
                    continue
                try:
                    rule = validate_rule(rule)
                except ValueError:
                    continue
                drafts.append({"name": name, "description": obj.get("description"),
                               "rule": rule, "ai_generated": True})
        except Exception:
            drafts = []

    if not drafts:   # fallback: one collection per top tag
        for label, count in tag_counts[:max_n]:
            drafts.append({
                "name": label.title(),
                "description": f"Products tagged '{label}'",
                "rule": {"match": "all", "conditions": [{"field": "tag", "op": "has", "value": label}]},
                "ai_generated": False,
            })

    # annotate each draft with a live member count
    dicts, tmap = load_products(db)
    for d in drafts:
        d["count"] = len(evaluate(d["rule"], dicts, tmap))
    return drafts
