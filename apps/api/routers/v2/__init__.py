from fastapi import FastAPI

from routers.v1 import (
    access_acknowledgements,
    audit,
    auth,
    brands,
    categories,
    collections,
    competitors,
    config,
    pricing,
    products,
    sku,
    stock,
    suppliers,
    sync,
    tags,
    users,
)


def include_routers(target: FastAPI, *, include_in_schema: bool = True) -> None:
    """Register the v2 inventory surface.

    v2 currently mirrors v1 auth and inventory/admin-support endpoints while
    intentionally excluding catalogue ingestion and catalogue reparse flows.
    """
    target.include_router(auth.router, include_in_schema=include_in_schema)
    target.include_router(products.router, include_in_schema=include_in_schema)
    target.include_router(competitors.router, include_in_schema=include_in_schema)
    target.include_router(pricing.router, include_in_schema=include_in_schema)
    target.include_router(suppliers.router, include_in_schema=include_in_schema)
    target.include_router(sku.router, include_in_schema=include_in_schema)
    target.include_router(stock.router, include_in_schema=include_in_schema)
    target.include_router(sync.router, include_in_schema=include_in_schema)
    target.include_router(access_acknowledgements.router, include_in_schema=include_in_schema)
    target.include_router(tags.router, include_in_schema=include_in_schema)
    target.include_router(collections.router, include_in_schema=include_in_schema)
    target.include_router(categories.router, include_in_schema=include_in_schema)
    target.include_router(brands.router, include_in_schema=include_in_schema)
    target.include_router(users.router, include_in_schema=include_in_schema)
    target.include_router(audit.router, include_in_schema=include_in_schema)
    target.include_router(config.router, include_in_schema=include_in_schema)
