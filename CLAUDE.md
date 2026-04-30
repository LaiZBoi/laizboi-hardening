# Project conventions for AI assistants

This file is loaded automatically by AI assistants (Claude Code etc.) when working in this repo. Conventions below are **mandatory** unless the user explicitly overrides them in the conversation.

## Roadmap discipline

**Every release that adds, extends, or completes a feature MUST update `docs/ROADMAP.md` in the same commit.**

Why: the roadmap is the single source of truth for shipped + in-progress + planned features. It's published in three surfaces (in-app at `/core/roadmap/`, About-page card, GitHub). If it gets stale, all three surfaces lie.

How:
- Annotate the matching bullet with the version: `*(shipped v3.17.NNN)*` or `*(partial — X shipped v3.17.NNN; Y deferred)*`.
- When a phase completes, change its header to `**— complete**` or `[complete]`.
- For new feature requests not on the roadmap yet, ADD them as planned items (not implemented) before / alongside building them.
- Update the **Sizing table** at the bottom when adding new phases.
- Bump `config/version.py`, write a `CHANGELOG.md` entry, and update `docs/ROADMAP.md` — all in one commit.

## Release pattern

Every release commit ships:
1. The actual feature change
2. `config/version.py` bumped (string + patch int both)
3. `CHANGELOG.md` entry at the top with `## [N.N.N] - YYYY-MM-DD`
4. `docs/ROADMAP.md` updated if the release touches a roadmap item
5. `git push origin HEAD:main` from the worktree (not `/home/administrator/`)

The post-commit hook auto-creates the version tag locally; the pre-push hook pushes unpushed tags.

## Wording for documentation

- **No competitor name-dropping** in roadmap or marketing prose. Don't position the project as "X parity" or "matches Y's wedge". Describe what the product does on its own merits.
- Integration listings (e.g. "We integrate with ConnectWise Manage, Autotask, etc.") ARE factual feature statements — keep those as-is.
- AI-assisted features must be explicitly tagged **OPTIONAL AI** in the roadmap and gated by `psa_ai_enabled`.
- Don't position planned items as fully implemented. Use "planned", "in progress", "extends X (shipped vN.N.N)" markers.

## Dev workflow

- All edits go through `/home/administrator/.dev-worktree/` on the `dev-work` branch.
- Don't restart gunicorn — the user clicks Apply.
- Don't run destructive git commands (force-push, reset --hard) without explicit user approval.

## Testing

- New models / views / API endpoints need a test in the matching app's `tests.py`.
- Run `manage.py test <app>` before committing — fail fast.
- View tests use `@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)` to dodge the 2FA + HTTPS-redirect middleware.
