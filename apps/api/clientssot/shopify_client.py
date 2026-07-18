# -*- coding: utf-8 -*-
"""Minimal Shopify Admin GraphQL client (read-only) for the Client SSOT. Reads token/domain from .env.
Token = the DURABLE Admin API token from the "Vetra SSOT" custom app (shpat_39a9bde…) — the durable link
we already set up. Not a 24h/online token; no OAuth refresh needed. (App OAuth creds: Client ID 1aca0b5d…)."""
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

DOMAIN = _cfg.get("SHOPIFY_DOMAIN", "")
TOKEN = _cfg.get("SHOPIFY_TOKEN", "")
URL = f"https://{DOMAIN}/admin/api/2024-10/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

def gql(query, variables=None):
    for attempt in range(6):
        try:
            r = requests.post(URL, json={"query": query, "variables": variables or {}}, headers=HEADERS, timeout=60)
            if r.status_code == 200:
                j = r.json()
                if "errors" in j and not j.get("data"):
                    print("  shopify errors:", str(j["errors"])[:200], flush=True)
                return j
            if r.status_code == 429:  # throttled
                time.sleep(min(2 ** attempt, 20)); continue
            print(f"  shopify HTTP {r.status_code}: {r.text[:160]}", flush=True)
        except Exception as e:
            print("  shopify exc:", e, flush=True)
        time.sleep(min(2 ** attempt, 20))
    return {}

ORDERS_Q = """
query($cursor: String) {
  orders(first: 100, after: $cursor, query: "created_at:>=2019-01-01", sortKey: CREATED_AT) {
    edges { cursor node {
      createdAt
      customer { id email phone firstName lastName numberOfOrders amountSpent { amount } tags }
      lineItems(first: 8) { edges { node { title } } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""

CARETAG_Q = """
query($cursor: String) {
  orders(first: 100, after: $cursor, query: "created_at:>=2019-01-01", sortKey: CREATED_AT) {
    edges { cursor node {
      customer { id email phone }
      lineItems(first: 15) { edges { node { title } } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""

PURCHASES_Q = """
query($cursor: String) {
  orders(first: 100, after: $cursor, query: "created_at:>=2019-01-01", sortKey: CREATED_AT) {
    edges { node {
      createdAt
      customer { id email phone }
      lineItems(first: 20) { edges { node {
        title sku quantity
        discountedUnitPriceSet { shopMoney { amount } }
        sellingPlan { name }
      } } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""

COLLECTIONS_Q = """
query($cursor: String) {
  collections(first: 25, after: $cursor) {
    edges { cursor node { title products(first: 250) { edges { node { title } } } } }
    pageInfo { hasNextPage endCursor }
  }
}"""

def collections():
    """Yield {collection, products:[titles]} for every Shopify collection (so customers can be filtered by
    collection membership in the Demand Record). Products capped at 250/collection."""
    cursor = None
    while True:
        j = gql(COLLECTIONS_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("collections")
        if not data:
            return
        for e in data["edges"]:
            n = e["node"]
            yield {"collection": n.get("title") or "",
                   "products": [p["node"].get("title") or "" for p in (n.get("products") or {}).get("edges", [])]}
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]; time.sleep(0.3)
        else:
            return

def order_purchases():
    """Yield each order's line items with SKU + date + customer (lean customer{id,email,phone} so it doesn't
    throttle like the full pull). Powers Shopify purchases in the timeline + PSG-SKU audience detection."""
    cursor = None
    while True:
        j = gql(PURCHASES_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("orders")
        if not data:
            return
        for e in data["edges"]:
            n = e["node"]; c = n.get("customer") or {}
            yield {
                "created": (n.get("createdAt") or "")[:10],
                "cid": c.get("id"), "email": (c.get("email") or "").strip(), "phone": (c.get("phone") or "").strip(),
                "items": [{"title": li["node"].get("title") or "", "sku": li["node"].get("sku") or "",
                           "qty": li["node"].get("quantity") or 1,
                           "price": float(((li["node"].get("discountedUnitPriceSet") or {}).get("shopMoney") or {}).get("amount") or 0),
                           "autoship": 1 if li["node"].get("sellingPlan") else 0}
                          for li in (n.get("lineItems") or {}).get("edges", [])],
            }
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]; time.sleep(0.2)
        else:
            return

def order_caretags():
    """Lean order stream (customer id/email/phone + line-item titles only) for care-tagging the full
    lifetime buyer base. Minimal fields = lower query cost = less throttling than the full order pull."""
    cursor = None
    while True:
        j = gql(CARETAG_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("orders")
        if not data:
            return
        for e in data["edges"]:
            n = e["node"]
            c = n.get("customer") or {}
            yield {
                "cid": c.get("id"), "email": (c.get("email") or "").strip(), "phone": (c.get("phone") or "").strip(),
                "products": [li["node"]["title"] for li in (n.get("lineItems") or {}).get("edges", [])],
            }
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]; time.sleep(0.2)
        else:
            return

LINEITEMS_Q = """
query($cursor: String) {
  orders(first: 100, after: $cursor, query: "created_at:>=2019-01-01", sortKey: CREATED_AT) {
    edges { cursor node { lineItems(first: 20) { edges { node { title quantity } } } } }
    pageInfo { hasNextPage endCursor }
  }
}"""

def order_lineitems_last_12mo():
    """Yield (product_title, quantity) for every order line item in the last 12 months.
    No `customer` field, so this works WITHOUT the read_customers scope — gives real sales-by-product."""
    cursor = None
    while True:
        j = gql(LINEITEMS_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("orders")
        if not data:
            return
        for e in data["edges"]:
            for li in (e["node"].get("lineItems") or {}).get("edges", []):
                n = li["node"]
                yield (n.get("title") or "", int(n.get("quantity") or 1))
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]; time.sleep(0.2)
        else:
            return

CUSTOMERS_Q = """
query($cursor: String) {
  customers(first: 250, after: $cursor) {
    edges { cursor node {
      id email phone firstName lastName numberOfOrders
      amountSpent { amount } tags createdAt
      lastOrder { createdAt }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""

def customers_all():
    """Yield every customer (lifetime) with LTV/orders/tags — cheap query, no nested order pull.
    Gets the FULL buyer base back to the store's start."""
    cursor = None
    while True:
        j = gql(CUSTOMERS_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("customers")
        if not data:
            return
        for e in data["edges"]:
            n = e["node"]
            yield {
                "id": n.get("id"), "email": (n.get("email") or "").strip(), "phone": (n.get("phone") or "").strip(),
                "first": n.get("firstName") or "", "last": n.get("lastName") or "",
                "orders": int(n.get("numberOfOrders") or 0),
                "ltv": float((n.get("amountSpent") or {}).get("amount") or 0),
                "tags": ",".join(n.get("tags") or []),
                "last_order": ((n.get("lastOrder") or {}).get("createdAt") or "")[:10],
            }
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]; time.sleep(0.2)
        else:
            return

UNFULFILLED_Q = """
query($cursor: String) {
  orders(first: 100, after: $cursor, query: "fulfillment_status:unfulfilled financial_status:paid created_at:>=2026-03-23", sortKey: CREATED_AT) {
    edges { node { name createdAt customer { id email phone } } }
    pageInfo { hasNextPage endCursor }
  }
}"""

def orders_unfulfilled():
    """Yield paid-but-unfulfilled orders (the CS ops concern: customer paid, still waiting)."""
    cursor = None
    while True:
        j = gql(UNFULFILLED_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("orders")
        if not data:
            return
        for e in data["edges"]:
            n = e["node"]; c = n.get("customer") or {}
            yield {"order": n.get("name"), "created": (n.get("createdAt") or "")[:10],
                   "cid": c.get("id"), "email": (c.get("email") or "").strip(), "phone": (c.get("phone") or "").strip()}
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]; time.sleep(0.3)
        else:
            return

def orders_last_12mo():
    """Yield each order (created_at, customer{...}, [product titles]) for the last 12 months."""
    cursor = None
    while True:
        j = gql(ORDERS_Q, {"cursor": cursor})
        data = (j.get("data") or {}).get("orders")
        if not data:
            return
        for e in data["edges"]:
            n = e["node"]
            yield {
                "created_at": n.get("createdAt", ""),
                "customer": n.get("customer") or {},
                "products": [li["node"]["title"] for li in (n.get("lineItems") or {}).get("edges", [])],
            }
        if data["pageInfo"]["hasNextPage"]:
            cursor = data["pageInfo"]["endCursor"]
            time.sleep(0.2)
        else:
            return
