#!/usr/bin/env bash
# Nightly platform reconciliation: live Shopify bulk pull + this morning's live DaySmart/HKTV
# downloads -> reconcile rosetta inventory + refresh per-platform status fields.
set -uo pipefail
LOG=/root/rosetta-ims/logs/reconcile.log; mkdir -p /root/rosetta-ims/logs
ts(){ date -u +%FT%TZ; }
{
echo "[$(ts)] === reconcile start ==="
cd /root/algo-dashboard/backend
docker compose run --rm -T --no-deps web python - < /root/recon/shopify_bulk_pull.py || { echo "[$(ts)] shopify pull FAILED"; exit 1; }
docker compose run --rm -T --no-deps web sh -c "cd /app && python manage.py shell" < /root/recon/extract_platform_items_live.py || { echo "[$(ts)] extract FAILED"; exit 1; }
docker cp /root/algo-dashboard/backend/exports/platform_items_fresh.json backend-api-1:/tmp/platform_items_fresh.json
docker exec -i -e APPLY=1 backend-api-1 python < /root/recon/reconcile_platform_skus.py || { echo "[$(ts)] reconcile FAILED"; exit 1; }
echo "[$(ts)] === reconcile done ==="
} >> "$LOG" 2>&1
