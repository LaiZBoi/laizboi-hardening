# VPS Production Deployment

Bare-metal install on Ubuntu/Debian. For **Docker Compose**, see [`docs/docker.md`](docker.md) instead. Client St0r may store MSP documentation, passwords, client records, API credentials, tickets, and operational data — use security-first defaults and keep gunicorn on **localhost only** behind Apache or Nginx.

Related docs:

- `docs/security-hardening.md` — baseline security settings
- `docs/outbound-network-calls.md` — outbound integration inventory
- `AUTO_UPDATE.md` — opt-in update execution only
- `SECURITY.md` — vulnerability disclosure

## Assumptions

- Ubuntu 22.04/24.04 LTS or Debian 12+ on a public VPS
- Root or sudo access for initial setup
- A domain name you control (e.g. `psa.laizboi.com`)
- You will **not** expose gunicorn (`127.0.0.1:8000`) to the internet
- Automatic update execution stays **disabled** unless you explicitly opt in

## Architecture

```text
Internet → Apache or Nginx (443/80, TLS) → 127.0.0.1:8000 (gunicorn) → MariaDB (127.0.0.1)
```

**Apache** — use when ports 80/443 are already owned by Apache (other sites can stay on different `ServerName` values). Set `PRIVATE_FILE_SERVER=apache` and use `deploy/apache-clientst0r.conf`.

**Nginx** — use on a greenfield VPS with no existing web server. Set `PRIVATE_FILE_SERVER=nginx` (default) and use `deploy/nginx-clientst0r.conf`.

## Recommended paths

| Path | Purpose |
|------|---------|
| `/opt/clientst0r` | Application code + Python virtualenv |
| `/etc/clientst0r/.env` | Secrets (mode `600`, owned by `root`) |
| `/var/lib/clientst0r/uploads` | Document/vault attachments (`UPLOAD_ROOT`) |
| `/opt/clientst0r/media` | Media files |
| `/opt/clientst0r/static_collected` | `collectstatic` output |
| `/var/log/itdocs` | Application + gunicorn logs |
| `/var/backups/clientst0r` | Local backup staging |

## DNS

Create an **A record** (and optional **AAAA** for IPv6) pointing your hostname to the VPS public IP:

```text
psa.laizboi.com  →  A  →  203.0.113.10
```

Wait for DNS propagation before running Certbot.

## VPS specifications

| Size | vCPU | RAM | Disk | Notes |
|------|------|-----|------|-------|
| Minimum | 2 | 4 GB | 40 GB SSD | Small team, light use |
| Recommended | 4 | 8 GB | 80 GB SSD | Production MSP workload |

Ensure the provider allows outbound HTTPS (integrations, manual update checks).

## 1. System packages

```bash
sudo apt update
sudo apt install -y \
  python3.12 python3.12-venv python3.12-dev \
  mariadb-server git build-essential ufw \
  libmariadb-dev pkg-config curl logrotate fail2ban
```

Install **one** reverse-proxy stack:

```bash
# Apache (when Apache already serves other sites on this VPS)
sudo apt install -y apache2 libapache2-mod-xsendfile python3-certbot-apache

# — OR — Nginx (greenfield VPS)
# sudo apt install -y nginx certbot python3-certbot-nginx
```

```bash
sudo systemctl enable --now mariadb
sudo mysql_secure_installation
```

## 2. Create `clientst0r` user and directories

```bash
sudo useradd --system --home /opt/clientst0r --shell /usr/sbin/nologin clientst0r
sudo mkdir -p /opt/clientst0r /etc/clientst0r \
  /var/lib/clientst0r/uploads /var/log/itdocs /var/log/clientst0r \
  /var/backups/clientst0r
sudo chown -R clientst0r:clientst0r /opt/clientst0r /var/lib/clientst0r /var/log/itdocs /var/log/clientst0r
sudo chmod 750 /etc/clientst0r
```

## 3. Clone repository

```bash
sudo -u clientst0r git clone https://github.com/LaiZBoi/laizboi-hardening.git /opt/clientst0r
cd /opt/clientst0r
```

## 4. Python virtualenv and dependencies

```bash
sudo -u clientst0r python3.12 -m venv /opt/clientst0r/venv
sudo -u clientst0r /opt/clientst0r/venv/bin/pip install --upgrade pip
sudo -u clientst0r /opt/clientst0r/venv/bin/pip install -r /opt/clientst0r/requirements.txt
```

## 5. MariaDB

```bash
sudo mysql -e "
CREATE DATABASE clientst0r CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'clientst0r'@'localhost' IDENTIFIED BY 'REPLACE_WITH_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON clientst0r.* TO 'clientst0r'@'localhost';
FLUSH PRIVILEGES;
"
```

Confirm MariaDB is not exposed publicly (`bind-address = 127.0.0.1` in MariaDB config).

## 6. Generate secrets

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
python3 -c "import secrets, base64; print('APP_MASTER_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python3 -c "import secrets; print('API_KEY_SECRET=' + secrets.token_urlsafe(64))"
```

**`APP_MASTER_KEY` is critical.** Vault ciphertext depends on it. Back up `/etc/clientst0r/.env` with your database. Losing the key means vault secrets **cannot** be decrypted.

## 7. Environment file (`/etc/clientst0r/.env`)

```bash
sudo cp /opt/clientst0r/.env.example /etc/clientst0r/.env
sudo nano /etc/clientst0r/.env
sudo chmod 600 /etc/clientst0r/.env
sudo chown root:root /etc/clientst0r/.env
```

Minimum production values:

```env
DEBUG=False
SECRET_KEY=<generated>
APP_MASTER_KEY=<generated>
API_KEY_SECRET=<generated>

DB_ENGINE=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=clientst0r
DB_USER=clientst0r
DB_PASSWORD=<strong>

ALLOWED_HOSTS=psa.laizboi.com
CSRF_TRUSTED_ORIGINS=https://psa.laizboi.com
BASE_URL=https://psa.laizboi.com

SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
REQUIRE_2FA=True

UPLOAD_ROOT=/var/lib/clientst0r/uploads
PRIVATE_FILE_SERVER=apache

AUTO_UPDATE_ENABLED=False
BETA_UPSTREAM_URL=
BETA_ADMIN_EMAIL=
HIBP_ENABLED=False
```

Optional symlink for convenience:

```bash
sudo ln -sf /etc/clientst0r/.env /opt/clientst0r/.env
```

Load for one-off commands:

```bash
set -a && source /etc/clientst0r/.env && set +a
```

## 8. File permissions

```bash
sudo chown -R clientst0r:clientst0r /opt/clientst0r /var/lib/clientst0r /var/log/itdocs /var/log/clientst0r
sudo find /opt/clientst0r -type d -exec chmod 750 {} \;
sudo find /opt/clientst0r -type f -exec chmod 640 {} \;
sudo chmod 750 /opt/clientst0r/venv/bin/*
```

The `clientst0r` user must write to `static_collected/`, `UPLOAD_ROOT` (`/var/lib/clientst0r/uploads`), and `/var/log/itdocs`.

## 9. Migrations, static files, superuser

```bash
cd /opt/clientst0r
set -a && source /etc/clientst0r/.env && set +a

sudo -u clientst0r venv/bin/python manage.py migrate --noinput
sudo -u clientst0r venv/bin/python manage.py collectstatic --noinput
sudo -u clientst0r venv/bin/python manage.py createsuperuser
```

## 10. systemd — main application

```bash
sudo cp /opt/clientst0r/deploy/clientst0r.service /etc/systemd/system/clientst0r.service
sudo systemctl daemon-reload
sudo systemctl enable --now clientst0r
```

`deploy/clientst0r.service` binds gunicorn to **`127.0.0.1:8000`** only and loads `/etc/clientst0r/.env`.

### Task scheduler (optional but recommended)

```bash
sudo cp /opt/clientst0r/deploy/clientst0r-scheduler.service /etc/systemd/system/
sudo cp /opt/clientst0r/deploy/clientst0r-scheduler.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clientst0r-scheduler.timer
```

## 11. Reverse proxy

Gunicorn listens on **`127.0.0.1:8000` only**. Choose Apache or Nginx — do not install both as public listeners on the same ports.

### 11a. Apache (existing Apache on 80/443)

For a VPS that already runs Apache for other sites (e.g. another web app on a different hostname):

```bash
sudo a2enmod proxy proxy_http headers ssl xsendfile
sudo cp /opt/clientst0r/deploy/apache-clientst0r.conf /etc/apache2/sites-available/clientst0r.conf
# Edit ServerName if not psa.laizboi.com
sudo a2ensite clientst0r
sudo apache2ctl configtest
sudo systemctl reload apache2
```

`deploy/apache-clientst0r.conf` is preconfigured for **`psa.laizboi.com`**. Apache proxies to `http://127.0.0.1:8000` and uses **mod_xsendfile** for private attachment downloads. Set `PRIVATE_FILE_SERVER=apache` in `/etc/clientst0r/.env`.

Confirm your existing sites are unaffected:

```bash
sudo apache2ctl -S
```

### 11b. Nginx (greenfield VPS)

```bash
sudo cp /opt/clientst0r/deploy/nginx-clientst0r.conf /etc/nginx/sites-available/clientst0r
# Edit server_name if needed
sudo nano /etc/nginx/sites-available/clientst0r
sudo ln -sf /etc/nginx/sites-available/clientst0r /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Set `PRIVATE_FILE_SERVER=nginx` in `/etc/clientst0r/.env` (this is the default).

## 12. HTTPS (Certbot)

**Apache:**

```bash
sudo certbot --apache -d psa.laizboi.com
```

After Certbot creates the `:443` vhost, ensure the SSL block also includes:

```apache
XSendFile On
XSendFilePath /var/lib/clientst0r/uploads
RequestHeader set X-Forwarded-Proto "https"
```

Then `sudo systemctl reload apache2`.

**Nginx:**

```bash
sudo certbot --nginx -d psa.laizboi.com
```

Verify `SECURE_SSL_REDIRECT=True` and `CSRF_TRUSTED_ORIGINS` match your HTTPS origin.

## 13. UFW firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
# Use the profile that matches your web server:
sudo ufw allow 'Apache Full'
# sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status verbose
```

Only **SSH**, **HTTP**, and **HTTPS** should be public.

## 14. fail2ban

SSH jail (default):

```bash
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
sudo systemctl enable --now fail2ban
```

Optional Client St0r application jail:

```bash
sudo cp /opt/clientst0r/deploy/clientst0r-fail2ban-filter.conf /etc/fail2ban/filter.d/clientst0r.conf
sudo bash -c 'cat /opt/clientst0r/deploy/clientst0r-fail2ban-jail.conf >> /etc/fail2ban/jail.local'
sudo systemctl restart fail2ban
sudo fail2ban-client status clientst0r
```

See `deploy/FAIL2BAN_SETUP.md` for details.

## 15. logrotate

```bash
sudo cp /opt/clientst0r/deploy/logrotate-clientst0r /etc/logrotate.d/clientst0r
sudo logrotate -d /etc/logrotate.d/clientst0r
```

## 16. Backups

Back up **database**, **uploads/media/static**, and **`/etc/clientst0r/.env`** together.

### Database

```bash
sudo mkdir -p /var/backups/clientst0r
set -a && source /etc/clientst0r/.env && set +a
mariadb-dump -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" \
  | gzip > /var/backups/clientst0r/clientst0r-$(date +%F).sql.gz
```

### Files

```bash
sudo tar czf /var/backups/clientst0r/files-$(date +%F).tgz \
  /var/lib/clientst0r/uploads \
  /opt/clientst0r/media \
  /opt/clientst0r/static_collected
```

### Off-site copy

```bash
gpg --symmetric --cipher-algo AES256 /var/backups/clientst0r/clientst0r-$(date +%F).sql.gz
# scp/rsync to remote storage
```

**Test a full restore** on a throwaway VM before relying on backups for production client data.

## 17. Restore

```bash
sudo systemctl stop clientst0r

set -a && source /etc/clientst0r/.env && set +a
gunzip -c /var/backups/clientst0r/clientst0r-YYYY-MM-DD.sql.gz \
  | mariadb -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME"

sudo tar xzf /var/backups/clientst0r/files-YYYY-MM-DD.tgz -C /

# Restore /etc/clientst0r/.env from secure backup if needed
sudo systemctl start clientst0r
```

Confirm vault entries decrypt, uploads open, and login works.

## 18. Safe manual updates

`AUTO_UPDATE_ENABLED=False` by default. Update deliberately:

```bash
sudo systemctl stop clientst0r
cd /opt/clientst0r
sudo -u clientst0r git fetch origin
sudo -u clientst0r git pull --ff-only origin main
sudo -u clientst0r venv/bin/pip install -r requirements.txt
set -a && source /etc/clientst0r/.env && set +a
sudo -u clientst0r venv/bin/python manage.py migrate --noinput
sudo -u clientst0r venv/bin/python manage.py collectstatic --noinput
sudo systemctl start clientst0r
```

Review the diff and take a backup before each update. See `AUTO_UPDATE.md` only if you intentionally enable scripted updates.

## 19. Validation

Run after install and after every change:

```bash
cd /opt/clientst0r
set -a && source /etc/clientst0r/.env && set +a

venv/bin/python manage.py check --deploy
venv/bin/python manage.py check_safe_deployment
venv/bin/python manage.py test core.tests.test_hardening_gates core.tests.test_updater -v2

systemctl status clientst0r
journalctl -u clientst0r -f

sudo apache2ctl configtest
# — or — sudo nginx -t
curl -I https://psa.laizboi.com/health/

# Gunicorn must be localhost-only
ss -tlnp | grep 8000

# Auto-update shell gate (should not download when disabled)
/opt/clientst0r/scripts/auto_update.sh
grep -i disabled /var/log/clientst0r/auto-update.log | tail -1
```

`check_safe_deployment` exits non-zero on FAIL.

## 20. Troubleshooting

| Symptom | Check |
|---------|-------|
| 502 Bad Gateway | `systemctl status clientst0r`; `journalctl -u clientst0r -n 50` |
| CSRF errors on login | `CSRF_TRUSTED_ORIGINS` includes `https://your-host` |
| Vault decrypt errors | `APP_MASTER_KEY` matches backup `.env` |
| DB connection refused | MariaDB running; `DB_HOST=127.0.0.1`; credentials in `.env` |
| Static files 404 | Re-run `collectstatic`; check Apache `Alias` or Nginx `alias` path |
| Attachments empty / 404 | `PRIVATE_FILE_SERVER` matches web server; Apache has `XSendFile On` on both :80 and :443 vhosts |
| Health check fails | `curl -I http://127.0.0.1:8000/health/` on the VPS |

```bash
# Django deploy checks
venv/bin/python manage.py check --deploy

# Security audit
venv/bin/python manage.py check_safe_deployment

# Apache syntax
sudo apache2ctl configtest

# Nginx syntax
sudo nginx -t

# Firewall
sudo ufw status verbose

# fail2ban
sudo fail2ban-client status
```
