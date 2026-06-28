# Frequently Asked Questions

## General

**Is ClientSt0r open source?**
Yes. ClientSt0r is open-source software.

**Is ClientSt0r self-hosted?**
Yes. It is designed to run on infrastructure you control. There is no SaaS version.

**Who should use ClientSt0r?**
Managed service providers and IT teams needing structured documentation with full data ownership.

**Is ClientSt0r a replacement for IT Glue?**
ClientSt0r covers the core use cases of IT Glue — asset documentation, password vault, knowledge base, network documentation, and PSA integrations — while being self-hosted and open-source. See [IT Glue Alternative](it-glue-alternative.md) for a detailed comparison.

**Is ClientSt0r a replacement for Hudu?**
ClientSt0r covers similar use cases to Hudu with the addition of fleet management, network scanner, and no licensing fee. See [Hudu Alternative](hudu-alternative.md) for a detailed comparison.

## Installation

**What are the system requirements?**
Ubuntu 22.04/24.04 or Debian 12+ VPS with Python 3.12+, MariaDB, Nginx, systemd, and Certbot. See [VPS deployment guide](deployment-vps.md).

**How do I install ClientSt0r?**
Follow [docs/deployment-vps.md](deployment-vps.md) for production VPS install, or use the legacy one-liner for development:
```bash
git clone https://github.com/agit8or1/clientst0r.git && cd clientst0r && bash install.sh
```
The installer handles dependencies, database setup, and service configuration automatically.

**Can I update without SSH?**
Manual update checks are available in Settings → Updates. **Applying** updates from the web UI requires `AUTO_UPDATE_ENABLED=True` (opt-in). Production VPS installs should use manual updates per `docs/deployment-vps.md`.

## Features

**Does ClientSt0r support multiple organizations?**
Yes. Multi-organization support with complete data isolation is a core feature, designed for MSP use.

**Does ClientSt0r support SSO?**
Yes. Azure AD / Microsoft Entra ID and LDAP/Active Directory are supported.

**Is 2FA supported?**
Yes. TOTP-based 2FA is supported and can be enforced organization-wide.

**What PSA integrations are available?**
ConnectWise, Autotask, HaloPSA, Kaseya BMS, Syncro, Freshservice, Zendesk, and ITFlow.
