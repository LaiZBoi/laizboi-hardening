# Running Client St0r with Docker

Docker Compose is the fastest way to run Client St0r on a fresh host — `app` + MariaDB start with one command. For bare-metal VPS installs (systemd + Apache/Nginx), see [`docs/deployment-vps.md`](deployment-vps.md).

Privacy-safe defaults apply in Docker too: `AUTO_UPDATE_ENABLED=False`, blank beta forwarding, `HIBP_ENABLED=False` unless you opt in.

---

## TL;DR

```bash
git clone -b main https://github.com/LaiZBoi/laizboi-hardening.git
cd laizboi-hardening
cp .env.example .env
# Edit .env — minimum: SECRET_KEY, APP_MASTER_KEY, API_KEY_SECRET,
# DB_ROOT_PASSWORD, DB_PASSWORD, ALLOWED_HOSTS
docker compose up -d --build
```

Open `http://localhost:8000` (or your hostname if using the proxy profile).

First boot runs migrations and `collectstatic` automatically. Set `DJANGO_SUPERUSER_*` in `.env` to bootstrap an admin on first boot.

```bash
docker compose logs -f app
```

---

## Requirements

- Docker Engine **24.0+**
- Docker Compose v2 (`docker compose`, not `docker-compose`)
- ~2 GB disk for image + ~1 GB for the database volume before data
- Ports: `8000` by default; with the Nginx profile also `80` / `443`

```bash
docker --version
docker compose version
```

---

## Production with Nginx (recommended)

For `psa.laizboi.com` (or your hostname), use the **proxy profile** so gunicorn is not published on the host:

```bash
docker compose -f docker-compose.yml -f docker-compose.proxy.yml \
  --profile proxy up -d --build
```

Or: `make docker-up-proxy`

This starts:

- `db` — MariaDB 10.11
- `app` — Django + gunicorn (internal network only)
- `nginx` — reverse proxy on ports 80/443

Configure `.env`:

```env
ALLOWED_HOSTS=psa.laizboi.com,localhost
CSRF_TRUSTED_ORIGINS=https://psa.laizboi.com
BASE_URL=https://psa.laizboi.com
PRIVATE_FILE_SERVER=nginx
SECURE_SSL_REDIRECT=True
```

Edit `docker/nginx/conf.d/clientst0r.conf` if your hostname differs. For TLS, place `cert.pem` and `key.pem` in `docker/nginx/ssl/` and uncomment the HTTPS server block in that file.

If you already run a reverse proxy on the host (Apache, Traefik, Caddy), **skip the proxy profile** and point it at `http://127.0.0.1:${WEB_PORT}`.

---

## Configuration

All settings come from `.env` (see `.env.example`).

**Required before first boot:**

```env
SECRET_KEY=...
APP_MASTER_KEY=...
API_KEY_SECRET=...
DB_ROOT_PASSWORD=...
DB_PASSWORD=...
ALLOWED_HOSTS=psa.laizboi.com,localhost
```

Generate secrets:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
python3 -c "import secrets, base64; print('APP_MASTER_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python3 -c "import secrets; print('API_KEY_SECRET=' + secrets.token_urlsafe(64))"
```

> **APP_MASTER_KEY is irreversible.** Back up `.env` with your database. Rotating the key makes vault entries unreadable.

**Docker-specific variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEB_PORT` | `8000` | Host port → gunicorn (omit with proxy override) |
| `NGINX_HTTP_PORT` | `80` | Nginx HTTP (proxy profile) |
| `NGINX_HTTPS_PORT` | `443` | Nginx HTTPS (proxy profile) |
| `CLIENTST0R_IMAGE` | *(build locally)* | Set to `ghcr.io/laizboi/laizboi-hardening:latest` to pull a published image |
| `DJANGO_SUPERUSER_*` | blank | Optional first-boot admin |

---

## Day-to-day commands

```bash
docker compose up -d              # or: make docker-up
docker compose down               # or: make docker-down
docker compose logs -f app        # or: make docker-logs
docker compose exec app bash       # or: make docker-shell
docker compose exec app python manage.py createsuperuser
docker compose exec app python manage.py check_safe_deployment
```

`make help` lists all targets.

---

## Profiles

| Profile | Command | Adds |
|---------|---------|------|
| *(default)* | `docker compose up -d` | `app` + `db` |
| `proxy` | `--profile proxy` (+ `docker-compose.proxy.yml` recommended) | Nginx on 80/443 |
| `cache` | `--profile cache` | Redis (only if you configure Django cache for it) |

---

## Persistent data

| Volume | Holds |
|--------|-------|
| `clientst0r-db-data` | MariaDB tables |
| `clientst0r-uploads` | Private attachments (`UPLOAD_ROOT`) |
| `clientst0r-media` | Other media files |
| `clientst0r-static` | `collectstatic` output |

Wipe everything (destructive): `docker compose down -v` or `make docker-down-clean`

---

## Backups

```bash
make docker-backup
```

Also archive upload and media volumes, and keep `.env` safe:

```bash
docker run --rm -v clientst0r-uploads:/data -v "$(pwd)/backups:/backup" \
  alpine tar czf /backup/uploads.tgz -C /data .
```

---

## Upgrading

```bash
git pull
docker compose build app    # or: docker compose pull if using CLIENTST0R_IMAGE
docker compose up -d
```

Migrations run on every container start via `docker-entrypoint.sh`. Container installs do **not** use the in-app auto-update script path — pull/rebuild deliberately. See `AUTO_UPDATE.md` if you intentionally enable update execution on a host install.

---

## Local development

```bash
make dev-up
```

Bind-mounts source, enables `gunicorn --reload`, `DEBUG=True`, defaults to SQLite. Comment `DB_ENGINE: sqlite3` in `docker-compose.dev.yml` to use MariaDB in dev.

---

## Validation

```bash
docker compose exec app python manage.py check --deploy
docker compose exec app python manage.py check_safe_deployment
curl -I http://localhost:8000/health/
```

With the proxy profile: `curl -I http://psa.laizboi.com/health/`

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `DB_ROOT_PASSWORD must be set` | Fill required vars in `.env` |
| **`app` unhealthy / nginx won't start** | See **Unhealthy app container** below |
| Healthcheck stuck on `starting` | First boot runs migrate + collectstatic (~90s). `docker compose logs -f app` |
| CSRF errors | `CSRF_TRUSTED_ORIGINS` must match your HTTPS origin |
| Attachments empty | `PRIVATE_FILE_SERVER=nginx` and proxy profile running |
| Vault decrypt errors | Restore original `APP_MASTER_KEY` from backup |

### Unhealthy app container

Docker marks `clientst0r-app` unhealthy when `/health/` does not return 200. Common causes:

1. **Missing secrets in `.env`** — all required in production:
   `SECRET_KEY`, `APP_MASTER_KEY`, `API_KEY_SECRET`, `DB_PASSWORD`, `DB_ROOT_PASSWORD`
2. **`ALLOWED_HOSTS` missing your public hostname** — use `psa.laizboi.com,localhost` (comma-separated; healthcheck uses the **first** host as the `Host` header)
3. **Migrations failed** — check `docker compose logs app --tail 100`

Diagnose on the VPS:

```bash
cd /opt/clientst0r
docker compose logs app --tail 100
docker inspect clientst0r-app --format='{{json .State.Health}}' | python3 -m json.tool
```

Quick recovery (after fixing `.env`):

```bash
cd /opt/clientst0r
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.proxy.yml \
  --profile proxy up -d --build --force-recreate
```

Test health manually inside the app container:

```bash
docker compose exec app /app/scripts/docker-healthcheck.sh && echo OK
```

---

## Layout

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage production image (non-root uid 1000) |
| `docker-compose.yml` | `app` + `db`; optional proxy/cache profiles |
| `docker-compose.proxy.yml` | Stops publishing gunicorn on the host |
| `docker-compose.dev.yml` | Dev bind-mount + reload |
| `docker-entrypoint.sh` | DB wait, migrate, collectstatic, optional superuser |
| `docker/nginx/` | Nginx config for proxy profile |
| `.github/workflows/docker-image.yml` | Build + publish to GHCR on push |
