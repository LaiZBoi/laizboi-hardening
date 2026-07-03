# Project conventions for AI assistants

This file is loaded automatically by AI assistants when working in this repo. Conventions below are **mandatory** unless the user explicitly overrides them in the conversation.

This is a **self-hosted fork** of Client St0r (`LaiZBoi/laizboi-hardening`). Do not assume upstream maintainer infrastructure or automatic production pushes.

## Deployment direction

Two supported production paths:

1. **Docker / Docker Compose** — [`docs/docker.md`](docs/docker.md) — fastest on a fresh host; `app` + MariaDB; optional Nginx proxy profile.
2. **Bare-metal VPS** — [`docs/deployment-vps.md`](docs/deployment-vps.md) — systemd + Gunicorn on `127.0.0.1:8000` + Apache or Nginx + MariaDB.

Do **not** add Unraid, Nginx Proxy Manager, or NAS-specific deployment docs unless the user explicitly asks.

## Canonical paths

| Path | Docker | VPS |
|------|--------|-----|
| Application | `/app` in container | `/opt/clientst0r` |
| Secrets | `.env` in project root | `/etc/clientst0r/.env` |
| Uploads | volume `clientst0r-uploads` | `/var/lib/clientst0r/uploads` |

Reverse proxy configs:

- `docker/nginx/conf.d/clientst0r.conf` — Nginx inside Compose (`PRIVATE_FILE_SERVER=nginx`)
- `deploy/apache-clientst0r.conf` — Apache on VPS (`PRIVATE_FILE_SERVER=apache`)
- `deploy/nginx-clientst0r.conf` — Nginx on VPS (`PRIVATE_FILE_SERVER=nginx`)

## Canonical docs

- `docs/docker.md` — Docker Compose install, profiles, backups, upgrades
- `docs/deployment-vps.md` — bare-metal VPS install
- `docs/security-hardening.md` — baseline security settings
- `docs/outbound-network-calls.md` — outbound integration inventory
- `AUTO_UPDATE.md` — opt-in update execution only

## Security defaults

These defaults are **mandatory** unless the user explicitly requests a change:

- `AUTO_UPDATE_ENABLED=False` — update execution is opt-in
- `BETA_UPSTREAM_URL=` blank
- `BETA_ADMIN_EMAIL=` blank
- `HIBP_ENABLED=False` — HaveIBeenPwned checks are opt-in
- Document new egress in `docs/outbound-network-calls.md`

Shell/systemd/cron update paths must respect `AUTO_UPDATE_ENABLED` and load `.env` / `/etc/clientst0r/.env`.

## Roadmap discipline

**Every release that adds, extends, or completes a feature MUST update `docs/ROADMAP.md` in the same commit.**

See existing `CLAUDE.md` / project rules for phase-header markers, version annotations, and JSON feed parsing.

## Release pattern

Every release commit ships:

1. The actual feature change
2. `config/version.py` bumped
3. `CHANGELOG.md` entry at the top
4. `docs/ROADMAP.md` updated if the release touches a roadmap item

Do **not** push to `origin/main` unless the user explicitly asks.

## Dev workflow

- Work in the **current repository/worktree** unless the user provides another path.
- Use **feature branches** for meaningful changes.
- Do **not** force-push or rewrite history without explicit user approval.
- Do **not** restart production services unless the user explicitly asks.

## Testing

- New models / views / API endpoints need tests in the matching app's `tests.py`.
- Run `manage.py test <app>` before committing.
- View tests use `@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)`.
- Hardening: `check_safe_deployment`, `core.tests.test_hardening_gates`, `core.tests.test_updater`.

## Validation (reference)

**Docker:**

```bash
docker compose exec app python manage.py check --deploy
docker compose exec app python manage.py check_safe_deployment
curl -I http://localhost:8000/health/
```

**VPS:**

```bash
python manage.py check --deploy
python manage.py check_safe_deployment
systemctl status clientst0r
curl -I https://<hostname>/health/
```
