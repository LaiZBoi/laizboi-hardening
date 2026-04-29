# Roadmap to PSA-mature parity (ConnectWise / Autotask / Halo)

> Living plan. Phase 1 in progress. Update as phases complete.

## Phasing principle

Foundations first — engines that *enable* downstream features. Then revenue-relevant features. Then ITIL + ecosystem + polish. Each phase is self-contained, ships incrementally via the Apply flow, and unblocks the next.

---

## Phase 1 — Contract / agreement engine deepening **(M · foundation)** [in progress]

ClientSt0r has the basics; mature PSAs have years of edge cases baked in. Without this, profitability reporting (Phase 3) is incomplete.

- Per-contract **overage rules** (different rate for billable hours past allowance — formalize what's half-modelled today)
- **Role-based inclusion/exclusion** — e.g. "T1 work included, T3 work billable at $X"
- **Prepaid block hours with rollover** — % rollover, expiry dates
- **Auto-renewal** — N days before end_date, optional auto-create-next-period
- **Proration** — mid-month start/cancel
- **Bundled services** — line items per agreement (managed AV + backup + monitoring as one)
- Agreement **profitability snapshot** — revenue vs. cost-of-delivery this period

## Phase 2 — Resource management foundation **(M · foundation)** [complete]

Required by capacity planning, profitability-by-tech, and scheduling improvements.

- `UserSkill`, `UserCertification` models
- `WorkingHours` (per user, per weekday + per-org override)
- `Holiday` / `LeaveRequest` (PTO booking with approval)
- `BillableTarget` (hours/week per tech, used for utilization KPI)
- **Capacity report** — forecast vs. scheduled vs. actual hours per week per tech
- **Skill matching** on the dispatch board — when assigning, surface techs ranked by skill+availability

## Phase 3 — Financial reporting + BI **(L · keystone)** [in progress]

Most-requested feature class. Big surface, but builds entirely on Phase 1+2 foundations.

- Canonical reporting query layer (`reports/queries.py`) — single source of truth for revenue, hours, costs *(3.1 — shipped v3.17.139)*
- **Profitability by**: client *(3.1 — shipped v3.17.139)* / contract / project / tech / agreement / ticket-type / closure-category
- **Effective hourly rate** report (revenue ÷ billable hours)
- **Revenue-leakage report** (unbilled time ≥ N days old + expired blocks + un-pushed invoices)
- **SLA trend report** — breach rate per client, per priority, over time
- **Margin analytics** by service line
- **Custom dashboards** — drag-and-drop widgets sourced from the canonical query layer
- **Scheduled reports** — cron-style, email PDF/CSV
- **Wallboard view** — TV-ready big-number display (active tickets, breaches, MTTR, queue depth)
- **Executive scorecard** — single page rolling 30-day MSP KPIs
- **Client-health score** — composite of SLA hits, ticket velocity, NPS proxy, billing aging

## Phase 4 — Procurement workflow **(L)**

Builds on existing distributor integrations (Ingram/Pax8/Synnex). Adds the workflow above the catalog.

- `PurchaseRequisition` → approval → `PurchaseOrder`
- POs auto-numbered + branded PDF + email-to-vendor (mirror Quote/Invoice pattern)
- **Receiving** — partial receive, back-orders, serial-number capture into Asset records
- **Vendor relationship** model — lead times, payment terms, contact preferences
- **Stock minimums + auto-replenish** suggestion
- **Drop-ship handling** — direct-to-customer flag with shipping address override
- **Fulfillment tracking** — link POs to tickets/projects, status pipeline
- **One-click PO from accepted quote** — converts quote line items to a draft PO

## Phase 5 — CRM / sales pipeline **(L)**

ConnectWise's wedge: PSA covers sales-pipeline-to-invoice. Currently we have quotes; we need everything *before* the quote.

- `Lead`, `Opportunity`, `Campaign`, `Commission` models
- Lead scoring + conversion funnel report
- Pipeline Kanban view (Discovery → Qualified → Proposal → Closed Won/Lost)
- **Quote-to-project automation** — one click on accepted quote spins a Project with tasks pre-populated from quote line items
- Sales-activity timeline per org/lead (calls, emails, meetings logged)
- Commission rules engine + per-tech commission report
- Lead capture from web form / IMAP / API

## Phase 6 — ITIL maturity **(M)**

Extends existing tickets + approvals; doesn't fork into a separate model layer.

- **Change requests** as a `Ticket.ticket_type='change'` extension with required CAB approval before status moves to "Implementing"
- **CAB workflow** — multi-approver gate (extends existing single-approval)
- **Problem records** — link N related tickets, root-cause analysis field, status pipeline
- **Release management** — group changes into release windows, freeze flags, rollback documentation
- **Service-catalog governance** — approval gate on catalog item changes

## Phase 7 — Outsourcing, integrations, polish **(continuous track)**

Not a single phase — runs alongside 1-6.

- **Outsourcing**: subcontractor org type, share-ticket-to-partner endpoint with HMAC, two-way sync of comments + status, optional billing markup
- **Integration SDK**: clean provider plugin interface; then steady drops — Datto Backup, ITGlue v2 import, Hudu sync, BackupRadar, ScreenConnect, Acronis, Liongard. Target: 5-10 new providers per quarter.
- **Polish backlog** — test coverage gaps, permission edge cases, audit improvements, mobile UI fixes, onboarding docs, import-tool maturity, API stability, third-party trust signals

## Phase 9 — Security alert ingestion: EDR / AV / Firewall on the dashboard **(M)**

MSPs run a stack of security tools that all alert independently — SentinelOne, CrowdStrike, Defender, Sophos, Bitdefender, Webroot, Fortinet, Palo Alto, Sonicwall, etc. The PSA dashboard should aggregate alerts from all of them, surface critical issues per client, and let techs triage from one screen.

### Sub-phase 9.1 — Connection framework

- Generic `SecurityVendorConnection` model: provider type (edr/av/firewall), org-scoped credentials (encrypted), poll interval, last_sync_at, last_error
- Provider type enums covering EDR (CrowdStrike Falcon, SentinelOne Singularity, Microsoft Defender for Endpoint, Sophos Central, Huntress, ThreatLocker), AV (Bitdefender GravityZone, Webroot, Malwarebytes, ESET), Firewall (Fortinet FortiGate, Palo Alto, Sonicwall, Cisco Meraki MX, Sophos XG, pfSense)
- Two-way mapping: each connection optionally pinned to a client `Organization` (multi-tenant alert routing)
- Reuses existing integration patterns (status pill from v3.17.135)

### Sub-phase 9.2 — Alert model + poller

- `SecurityAlert` model: connection FK, external_id (dedupe key), severity (info/low/medium/high/critical), title, description, asset hint, raw_payload (JSON), seen_at, acknowledged_by, acknowledged_at, status (new/acknowledged/dismissed/resolved), auto_ticket FK (optional)
- Per-vendor poller adapters returning normalized alert dicts; one mgmt cmd `psa_poll_security_alerts` runs every 5 min via cron
- Idempotent dedupe by (connection_id, external_id)
- Alert webhook receiver endpoint `/security-alerts/webhook/<token>/` for vendors that push (HMAC-verified)

### Sub-phase 9.3 — Dashboard + auto-ticketing

- New "Security alerts" card on the global dashboard + per-org dashboard: count by severity in the last 24h, drill-down to filtered list, color-coded
- New page `/psa/security-alerts/` — full triage UI; filter by severity / vendor / client / status
- Per-vendor or per-severity rule: auto-create a PSA ticket from an alert (priority mapped from severity, queue configurable, assignee from a default rule). Mirrors workflow rules engine (v3.17.111).
- Bulk "ack / dismiss / convert to ticket" actions

### Sub-phase 9.4 — Reporting

- "Mean time to acknowledge" metric per client / per vendor
- Suppression rules (don't auto-ticket from this vendor between 22:00–06:00 etc.)
- Weekly digest email to client of unresolved alerts (opt-in)

**Sizing:** **M** — 9.1+9.2 = 2 weeks (one reference adapter + the framework), 9.3 = 2 weeks, 9.4 = 1 week. Each new vendor adapter beyond the first is ~2-4 days. Ship the framework + 1-2 reference adapters first, then add vendors as customer demand arrives.

**Dependencies:** none. Can run alongside Phase 2 / 3 / 8.

## Phase 8 — Native mobile apps (iOS + Android) with GPS auto-time + Timeclock **(L · keystone)**

Reverses the earlier "PWA only" deferral. The combination of GPS auto-documentation + employee timeclock makes this a force-multiplier for billable-hours capture, not just a UX improvement.

### Sub-phase 8.1 — Backend foundation
- `TechnicianLocation` model — append-only GPS pings (lat/lon/accuracy/timestamp/source); retention policy + per-org enable flag.
- `TimeclockEntry` model — clock-in / clock-out events with tech, organization, location, optional ticket, optional project, source (`'mobile'` / `'web'` / `'manual'`); derives a `TimeEntry` row on clock-out so existing billing rolls up unchanged.
- `ClientSiteGeofence` model — per-client polygon or radius around their physical address(es). Used to auto-detect "tech is on site" for ticket time tracking.
- REST API additions: `/api/v2/mobile/locations/`, `/api/v2/mobile/timeclock/`, `/api/v2/mobile/active-ticket/`.
- Token auth (long-lived per-device tokens stored in `MobileDevice` model with revoke-on-demand).

### Sub-phase 8.2 — GPS auto-documentation engine
- Background worker: every GPS ping with the tech "inside a client geofence" auto-starts a `TicketTimeEntry` against their currently-active ticket for that client (or creates a placeholder if no ticket open).
- On exit-geofence event: stop the time entry, write the duration, optionally prompt the tech to confirm + add notes via push notification.
- Selectable per-tech: **Always on** / **Ask first** / **Off**. Per-tech UserProfile flag.
- Audit log every auto-time event so disputed billing can be traced.

### Sub-phase 8.3 — Timeclock feature
- Web UI: Timeclock dashboard at `/timeclock/` for staff to view who's clocked in, total hours per pay period, exception flags (long shifts, missing clock-out).
- Mobile UI: prominent "Clock in / Clock out" button on the app home screen. Optional tie to the active ticket.
- Selectable per-org and per-tech: required vs optional, with vs without GPS context, separate from per-ticket time tracking.
- Payroll export — CSV per pay period, hooks for QuickBooks Time / Gusto / etc. (defer the integration; just structured export first).

### Sub-phase 8.4 — App build
- React Native (Expo SDK 51+ with EAS cloud build — revisits the earlier "no Expo accounts" rejection; required for store submission).
- Auth: Azure SSO + email/password fallback; session-token cookie not used (mobile uses long-lived bearer).
- Screens: Dashboard / My Tickets / Active Ticket / Clock In-Out / Map / Settings.
- Background location: foreground-only by default, opt-in for background; iOS "Always" permission requested only for techs who enable Always-on auto-time.
- Push notifications via FCM/APNS for ticket assignment, clock-in reminders, geofence exit prompts.

### Sub-phase 8.5 — Privacy + safeguards
- **Off-shift suppression**: GPS pings outside the tech's `WorkingHours` (Phase 2) are dropped at the API layer — never stored.
- Per-tech UI to view + delete their own location history.
- Org-admin retention policy (default: 90 days).
- Geofence-only mode: store only "entered/exited geofence X at time T", never raw lat/lon.
- Audit trail of every location-history view + export.

**Dependencies:** Phase 2 (`WorkingHours` for off-shift suppression). Recommended before Phase 3 (so timeclock data flows into utilization reporting).

**Sizing:** **L** — backend foundation 2 weeks, auto-time engine 2 weeks, timeclock UI 1-2 weeks, mobile app build 4-6 weeks, privacy hardening 1 week. ~10-13 weeks total. Sub-phase 8.1 is the first concrete deliverable and is a useful release on its own (web-only timeclock + GPS API endpoints) even before any mobile app ships.

---

## What's explicitly NOT in this plan

- Multi-currency beyond per-record `currency` field
- Multi-language support beyond Django i18n hooks
- ~~Native mobile apps (PWA only — per memory)~~ → **moved into Phase 8**
- Marketplace/app store
- White-label tenant branding beyond per-org logo

---

## Sizing

| Phase | Size | Estimated effort | Dependencies |
|---|---|---|---|
| 1 — Contract engine | M | 2-3 weeks | none — **1.1 + 1.2 shipped (v3.17.126 / v3.17.130)** |
| 2 — Resource mgmt | M | 2-3 weeks | none |
| 3 — Financial reporting + BI | L | 4-6 weeks | 1, 2 |
| 4 — Procurement | L | 4-5 weeks | none (ideally after 1) |
| 5 — CRM | L | 4-5 weeks | none |
| 6 — ITIL | M | 2-3 weeks | none |
| 7 — Outsourcing + ecosystem + polish | Continuous | ongoing | runs alongside |
| 8 — Mobile apps + GPS auto-time + Timeclock | L | 10-13 weeks | Phase 2 (WorkingHours); ideally before Phase 3 |
| 9 — Security alert ingestion (EDR / AV / Firewall) | M | 5 weeks | none — can run alongside any |

**Phases 1-6**: ~4 months of focused work at the established cadence.

**Phase 8** adds another ~2.5-3 months on top, but sub-phase 8.1 (web timeclock + GPS APIs) is shippable as a 2-week chunk well before the mobile app itself.
