# Security Hardening

Client St0r can store passwords, client records, network documentation, and operational notes. Run it with privacy-safe defaults and review every integration before enabling it.

## Required baseline

- Never run `DEBUG=True` on a public or shared network.
- Require HTTPS through a reverse proxy and keep `SECURE_SSL_REDIRECT=True`.
- Keep `SESSION_COOKIE_SECURE=True` and `CSRF_COOKIE_SECURE=True`.
- Set `REQUIRE_2FA=True` for all users.
- Use unique high-entropy values for `SECRET_KEY`, `APP_MASTER_KEY`, and `API_KEY_SECRET`.
- Back up `.env`; encrypted vault data depends on `APP_MASTER_KEY`.
- Leave `AUTO_UPDATE_ENABLED=False` unless you intentionally trust update scripts from the configured GitHub repository.
- Leave `BETA_UPSTREAM_URL=` blank unless you intentionally forward beta signup data.
- Leave `HIBP_ENABLED=False` unless you intentionally enable HaveIBeenPwned breach checks.

## Outbound integrations

Review `docs/outbound-network-calls.md` before enabling any external service. Pay special attention to:

- AI providers, because prompts may include ticket/documentation context.
- Maps and property APIs, because addresses and coordinates may leave the instance.
- RMM, PSA, accounting, payment, distributor, email, SMS, and webhook integrations.
- HaveIBeenPwned checks (opt-in via `HIBP_ENABLED=True`), which use k-anonymity range queries but still require outbound requests.

## Reverse proxy headers

Django reads `X-Forwarded-Proto` via `SECURE_PROXY_SSL_HEADER` in production. Forward the original request context from **Nginx** or **Apache**.

**Nginx:**

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $host;
proxy_set_header X-Forwarded-Port $server_port;
client_max_body_size 100m;
```

**Apache** (see `deploy/apache-clientst0r.conf`):

```apache
RequestHeader set X-Forwarded-Proto "https"
RequestHeader set X-Forwarded-Host "psa.laizboi.com"
```

For private attachment downloads, set `PRIVATE_FILE_SERVER=apache` and enable `mod_xsendfile` with `XSendFile On` and `XSendFilePath` pointing at `UPLOAD_ROOT`. For Nginx, use `PRIVATE_FILE_SERVER=nginx` (default) with the internal `/internal_uploads/` location.

Set `ALLOWED_HOSTS` to the public hostname and `CSRF_TRUSTED_ORIGINS` to the full HTTPS origin, for example:

```env
ALLOWED_HOSTS=psa.laizboi.com
CSRF_TRUSTED_ORIGINS=https://psa.laizboi.com
```

## Firewall recommendations

- Expose only the reverse proxy to the internet.
- Do not expose MariaDB publicly.
- Restrict SSH and VPS admin access to trusted IPs or VPN.
- Block direct public access to gunicorn (`127.0.0.1:8000`); only Apache or Nginx should be public.
- Allow outbound traffic only to integrations you actually use where your firewall supports egress rules.

## Backup discipline

- Back up database, media, uploads, static files, logs, and `.env` together.
- Encrypt off-host backups.
- Test restore regularly into a separate environment.
- Confirm vault entries decrypt after restore.
- Confirm uploaded files and media previews survive restore.

## Updates

Prefer manual updates on your VPS — see [`docs/deployment-vps.md`](deployment-vps.md):

```bash
sudo systemctl stop clientst0r
cd /opt/clientst0r
git pull --ff-only origin main
set -a && source /etc/clientst0r/.env && set +a
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py collectstatic --noinput
sudo systemctl start clientst0r
```

Take a backup first and review changes that touch authentication, vault encryption, migrations, update logic, integrations, or outbound network behavior.

## Deployment audit

After configuring `.env`, run:

```bash
python manage.py check_safe_deployment
```

Exits non-zero on FAIL (missing secrets, `DEBUG=True`, wildcard `ALLOWED_HOSTS`, etc.). See [`docs/deployment-vps.md`](deployment-vps.md) for full VPS validation commands.
