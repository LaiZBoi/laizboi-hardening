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

## Phase 2 — Resource management foundation **(M · foundation)**

Required by capacity planning, profitability-by-tech, and scheduling improvements.

- `UserSkill`, `UserCertification` models
- `WorkingHours` (per user, per weekday + per-org override)
- `Holiday` / `LeaveRequest` (PTO booking with approval)
- `BillableTarget` (hours/week per tech, used for utilization KPI)
- **Capacity report** — forecast vs. scheduled vs. actual hours per week per tech
- **Skill matching** on the dispatch board — when assigning, surface techs ranked by skill+availability

## Phase 3 — Financial reporting + BI **(L · keystone)**

Most-requested feature class. Big surface, but builds entirely on Phase 1+2 foundations.

- Canonical reporting query layer (`reports/queries.py`) — single source of truth for revenue, hours, costs
- **Profitability by**: client / contract / project / tech / agreement / ticket-type / closure-category
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

---

## What's explicitly NOT in this plan

- Multi-currency beyond per-record `currency` field
- Multi-language support beyond Django i18n hooks
- Native mobile apps (PWA only — per memory)
- Marketplace/app store
- White-label tenant branding beyond per-org logo

---

## Sizing

| Phase | Size | Estimated effort | Dependencies |
|---|---|---|---|
| 1 — Contract engine | M | 2-3 weeks | none |
| 2 — Resource mgmt | M | 2-3 weeks | none |
| 3 — Financial reporting + BI | L | 4-6 weeks | 1, 2 |
| 4 — Procurement | L | 4-5 weeks | none (ideally after 1) |
| 5 — CRM | L | 4-5 weeks | none |
| 6 — ITIL | M | 2-3 weeks | none |
| 7 — Outsourcing + ecosystem + polish | Continuous | ongoing | runs alongside |

**Phases 1-6**: ~4 months of focused work at the established cadence.
