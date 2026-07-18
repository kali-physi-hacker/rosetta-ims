# -*- coding: utf-8 -*-
"""Minimal DaySmart Vet API client (read-only) for the Client SSOT ingest.
Reads creds from the project-root .env. Same auth recipe as the MCP server:
token POST {BASE}/oauth/access_token (scope=APIService); resources at {BASE}/api/1.0.0/{API_KEY}/..."""
import time
from pathlib import Path
import requests

_ENV = Path(__file__).resolve().parents[2] / ".env"   # project root
_cfg = {}
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            _cfg[k.strip()] = v.strip()

BASE   = _cfg.get("DAYSMART_BASE_URL", "").rstrip("/")
CID    = _cfg.get("DAYSMART_CLIENT_ID", "")
SECRET = _cfg.get("DAYSMART_CLIENT_SECRET", "")
APIKEY = _cfg.get("DAYSMART_API_KEY", "")
TOKEN_URL = f"{BASE}/oauth/access_token"
RES_BASE  = f"{BASE}/api/1.0.0/{APIKEY}"
_tok = {"v": None, "exp": 0}

def _token():
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    r = requests.post(TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": CID,
              "client_secret": SECRET, "scope": "APIService"},
        headers={"Content-Type": "application/x-www-form-urlencoded", "x-api-key": SECRET}, timeout=30)
    r.raise_for_status()
    j = r.json()
    _tok["v"] = j["access_token"]; _tok["exp"] = time.time() + int(j.get("expires_in", 3600))
    return _tok["v"]

def get(path, params=None):
    r = requests.get(f"{RES_BASE}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {_token()}", "x-api-key": SECRET}, params=params or {}, timeout=60)
    r.raise_for_status()
    return r.json()

def paginate(path, per_page=200, max_pages=None):
    """Yield each resource across all pages of a DaySmart list endpoint."""
    page = 1
    while True:
        out = get(path, {"page": page, "perPage": per_page})
        resp = out.get("response", {})
        for res in resp.get("resources", []):
            yield res
        meta = resp.get("meta", {})
        last = meta.get("lastPage", 1)
        if page >= last or (max_pages and page >= max_pages):
            break
        page += 1
