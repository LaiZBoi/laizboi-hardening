# Outbound Network Calls

This file summarizes outbound network behavior found in the codebase. "Enabled by default" means a fresh production install can make the call without an admin adding provider-specific credentials or connection records.

| Area | Destination or provider | Enabled by default | Controls |
| --- | --- | --- | --- |
| GitHub update checks | `api.github.com/repos/<owner>/<repo>/tags` and releases APIs | No automatic polling when `AUTO_UPDATE_ENABLED=False`; manual admin checks still call GitHub | `AUTO_UPDATE_ENABLED`, `GITHUB_REPO_OWNER`, `GITHUB_REPO_NAME`, `GITHUB_TOKEN`; manual button at Settings > Updates |
| Web-based update execution | Downloads `deploy/update_instructions.sh` from `raw.githubusercontent.com` and executes it | No | `AUTO_UPDATE_ENABLED=True` required; superuser-only web action or management command |
| Legacy/scripted auto-update | Local `scripts/auto_update.sh` through `manage.py auto_update` | No | `AUTO_UPDATE_ENABLED=True` required for execution (shell scripts load `.env` and refuse when disabled); `--check-only` still performs Git remote checks |
| HaveIBeenPwned password checks | `api.pwnedpasswords.com/range/<hash-prefix>` | No (`HIBP_ENABLED=False` by default) | `HIBP_ENABLED`, `HIBP_API_KEY`, `HIBP_CHECK_ON_SAVE`, `HIBP_BLOCK_BREACHED`, `HIBP_SCAN_FREQUENCY` |
| AI documentation and PSA helpers | Anthropic, Moonshot, MiniMax, OpenAI, Ollama-compatible endpoint | No practical external calls without API keys or explicit AI use; default provider name is `anthropic` | `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY`, `MINIMAX_API_KEY`, `MINIMAX_CODING_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_BASE_URL` |
| AI floor plans and receipt OCR | Anthropic SDK | No practical external calls without `ANTHROPIC_API_KEY` and feature use | `ANTHROPIC_API_KEY` and related feature views |
| Maps and geocoding | Google Maps, Mapbox, Bing Maps | No practical external calls without API keys and map/geocode actions | `GOOGLE_MAPS_API_KEY`, `MAPBOX_ACCESS_TOKEN`, `BING_MAPS_API_KEY` |
| Property data | Regrid, ATTOM, municipal public-record APIs | Municipal lookups may run when a user requests property refresh; paid APIs require keys | `REGRID_API_KEY`, `ATTOM_API_KEY`; location/property refresh actions |
| Beta upstream forwarding | Configured `BETA_UPSTREAM_URL` | No | `BETA_UPSTREAM_URL`; blank means local-only signup storage |
| Beta signup notification email | SMTP recipient configured as `BETA_ADMIN_EMAIL` | No | `BETA_ADMIN_EMAIL`; blank skips beta signup notification email |
| Azure AD / Microsoft Graph / M365 | Microsoft login, Graph, and M365 APIs | No | Azure/M365 settings and configured directory or M365 integration records |
| RMM/PSA/network integrations | ConnectWise, Autotask, Halo, Freshservice, Syncro, Kaseya, NinjaOne, Atera, Datto, Tactical RMM, UniFi, Omada, Grandstream, Alga, ITFlow, RangerMSP | No | Per-integration connection records, provider base URLs, credentials, and sync commands |
| Accounting/payment integrations | QuickBooks Online, Xero, Stripe, GoCardless | No | Per-connection credentials and provider settings |
| Distributor/warranty/tax integrations | Ingram, Pax8, TD Synnex, Dell, HPE, Lenovo, Avalara, TaxJar | No | Per-provider connection records and API keys |
| Webhooks | Arbitrary outbound webhook URLs configured by admins | No | Webhook configuration in the app |
| Email sending | SMTP server | No unless SMTP settings are configured and a notification flow runs | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` |
| Email ingestion | IMAP servers for PSA ticket ingestion | No | Email ingestion configuration records and `psa_poll_email` |
| Mobile/web push | Expo push or configured web push endpoints | No | Mobile device push tokens, web push subscriptions, push endpoint settings |
| Website monitoring | User-configured monitored URLs and SSL sockets | No | Website monitor records and monitoring scheduled tasks |
| GeoIP firewall lookup | External GeoIP service used by firewall middleware | Depends on firewall configuration and lookup path | Firewall/GeoIP settings in core firewall features |
| Snyk/security tooling | Snyk CLI/API and package manager repositories | No | Snyk settings, `SNYK_TOKEN`, scanner commands, package scan/update actions |
| Admin-triggered dependency installers | NVM install script, NodeSource setup, OS package repositories | No | Explicit admin actions in mobile build/Snyk/package tooling |
| GitHub issues/releases tooling | GitHub Issues and Releases APIs | No | GitHub token and explicit issue/release management actions |
| Documentation import tooling | Raw GitHub content for KB import | No | `fetch_kb_from_github` command options |

Review this list before enabling outbound firewall egress. For high-sensitivity deployments, start with all optional provider keys blank, `AUTO_UPDATE_ENABLED=False`, `BETA_UPSTREAM_URL=`, and `HIBP_ENABLED=False`.
