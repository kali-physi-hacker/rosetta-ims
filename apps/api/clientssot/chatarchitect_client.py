# -*- coding: utf-8 -*-
"""Minimal ChatArchitect WhatsApp Business API client (reads creds from .env).
- /export uses a URL-token auth (basic=<base64(appid:appsecret)>) -> CSV.
- /whatsappmessage, /submit_template etc. use HTTP Basic auth (appid:appsecret).
Currently only the read-only export is used (opt-in list). Sending is intentionally NOT wired up yet."""
import base64, time
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

BASE = _cfg.get("CHATARCHITECT_BASE", "https://api.chatarchitect.com").rstrip("/")
APP_ID = _cfg.get("CHATARCHITECT_APP_ID", "")
APP_SECRET = _cfg.get("CHATARCHITECT_APP_SECRET", "")
_TOKEN = base64.b64encode(f"{APP_ID}:{APP_SECRET}".encode()).decode()

def export(action="phones", date_range="this_month", page=0):
    """GET /export/ -> CSV text. action: phones|sent|received|conversations. range: last_month|this_month|yesterday|today."""
    url = f"{BASE}/export/?basic={_TOKEN}&action={action}&range={date_range}&page={page}"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=40)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:
                time.sleep(2 ** attempt); continue
            return ""
        except Exception:
            time.sleep(2 ** attempt)
    return ""

def optin_phones():
    """Union of all opted-in WhatsApp phone numbers across the available ranges (paged)."""
    phones = set()
    for rng in ("this_month", "last_month", "yesterday", "today"):
        for page in range(0, 1000):
            csv = export("phones", rng, page)
            nums = [ln.strip() for ln in csv.splitlines() if ln.strip() and ln.strip().lower() != "phone"]
            if not nums:
                break
            phones.update(nums)
            if len(nums) < 2:   # tiny page -> likely the last
                break
            time.sleep(0.2)
    return phones
