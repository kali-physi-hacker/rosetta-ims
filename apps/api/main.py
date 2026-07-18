"""Rosetta IMS API — FastAPI app.

Auto-deployed to Fly.io on every push to main that touches backend/**.
See .github/workflows/fly-deploy.yml + backend/README.md → Deployment.
"""
import os
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from sqlalchemy.orm import Session

import models
import database
from routers import products, pricing, suppliers, sku, catalogues, reparse, stock, sync, auth, access_acknowledgements, tags, collections, categories, brands, users, audit, competitors, config
from clientssot.router import router as clientssot_router

models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)
database.seed_default_users(database.engine)

# Config-driven transformation engine (Phase A): seed the registry + default config version so
# the engine reproduces the previously hard-coded formulas. Idempotent; behaviour-neutral.
from services import transform_engine
transform_engine.seed_defaults(database.engine)

# orjson as the default serializer for every endpoint that returns a dict/list — ~5-8x faster
# than stdlib json across the whole API (catalogue queues, audit, suppliers, clients, …).
app = FastAPI(title="Rosetta IMS API", version="1.0.0", default_response_class=ORJSONResponse)

_default_origins = "http://localhost:3001,http://localhost:3000"
# Explicit allowlist (env-overridable); trim blanks/whitespace so a stray space can't
# silently void an entry.
_allowed = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
# Always allow this project's managed frontend domains — Cloudflare (Workers/Pages: pages.dev,
# workers.dev) and, during the migration, the legacy Vercel domains — regardless of the
# ALLOWED_ORIGINS secret, so a missing/incorrect secret can't take prod offline. Matches e.g.
# rosetta-ims.pages.dev, rosetta-ims-*.workers.dev, rosetta-ims.vercel.app. A custom domain
# (e.g. app.algogroup.io) goes in the ALLOWED_ORIGINS env var.
_frontend_regex = r"https://rosetta-ims[a-z0-9-]*\.(pages\.dev|workers\.dev|vercel\.app)"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_origin_regex=_frontend_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)

_API_KEY = os.environ.get("IMS_API_KEY", "")

_PUBLIC_PATHS = {"/health", "/auth/login", "/auth/accept-invite",
                 "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}

@app.middleware("http")
async def require_api_key(request: Request, call_next):
    # CORS preflight (OPTIONS) must pass through without auth so browser gets CORS headers
    if request.method == "OPTIONS":
        return await call_next(request)
    # Public routes — login + invite onboarding (the invited user has no session yet)
    if request.url.path in _PUBLIC_PATHS or request.url.path.startswith("/auth/invite/"):
        return await call_next(request)
    # Legacy API key gate — skipped if IMS_API_KEY not set (dev mode)
    if not _API_KEY:
        return await call_next(request)
    if request.headers.get("X-API-Key") == _API_KEY:
        return await call_next(request)
    # Also accept Bearer JWT — JWT auth is validated per-endpoint via Depends(get_current_user)
    if request.headers.get("Authorization", "").startswith("Bearer "):
        return await call_next(request)
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(competitors.router)
app.include_router(pricing.router)
app.include_router(suppliers.router)
app.include_router(sku.router)
app.include_router(reparse.router)       # catalogue re-parse — /catalogues/reparse/* (before the catalogues catch-all)
app.include_router(catalogues.router)
app.include_router(stock.router)
app.include_router(sync.router)
app.include_router(access_acknowledgements.router)
app.include_router(tags.router)
app.include_router(collections.router)
app.include_router(categories.router)
app.include_router(brands.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(config.router)       # transformation config engine (Phase B) — /config/*
app.include_router(clientssot_router)   # Client SSOT / Clientbase — /clients/*


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
