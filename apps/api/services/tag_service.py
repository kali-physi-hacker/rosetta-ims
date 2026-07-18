"""Persist free-form product tags (get-or-create Tag rows + product↔tag links)."""
from datetime import datetime


def _norm(label: str) -> str:
    return " ".join((label or "").strip().lower().split())


def get_or_create_tag(db, label: str):
    """Find or create a Tag by normalised slug. Returns the Tag (or None for empty)."""
    import models
    slug = _norm(label)
    if not slug or len(slug) > 40:
        return None
    tag = db.query(models.Tag).filter(models.Tag.slug == slug).first()
    if not tag:
        tag = models.Tag(slug=slug, label=slug, created_at=datetime.utcnow().isoformat())
        db.add(tag)
        db.flush()
    return tag


def clear_tags(db, product):
    """Remove all of a product's tag links (via collection mutation, so the
    in-memory state stays consistent with the DB; cascade delete-orphan handles
    the rows). Used by the manual full-replace path."""
    product.tag_links.clear()
    db.flush()


def apply_tags(db, product, labels, *, source="ai", user=None, replace=False):
    """Attach `labels` to `product`. Idempotent (skips dupes). When replace=True,
    first removes existing links of the SAME source (so an AI re-run doesn't wipe
    human-added tags, and vice-versa). Returns the final list of tag labels.

    Links are removed via collection mutation (not db.delete) so `product.tag_links`
    reflects the deletion immediately — otherwise the dedupe set below would still
    see just-deleted tag_ids and skip re-adding them."""
    import models
    now = datetime.utcnow().isoformat()
    by = getattr(user, "display_name", None)

    if replace:
        for link in [l for l in product.tag_links if l.source == source]:
            product.tag_links.remove(link)   # cascade delete-orphan removes the row
        db.flush()

    existing = {l.tag_id for l in product.tag_links}
    for label in (labels or []):
        tag = get_or_create_tag(db, label)
        if tag and tag.id not in existing:
            db.add(models.ProductTag(product_id=product.id, tag_id=tag.id,
                                     source=source, created_by=by, created_at=now))
            existing.add(tag.id)
    db.flush()
    return tags_for_product(db, product.id)


def tags_for_product(db, product_id) -> list[str]:
    import models
    rows = (db.query(models.Tag.label)
            .join(models.ProductTag, models.ProductTag.tag_id == models.Tag.id)
            .filter(models.ProductTag.product_id == product_id)
            .order_by(models.Tag.label).all())
    return [r[0] for r in rows]
