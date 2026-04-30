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

## Phase 3 — Financial reporting + BI **(L · keystone)** [complete]

Most-requested feature class. Big surface, but builds entirely on Phase 1+2 foundations.

- Canonical reporting query layer (`reports/queries.py`) — single source of truth for revenue, hours, costs *(3.1 — shipped v3.17.139)*
- **Profitability by**: client *(3.1 — shipped v3.17.139)* / contract / project / tech *(3.2 — shipped v3.17.140)* / agreement / ticket-type / closure-category
- **Effective hourly rate** report (revenue ÷ billable hours) *(3.3 — shipped v3.17.141)*
- **Revenue-leakage report** (unbilled time ≥ N days old + expired blocks + un-pushed invoices) *(3.3 — shipped v3.17.141)*
- **SLA trend report** — breach rate per client, per priority, over time *(3.4 — shipped v3.17.143)*
- **Margin analytics** by service line *(3.4 — shipped v3.17.143)*
- **Custom dashboards** — drag-and-drop widgets sourced from the canonical query layer *(3.5 — shipped v3.17.142)*
- **Scheduled reports** — cron-style, email PDF/CSV *(3.6 wave B — shipped v3.17.147)*
- **Wallboard view** — TV-ready big-number display (active tickets, breaches, MTTR, queue depth) *(3.6 wave A — shipped v3.17.146)*
- **Executive scorecard** — single page rolling 30-day MSP KPIs *(3.6 wave A — shipped v3.17.146)*
- **Client-health score** — composite of SLA hits, ticket velocity, NPS proxy, billing aging *(3.6 wave B — shipped v3.17.147)*

## Phase 4 — Procurement workflow **(L)** **— shipped**

Builds on existing distributor integrations (Ingram/Pax8/Synnex). Adds the workflow above the catalog.

- `PurchaseRequisition` → approval → `PurchaseOrder` *(4.1 — shipped v3.17.148)*
- POs auto-numbered + branded PDF + email-to-vendor (mirror Quote/Invoice pattern) *(4.1 — shipped v3.17.148)*
- **Receiving** — partial receive, back-orders, serial-number capture into Asset records *(4.2 — shipped v3.17.149)*
- **Vendor relationship** model — lead times, payment terms, contact preferences *(4.3 — shipped v3.17.150)*
- **Stock minimums + auto-replenish** suggestion *(4.3 — shipped v3.17.150)*
- **Drop-ship handling** — direct-to-customer flag with shipping address override *(4.1 — shipped v3.17.148)*
- **Fulfillment tracking** — link POs to tickets/projects, status pipeline *(4.1 — shipped v3.17.148)*
- **One-click PO from accepted quote** — converts quote line items to a draft PO *(4.4 — shipped v3.17.151)*

## Phase 5 — CRM / sales pipeline **(L)** **— complete**

ConnectWise's wedge: PSA covers sales-pipeline-to-invoice. Currently we have quotes; we need everything *before* the quote.

- `Lead`, `Opportunity`, `Campaign`, `Commission` models *(5.1 — shipped v3.17.152; 5.2 — shipped v3.17.153)*
- Lead scoring + conversion funnel report *(5.2 — shipped v3.17.153)*
- Pipeline Kanban view (Discovery → Qualified → Proposal → Closed Won/Lost) *(5.1 — shipped v3.17.152)*
- **Quote-to-project automation** — one click on accepted quote spins a Project with tasks pre-populated from quote line items *(5.1 — opportunity → quote shipped v3.17.152; quote → project deferred to Phase 7 polish backlog)*
- Sales-activity timeline per org/lead (calls, emails, meetings logged) *(5.3 — shipped v3.17.155)*
- Commission rules engine + per-tech commission report *(5.2 — shipped v3.17.153)*
- Lead capture from web form / IMAP / API *(5.3 — web + API shipped v3.17.155; IMAP poller stubbed, full implementation deferred to 5.4 follow-up)*

## Phase 6 — ITIL maturity **(M)**

Extends existing tickets + approvals; doesn't fork into a separate model layer.

- **Change requests** as a `Ticket.ticket_type='change'` extension with required CAB approval before status moves to "Implementing" *(6.1 — shipped v3.17.158)*
- **CAB workflow** — multi-approver gate (extends existing single-approval) *(6.1 — shipped v3.17.158)*
- **Problem records** — link N related tickets, root-cause analysis field, status pipeline *(6.2 — shipped v3.17.160)*
- **Release management** — group changes into release windows, freeze flags, rollback documentation *(6.3 — pending)*
- **Service-catalog governance** — approval gate on catalog item changes *(6.3 — pending)*

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

# Long-term roadmap — Phases 10-23

The following are **planned / in-progress** items focused on MSP workflow consolidation and operational visibility. None are positioned as fully implemented. Items overlapping with already-shipped phases are noted as "Extends X" so the deltas are explicit. AI-assisted features are clearly marked **OPTIONAL AI**.

## Phase 10 — Advanced Email-to-Ticket Engine **(M)**

**Roadmap item:** Advanced Email Processing & Ticket Intelligence. Extends the basic IMAP poller already shipped (v3.17.83+).

Planned capabilities:
- Advanced inbound email parsing (HTML + plain-text fallback)
- Thread reconstruction across replies + forwards
- Reply correlation by Message-ID + In-Reply-To headers (more reliable than current subject-regex match)
- Signature stripping
- Loop detection (ignore auto-responders)
- Spam scoring before ticket creation
- Attachment extraction with MIME-type allowlist
- Automatic contact association (match sender email → existing contact / membership)
- Per-client parsing rules
- Ticket categorization (rule-based)
- Ticket tagging
- Email security validation (SPF / DKIM / DMARC inspection)
- Ticket summarization (**OPTIONAL AI**)
- Intent detection (**OPTIONAL AI**)

**Goal:** Reduce dispatcher and technician overhead while improving ticket workflow accuracy.

## Phase 11 — Advanced Dispatch & Technician Scheduling **(M)**

**Roadmap item:** Dispatch Optimization & Technician Coordination. Extends Phase 2 (resourcing) + the dispatch board (v3.17.112) + skill ranking (v3.17.138).

Planned capabilities:
- Drag/drop dispatch board *(shipped — v3.17.112)*
- Technician scheduling *(partial — Phase 2 WorkingHours shipped)*
- Shift management
- PTO conflict awareness *(extends Phase 2.2 LeaveRequest)*
- Calendar conflict detection
- Recurring onsite scheduling
- Dispatch prioritization (auto-rank queue by SLA + priority)
- SLA-aware dispatching
- Technician utilization metrics *(shipped — Phase 2.3 capacity report)*
- Geo-aware technician routing
- Travel time estimation
- Dispatch heatmaps

**Goal:** Improve technician coordination and service workflow efficiency.

## Phase 12 — Customer Communication Workflows **(M)**

**Roadmap item:** Enhanced Client Communication & Portal Workflows. Extends the existing customer portal.

Planned capabilities:
- Customer satisfaction (CSAT) surveys post-ticket-close
- Branded client portals *(extends Phase v3.17.112 per-org branding)*
- Portal announcements
- Customer approval workflows
- Threaded customer communication
- SMS ticket communication (using existing SMS provider plumbing)
- Customer escalation workflows
- Customer-facing knowledge base *(partial — `Document.is_client_visible` shipped)*
- Customer ticket voting / prioritization
- Secure customer messaging
- Customer notification preferences

**Goal:** Improve client interaction visibility and communication consistency.

## Phase 13 — Procurement & Lifecycle Management **(M)**

**Roadmap item:** Advanced Procurement & Asset Lifecycle Management. Extends Phase 4 (Procurement) + the asset lifespan tracking already shipped.

Planned capabilities:
- Serial lifecycle tracking *(partial — serial capture shipped v3.17.149)*
- Warranty expiration tracking
- Vendor inventory checks (live stock from distributor APIs)
- Procurement approval workflows *(shipped — Phase 4.1 PR/PO)*
- Purchase receiving workflows *(shipped — Phase 4.2)*
- Margin analytics on resold hardware
- RMA tracking (return / replace lifecycle)
- Asset lifecycle scoring (composite age × usage × warranty)
- Procurement forecasting from historical PR/PO data
- Recurring purchasing templates (e.g. "monthly toner refill")
- Vendor cost history (price-at-time-of-PO trend)
- Procurement reporting

**Goal:** Improve operational procurement visibility and hardware lifecycle management.

## Phase 14 — Visual Workflow Automation Engine **(L)**

**Roadmap item:** Visual Workflow & Operational Automation Engine. Extends the workflow rules engine (v3.17.111) + visual rule builder (v3.17.112).

Planned capabilities:
- Visual workflow builder *(partial — visual rule builder shipped v3.17.112)*
- Conditional workflow routing (branching based on ticket fields)
- SLA-driven automation
- Ticket orchestration (multi-step automated sequences)
- Approval chains (extends Phase 6.1 CAB pattern)
- Trigger / action workflows
- Escalation logic (auto-escalate after N hours unanswered)
- Scheduled automations
- State-based workflows
- Dynamic technician assignment (round-robin, skill-match, load-balanced)
- Workflow templates
- Cross-module workflow integration (PSA ↔ procurement ↔ CRM)
- AI-assisted workflow suggestions (**OPTIONAL AI**)

**Goal:** Reduce repetitive operational tasks while improving workflow consistency.

## Phase 15 — Recurring Billing & Contract Management **(M)**

**Roadmap item:** Recurring Billing & Financial Workflow Automation. Extends Phase 1 (contract engine) + invoicing.

Planned capabilities:
- Recurring invoices (auto-generated from contract bundles)
- Usage-based billing (per-seat / per-device / per-GB metered)
- Contract renewals *(partial — auto-renewal cron shipped Phase 1.2)*
- Proration handling
- Service bundles *(shipped — Phase 1.2 ContractBundleItem)*
- Billing reconciliation
- Late fee automation
- ACH / payment integrations (Stripe ACH, GoCardless, etc.)
- MRR forecasting
- Contract profitability tracking *(partial — Phase 3 profitability-by-contract shipped)*
- Invoice automation
- Tax handling support (Avalara / TaxJar integrations)
- Subscription lifecycle management

**Goal:** Improve recurring service management and operational billing visibility.

## Phase 16 — Documentation Relationship Mapping **(M)**

**Roadmap item:** Infrastructure Relationship Mapping & Dependency Visualization.

Planned capabilities:
- Asset relationship mapping (parent / child / depends-on)
- Visual dependency graphs (DAG renders)
- Topology visualization
- Nested organization mapping (extends Phase 17 multi-location)
- Shared infrastructure relationships
- Automatic asset linking (heuristic — same subnet, same rack, etc.)
- Infrastructure dependency chains
- Rack relationship visualization *(partial — racks already shipped)*
- Service relationship tracking ("Email service depends on Exchange Online + DNS X + Connector Y")
- Documentation inheritance (child sites inherit parent SOPs)

**Goal:** Improve infrastructure visibility and operational context awareness.

## Phase 17 — Advanced Asset Intelligence **(L)**

**Roadmap item:** Asset Intelligence & Infrastructure Visibility.

Planned capabilities:
- Asset drift detection (compare current state vs. last-known baseline)
- Baseline comparison
- Software compliance auditing
- Hardware lifecycle scoring (composite — see Phase 13)
- Warranty lookups (vendor API integrations)
- Patch correlation (this CVE matches these N assets)
- Smart asset grouping (auto-cohort by role/version/location)
- Vulnerability-to-ticket linking
- Configuration monitoring
- Operational health scoring per asset
- Automated remediation suggestions (**OPTIONAL AI**)

**Goal:** Improve infrastructure awareness and proactive operational management.

## Phase 18 — Multi-Location Client Hierarchy **(M)**

**Roadmap item:** Advanced Multi-Location Organization Management.

Planned capabilities:
- Parent / child organizations (`Organization.parent` self-FK)
- Multi-site hierarchy
- Shared infrastructure inheritance
- Location-specific documentation
- Location-level contacts
- Site-level SLA assignment (override parent SLA)
- Site filtering on every list page
- Shared services mapping
- Regional operational views (group sites by region)
- Multi-location reporting

**Goal:** Improve management of larger MSP client environments.

## Phase 19 — Advanced Reporting & Analytics **(continuous)**

**Roadmap item:** Operational Analytics & Business Intelligence. Extends Phase 3 (Financial Reporting + BI) — these are the next-tier analytics on top of the canonical query layer.

Planned capabilities:
- Technician utilization reporting *(shipped — Phase 2.3 + 3.2)*
- SLA forecasting (predict breach risk before it happens)
- Ticket aging analytics
- Contract profitability *(shipped — Phase 3.2)*
- Quote conversion tracking
- Customer health scoring *(shipped — Phase 3.6B)*
- Executive dashboards *(shipped — Phase 3.6A scorecard)*
- KPI dashboards
- Operational metrics
- Workflow performance analytics
- Trend analysis
- Capacity forecasting
- Reporting exports (already CSV; add PDF + scheduled email)

**Goal:** Provide MSP operational visibility and business insight.

## Phase 20 — Approval & Change Management Workflows **(M)**

**Roadmap item:** Approval Routing & Change Management. Extends Phase 6.1 (CAB) + the existing approvals queue.

Planned capabilities:
- Multi-stage approvals (sequential gates: tech lead → manager → CAB)
- Change advisory workflows *(shipped — Phase 6.1 CAB)*
- Quote approval routing (gate quotes above threshold)
- Financial approval chains (POs / invoices over $X)
- Escalation approvals (auto-escalate idle approvals)
- Conditional approvals (rules: "if value > $5k, route to owner")
- Approval audit trails *(partial — single-approver audit shipped)*
- Workflow enforcement
- Change tracking
- Operational sign-off workflows

**Goal:** Improve workflow accountability and operational governance.

## Phase 21 — Advanced Mobile Technician Workflows **(L)**

**Roadmap item:** Mobile Technician Workflow Expansion. Extends Phase 8 (mobile apps + GPS auto-time + Timeclock).

Planned capabilities:
- Offline workflow support (work without connectivity, sync on reconnect)
- Camera uploads
- Barcode scanning *(partial — vehicle inventory QR shipped)*
- QR scanning *(partial — same)*
- NFC scanning
- GPS time tracking *(planned — Phase 8.2)*
- Technician signatures (canvas signature pad on completion)
- Onsite checklist enforcement (must complete X before close)
- Push notifications *(planned — Phase 8.4)*
- Voice-to-ticket workflows
- Mobile dispatch routing (turn-by-turn from current GPS to next ticket)
- Mobile asset lookup

**Goal:** Improve field technician workflow efficiency and mobility.

## Phase 22 — Knowledge Base & SOP Management **(M)**

**Roadmap item:** Knowledge Base & Operational Documentation Expansion. Extends Phase v3.17.128 (KB tree) + v3.17.134 (KB perms).

Planned capabilities:
- Knowledge base versioning (history of edits, rollback)
- Article approvals (review-before-publish gate)
- Article ownership
- SOP workflows (links the KB to step-by-step Process executions)
- Review reminders (article hasn't been reviewed in 90 days → email owner)
- Internal / external KB separation *(partial — `is_client_visible` shipped)*
- KB analytics (most-viewed, least-viewed, dead links)
- Public knowledge publishing (selected articles → public URL)
- Documentation lifecycle management (draft → published → archived)
- Linked SOP automation (KB article triggers a workflow run)

**Goal:** Improve operational knowledge management and documentation governance.

## Phase 23 — Security Event & Incident Workflows **(L)**

**Roadmap item:** Security Event Correlation & Incident Operations. Extends Phase 9 (security alert ingestion) — these are the next-tier incident workflows on top of the basic alert dashboard.

Planned capabilities:
- Security event ingestion *(planned — Phase 9.1)*
- SIEM integrations
- Vulnerability correlation (CVE → affected assets → exposure)
- CVE-to-ticket workflows
- Security incident timelines
- Exposure scoring
- Incident SLA tracking
- Automated remediation workflows
- Security dashboarding *(planned — Phase 9.3)*
- Threat visibility
- Security event reporting *(planned — Phase 9.4)*
- AI-assisted incident summarization (**OPTIONAL AI**)

Potential integrations:
- Huntress
- SentinelOne
- CrowdStrike
- Microsoft security ecosystem (Defender, Sentinel)
- Sophos
- Other security platforms

**Goal:** Improve operational security visibility and incident response workflows.

## Phase 24 — Native RMM Agent + Endpoint Management **(XL — major undertaking)**

**Roadmap item:** First-party RMM stack — patch management, scripting, remote access. Today the project integrates with external RMMs (Tactical RMM, NinjaOne, Datto, Atera, ConnectWise Automate); this phase adds an in-house alternative for installs that want a self-hosted RMM.

Planned capabilities:
- Lightweight cross-platform agent (Go or Python) that registers via secure token
- Endpoint inventory (CPU / RAM / disk / installed software / network state)
- **Patch management** — Windows Update / apt / yum / Homebrew push + scheduling + rollback. Extends OS Package Scanner shipped earlier.
- **Scripting / automation against endpoints** — sandboxed PowerShell / Bash / Python with audit log + approval gate for high-risk scripts
- **Remote access** — browser-launched session over WebSocket relay (no third-party agent install). Optional fallback to existing ScreenConnect / RustDesk integration.
- Health monitoring + drift detection (extends Phase 17)
- Performance metrics + alerting
- Software deployment + uninstall
- Background task queue (push command → agent ack → result back via webhook)

Dependencies: Phase 9 (security framework — auth + audit), Phase 17 (asset intelligence baselines). Significantly larger scope than other phases — likely a multi-quarter program. Recommended only if customer demand for self-hosted RMM is strong.

**Goal:** Provide a self-hosted RMM option that integrates natively with the existing PSA + asset stack instead of requiring a third-party RMM.

## Phase 25 — Mature Timesheet Approval Workflows **(M)**

**Roadmap item:** Formal time-entry approval pipeline. Today `psa.TicketTimeEntry` is logged ad-hoc by techs; there's no formal weekly approval before billing.

Planned capabilities:
- Weekly timesheet model — groups a tech's TicketTimeEntry rows for a payroll period
- Submit → review → approve / reject pipeline (reuses Phase 6.1 CAB approval pattern)
- Multi-tier approval (tech → team lead → finance)
- Per-entry rejection with note ("re-classify this billable entry as project work")
- Lock approved timesheets — entries can't be retroactively edited after approval
- Auto-reminder cron at end of payroll period for un-submitted timesheets
- Bulk-approve UI for managers
- Export approved timesheets to payroll (CSV / QuickBooks Time / Gusto)
- Audit trail per entry: who approved, when, with what note

Dependencies: extends Phase 2 (BillableTarget + utilization) + the existing approval queue pattern.

**Goal:** Add billing-grade rigor to time entries before they flow into invoices and commissions.

## Phase 26 — Custom Report Writer + Saved Queries **(L)**

**Roadmap item:** User-defined reports without writing Python. Today reports are templated (Phase 3 ships ~15 canned reports); this phase lets non-developers build their own.

Planned capabilities:
- Visual query builder — pick model (Ticket / Invoice / TimeEntry / Asset / etc), filters, group-by, aggregates, sort, limit
- Saved query model — per-org or shared
- Run as report (renders as table + auto-chart for numeric columns)
- Schedule a saved query as a recurring email-PDF (extends Phase 3.6 scheduled-reports runner)
- Pin a saved query as a dashboard widget (extends Phase 3.5 widget registry)
- Export to CSV / JSON / Excel
- SQL escape hatch for power users (gated behind a separate permission; sandboxed read-only DB connection)
- Report-template marketplace — share / import community report definitions

Dependencies: Phase 3.5 (dashboards), Phase 3.6 (scheduled reports).

**Goal:** Reduce the gap between what owners want to know and what's pre-templated.

## Phase 27 — Advanced Accounting Reconciliation **(M)**

**Roadmap item:** Deeper accounting integration than the basic invoice push that ships today (QBO + Xero — Phase shipped earlier). Adds true reconciliation between Client St0r's books and the accounting system.

Planned capabilities:
- Bidirectional payment sync — when a payment lands in QBO/Xero, mark the source Invoice as paid
- Invoice deduplication detection (catch double-pushes)
- Unpaid-vs-pushed reconciliation report (what's invoiced here but missing in QBO?)
- Per-invoice line-item mapping to GL accounts (revenue vs. cost-of-services-sold splits)
- Tax reconciliation (compare what we calculated vs. what QBO recorded)
- Accounts receivable aging tied directly back to QBO/Xero AR
- Bank-account reconciliation hooks (mark which payments matched which bank-deposit batches)
- Refund / credit-memo workflows (today only credit-charge type exists)
- Multi-entity / multi-book support for MSPs operating multiple legal entities
- Audit trail of every accounting-system interaction (req/resp pairs stored encrypted)

Dependencies: existing AccountingConnection pattern. Builds on Phase 15.

**Goal:** Eliminate manual cross-checking between Client St0r and the accounting system.

## Phase 28 — Browser Extension + Offline Vault Access **(L)**

**Roadmap item:** Chrome / Firefox / Edge extension for password autofill from the vault, plus an offline-capable PWA mode for read-access to the vault.

Planned capabilities:
- WebExtension (cross-browser via WebExtensions API)
- One-click autofill on login pages from `vault.Password` matched by URL pattern
- Master-password unlock (re-derive AES-GCM key locally; never transmit master)
- Per-organization isolation (extension UI matches the active org context in-app)
- Offline-encrypted vault cache — last-fetched passwords are stored encrypted under a session key, valid for N hours so a tech can still pull a credential when the server is unreachable
- TOTP code generation in-extension (existing `totp_secret` field on Password)
- Audit log of every autofill (logged when the extension reconnects)
- Generate-strong-password helper (matches the existing in-app generator)
- Browser-extension specific permissions on RoleTemplate (`vault_extension_use`, `vault_extension_offline_cache`)

Dependencies: existing vault model + AES-GCM key infra. Browser extension is a separate codebase + store-submission process.

**Goal:** Match the IT Glue / Bitwarden experience that techs already expect.

## Phase 29 — Commercial Operations Ecosystem **(continuous · meta)**

**Roadmap item:** Not a feature — the *commercial* support stack around the open-source product: SLAs, professional onboarding, commercial support tiers. This is the ecosystem an enterprise buyer evaluates before adopting a self-hosted MSP platform.

Planned capabilities:
- Tiered commercial support offering (Bronze / Silver / Gold / Platinum) with response-time SLAs
- Paid onboarding service — installation, data migration, integration setup
- Migration scripts for ConnectWise, Autotask, Halo, IT Glue, Hudu (one-time imports)
- Architect-led implementation packages for installs > 50 techs
- Per-customer commercial-support portal (dedicated case queue, escalation path)
- Public status page (https://status.huduglue.example) with planned-maintenance windows
- Roadmap voting page where commercial customers can prioritize phases
- Quarterly customer advisory board
- Public security-disclosure / responsible-disclosure program with bug-bounty
- SOC 2 readiness (controls inventory + audit trail + auditor-ready evidence pack)
- Trust portal: vendor security questionnaire pre-answers, DPIA, sub-processor list
- Commercial license / EULA optionality for enterprise buyers who can't accept MIT-only
- Reseller / partner program for IT consultancies who deploy on customer premises

Dependencies: this runs alongside the technical phases — the commercial program matures with the product. Items here are not unit-testable; they're operational + organizational.

**Goal:** Provide the commercial trust signals an enterprise buyer expects on top of the open-source product.

## Phase 30 — Endpoint Remote Access (alternative to Phase 24) **(L)**

If Phase 24 (Native RMM) is too large to take on directly, Phase 30 is the smaller-scope remote-access-only slice that doesn't require building a full RMM agent.

Planned capabilities:
- WebSocket relay service: tech browser ↔ relay ↔ end-user agent
- Browser-only client (canvas-based VNC-over-WebSocket or RDP-relay)
- Lightweight per-endpoint helper agent (Windows / macOS / Linux) — accepts inbound relay sessions only; no scripting / no scheduling
- Recording of sessions (opt-in per client) for audit
- Per-session approval prompt on the user's screen
- Permission gates: `remote_access_view`, `remote_access_initiate`, `remote_access_record`
- Optional integration with existing ScreenConnect / RustDesk / MeshCentral (preferred for installs that already have one)

Dependencies: none direct. Strictly smaller than Phase 24.

**Goal:** Provide remote access without building a full RMM stack.

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
| 3 — Financial reporting + BI | L | 4-6 weeks | 1, 2 — **complete (v3.17.139–147)** |
| 4 — Procurement | L | 4-5 weeks | none (ideally after 1) |
| 5 — CRM | L | 4-5 weeks | none |
| 6 — ITIL | M | 2-3 weeks | none |
| 7 — Outsourcing + ecosystem + polish | Continuous | ongoing | runs alongside |
| 8 — Mobile apps + GPS auto-time + Timeclock | L | 10-13 weeks | Phase 2 (WorkingHours); ideally before Phase 3 |
| 9 — Security alert ingestion (EDR / AV / Firewall) | M | 5 weeks | none — can run alongside any |
| 10 — Advanced Email-to-Ticket Engine | M | 2-3 weeks | extends existing IMAP poller |
| 11 — Advanced Dispatch & Tech Scheduling | M | 2-3 weeks | extends Phase 2 + dispatch board |
| 12 — Customer Communication Workflows | M | 2-3 weeks | extends customer portal |
| 13 — Procurement & Lifecycle Mgmt | M | 2-3 weeks | extends Phase 4 |
| 14 — Visual Workflow Automation Engine | L | 4-5 weeks | extends workflow rules engine |
| 15 — Recurring Billing & Contract Mgmt | M | 3-4 weeks | extends Phase 1 |
| 16 — Documentation Relationship Mapping | M | 2-3 weeks | none |
| 17 — Advanced Asset Intelligence | L | 4-5 weeks | extends RMM sync |
| 18 — Multi-Location Client Hierarchy | M | 2-3 weeks | none |
| 19 — Advanced Reporting & Analytics | Continuous | ongoing | extends Phase 3 |
| 20 — Approval & Change Management Workflows | M | 2-3 weeks | extends Phase 6.1 |
| 21 — Advanced Mobile Technician Workflows | L | 4-6 weeks | requires Phase 8 |
| 22 — Knowledge Base & SOP Management | M | 2-3 weeks | extends KB v3.17.128/134 |
| 23 — Security Event & Incident Workflows | L | 4-6 weeks | requires Phase 9 |
| 24 — Native RMM Agent + Endpoint Mgmt | XL | 6+ months | major undertaking — see notes |
| 25 — Mature Timesheet Approval Workflows | M | 2-3 weeks | extends Phase 2 + approvals |
| 26 — Custom Report Writer + Saved Queries | L | 4-5 weeks | extends Phase 3.5 + 3.6 |
| 27 — Advanced Accounting Reconciliation | M | 2-3 weeks | extends QBO/Xero connection |
| 28 — Browser Extension + Offline Vault Access | L | 4-5 weeks | separate codebase |
| 29 — Commercial Operations Ecosystem | Continuous · meta | ongoing | runs alongside |
| 30 — Endpoint Remote Access (alt to Phase 24) | L | 4-6 weeks | none |

**Phases 1-6**: ~4 months of focused work at the established cadence.

**Phase 8** adds another ~2.5-3 months on top, but sub-phase 8.1 (web timeclock + GPS APIs) is shippable as a 2-week chunk well before the mobile app itself.

**Phases 10-30**: long-term operational deepening. None should be positioned as fully implemented today; each extends or adds to the foundations already shipped. Items overlapping shipped phases call out the deltas explicitly. AI-assisted features are explicitly **OPTIONAL AI** and gated by `psa_ai_enabled` (existing pattern from v3.17.125 AI Triage).

**Phase 24** is by far the largest — building a self-hosted RMM agent + patch management + scripting + remote access is a multi-quarter program. Phase 30 is the smaller "remote access only" alternative for installs that don't need full RMM.
