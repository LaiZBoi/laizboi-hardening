# Client St0r

[![Version 3.10.4](https://img.shields.io/badge/version-3.10.4-brightgreen)](https://github.com/agit8or1/clientst0r)
[![Production Ready](https://img.shields.io/badge/status-production%20ready-green)](https://github.com/agit8or1/clientst0r)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Django 6.0](https://img.shields.io/badge/django-6.0-blue)](https://www.djangoproject.com/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)](https://www.python.org/)
[![Known Vulnerabilities](https://snyk.io/test/github/agit8or1/clientst0r/badge.svg)](https://snyk.io/test/github/agit8or1/clientst0r)
[![Security Monitoring](https://img.shields.io/badge/security-Snyk%20%7C%20HaveIBeenPwned-blue)](https://github.com/agit8or1/clientst0r)

> **Open-source, self-hosted MSP documentation platform — an alternative to IT Glue and Hudu.**
> Full data ownership. No SaaS fees. Runs on your infrastructure.

| [IT Glue Alternative →](docs/it-glue-alternative.md) | [Hudu Alternative →](docs/hudu-alternative.md) | [What is ClientSt0r? →](docs/what-is-clientst0r.md) |
|---|---|---|

A complete, self-hosted IT documentation platform designed for Managed Service Providers (MSPs) and IT departments. Built with Django 6, Client St0r provides secure asset management, encrypted password vault, knowledge base, PSA integrations, and comprehensive monitoring tools.

Client St0r is commonly evaluated as an **open-source IT Glue alternative** and a **self-hosted Hudu alternative** for MSP documentation.

## 📸 Screenshots

*All screenshots include demo data and are watermarked. Random background feature enabled. [View full gallery →](docs/screenshots/)*

<table>
<tr>
<td width="50%">

### 🏠 Dashboard & Quick Access
![Dashboard](docs/screenshots/dashboard.png)
![Quick Add](docs/screenshots/quick-add.png)

</td>
<td width="50%">

### 📦 Asset Management
![Assets](docs/screenshots/assets-list.png)
![Racks](docs/screenshots/racks.png)

</td>
</tr>
<tr>
<td width="50%">

### 🔐 Password Vault & Security
![Password Vault](docs/screenshots/password-vault.png)
![Personal Vault](docs/screenshots/personal-vault.png)

</td>
<td width="50%">

### 📚 Documentation & Knowledge Base
![Knowledge Base](docs/screenshots/knowledge-base.png)
![Diagrams](docs/screenshots/diagrams.png)

</td>
</tr>
<tr>
<td width="50%">

### 🔒 Security Dashboard
![Security Dashboard](docs/screenshots/security-dashboard.png)
![Vulnerability Scans](docs/screenshots/vulnerability-scans.png)

</td>
<td width="50%">

### 🌐 Monitoring
![Website Monitors](docs/screenshots/website-monitors.png)
![Expirations](docs/screenshots/expirations.png)

</td>
</tr>
<tr>
<td width="50%">

### ⚙️ System Management
![System Updates](docs/screenshots/system-updates.png)
![System Status](docs/screenshots/system-status.png)

</td>
<td width="50%">

### 🏢 Multi-Organization & Access
![Organizations](docs/screenshots/organizations.png)
![Access Management](docs/screenshots/access-management.png)

</td>
</tr>
</table>

<details>
<summary><strong>📋 View All Screenshots (34 total)</strong></summary>

### Core Features
- [Dashboard](docs/screenshots/dashboard.png) - Main dashboard with random backgrounds
- [Quick Add](docs/screenshots/quick-add.png) - Fast creation menu for assets, passwords, documents
- [Profile](docs/screenshots/profile.png) - User profile and settings
- [Favorites](docs/screenshots/favorites.png) - Quick access to favorited items

### Asset Management
- [Assets List](docs/screenshots/assets-list.png) - Comprehensive asset tracking
- [Racks](docs/screenshots/racks.png) - Rack management with U-space tracking
- [Network Closets](docs/screenshots/network-closets.png) - Network infrastructure management
- [IPAM/Subnets](docs/screenshots/ipam-subnets.png) - IP address management
- [VLANs](docs/screenshots/vlans.png) - VLAN configuration and tracking
- [Locations](docs/screenshots/locations.png) - Physical location management

### Password Vault
- [Password Vault](docs/screenshots/password-vault.png) - AES-256-GCM encrypted password storage
- [Personal Vault](docs/screenshots/personal-vault.png) - Private user password vault
- [Secure Notes](docs/screenshots/secure-notes.png) - Encrypted ephemeral messaging

### Documentation & Diagrams
- [Knowledge Base](docs/screenshots/knowledge-base.png) - Document management system
- [Diagrams](docs/screenshots/diagrams.png) - Draw.io integrated diagramming
- [Floor Plans Import](docs/screenshots/floor-plans-import.png) - MagicPlan floor plan import

### Workflows & Processes
- [Workflows](docs/screenshots/workflows.png) - Process automation and tracking

### Monitoring & Expirations
- [Website Monitors](docs/screenshots/website-monitors.png) - Uptime monitoring dashboard
- [Expirations](docs/screenshots/expirations.png) - SSL, domain, and credential expiration tracking

### Security & Scanning
- [Security Dashboard](docs/screenshots/security-dashboard.png) - Security overview and vulnerability status
- [Vulnerability Scans](docs/screenshots/vulnerability-scans.png) - Snyk scan history
- [Scan Configuration](docs/screenshots/scan-configuration.png) - Security scan settings

### System Administration
- [Settings](docs/screenshots/settings-general.png) - General system configuration
- [System Status](docs/screenshots/system-status.png) - Health monitoring and scheduled tasks
- [System Updates](docs/screenshots/system-updates.png) - One-click update system
- [Organizations](docs/screenshots/organizations.png) - Multi-organization management for MSPs
- [Access Management](docs/screenshots/access-management.png) - User and role management
- [Integrations](docs/screenshots/integrations.png) - PSA and external integrations
- [Import Data](docs/screenshots/import-data.png) - Bulk data import tools

### MSP/Global Features (Staff Only)
- [Global Dashboard](docs/screenshots/global-dashboard.png) - Cross-organization overview
- [Global KB](docs/screenshots/global-kb.png) - Internal staff documentation
- [Global Workflows](docs/screenshots/global-workflows.png) - Reusable process templates

</details>

## 🐕 About Luna

This project was developed with the assistance of **Luna**, a brilliant German Shepherd Dog with exceptional problem-solving abilities and a keen eye for security best practices. Luna's contributions to code review, architecture decisions, and bug hunting have been invaluable.

## IT Glue / Hudu Alternative

If you're comparing documentation platforms for MSP workflows, Client St0r is designed to cover core documentation needs (assets, credentials, procedures/runbooks, and knowledge base content) while remaining fully self-hosted.

* [Client St0r as an IT Glue alternative](docs/it-glue-alternative.md)
* [Client St0r as a Hudu alternative](docs/hudu-alternative.md)

## ✨ Key Features

### 🔐 Security & Authentication
- **Azure AD / Microsoft Entra ID SSO** with auto-user creation
- **LDAP/Active Directory** enterprise integration
- **Enforced TOTP 2FA** with SSO bypass
- **AES-GCM Encryption** for all sensitive data
- **Password Breach Detection** via HaveIBeenPwned (k-anonymity)
- **Snyk Security Scanning** with automated CVE detection
- **Rate Limiting**, CSRF, XSS, SQL injection, SSRF, path traversal protection
- **Encrypted Backups** with automatic retention policies

### 🏢 Multi-Organization Management & Access Control
- **Complete Organization Isolation** - Manage multiple client organizations with data separation and 42 granular permissions
- **Four-Tier Access Levels** - Owner, Admin, Editor, Read-Only
- **MSP User Types** - Staff users (global access to all organizations) and Organization users (scoped to specific clients)

### 📦 Core Platform
- **Auto-Update System** - One-click web updates (20-30 seconds, no SSH)
- **Asset Management** - Comprehensive tracking with interactive rack/board visualization
- **Network Scanner** - Automated network discovery with nmap, smart asset import, duplicate prevention
- **Interactive Racks** - Drag-and-drop device positioning, realistic equipment visuals, zoom controls
- **Wall-Mounted Boards** - 2D canvas layout for wall/ceiling equipment, snap-to-grid, free-form positioning
- **Patch Panels** - Click-to-connect port management, visual cable paths, color-coded connections
- **Equipment Visuals** - Type-specific indicators (LEDs, ports, drive bays), equipment model images
- **Password Vault** - AES-GCM encrypted with breach detection, personal vaults, and Bitwarden import
- **Bitwarden Import** - Import passwords from Bitwarden/Vaultwarden JSON exports (logins, notes, cards, identities, folders, custom fields, TOTP)
- **SMS/Navigation** - Send location navigation links via SMS (Twilio, Plivo, Vonage, Telnyx, AWS SNS)
- **Documentation** - Per-org docs with version control, templates, and global MSP knowledge base
- **Diagrams & Floor Plans** - Draw.io integration, MagicPlan import, auto-generated flowcharts
- **Infrastructure** - IPAM with subnet management, VLAN tracking, network closets, cable documentation
- **Service Vehicles** - Fleet management with mileage tracking, maintenance schedules, fuel logs, damage reports with interactive diagrams, vehicle inventory, GPS location, insurance tracking
- **OS Package Scanner** - System package vulnerability scanning (apt/yum/dnf), automated security update detection, scheduled scans
- **Monitoring** - Website uptime, SSL certificates, domain expiration, custom alerts, WAN monitoring
- **Workflows** - Process automation with audit logging, PSA integration, execution tracking
- **Firewall Management** - iptables firewall rules, GeoIP country blocking, IP whitelist/blacklist
- **Intrusion Prevention** - Fail2ban integration with ban management and IP checking
- **Reporting & Analytics** - Advanced reports, custom dashboards, scheduled reports, data visualization
- **Backup/Restore** - Encrypted backups, automated scheduling, retention policies, one-click restore
- **Progressive Web App** - Install on mobile devices, offline support, push notifications
- **Native Mobile App** - React Native app for iOS and Android with full feature access

### 🔌 Integrations & APIs
- **8 PSA Providers** - ConnectWise, Autotask, HaloPSA, Kaseya BMS, Syncro, Freshservice, Zendesk, ITFlow
- **5 RMM Providers** - Tactical RMM (full), NinjaOne, Datto, Atera, CW Automate (infrastructure ready)
- **Organization Auto-Import** - Automatically create orgs from PSA companies or RMM sites
- **Asset Mapping** - Auto-link RMM devices to assets
- **REST API v1** - Full-featured REST API with authentication and rate limiting
- **GraphQL API v2** - Modern GraphQL API with filtering, pagination, and real-time capabilities
- **Webhook Support** - Event-driven integrations with external systems

**For complete feature details, see [FEATURES.md](FEATURES.md)**

## 🆕 What's New

### Latest Release - v3.10.4 (February 2026)

**🎉 Major Features in v3.10:**
- **🚗 Service Vehicles Fleet Management** - Complete fleet tracking with mileage, maintenance, fuel logs, damage reports, inventory, GPS location, and insurance tracking
- **📦 OS Package Security Scanner** - Automated vulnerability scanning for system packages (apt/yum/dnf) with scheduled scans and security update tracking
- **🖱️ Enhanced Rack Drag-and-Drop** - Native HTML5 drag-and-drop for moving rack devices with visual feedback and collision detection
- **🚙 Interactive Vehicle Damage Diagrams** - SVG-based vehicle diagrams with clickable areas for damage reporting and visualization
- **📊 Clickable Dashboard Statistics** - All dashboard stat cards now link to filtered views for quick access

**Previous Release - v3.8.0:**
- **🔍 Network Scanner & Asset Discovery** - Automated network scanning using nmap with intelligent device detection and smart import system
- **🎨 Interactive Rack/Board Visualization** - Wall-mounted board layout with drag-and-drop, realistic device visuals, and snap-to-grid
- **📡 Patch Panel Management** - Interactive port management with click-to-connect, visual cable tracking, and connection visualization

#### Service Vehicles Fleet Management (v3.9.0+)
Complete fleet management system for tracking service vehicles with comprehensive features:
- **Vehicle Tracking**: Make, model, year, VIN, license plate, mileage, condition status
- **Maintenance Management**: Service history, recurring schedules, costs, repair tracking, overdue detection
- **Fuel Tracking**: Fuel purchases with automatic MPG calculation, cost analysis, efficiency trends
- **Damage Reports**: Interactive vehicle diagrams with clickable areas, photo uploads, repair status, insurance claims
- **Vehicle Inventory**: Per-vehicle inventory tracking (cables, tools, supplies), low-stock alerts
- **User Assignments**: Assignment history with mileage tracking, active assignment management
- **Insurance Tracking**: Policy details, expiration warnings, premium tracking
- **GPS Location**: Store current vehicle coordinates (6 decimal precision), last update timestamp
- **Dashboard & Analytics**: Fleet statistics, maintenance alerts, fuel costs, vehicle cards with status
- **Feature Toggle**: Enable/disable vehicles module via system settings

#### OS Package Security Scanner (v3.9.0+)
Automated vulnerability scanning for system packages with security update tracking:
- **Multi-Platform Support**: apt (Debian/Ubuntu), yum/dnf (RedHat/CentOS), pacman (Arch)
- **Security Updates**: Detect security-specific updates from official repositories
- **Scheduled Scans**: Automated daily scans with configurable schedule
- **Dashboard Widget**: Security status overview on security dashboard
- **Scan History**: Track scan results over time with trend visualization
- **Manual Triggers**: Run scans on-demand via web interface
- **Package Details**: Total packages, upgradeable packages, security updates count
- **Alert System**: Webhook notifications for critical security updates

#### Enhanced Rack Device Management (v3.10.0+)
Improved drag-and-drop with native HTML5 events and better UX:
- **Native Drag Events**: Replaced SortableJS with HTML5 drag-and-drop for better reliability
- **Visual Feedback**: Blue highlight on valid drop targets, grab/grabbing cursor states
- **Collision Detection**: Prevents overlapping devices, validates space before drop
- **Real-time Updates**: API-driven position updates with error handling
- **Device Wiring**: Connection management with SVG cable visualization between devices
- **Port Configuration**: Label and configure network ports on rack-mounted equipment

#### Network Scanner & Asset Discovery (v3.8.0)
Scan your network to automatically discover and import devices into your asset inventory:
- **Automated Discovery**: Uses nmap to scan network ranges (CIDR, IP ranges, single IPs)
- **Intelligent Matching**: Matches devices by MAC address (primary) or IP address (secondary)
- **Smart Import**: Preview what will be created/updated, select devices to import, avoid duplicates
- **Device Detection**: Auto-identifies servers, switches, routers, printers, cameras, phones, APs
- **Rich Metadata**: Captures IP, MAC, hostname, OS, open ports, services, vendor info
- **Conflict Resolution**: Flags potential duplicates for manual review
- **Selective Import**: Checkbox selection for each discovered device
- **Update Existing**: Updates existing assets with latest network data without creating duplicates

```bash
# Run scanner
python3 scripts/network_scanner.py 192.168.1.0/24

# Upload scan file to: Assets → Import Network Scan
# Review matches, select devices, confirm import
```

#### Wall-Mounted Board Layout (v3.7.0)
Transform rack visualization into a 2D board for wall/ceiling mounted equipment:
- **Dual View Modes**: Toggle between vertical rack view and horizontal board layout
- **Free-Form Positioning**: Drag devices anywhere on 2D canvas
- **Snap-to-Grid**: 50px grid overlay with toggle for precise alignment
- **Drag-to-Resize**: Resize devices visually by dragging corners
- **Zoom Controls**: Zoom in/out works in both rack and board views
- **Asset Sidebar**: Drag assets directly from sidebar onto board or rack

#### Realistic Device Visuals (v3.7.0)
Devices now look like actual equipment with type-specific visual indicators:
- **Equipment Images**: Display actual product photos from equipment models
- **Server Visuals**: Drive bays + power/status/activity LEDs
- **Network Equipment**: Port indicators (24/48 ports) + link status LEDs
- **Patch Panels**: Port grid layout with visual numbering
- **UPS/PDU**: Power outlet indicators + dual power LEDs
- **Wireless APs**: Signal indicator (📡) + status LEDs
- **Security Cameras**: Recording indicator (📹) + activity LED
- **Storage Devices**: Drive bay grid + activity LEDs
- **Auto-Scaling**: Visual indicators scale with device size

#### Interactive Patch Panel Management (v3.6.0)
Click-to-connect interface for managing patch panel connections:
- **Click-to-Connect**: Click source port → click destination port to create connection
- **Visual Connections**: SVG curved lines show cable paths between connected ports
- **Color-Coded Cables**: Customize cable colors for visual organization
- **Port Status**: Color-coded ports (available, in-use, reserved)
- **Connection Details**: Track destination, cable type, notes per port
- **Drag Assets to Ports**: Drag assets from sidebar directly to ports
- **Port Grid View**: Visual 24/48 port layouts matching physical panels
- **Quick Disconnect**: Right-click or button to disconnect ports

**Previous Highlights (v2.76):**
- **Asset Lifespan Tracking** - Track purchase dates, expected lifespan, and end-of-life reminders
- **Bitwarden/Vaultwarden Import** - Import passwords with folders, TOTP, custom fields
- **SMS/Navigation Links** - Send location navigation via SMS (Google Maps, Apple Maps, Waze)
- **Firewall & GeoIP** - iptables management with country blocking
- **Fail2ban Integration** - Automated intrusion prevention
- **Progressive Web App** - Install on mobile devices with offline support

**For complete version history, see [CHANGELOG.md](CHANGELOG.md)**

## 🚀 Quick Start

### One-Line Installation (Recommended)

The easiest way to install Client St0r:

```bash
git clone https://github.com/agit8or1/clientst0r.git && cd clientst0r && bash install.sh
```

This automated installer will:
- ✅ Install all prerequisites (Python 3.12, pip, venv, MariaDB server & client)
- ✅ Create virtual environment and install dependencies
- ✅ Generate secure encryption keys automatically
- ✅ Create `.env` configuration file
- ✅ Setup database and user
- ✅ Create log directory
- ✅ Run migrations
- ✅ Create superuser account
- ✅ Collect static files
- ✅ **Start production server automatically** (Gunicorn with systemd)
- ✅ **Configure auto-update permissions** (sudoers for one-click web updates)

**When the installer finishes, your server is RUNNING and ready to use!**

### Smart Detection

The installer automatically detects existing installations and offers:

1. **Upgrade/Update** - Pull latest code, run migrations, restart service (zero downtime)
2. **System Check** - Verify all components are working properly
3. **Clean Install** - Remove everything and reinstall from scratch
4. **Exit** - Leave existing installation untouched

No manual cleanup needed! The installer handles everything.

### Web-Based Auto-Update (NEW in 2.14.21!)

Once installed, you can update Client St0r **directly from the web interface**:

1. Navigate to **System Settings → System Updates**
2. Click **"Check for Updates Now"** to detect new versions
3. Click **"Apply Update"** when an update is available
4. Watch real-time progress through all 5 steps:
   - Step 1: Git Pull
   - Step 2: Install Dependencies
   - Step 3: Run Migrations
   - Step 4: Collect Static Files
   - Step 5: Restart Service
5. Page automatically reloads with the new version (20-30 seconds total)

**No SSH access required!** Non-technical users can update safely from the web interface.

**System Requirements:**
- Ubuntu 20.04+ or Debian 11+
- 2GB RAM minimum (4GB recommended)
- Internet connection for package installation

### Optional Features

#### LDAP/Active Directory Integration

By default, Client St0r installs with Azure AD SSO support but **without** LDAP/Active Directory. This is because LDAP requires C compilation and system libraries.

**If you need LDAP/AD support**, install it after the main installation:

```bash
# Install system build dependencies
sudo apt-get update
sudo apt-get install -y build-essential python3-dev libldap2-dev libsasl2-dev

# Install LDAP Python packages
cd ~/clientst0r
source venv/bin/activate
pip install -r requirements-optional.txt
sudo systemctl restart clientst0r-gunicorn.service
```

**Note:** Azure AD SSO does **not** require these packages. LDAP is only needed for on-premises Active Directory or other LDAP servers.

#### Mobile App (iOS & Android)

Client St0r includes a native React Native mobile app for iOS and Android devices.

**Features:**
- 📱 Native iOS and Android apps
- 🔐 Secure token-based authentication
- 📊 Dashboard with quick stats
- 💼 Asset management on the go
- 🔒 Password vault access
- 📚 Document browsing
- 🌙 Dark mode optimized for mobile
- 🔄 Real-time sync via GraphQL API

**Prerequisites:**
- Node.js 18+
- Expo CLI
- Client St0r backend with GraphQL enabled

**Setup:**

```bash
# 1. Install GraphQL dependencies on backend
cd ~/clientst0r
source venv/bin/activate
pip install -r requirements-graphql.txt
sudo systemctl restart clientst0r-gunicorn.service

# 2. Set up mobile app
cd ~/clientst0r/mobile-app
npm install

# 3. Configure API URL
# Edit app.json and set your Client St0r server URL

# 4. Start development server
npm start

# 5. Run on device
# - iOS: Press 'i' or run: npm run ios
# - Android: Press 'a' or run: npm run android
```

**For complete mobile app documentation, see [mobile-app/README.md](mobile-app/README.md)**

### Manual Installation

If you prefer to install manually or need more control:

<details>
<summary>Click to expand manual installation steps</summary>

#### Prerequisites
- Python 3.12+
- MariaDB 10.5+ or MySQL 8.0+
- Nginx (production only)

```bash
# 1. Clone repository
git clone https://github.com/agit8or1/clientst0r.git
cd clientst0r

# 2. Install system dependencies
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip mariadb-client mariadb-server

# 3. Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# 4. Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Generate secrets
python3 -c "from cryptography.fernet import Fernet; print('APP_MASTER_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(50))"
python3 -c "import secrets; print('API_KEY_SECRET=' + secrets.token_urlsafe(50))"

# 6. Create .env file
# Copy the generated secrets from step 5 into this file
cat > .env << 'EOF'
DEBUG=True
SECRET_KEY=<paste_secret_key_here>
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=clientst0r
DB_USER=clientst0r
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=3306

APP_MASTER_KEY=<paste_master_key_here>
API_KEY_SECRET=<paste_api_key_secret_here>

EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
SITE_NAME=Client St0r
SITE_URL=http://localhost:8000
EOF

# 7. Start MariaDB and create database
sudo systemctl start mariadb
sudo mysql << 'EOSQL'
CREATE DATABASE clientst0r CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'clientst0r'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON clientst0r.* TO 'clientst0r'@'localhost';
FLUSH PRIVILEGES;
EOSQL

# 8. Run migrations
python3 manage.py migrate

# 9. Create superuser
python3 manage.py createsuperuser

# 10. Collect static files
python3 manage.py collectstatic --noinput

# 11. Run development server
python3 manage.py runserver 0.0.0.0:8000
```

Visit `http://localhost:8000` and log in with the credentials you created in step 9.

</details>

## 📚 Documentation

**Installation:**
- **[INSTALL.md](INSTALL.md)** - Complete installation guide (quick start, upgrade, troubleshooting)

**Core Documentation:**
- **[ORGANIZATIONS.md](ORGANIZATIONS.md)** - Complete guide to organizations, user types, roles, and permissions
- **[SECURITY.md](SECURITY.md)** - Security best practices and vulnerability disclosure
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Development and contribution guidelines
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and release notes
- **[deploy/](deploy/)** - Production deployment configs (Nginx, Gunicorn, systemd services)

## 🏗️ Architecture

### Technology Stack
- **Framework**: Django 6.0
- **API**: Django REST Framework 3.15
- **Database**: MariaDB 10.5+ (MySQL 8.0+ supported)
- **Web Server**: Nginx + Gunicorn
- **Authentication**: django-two-factor-auth (TOTP)
- **Encryption**: Python cryptography (AES-GCM)
- **Password Hashing**: Argon2
- **Frontend**: Bootstrap 5, vanilla JavaScript

### Design Philosophy
- ✅ **Flexible Deployment** - Pure systemd deployment OR optional Docker
- ✅ **No Redis** - systemd timers for scheduling (Redis optional for Docker)
- ✅ **Minimal Dependencies** - Only essential packages
- ✅ **Security First** - Built with security in mind
- ✅ **Self-Hosted** - Complete data control
- ✅ **Mobile-First** - Responsive design with PWA support
- ✅ **API-Driven** - REST and GraphQL APIs for integrations

## 🔒 Security

Client St0r has undergone comprehensive security auditing and continuous vulnerability monitoring:

### Continuous Security Monitoring
- ✅ **Automated CVE Scanning** - Codebase scanned for known vulnerabilities and CVEs
- ✅ **AI-Assisted Detection** - Pattern matching for SQL injection, XSS, CSRF, path traversal
- ✅ **Dependency Monitoring** - Python packages checked against security advisories
- ✅ **Weekly Manual Audits** - Regular security reviews by development team
- ✅ **Alert-Only System** - No automated code changes, human verification required

### Fixed Vulnerabilities
- ✅ SQL Injection - Parameterized queries and identifier quoting
- ✅ SSRF - URL validation with IP blacklisting
- ✅ Path Traversal - Strict file path validation
- ✅ IDOR - Object access verification
- ✅ Insecure File Uploads - Type, size, and extension validation
- ✅ Hardcoded Secrets - Environment variable enforcement
- ✅ Weak Encryption - AES-GCM with validated keys
- ✅ CSRF Protection - Multi-domain support

### Security Features
- All passwords encrypted with AES-GCM
- API keys hashed with HMAC-SHA256
- Rate limiting on all endpoints
- Brute-force protection
- Security headers (CSP, HSTS)
- Private file serving
- Audit logging
- Password breach detection (HaveIBeenPwned integration)

**Security Disclosure**: If you discover a vulnerability, please email agit8or@agit8or.net. See [SECURITY.md](SECURITY.md) for details.

## 🤝 Contributing

We welcome contributions! Here's how you can help:

### 💡 Feature Requests & Ideas

Have an idea for a new feature? We use a community-driven voting system:

1. **Start with a Discussion** → [Share your idea](https://github.com/agit8or1/clientst0r/discussions/new?category=ideas)
2. **Vote on existing ideas** → [Browse and upvote](https://github.com/agit8or1/clientst0r/discussions/categories/ideas) (👍 reactions)
3. **Track the Roadmap** → [View what's being built](https://github.com/agit8or1/clientst0r/projects)

Popular ideas (high votes + alignment with project goals) are promoted to Feature Request issues and added to the Roadmap.

📖 **Read the full guide:** [docs/FEATURE_REQUESTS.md](docs/FEATURE_REQUESTS.md)

### 🐛 Bug Reports

Found a bug? [Report it here](https://github.com/agit8or1/clientst0r/issues/new?template=bug_report.yml)

### 🔨 Code Contributions

Ready to contribute code? See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Setup

```bash
# 1. Fork and clone
git clone https://github.com/agit8or1/clientst0r.git
cd clientst0r

# 2. Create feature branch
git checkout -b feature/amazing-feature

# 3. Make changes and test
python3 manage.py test

# 4. Commit and push
git commit -m 'Add amazing feature'
git push origin feature/amazing-feature

# 5. Open Pull Request
```

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Luna the GSD** - Development assistance, security review, and bug hunting
- **Django & DRF** - Excellent web framework
- **Bootstrap 5** - Beautiful, responsive UI
- **Font Awesome** - Icon library
- **Community** - All contributors and users

## 📊 Project Status

- **Version**: 3.8.0
- **Release Date**: February 2026
- **Status**: Production Ready
- **Maintained**: Yes
- **Security**: Continuous monitoring, automated scanning, HaveIBeenPwned integrated

## 💬 Support

- **Issues**: [GitHub Issues](https://github.com/agit8or1/clientst0r/issues)
- **Discussions**: [GitHub Discussions](https://github.com/agit8or1/clientst0r/discussions)
- **Security**: See [SECURITY.md](SECURITY.md) for vulnerability disclosure

## 💝 Supporting This Project

If you find Client St0r useful for your MSP or IT department, please consider supporting the developer's business: **[MSP Reboot](https://www.mspreboot.com)** - Professional MSP services and consulting.

Your support allows me to continue developing open-source tools like Client St0r and contribute to the MSP community. Thank you!

## 🗺️ Roadmap

- [x] Mobile-responsive UI improvements ✅
- [x] Advanced reporting and analytics ✅
- [x] Backup/restore functionality ✅
- [x] Docker deployment option (optional) ✅
- [x] Additional PSA/RMM integrations ✅
- [x] API v2 with GraphQL ✅
- [x] MagicPlan floor plan integration ✅
- [x] Mobile app (development plan complete) ✅

## ⚡ Performance

- Handles 1000+ assets per organization
- Sub-second page load times
- Efficient database queries
- Optimized for low-resource environments
- Horizontal scaling support

---

**Made with ❤️ and 🐕 by the Client St0r Team and Luna the German Shepherd**

---

## Keywords

Open-source IT Glue alternative · Self-hosted Hudu alternative · MSP documentation platform · IT documentation system · MSP knowledge base · self-hosted IT documentation
