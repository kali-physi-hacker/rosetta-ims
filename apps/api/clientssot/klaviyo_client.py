# -*- coding: utf-8 -*-
"""Minimal Klaviyo API client (read-only) for the CRM mesh. Reads KLAVIYO_API_KEY from project .env."""
import time
from pathlib import Path
import requests

_ENV = Path(__file__).resolve().parents[2] / ".env"
_cfg = {}
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            _cfg[k.strip()] = v.strip()

KEY = _cfg.get("KLAVIYO_API_KEY", "")
BASE = "https://a.klaviyo.com/api"
HEADERS = {"Authorization": f"Klaviyo-API-Key {KEY}", "revision": "2024-10-15", "accept": "application/json"}

def all_profiles(page_size=100):
    """Yield every profile with REAL marketing consent + engagement recency:
    {email, phone, email_consent(bool), last_event}. One pass -> real consent + 'last engaged' date."""
    url = (f"{BASE}/profiles/?additional-fields%5Bprofile%5D=subscriptions"
           f"&fields%5Bprofile%5D=email,phone_number,last_event_date,subscriptions&page%5Bsize%5D={page_size}")
    while url:
        r = None
        for attempt in range(6):
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                if r.status_code == 200:
                    break
            except Exception:
                r = None
            time.sleep(min(2 ** attempt, 20))
        if r is None or r.status_code != 200:
            print(f"  klaviyo profiles giving up: HTTP {getattr(r,'status_code','—')}", flush=True)
            return
        j = r.json()
        for d in j.get("data", []):
            a = d.get("attributes", {})
            consent = (((a.get("subscriptions") or {}).get("email") or {}).get("marketing") or {}).get("consent")
            yield {"email": (a.get("email") or "").strip(), "phone": (a.get("phone_number") or "").strip(),
                   "email_consent": consent == "SUBSCRIBED", "last_event": (a.get("last_event_date") or "")[:10]}
        url = (j.get("links") or {}).get("next")
        time.sleep(0.2)

def list_profiles(list_id, page_size=100):
    """Yield {id, email, phone} for every profile on a Klaviyo list (paginated, with retry/back-off
    for transient 502/429s so a long pull doesn't abort half-way)."""
    url = f"{BASE}/lists/{list_id}/profiles/?fields%5Bprofile%5D=email,phone_number&page%5Bsize%5D={page_size}"
    while url:
        r = None
        for attempt in range(6):
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                if r.status_code == 200:
                    break
            except Exception:
                r = None
            time.sleep(min(2 ** attempt, 20))   # 1,2,4,8,16,20s back-off
        if r is None or r.status_code != 200:
            print(f"  klaviyo {list_id} giving up: HTTP {getattr(r,'status_code','—')}", flush=True)
            return
        j = r.json()
        for d in j.get("data", []):
            a = d.get("attributes", {})
            yield {"id": d.get("id"), "email": (a.get("email") or "").strip(),
                   "phone": (a.get("phone_number") or "").strip()}
        url = (j.get("links") or {}).get("next")
        time.sleep(0.15)
