"""Fetch competitor selling prices from their product pages.

Read-only against competitor sites. For each linked competitor URL we try, in order:
  1. Shopify JSON  — append `.json` to a `/products/<handle>` URL → structured price + stock (free, robust).
  2. HTML fallback — schema.org JSON-LD, then microdata / OpenGraph / OpenCart / JS-blob /
     HK$-in-a-price-element (covers WooCommerce, OpenCart, HKTVmall and most custom sites).

Scraping runs in a small thread pool (pure HTTP, no DB); results are applied to the
competitor_prices rows on the calling thread, then committed. A failed scrape keeps the last
known price and records the reason in `last_status` — we never wipe a good price on a blip.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import requests

import models

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
_TIMEOUT = 12
_MAX_WORKERS = 12
_RETRIES = 2            # extra attempts on 429 / 5xx (per-IP rate limits)
_SAME_DOMAIN_GAP = 0.5  # seconds between requests to the same host — stay under rate limits

# Friendly names for the competitor stores in the price-match sheet (domain -> label).
_KNOWN_STORES = {
    "vetmacy.com": "Vetmacy", "petdoghk.com": "PetDogHK", "petcific.com.hk": "Petcific",
    "shop.npv.org.hk": "NPV", "vetopia.com.hk": "Vetopia", "pettington.com": "Pettington",
    "farmavet.com.hk": "Farmavet", "gogopet.com.hk": "GoGoPet", "cityuvb.com.hk": "CityU VB",
    "hktvmall.com": "HKTVmall", "daydaypet.net": "DayDayPet", "a-pets.com": "A-Pets",
    "princessparadise99.com": "Princess Paradise", "pethaven.com.hk": "Pet Haven",
    "hksev.com": "HKSEV", "898buy.com.hk": "898buy", "homevet.com.hk": "HomeVet",
    "petshack.hk": "Petshack", "petmarthk.com": "PetMart HK",
    "pets-central.com": "Pets Central", "thebestpet.com.hk": "The Best Pet",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def detect_platform(url: str) -> str:
    host, path, low = _host(url), urlparse(url).path.lower(), url.lower()
    if "hktvmall" in host:
        return "hktvmall"
    if "route=product" in low:
        return "opencart"
    if "/products/" in path:
        return "shopify"
    if "/product/" in path:
        return "woocommerce"
    return "generic"


def guess_name(url: str) -> str:
    host = _host(url)
    for dom, label in _KNOWN_STORES.items():
        if host == dom or host.endswith("." + dom):
            return label
    return host or "Competitor"


def _to_float(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("HK$", "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _get(url: str):
    """GET with a browser UA, retrying on 429/5xx (honouring Retry-After). Raises on final failure."""
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    last = None
    for attempt in range(_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code == 429 or 500 <= r.status_code < 600:
            retry_after = r.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else 2.0 * (attempt + 1)
            last = requests.HTTPError(f"HTTP {r.status_code}", response=r)
            time.sleep(min(wait, 8))
            continue
        r.raise_for_status()   # other 4xx (403/404) won't recover — surface it
        return r
    raise last if last is not None else requests.HTTPError("no response")


def _scrape_shopify(url: str) -> dict:
    base = url.split("?")[0].split("#")[0].rstrip("/")
    r = _get(base + ".json")
    prod = r.json()["product"]
    variants = prod.get("variants") or []
    # Cheapest available variant; else cheapest variant.
    priced = [(_to_float(v.get("price")), v) for v in variants]
    priced = [(p, v) for p, v in priced if p is not None]
    if not priced:
        return {"price": None, "in_stock": None, "title": prod.get("title"), "status": "no price found"}
    avail = [(p, v) for p, v in priced if v.get("available")]
    price, _v = min(avail or priced, key=lambda t: t[0])
    flags = [v.get("available") for _, v in priced]
    # True anywhere -> in stock; explicit False and never True -> out; missing everywhere -> unknown.
    in_stock = 1 if any(a is True for a in flags) else (0 if any(a is False for a in flags) else None)
    return {"price": price, "in_stock": in_stock, "title": prod.get("title"), "status": "ok"}


# HTML price strategies tried (in order) after JSON-LD. Each captures the numeric price.
_HTML_PRICE_STRATEGIES = [
    r'itemprop=["\']price["\'][^>]*content=["\']([0-9][0-9,]*\.?[0-9]*)',                       # microdata
    r'(?:product:price:amount|og:price:amount)["\'][^>]*content=["\']([0-9][0-9,]*\.?[0-9]*)',  # OpenGraph
    r'class=["\']price-(?:new|nochange|special)["\'][^>]*>\s*HK\$\s*([0-9][0-9,]*\.?[0-9]*)',    # OpenCart
    r'"price"\s*:\s*"?([0-9][0-9,]*\.?[0-9]*)"?',                                                # JS / JSON blob
    r'class=["\'][^"\']*price[^"\']*["\'][^>]*>[^<]*?HK\$\s*([0-9][0-9,]*\.?[0-9]*)',            # HK$ inside a price element
]


def _jsonld_prices(html: str) -> list[float]:
    """Every price in the page's schema.org JSON-LD blocks (walks nested offers/lowPrice/price)."""
    prices: list[float] = []
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            obj = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [obj]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key in ("price", "lowPrice"):
                    val = _to_float(node.get(key))
                    if val is not None:
                        prices.append(val)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return prices


def _scrape_html(url: str) -> dict:
    r = _get(url)
    html = r.text
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip()[:200] if title_m else None
    in_stock = 0 if re.search(r"sold\s*out|out\s*of\s*stock|售罄|缺貨", html, re.I) else None
    # 1. schema.org JSON-LD — most reliable; take the lowest offered price.
    jsonld = [p for p in _jsonld_prices(html) if p > 0]
    if jsonld:
        return {"price": min(jsonld), "in_stock": in_stock, "title": title, "status": "ok"}
    # 2. Ordered site-shape strategies (microdata -> OG -> OpenCart -> JS blob -> HK$-in-price).
    for pat in _HTML_PRICE_STRATEGIES:
        m = re.search(pat, html, re.I | re.S)
        if m:
            price = _to_float(m.group(1))
            if price is not None and 0 < price < 1_000_000:
                return {"price": price, "in_stock": in_stock, "title": title, "status": "ok"}
    return {"price": None, "in_stock": in_stock, "title": title, "status": "no price found"}


def scrape_url(url: str) -> dict:
    """Pure HTTP — never touches the DB. Returns price/in_stock/title/platform/status."""
    platform = detect_platform(url)
    try:
        if platform == "shopify":
            try:
                res = _scrape_shopify(url)
            except Exception:
                res = _scrape_html(url)   # some /products/ pages aren't Shopify — fall back
        else:
            res = _scrape_html(url)
        return {**res, "platform": platform}
    except Exception as exc:  # network/HTTP/parse failure — keep it, don't raise
        resp = getattr(exc, "response", None)
        detail = f"HTTP {resp.status_code}" if resp is not None else type(exc).__name__
        return {"price": None, "in_stock": None, "title": None,
                "platform": platform, "status": f"error: {detail}"}


def scrape_rows(db, rows: list[models.CompetitorPrice]) -> dict:
    """Scrape each row's URL concurrently, apply results, commit. Keeps the last good price
    on failure. Returns {scraped, ok, failed}."""
    targets = [(cp.id, cp.url) for cp in rows if cp.url]
    # Group by host: scrape each host's URLs SEQUENTIALLY (with a small gap) while running
    # different hosts in parallel — one IP hammering a single store trips its rate limit.
    by_host: dict[str, list] = {}
    for cid, url in targets:
        by_host.setdefault(_host(url), []).append((cid, url))

    def _scrape_host(items):
        out = {}
        for i, (cid, url) in enumerate(items):
            if i:
                time.sleep(_SAME_DOMAIN_GAP)
            out[cid] = scrape_url(url)
        return out

    results: dict[int, dict] = {}
    if by_host:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(by_host))) as ex:
            for out in ex.map(_scrape_host, by_host.values()):
                results.update(out)
    today, now, ok, failed = date.today().isoformat(), _now(), 0, 0
    for cp in rows:
        res = results.get(cp.id)
        if res is None:
            continue
        if res.get("price") is not None:
            cp.price = res["price"]
            ok += 1
        else:
            failed += 1
        if res.get("in_stock") is not None:
            cp.in_stock = res["in_stock"]
        if res.get("title"):
            cp.title = res["title"]
        cp.platform = res.get("platform") or cp.platform
        cp.last_status = res.get("status")
        cp.last_checked = today
        cp.updated_at = now
    db.commit()
    return {"scraped": len(targets), "ok": ok, "failed": failed}


def scrape_product(db, product_id: int) -> dict:
    rows = db.query(models.CompetitorPrice).filter(
        models.CompetitorPrice.product_id == product_id).all()
    return scrape_rows(db, rows)


def scrape_all(db) -> dict:
    return scrape_rows(db, db.query(models.CompetitorPrice).all())
