# Deploying Rosetta IMS API on the DigitalOcean droplet (Docker)

The backend runs on the DigitalOcean droplet at `root@178.128.127.5` using Docker
Compose from `/root/rosetta-ims/backend`. The Vercel frontend stays separate and
auto-deploys from its existing Vercel Git integration.

**Architecture:** `Caddy` (HTTPS, auto Let's Encrypt) → `api` (FastAPI/uvicorn) →
SQLite file on a mounted volume. CORS is handled by the app.

---

## 0. Prerequisites

- A droplet running Ubuntu 22.04/24.04. **Recommend >= 2 GB RAM** because AI extraction
  can spike memory. If you're on 1 GB, add swap (see §8).
- A **domain/subdomain** for the API. The current host uses
  `https://178.128.127.5.nip.io`. HTTPS is mandatory
  because the Vercel site is HTTPS — browsers block an HTTP API from an HTTPS page.
- DNS: an **A record** for that subdomain pointing at the droplet's public IP.
- Ports **80** and **443** reachable (see firewall, §8).

## 1. Install Docker on the droplet

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"     # then log out/in so `docker` works without sudo
docker --version && docker compose version
```

## 2. Get the code onto the droplet

```bash
mkdir -p /root/rosetta-ims/backend
rsync -az apps/api/ root@178.128.127.5:/root/rosetta-ims/backend/
ssh root@178.128.127.5
cd /root/rosetta-ims/backend
```

The production directory is not a Git checkout today. Deployment syncs the `apps/api/`
folder into `/root/rosetta-ims/backend` and preserves `.env`, `data/`, and `secrets/`.

## 3. Configure environment + secrets

```bash
cp .env.example .env
nano .env                       # fill in every REQUIRED value
```

Must-set values in `/root/rosetta-ims/backend/.env`:
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

> The GitHub Action does not sync `.env`, `data/`, or `secrets/`. Keep runtime secrets
> on the droplet or in the password manager, not in Git.

## 4. Production database

SQLite lives at:

```bash
/root/rosetta-ims/backend/data/ims.db
```

To restore a database copy, place it there:

```bash
scp ./ims.db root@178.128.127.5:/root/rosetta-ims/backend/data/ims.db
```

To start fresh, skip the copy. On first boot the app creates the schema and
seeds the default admin (`seph` / `rosetta2024`) automatically. You'd re-onboard data from
scratch and lose the existing verified products + audit trail.

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

## 7. GitHub Actions auto-deploy

The checked-in workflow is:

```text
.github/workflows/deploy-api-droplet.yml
```

It runs on:

- pushes to `main` that touch `apps/api/**` or the workflow file
- manual `workflow_dispatch`

The workflow:

1. checks out the repo
2. runs API smoke tests
3. rsyncs `apps/api/` to `/root/rosetta-ims/backend`
4. preserves `.env`, `data/`, `secrets/`, cache files, and local DB/runtime files
5. runs `docker compose up -d --build api caddy`
6. verifies the container health endpoint from inside the API container

Required GitHub Actions secret:

```text
DROPLET_SSH_PRIVATE_KEY
```

This is a private SSH key whose public key is present in
`root@178.128.127.5:/root/.ssh/authorized_keys`.

## 8. Firewall, swap, backups

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

## 9. Vercel frontend

The frontend is already connected to Vercel and auto-deploys on push. In the
Vercel project -> **Settings -> Environment Variables**, the backend origin should be:

```
VITE_API_URL = https://178.128.127.5.nip.io
```

The frontend appends `/v1` through its shared API config.

---

## Day-2 operations

| Task | Command (run in `/root/rosetta-ims/backend`) |
|------|------|
| Deploy latest code | push to `main`, or run the GitHub Action manually |
| Manual update from an already-synced directory | `docker compose up -d --build api caddy` |
| View logs | `docker compose logs -f api` |
| Restart | `docker compose restart api` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Shell into the app | `docker compose exec api sh` |
| Back up DB now | `cp data/ims.db ~/ims-$(date +%F).db` |
| Raise throughput | set `WEB_CONCURRENCY=2` in `.env`, then `docker compose up -d` |
