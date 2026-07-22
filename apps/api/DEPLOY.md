# Deploying Rosetta IMS API on a DigitalOcean droplet (Docker)

This replaces the Fly.io backend. The Vercel frontend stays as-is — you only repoint
it at the new API URL (last step).

**Architecture:** `Caddy` (HTTPS, auto Let's Encrypt) → `api` (FastAPI/uvicorn) →
SQLite file on a mounted volume. CORS is handled by the app.

---

## 0. Prerequisites

- A droplet running Ubuntu 22.04/24.04. **Recommend ≥ 2 GB RAM** — AI extraction spikes
  memory, and the old 512 MB Fly machine was being OOM-killed (that was the real cause of
  the intermittent "CORS" failures). If you're on 1 GB, add swap (see §7).
- A **domain/subdomain** for the API (e.g. `api.your-domain.com`). HTTPS is mandatory
  because the Vercel site is HTTPS — browsers block an HTTP API from an HTTPS page.
- DNS: an **A record** for that subdomain pointing at the droplet's public IP.
- Ports **80** and **443** reachable (see firewall, §7).

## 1. Install Docker on the droplet

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"     # then log out/in so `docker` works without sudo
docker --version && docker compose version
```

## 2. Get the code onto the droplet

```bash
git clone <your-repo-url> rosetta-ims
cd rosetta-ims/backend
```
(Only the `backend/` folder is needed — the frontend is on Vercel.)

## 3. Configure environment + secrets

```bash
cp .env.example .env
nano .env                       # fill in every REQUIRED value
```

Must-set values:
- `API_DOMAIN` — your API subdomain (matches the DNS A record).
- `JWT_SECRET` — generate with `openssl rand -hex 32`.
- `ANTHROPIC_API_KEY` — for AI extraction/tagging/species.
- `ALLOWED_ORIGINS` — keep the Vercel + localhost defaults.

Google Sheets credential (mounted as a file, not inline):
```bash
mkdir -p secrets data
nano secrets/google-sa.json     # paste the service-account JSON
```
Leave `GOOGLE_SA_KEY_JSON` empty in `.env` — compose sets `GOOGLE_SA_KEY_PATH=/secrets/google-sa.json`.

> The values for `ANTHROPIC_API_KEY`, `GOOGLE_SA_KEY_JSON`, `RESEND_API_KEY`,
> `ROSETTA_TECH_API_KEY`, etc. are currently Fly secrets. Grab them from wherever they
> were originally stored (password manager / Fly was just holding copies).

## 4. Bring over the production database

**Option A — migrate the live data from Fly (recommended).** On a machine that still has
working Fly access:
```bash
# checkpoint WAL into the main file so the copy is consistent, then download it
fly ssh console -a rosetta-ims-api -C "python -c \"import sqlite3; sqlite3.connect('/data/ims.db').execute('PRAGMA wal_checkpoint(TRUNCATE)')\""
fly ssh sftp get /data/ims.db ./ims.db -a rosetta-ims-api
```
Copy that `ims.db` to the droplet and place it at `backend/data/ims.db`:
```bash
scp ./ims.db root@<droplet-ip>:/root/rosetta-ims/backend/data/ims.db
```

**Option B — start fresh.** Skip the copy. On first boot the app creates the schema and
seeds the default admin (`seph` / `rosetta2024`) automatically. You'd re-onboard data from
scratch and lose the existing verified products + audit trail, so prefer Option A.

## 5. Launch

```bash
docker compose up -d --build
docker compose ps           # both services should be "running"/"healthy"
docker compose logs -f api  # watch startup: "Uvicorn running on http://0.0.0.0:8080"
```
Caddy fetches the TLS cert on first request to `https://$API_DOMAIN` (DNS must already
resolve to the droplet). Watch `docker compose logs caddy` if the cert doesn't issue.

## 6. Verify

```bash
# health (public)
curl https://api.your-domain.com/health

# CORS preflight from the Vercel origin should return the allow-origin header
curl -si -X OPTIONS https://api.your-domain.com/v1/suppliers \
  -H "Origin: https://rosetta-ims.vercel.app" \
  -H "Access-Control-Request-Method: GET" | grep -i access-control-allow-origin

# login works (data migrated correctly)
curl -s -X POST https://api.your-domain.com/v1/auth/login \
  -H "Content-Type: application/json" -d '{"username":"seph","password":"rosetta2024"}'
```

## 7. Firewall, swap, backups

```bash
# firewall
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable

# (only if < 2 GB RAM) add 2 GB swap so a memory spike can't OOM-kill the app
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile \
  && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# nightly DB backup (the whole DB is one file)
# crontab -e:
# 0 3 * * * cp /root/rosetta-ims/backend/data/ims.db /root/ims-backups/ims-$(date +\%F).db
```

## 8. Point the Vercel frontend at the new API

In the Vercel project → **Settings → Environment Variables**, set:
```
VITE_API_URL = https://api.your-domain.com
```
…then **redeploy** the frontend. The frontend appends `/v1` through its shared API config.

## 9. Decommission Fly

Once the droplet is verified and the frontend is repointed, stop/destroy the Fly app:
`fly apps destroy rosetta-ims-api`. Keep a copy of the migrated `ims.db` first.

---

## Day-2 operations

| Task | Command (run in `backend/`) |
|------|------|
| Update to latest code | `git pull && docker compose up -d --build` |
| View logs | `docker compose logs -f api` |
| Restart | `docker compose restart api` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Shell into the app | `docker compose exec api sh` |
| Back up DB now | `cp data/ims.db ~/ims-$(date +%F).db` |
| Raise throughput | set `WEB_CONCURRENCY=2` in `.env`, then `docker compose up -d` |
