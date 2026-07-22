from fastapi import FastAPI

from clientssot.router import router as clientssot_router
from . import (
    access_acknowledgements,
    audit,
    auth,
    brands,
    catalogues,
    categories,
    collections,
    competitors,
    config,
    pricing,
    products,
    reparse,
    sku,
    stock,
    suppliers,
    sync,
    tags,
    users,
)


def include_routers(target: FastAPI, *, include_in_schema: bool = True) -> None:
    target.include_router(auth.router, include_in_schema=include_in_schema)
    target.include_router(products.router, include_in_schema=include_in_schema)
    target.include_router(competitors.router, include_in_schema=include_in_schema)
    target.include_router(pricing.router, include_in_schema=include_in_schema)
    target.include_router(suppliers.router, include_in_schema=include_in_schema)
    target.include_router(sku.router, include_in_schema=include_in_schema)
    target.include_router(reparse.router, include_in_schema=include_in_schema)
    target.include_router(catalogues.router, include_in_schema=include_in_schema)
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
    target.include_router(clientssot_router, include_in_schema=include_in_schema)
