# Client St0r vs IT Glue vs Hudu

> **Open-source, self-hosted MSP documentation — no per-seat fees, no vendor lock-in, full data ownership.**

[![Self-Hosted](https://img.shields.io/badge/hosting-self--hosted-brightgreen)](https://github.com/agit8or1/clientst0r)
[![MIT License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Free Forever](https://img.shields.io/badge/cost-free-brightgreen)](https://github.com/agit8or1/clientst0r)
[![Version](https://img.shields.io/badge/version-3.13.x-blue)](CHANGELOG.md)

---

## Cost Comparison

| | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| **Monthly cost** | **$0** | ~$29–99 /user/mo | ~$16–37 /user/mo |
| **Annual cost (5 users)** | **$0** | ~$1,740–5,940/yr | ~$960–2,220/yr |
| **Annual cost (25 users)** | **$0** | ~$8,700–29,700/yr | ~$4,800–11,100/yr |
| **Setup fee** | **$0** | Varies | Varies |
| **License required** | **No** | Yes | Yes (self-hosted) |
| **Open source** | **MIT** | No | No |
| **Vendor lock-in** | **None** | High | Medium |

> IT Glue pricing sourced from public pricing pages. Hudu pricing varies by plan. Client St0r is free forever — you only pay for your own server.

---

## Hosting & Data Control

| | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| Self-hosted | ✅ | ❌ | ✅ |
| Cloud option | ❌ (bring your own) | ✅ (SaaS only) | ✅ |
| You own the database | ✅ | ❌ | ✅ |
| Air-gapped / offline | ✅ | ❌ | ✅ |
| GDPR / data residency | ✅ Full control | ⚠️ Vendor-dependent | ✅ |
| Modifiable source code | ✅ | ❌ | ❌ |
| Multi-tenant (MSP orgs) | ✅ | ✅ | ✅ |
| White-labeling | ✅ (self-hosted) | ❌ | ⚠️ (paid add-on) |

---

## Feature Comparison

### Documentation & Knowledge Base

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| Knowledge base / articles | ✅ | ✅ | ✅ |
| Checklists / runbooks | ✅ | ✅ | ✅ |
| Rich text editor | ✅ | ✅ | ✅ |
| File attachments | ✅ | ✅ | ✅ |
| Related items / linking | ✅ | ✅ | ✅ |
| Markdown support | ✅ | ⚠️ Limited | ⚠️ Limited |
| Custom article templates | ✅ | ✅ | ✅ |
| Version history | ✅ | ✅ | ✅ |
| Full-text search | ✅ | ✅ | ✅ |
| Favorites / bookmarks | ✅ | ⚠️ | ✅ |

### Asset Management

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| Asset tracking | ✅ | ✅ | ✅ |
| Custom asset types | ✅ | ✅ | ✅ |
| Custom fields | ✅ | ✅ | ✅ |
| Asset relationships | ✅ | ✅ | ✅ |
| Asset photos / attachments | ✅ | ✅ | ✅ |
| Port configuration (switch/panel) | ✅ | ⚠️ | ⚠️ |
| Warranty / lifecycle tracking | ✅ | ✅ | ✅ |
| Bulk import (CSV) | ✅ | ✅ | ✅ |

### Network & Infrastructure

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| Visual rack management | ✅ | ⚠️ (Network Glue add-on) | ✅ |
| Network closet / board view | ✅ | ❌ | ❌ |
| Drag-and-drop rack editor | ✅ | ❌ | ⚠️ |
| Network diagrams (draw.io) | ✅ | ⚠️ (limited) | ✅ |
| IP address management | ✅ | ⚠️ (Network Glue) | ✅ |
| VLAN tracking | ✅ | ⚠️ (Network Glue) | ✅ |
| Subnet management | ✅ | ⚠️ (Network Glue) | ✅ |
| Switch port mapping | ✅ | ⚠️ (Network Glue) | ⚠️ |
| Patch panel management | ✅ | ❌ | ⚠️ |
| Cable management | ✅ | ❌ | ❌ |

> **Note:** IT Glue Network Glue is a paid add-on (~$10–25/user/mo extra).

### Security

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| Encrypted password vault | ✅ AES-256-GCM | ✅ | ✅ |
| Encryption algorithm | **AES-256-GCM + HKDF-SHA256** | AES-256 | AES-256 |
| Password sharing | ✅ | ✅ | ✅ |
| Two-factor authentication (TOTP) | ✅ Enforced | ✅ | ✅ |
| SSO / Azure AD / SAML | ✅ Azure AD | ✅ SAML | ✅ SAML |
| LDAP / Active Directory | ✅ | ✅ | ✅ |
| Argon2 password hashing | ✅ | ❓ | ❓ |
| Audit log (all actions) | ✅ | ✅ | ✅ |
| Role-based access control | ✅ | ✅ | ✅ |
| Session timeout | ✅ Configurable | ✅ | ✅ |
| Brute-force protection | ✅ | ✅ | ✅ |
| Rate limiting | ✅ | ✅ | ✅ |
| Snyk vulnerability scanning | ✅ Built-in | ❌ | ❌ |
| OS package security scanning | ✅ Built-in | ❌ | ❌ |
| Security headers (CSP, HSTS) | ✅ | ✅ | ✅ |
| HaveIBeenPwned integration | ✅ | ❌ | ❌ |
| Firewall / GeoIP management | ✅ | ❌ | ❌ |
| Open-source auditability | ✅ Full | ❌ | ❌ |

### API & Integrations

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| REST API | ✅ | ✅ | ✅ |
| GraphQL API | ✅ | ❌ | ❌ |
| API key authentication | ✅ | ✅ | ✅ |
| Webhook support | ✅ | ✅ | ✅ |
| PSA integrations | ✅ | ✅ | ✅ |
| ConnectWise Manage | ✅ | ✅ | ✅ |
| Autotask | ✅ | ✅ | ✅ |
| HaloPSA | ✅ | ⚠️ | ✅ |
| NinjaRMM | ✅ | ✅ | ✅ |

### AI & Automation

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| AI-assisted documentation | ✅ Claude API | ⚠️ (roadmap) | ❌ |
| AI knowledge base search | ✅ | ❌ | ❌ |
| Automated asset discovery | ✅ | ⚠️ (add-on) | ⚠️ |
| Auto-update system | ✅ Self-hosted | N/A (SaaS) | ✅ |
| Workflow automation | ✅ | ⚠️ | ⚠️ |
| Scheduled tasks | ✅ | ⚠️ | ⚠️ |

### User Experience

| Feature | **Client St0r** | **IT Glue** | **Hudu** |
|---|:---:|:---:|:---:|
| Mobile-friendly (PWA) | ✅ | ✅ | ✅ |
| Dark mode | ✅ | ❌ | ✅ |
| Customizable dashboard | ✅ | ⚠️ | ✅ |
| Quick search | ✅ | ✅ | ✅ |
| Keyboard shortcuts | ✅ | ✅ | ✅ |
| Multi-language | ⚠️ (English) | ✅ | ⚠️ |
| Custom branding | ✅ | ❌ | ⚠️ (paid) |
| Randomized backgrounds | ✅ | ❌ | ❌ |

---

## Unique to Client St0r

These features are **not available in IT Glue or Hudu**:

| Feature | Details |
|---|---|
| **100% Free & Open Source** | MIT license — fork it, modify it, audit it |
| **GraphQL API** | Full GraphQL alongside REST |
| **Visual Board/Closet View** | Drag-and-drop mounted board for network closets |
| **Built-in Snyk Scanning** | Dependency vulnerability scanning with web dashboard |
| **OS Package Scanner** | System-level security update detection |
| **HaveIBeenPwned Integration** | Check credentials against breach databases |
| **Firewall Management** | iptables web UI + GeoIP country blocking |
| **AI Documentation (Claude)** | On-premise AI with PII redaction before sending |
| **Full Source Auditability** | Every line of code is public and reviewable |
| **Zero Vendor Risk** | No acquisition risk, no pricing changes, no sunset |

---

## Why Teams Switch

### From IT Glue

- **Cost**: A 10-person MSP pays $0/mo instead of ~$3,000–12,000/yr
- **No Kaseya lock-in**: IT Glue was acquired by Kaseya in 2021 — pricing and roadmap are now vendor-controlled
- **Network features**: Client St0r includes rack management, IPAM, and diagrams without extra fees
- **Data ownership**: Your data stays on your infrastructure, not in a third-party SaaS

### From Hudu

- **Truly free**: Hudu requires a commercial license even for self-hosting
- **Open source**: Hudu is proprietary — you cannot audit, fork, or modify it
- **GraphQL API**: Client St0r offers a full GraphQL API for advanced integrations
- **Security scanning**: Built-in Snyk and OS package vulnerability scanning

---

## Getting Started

```bash
# Clone and deploy in minutes
git clone https://github.com/agit8or1/clientst0r.git
cd clientst0r
cp .env.example .env
# Edit .env with your settings
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Full deployment guide: [INSTALL.md](INSTALL.md)

---

## Learn More

- [Full Feature List](FEATURES.md)
- [IT Glue vs Client St0r (detailed)](docs/it-glue-alternative.md)
- [Hudu vs Client St0r (detailed)](docs/hudu-alternative.md)
- [API Documentation](API_V2_GRAPHQL.md)
- [Security Policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

---

<p align="center">
  <strong>Client St0r</strong> — Built by MSPs, for MSPs.<br>
  <a href="https://github.com/agit8or1/clientst0r/issues">Report a Bug</a> ·
  <a href="https://github.com/agit8or1/clientst0r/issues">Request a Feature</a> ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>
