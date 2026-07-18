"""Internal SKU auto-generation. IMS owns the 8-digit internal SKU namespace.

Format (per the SKU-code spec sheet): an 8-digit number =
    [1 leading digit derived from the item category][7-digit zero-padded global sequence]

    e.g. Medicine -> digit 5, last-created 0010384 -> next 0010385 -> 50010385

The leading digit is derived from the product's operational ITEM category (the
only category list IMS uses); the underlying digit scheme follows the SKU-code
sheet (1 Food, 4 Cleaning/grooming, 5 Healthcare, 7 Others). The 7-digit sequence
is a SINGLE GLOBAL ascending counter shared across every category (NOT per-
category): each new SKU takes the next number and prepends its own leading digit.

A reserved block of sentinel SKUs (suffix >= 9_000_000, e.g. X9999999) is
excluded from the max so the counter never jumps into the reserved range.
"""
from sqlalchemy.orm import Session
from models import Product

# Operational item category -> SKU leading digit. The item category is the only
# category the picker offers; several map to the same digit (the SKU-code scheme).
ITEM_CATEGORY_DIGIT: dict[str, str] = {
    'Food':         '1',
    'Medicine':     '5',
    'Preventative': '5',
    'Supplement':   '5',
    'Shampoo':      '4',
    'Pet Hygiene':  '4',
    'Cat Litter':   '4',
    'Not-For-Sale': '6',
    'Others':       '7',
}

# Backward-compat aliases — routers validate membership against these names.
SKU_CATEGORY_DIGIT = ITEM_CATEGORY_DIGIT
CATEGORY_PREFIX = ITEM_CATEGORY_DIGIT

_SENTINEL_FLOOR = 9_000_000   # suffixes at/above this are reserved sentinels, never auto-issued
_MAX_SUFFIX = 9_999_999       # 7 digits


def _max_real_suffix(db: Session) -> int:
    """Highest 7-digit suffix across all live 8-digit numeric SKUs, ignoring the
    reserved sentinel block. Returns 0 when there are no numeric SKUs yet."""
    best = 0
    for (code,) in db.query(Product.sku_code).all():
        if code and len(code) == 8 and code.isdigit():
            suffix = int(code[1:])
            if best < suffix < _SENTINEL_FLOOR:
                best = suffix
    return best


def next_sku(category: str, db: Session) -> str:
    """Next internal 8-digit SKU for an item category.

    Leading digit = the category's digit; the remaining 7 digits are the next
    value of the single global ascending counter (max real suffix + 1).
    Collision-guarded against the (effectively impossible) case where the
    composed code already exists.
    """
    # Prefer the data-driven digit from category_rules (lets categories be added/edited
    # without a code change); fall back to the static map for safety.
    import models
    rule = db.query(models.CategoryRule).filter(models.CategoryRule.category == category).first()
    digit = (rule.sku_digit if rule and rule.sku_digit else None) or ITEM_CATEGORY_DIGIT.get(category)
    if not digit:
        raise ValueError(f"Unknown item category '{category}' (no SKU digit). "
                         f"Add it in Categories or use one of: {list(ITEM_CATEGORY_DIGIT)}")

    suffix = _max_real_suffix(db) + 1
    while suffix <= _MAX_SUFFIX:
        code = f"{digit}{suffix:07d}"
        if not db.query(Product.id).filter(Product.sku_code == code).first():
            return code
        suffix += 1
    raise ValueError("SKU sequence exhausted (7-digit suffix overflow)")
