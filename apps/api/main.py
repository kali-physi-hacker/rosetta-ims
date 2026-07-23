"""Rosetta IMS API — FastAPI app.

Auto-deployed to the DigitalOcean Docker host on pushes to main that touch apps/api/**.
See .github/workflows/deploy-api-droplet.yml + apps/api/README.md -> Deployment.
"""
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse, RedirectResponse

import models
import database
import v2.models  # noqa: F401 — registers v2 tables (e.g. IngestionRun) on Base.metadata
from routers.v1 import include_routers as include_v1_routers
from routers.v2 import include_routers as include_v2_routers

models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)
database.seed_default_users(database.engine)

# Config-driven transformation engine (Phase A): seed the registry + default config version so
# the engine reproduces the previously hard-coded formulas. Idempotent; behaviour-neutral.
from services import transform_engine
transform_engine.seed_defaults(database.engine)

# orjson as the default serializer for every endpoint that returns a dict/list — ~5-8x faster
# than stdlib json across the whole API (catalogue queues, audit, suppliers, clients, …).
API_V1_PREFIX = "/v1"
API_V2_PREFIX = "/v2"
API_VERSION_PREFIXES = (API_V1_PREFIX, API_V2_PREFIX)


app = FastAPI(
    title="Rosetta IMS API",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
api_v1 = FastAPI(title="Rosetta IMS API v1", version="1.0.0", default_response_class=ORJSONResponse)
api_v2 = FastAPI(title="Rosetta IMS API v2", version="2.0.0", default_response_class=ORJSONResponse)

include_v1_routers(api_v1)
include_v2_routers(api_v2)
# Backwards-compatible aliases for existing scripts/tests. New clients should use /v1.
include_v1_routers(app, include_in_schema=False)

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


def _strip_api_version(path: str) -> str:
    for prefix in API_VERSION_PREFIXES:
        if path == prefix:
            return "/"
        if path.startswith(f"{prefix}/"):
            return path[len(prefix):]
    return path


def _is_public_path(path: str) -> bool:
    unversioned = _strip_api_version(path)
    return unversioned in _PUBLIC_PATHS or unversioned.startswith("/auth/invite/")


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    # CORS preflight (OPTIONS) must pass through without auth so browser gets CORS headers
    if request.method == "OPTIONS":
        return await call_next(request)
    # Public routes — login + invite onboarding (the invited user has no session yet)
    if _is_public_path(request.url.path):
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


@app.get("/", include_in_schema=False)
def api_index():
    return {
        "name": "Rosetta IMS API",
        "current": "v1",
        "versions": {
            "v1": {"base_path": API_V1_PREFIX, "status": "current"},
            "v2": {"base_path": API_V2_PREFIX, "status": "inventory-preview"},
        },
    }


@app.get("/docs", include_in_schema=False)
def docs_redirect():
    return RedirectResponse(url="v1/docs")


@app.get("/redoc", include_in_schema=False)
def redoc_redirect():
    return RedirectResponse(url="v1/redoc")


@app.get("/openapi.json", include_in_schema=False)
def openapi_redirect():
    return RedirectResponse(url="v1/openapi.json")


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "current": "v1"}


@api_v1.get("/health", tags=["system"])
def health_v1():
    return {"status": "ok", "version": "1.0.0"}


app.mount(API_V1_PREFIX, api_v1)
app.mount(API_V2_PREFIX, api_v2)
