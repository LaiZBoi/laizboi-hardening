# Client St0r Auto-Update System

> **Update execution is opt-in and disabled by default.** Fresh installs ship with
> `AUTO_UPDATE_ENABLED=False`. The server will **not** download or execute update
> scripts from GitHub unless an admin explicitly sets `AUTO_UPDATE_ENABLED=True`
> in `.env` (or `/etc/clientst0r/.env` on VPS).
>
> For production VPS deployments, prefer **manual updates** after reviewing the
> diff and taking a backup — see [`docs/deployment-vps.md`](docs/deployment-vps.md).

## What is disabled by default

| Action | Default behavior |
|--------|------------------|
| Web “Apply Update” button | Blocked (403) |
| `manage.py auto_update` (without `--check-only`) | Refused |
| `manage.py check_updates --apply` | Refused |
| Scheduler GitHub update checks | Skipped |
| `scripts/auto_update.sh` | Logs disabled message, exits |
| `scripts/check_update_trigger.sh` | Removes trigger, does not run update |
| Background GitHub polling in Settings → Updates | Off until manual check |

## What still works without opt-in

- **Manual update checks** — superuser button at Settings → Updates, or `manage.py auto_update --check-only` / `git fetch`
- **Manual deployment** — `git pull`, `migrate`, `collectstatic`, restart gunicorn (see VPS guide)
- **Reading** release notes and version info after a deliberate check

## When to enable automatic execution

Set `AUTO_UPDATE_ENABLED=True` only if **all** of the following are true:

1. You trust the configured `GITHUB_REPO_OWNER` / `GITHUB_REPO_NAME` and branch.
2. You accept the server downloading `deploy/update_instructions.sh` from GitHub and executing it as the app user.
3. You have backups and a rollback plan.
4. Passwordless sudo for service restarts is configured (web updates on bare metal).

```env
AUTO_UPDATE_ENABLED=True
```

Restart gunicorn after changing `.env`.

## Opt-in paths (all require `AUTO_UPDATE_ENABLED=True`)

### Web UI

Settings → Updates → **Apply Update** (superuser only).

### Django management commands

```bash
python manage.py auto_update          # runs scripts/auto_update.sh
python manage.py check_updates --apply
```

### Shell script

```bash
/opt/clientst0r/scripts/auto_update.sh
```

The script loads `.env` from the project directory or `/etc/clientst0r/.env`. If disabled, it logs:

```text
Auto-update execution is disabled. Set AUTO_UPDATE_ENABLED=True to opt in.
```

### Systemd timer (optional)

```bash
cd /opt/clientst0r   # or your install path
./scripts/install_auto_update.sh
```

This installs `clientst0r-auto-update.service` + timer. The service loads `EnvironmentFile=-/etc/clientst0r/.env` (or project `.env`). **The timer will not apply updates until you opt in.**

```bash
sudo systemctl enable clientst0r-auto-update.timer
sudo systemctl start clientst0r-auto-update.timer
```

Disable the timer anytime:

```bash
sudo systemctl disable --now clientst0r-auto-update.timer
```

## Recommended production workflow (manual)

```bash
sudo systemctl stop clientst0r
cd /opt/clientst0r
git fetch origin
git pull --ff-only origin main
venv/bin/pip install -r requirements.txt
set -a && source /etc/clientst0r/.env && set +a
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py collectstatic --noinput
sudo systemctl start clientst0r
```

This fork does not support container deployment — use manual VPS updates in `docs/deployment-vps.md`.

## Check-only (always allowed)

```bash
python manage.py auto_update --check-only
python manage.py check_updates
git fetch origin main
git log HEAD..origin/main
```

## Logging

| Log | Path |
|-----|------|
| Shell auto-update | `/var/log/clientst0r/auto-update.log` |
| Triggered updates | `/var/log/clientst0r/triggered-update.log` |
| Systemd service | `journalctl -u clientst0r-auto-update.service` |

## Security notes

- Auto-update requires **minimal sudo** for `systemctl restart` on Client St0r units only (`/etc/sudoers.d/clientst0r-auto-update`).
- Never run `auto_update.sh` as root.
- Review every update diff before enabling execution on sensitive MSP data.

## Uninstall optional auto-update timer

```bash
sudo systemctl disable --now clientst0r-auto-update.timer
sudo rm /etc/systemd/system/clientst0r-auto-update.{service,timer}
sudo rm /etc/sudoers.d/clientst0r-auto-update
sudo systemctl daemon-reload
```

## FAQ

**Q: Will I still see available updates in the UI?**  
A: Yes, after you click **Check for Updates** or when automatic polling is enabled via `AUTO_UPDATE_ENABLED=True`.

**Q: Does this fork support container deployment?**  
A: No. This fork is VPS-only. Use manual updates documented in `docs/deployment-vps.md`.

**Q: Can I disable everything again?**  
A: Set `AUTO_UPDATE_ENABLED=False`, disable the systemd timer, and use manual updates.

## Related docs

- [`docs/deployment-vps.md`](docs/deployment-vps.md) — recommended VPS production guide
- [`docs/security-hardening.md`](docs/security-hardening.md) — baseline settings
- [`docs/outbound-network-calls.md`](docs/outbound-network-calls.md) — GitHub egress when checking/applying
