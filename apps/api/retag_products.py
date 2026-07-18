"""One-off bulk re-tag: assign every product the controlled Shopify tag vocabulary
(via tagging_service) so it falls into the imported Shopify smart collections.

Only AI-sourced tags are replaced — manual tags are preserved. Category is left
untouched (human-confirmed at onboarding); subcategory is filled only if empty.

Usage (inside the api container):
    python retag_products.py            # all active products
    python retag_products.py 50         # first 50 only (sample / validation)
"""
import sys
import database, models
from services import tagging_service, tag_service

BATCH = 30


def main(limit=None):
    db = database.SessionLocal()
    q = (db.query(models.Product)
         .filter(models.Product.status != "DISCONTINUED")
         .order_by(models.Product.id))
    if limit:
        q = q.limit(limit)
    products = q.all()
    print(f"retagging {len(products)} products (vocab size {len(tagging_service.AI_TAGS)})...", flush=True)

    tagged = 0
    for base in range(0, len(products), BATCH):
        batch = products[base:base + BATCH]
        items = [{"description": p.name, "brand": p.brand, "supplier": None} for p in batch]
        sugs = tagging_service.suggest_tags(items)
        for p, sug in zip(batch, sugs):
            tags = sug.get("tags") or []
            if tags:
                tag_service.apply_tags(db, p, tags, source="ai", replace=True)
                tagged += 1
            sub = sug.get("subcategory")
            if sub and not p.subcategory:
                p.subcategory = sub
        db.commit()
        print(f"  {min(base + BATCH, len(products))}/{len(products)} processed", flush=True)
    print(f"DONE. applied tags to {tagged} products.", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
