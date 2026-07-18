import json, time, requests
cfg = json.load(open("/app/integrations/downloaders/configs/shopify/config.json"))
shop = cfg["shop"]
base = f"https://{shop}" if "." in shop else f"https://{shop}.myshopify.com"
r = requests.post(f"{base}/admin/oauth/access_token",
                  data={"grant_type": "client_credentials",
                        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]}, timeout=30)
r.raise_for_status()
H = {"X-Shopify-Access-Token": r.json()["access_token"], "Content-Type": "application/json"}
api = f"{base}/admin/api/2024-10/graphql.json"
MUT = 'mutation { bulkOperationRunQuery(query: """{ products { edges { node { id title vendor productType status variants { edges { node { id sku barcode price title inventoryItem { unitCost { amount } } } } } } } } }""") { bulkOperation { id status } userErrors { field message } } }'
res = requests.post(api, headers=H, json={"query": MUT}, timeout=60).json()
errs = res.get("data", {}).get("bulkOperationRunQuery", {}).get("userErrors")
if errs: raise SystemExit(f"bulk start failed: {errs}")
op = {}
for _ in range(90):
    time.sleep(10)
    op = requests.post(api, headers=H, json={"query": "{ currentBulkOperation { status errorCode url objectCount } }"},
                       timeout=60).json()["data"]["currentBulkOperation"]
    if op["status"] in ("COMPLETED", "FAILED", "CANCELED"): break
assert op.get("status") == "COMPLETED", f"bulk op: {op}"
data = requests.get(op["url"], timeout=600)
open("/app/exports/shopify_bulk.jsonl", "wb").write(data.content)
print(f"shopify live bulk: {op['objectCount']} objects")
