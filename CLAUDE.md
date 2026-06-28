# Project conventions for AI assistants

This file is loaded automatically by AI assistants when working in this repo. Conventions below are **mandatory** unless the user explicitly overrides them in the conversation.

This is a **VPS-only fork** of Client St0r (`LaiZBoi/laizboi-hardening`). Do not assume upstream maintainer infrastructure, container deployment, or automatic production pushes.

## Deployment direction

This fork is **VPS-only**.

Do **not** add or reintroduce Docker, Docker Compose, Unraid, Nginx Proxy Manager, container nginx, container healthchecks, or container deployment docs unless the user explicitly reverses this decision.

## Canonical deployment path

The only supported deployment target is:

**Ubuntu/Debian VPS** + Python venv + **Gunicorn bound to `127.0.0.1:8000`** + Nginx reverse proxy + MariaDB + systemd + Certbot + UFW + fail2ban.

Recommended paths:

| Path | Purpose |
|------|---------|
| `/opt/clientst0r` | Application code + virtualenv |
| `/etc/clientst0r/.env` | Secrets and configuration |
| `/var/lib/clientst0r/uploads` | Uploads (`UPLOAD_ROOT`) |
| `/var/log/itdocs` | Application logs |

Primary systemd unit: `clientst0r.service` (see `deploy/clientst0r.service`).

## Canonical docs

When writing or updating deployment, security, or operations guidance, treat these as authoritative:

- `docs/deployment-vps.md` — production VPS install, backups, manual updates, validation
- `docs/security-hardening.md` — baseline security settings
- `docs/outbound-network-calls.md` — outbound integration inventory
- `AUTO_UPDATE.md` — opt-in update execution only

Do not point operators at container or NAS deployment guides — this fork ships VPS documentation only.

## Security defaults

These defaults are **mandatory** for this fork unless the user explicitly requests a change:

- `AUTO_UPDATE_ENABLED=False` — update execution is opt-in; no background download/execute of update scripts by default
- `BETA_UPSTREAM_URL=` blank — no beta signup forwarding off-instance by default
- `BETA_ADMIN_EMAIL=` blank — no beta signup notification email by default
- `HIBP_ENABLED=False` — HaveIBeenPwned checks are opt-in
- Outbound integrations should remain **privacy-safe by default**; document new egress in `docs/outbound-network-calls.md`

Shell/systemd/cron update paths (`scripts/auto_update.sh`, `scripts/check_update_trigger.sh`, auto-update units) must respect `AUTO_UPDATE_ENABLED` and load `.env` / `/etc/clientst0r/.env`.

## Roadmap discipline

**Every release that adds, extends, or completes a feature MUST update `docs/ROADMAP.md` in the same commit.**

Why: the roadmap is the single source of truth for shipped + in-progress + planned features. It's published in **four** surfaces (in-app HTML at `/core/roadmap/`, About-page card, GitHub, and a polling-friendly JSON feed at `/core/roadmap.json`). If it gets stale, all four surfaces lie.

How (manual sub-bullets):

- Annotate the matching sub-bullet with the version: `*(shipped v3.17.NNN)*` or `*(partial — X shipped v3.17.NNN; Y deferred)*`.
- For new feature requests not on the roadmap yet, ADD them as planned items (not implemented) before / alongside building them.
- Update the **Sizing table** at the bottom when adding new phases.
- Bump `config/version.py`, write a `CHANGELOG.md` entry, and update `docs/ROADMAP.md` — all in one commit.

How (phase-header status — required for the JSON feed):

- The JSON endpoint at `/core/roadmap.json` parses each `## Phase N — Title ...` header to compute status. Use **one of these** parseable status markers on the header line so the feed updates automatically:
  - `[planned]` — default (or no marker)
  - `[in progress]`
  - `[shipped — v3.17.NNN]` — preferred form for completed phases that mark the version
  - `[complete]` — preferred when the entire phase + all sub-phases are done
  - `**— shipped**` or `**— complete**` (legacy inline form, still parseable)
- When a phase **completes**, ALWAYS update the header marker. Otherwise the JSON feed shows it as planned and the website's polling won't reflect reality.
- Sub-phase items (under a phase) carry their own `*(shipped vN.N.N)*` annotations on the bullet — those are visible in the rendered HTML but don't appear in the JSON feed (which is per-phase, not per-bullet).

How (downstream consumers):

- External dashboards / status pages can poll `GET /core/roadmap.json`. Cached server-side; light to call. Response shape: `{generated_at, current_version, phase_count, shipped_count, phases: [{number, title, size, status, version}, ...]}`. Status enum: `planned` / `in_progress` / `shipped` / `complete`.

## Release pattern

Every release commit ships:

1. The actual feature change
2. `config/version.py` bumped (string + patch int both)
3. `CHANGELOG.md` entry at the top with `## [N.N.N] - YYYY-MM-DD`
4. `docs/ROADMAP.md` updated if the release touches a roadmap item

Do **not** push to `origin/main` unless the user explicitly asks. Do not assume post-commit/pre-push hooks or tag automation on the operator's machine.

## Wording for documentation

- **No competitor name-dropping** in roadmap or marketing prose. Don't position the project as "X parity" or "matches Y's wedge". Describe what the product does on its own merits.
- Integration listings (e.g. "We integrate with ConnectWise Manage, Autotask, etc.") ARE factual feature statements — keep those as-is.
- AI-assisted features must be explicitly tagged **OPTIONAL AI** in the roadmap and gated by `psa_ai_enabled`.
- Don't position planned items as fully implemented. Use "planned", "in progress", "extends X (shipped vN.N.N)" markers.
- VPS deployment docs should describe manual, reviewed updates — not automatic execution as the default path.

## Dev workflow

- Work in the **current repository/worktree** unless the user explicitly provides another path.
- Use **feature branches** for meaningful changes.
- Do **not** force-push, `reset --hard`, delete branches, or rewrite history without explicit user approval.
- Do **not** push to `origin/main` unless the user explicitly asks.
- Do **not** restart production services (`systemctl restart clientst0r`, gunicorn, nginx, etc.) unless the user explicitly asks.
- For VPS deployment, **prefer documenting commands** in `docs/deployment-vps.md` (or the relevant doc) instead of executing service changes automatically.
- Do not run destructive git commands without explicit user approval.

## Cleanup discipline

Before completing cleanup or deployment-related changes, search for stale references to:

`Docker`, `docker`, `compose`, `container`, `Unraid`, `unraid`, `appdata`, `Nginx Proxy Manager`, `APP_PORT`, `WEB_PORT`, `/mnt/user`

Remove or rewrite stale references unless they are unavoidable historical `CHANGELOG.md` entries. If historical entries remain, add or preserve a **current note** (see top of `CHANGELOG.md`) that this fork is VPS-only and container/Unraid deployment is not supported.

## Testing

- New models / views / API endpoints need a test in the matching app's `tests.py` (or `core/tests/` where appropriate).
- Run `manage.py test <app>` before committing — fail fast.
- View tests use `@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)` to dodge the 2FA + HTTPS-redirect middleware.
- Hardening-related commands and gates: `manage.py check_safe_deployment`, `core.tests.test_hardening_gates`, `core.tests.test_updater`.

## VPS validation (reference)

Operators validate production installs with:

```bash
python manage.py check --deploy
python manage.py check_safe_deployment
python manage.py test core.tests.test_hardening_gates core.tests.test_updater -v2
systemctl status clientst0r
nginx -t
curl -I https://<hostname>/health/
```

Document new validation steps in `docs/deployment-vps.md` when behavior changes.
