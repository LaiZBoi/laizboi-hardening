# PSA Roadmap

Living document for the native PSA built into clientst0r. Tracks what
ships now (the foundation), what's queued, and how each workstream
plugs into existing models so we don't duplicate.

## Guiding principles

1. **Off by default.** Both the global `core.SystemSetting.psa_enabled`
   and every per-surface flag default OFF. Auto-detect opts clients with
   an external PSA out completely.
2. **Reuse over rebuild.** Every PSA model FKs into existing infrastructure
   (`core.Organization`, `assets.Contact`, `assets.Asset`, `vault.Password`,
   `docs.Document`, `scheduling.ScheduledTask`, `audit.AuditLog`,
   `accounts.Membership`/`RoleTemplate`, `core.Webhook`, `integrations.*`).
   See `psa/INTEGRATION_MAP.md`.
3. **Permissions everywhere.** All PSA writes go through
   `@require_write` (Editor+); destructive actions through
   `@require_admin`. Per-feature granularity moves to the existing
   `accounts.RoleTemplate` boolean fields as we add them — see the
   "Permissions plan" section at the bottom.
4. **Audit everything.** Every mutation calls `AuditLog.log(...)`.
5. **Tenant scoping is non-negotiable.** All querysets go through
   `_scoped_ticket_qs(request)` (or equivalent for non-Ticket models).
6. **Native PSA only for clients without another PSA.** Active
   `integrations.PSAConnection` is a hard opt-out, no override.

## Status legend

- ✅ shipped
- 🟡 in progress / partial
- 🔵 next up
- ⚪ planned

---

## Workstream 1 — Ticketing / Service Desk

> Multi-channel ticket intake, SLA management, routing, and resolution workflows.

### Shipped
- ✅ **Phase 1 foundation** (v3.17.70) — `psa.Ticket` with the full spec
  field set (auto-numbered `PSA-YYYY-NNNNNN`), `Queue`, `TicketStatus`,
  `TicketPriority`, `TicketType`, `TicketComment`, `TicketAttachment`,
  `ClientPSASettings`. Seed mgmt command (`psa_seed_defaults`) populates
  7 queues, 10 statuses, 5 priorities (P1..P5 with default SLA targets),
  14 ticket types.
- ✅ **Global ticket list with filtering** (v3.17.79) — client / status /
  priority / queue / assignee / search. URL-driven so admins can bookmark
  a view.
- ✅ **Phase 2a service-desk depth** (v3.17.80) — reply / internal note
  posting, attachments (25 MB cap, MIME allowlist, sanitised filenames),
  quick actions (assign-to-me, status change, reopen, close-with-required-
  resolution-summary), closure categories.
- ✅ **Vault context** (v3.17.70) — read-only metadata view of a client's
  vault entries on each ticket; "Open in Vault" deep-links never inline
  secrets, every open is audit-logged.
- ✅ **Phase 2b watchers + canned replies** (this version) — subscribe
  for emails on activity; reusable comment templates with variable
  substitution (`{{ticket.number}}`, `{{ticket.subject}}`,
  `{{ticket.client}}`, `{{user.first_name}}`, etc.).

### Queued
- 🔵 **Multi-channel intake** (Workstream 8 dependency): email-to-ticket,
  alert API ingestion, anonymous portal form. Per-surface flags already
  exist on `core.SystemSetting`.
- ⚪ **Ticket merge / split** — high-risk; reserved for a focused session
  with red-team tests for data-integrity invariants.
- ⚪ **@mentions in comments** — user picker autocomplete + email notify.
- ⚪ **Recurring issue detection** — similarity match by subject/asset/client.
- ⚪ **Hygiene checks** — flag tickets missing asset / no time / no
  resolution / no KB link.
- ⚪ **SLA engine** — business-hours, holidays, pause-on-waiting-client,
  warning + breach thresholds, escalation rules. Defaults already on
  `TicketPriority.response_target_minutes` / `resolution_target_minutes`.
- ⚪ **Approvals workflow** — request approval, manager sign-off, audit chain.

---

## Workstream 2 — Time & Expense Tracking

> Billable vs. non-billable time, mobile capture, approvals.

### Status: ⚪ planned

### Models (planned)
- `psa.TicketTimeEntry(ticket, user, started_at, ended_at, duration_minutes,
  is_billable, rate_override, notes, expense_category, approved_by,
  approved_at)`
- Reuses `auth.User` for the technician. FK to `psa.Ticket` — every entry
  rolls up to a ticket (which already FKs to Organization).
- Mobile capture: PWA already shipped; we add a "start timer" button on
  the ticket detail page that posts to `/psa/t/<num>/timer/start/` and
  `.../stop/`. State stored in browser localStorage so closing the app
  doesn't lose the running timer.

### Approvals path
- New `accounts.RoleTemplate.psa_time_approve` boolean. Manager-role users
  can review/approve via a queue under Settings → PSA → Time Review.
- Audit-logged on every state transition.

---

## Workstream 3 — Project & Task Management

> Onboarding, break-fix, recurring projects, milestones.

### Status: ⚪ planned

### How it plugs in
- The existing `processes/` Django app already has a `ProcessExecution`
  model with `psa_ticket` FK and audit fields. **Do not duplicate.**
  PSA projects = `processes.Process` instances, with a `psa.Project`
  side-car holding the multi-ticket linkage:
  - `psa.Project(name, organization, owner, process, started_at,
    due_at, status, milestone_set, ...)`
  - `psa.ProjectTask(project, ticket, sort_order)` — child tickets
    are first-class `psa.Ticket` rows; the project is the umbrella.
- Recurring projects driven by the existing `scheduling.ScheduledTask`
  (`recurrence` + `recurrence_interval_days` + `spawn_next_occurrence()`)
  — the recurrence kicks the project from a Process template.

---

## Workstream 4 — Resource Scheduling & Utilization

> Technician capacity planning and skills-based assignment.

### Status: ⚪ planned

### How it plugs in
- Reuses `scheduling.ScheduledTask` for the dispatch calendar.
- New `psa.TechnicianProfile(user, skills_json, hours_per_week,
  vacation_calendar_url)` keyed on User. Skills as a JSON array of
  string tags (free-form for v1, taxonomy-driven later).
- Auto-assignment heuristic: when a ticket is created without an
  assignee, we score eligible technicians by skill match + current
  load and suggest top 3 (one-click accept). Manual override always
  wins.

---

## Workstream 5 — Contract & Billing

> Managed services agreements, usage-based billing, recurring invoices,
> automated revenue recognition.

### Status: ⚪ planned (Phase 2c at the earliest)

### Models (planned)
- `psa.Contract(organization, name, type [block_hours|t_and_m|retainer|
  msp_msa], started_at, ended_at, monthly_rate, included_hours,
  emergency_rate, after_hours_rate, currency, billing_cycle, ...)`
- `psa.ContractBalance(contract, period_start, period_end,
  hours_consumed, hours_remaining, dollars_billed, ...)`
- Time entries roll up against contracts at billing-cycle close.
- Invoicing: integration with QuickBooks Online + Xero (Workstream 8).
  Stay out of the actual accounting business — just hand over invoice
  drafts.
- Revenue recognition: ship balance reports, do NOT replace an
  accounting system.

---

## Workstream 6 — Reporting & Dashboards

> Profitability per client/ticket, utilization rates, SLA compliance,
> financial metrics.

### Status: ⚪ planned (some pieces already exist in `reports/`)

### How it plugs in
- The existing `reports/` app already has the chrome — we add PSA
  reports as new entries there:
  - Open tickets / SLA warnings / SLA breaches
  - Response time and resolution time distributions
  - Tickets by client / tech / queue / type
  - Recurring issues, noisy assets
  - CSAT (per Workstream 1 — survey-after-close)
  - Billable time and contract utilization
  - Portal usage, SMS-alert usage
  - Calendar-dispatch performance
- Profitability and financial metrics light up after Workstream 5.

---

## Workstream 7 — Client Portal

> Self-service access for clients to submit tickets and view status.

### Status: ⚪ planned (Phase 3)

### Hard constraints
- **Internal notes never reach the portal.** The `is_internal` flag on
  `TicketComment` and `TicketAttachment` is already in place; portal
  querysets MUST filter them out at the queryset layer with a
  red-team test asserting non-leakage.
- **Vault data never reaches the portal.** `vault.Password` is staff-only
  forever. The vault context page (`/psa/t/<num>/context/`) requires
  full staff auth — the portal is a separate Django app with its own
  middleware.
- Per-client opt-in — `core.SystemSetting.psa_portal_enabled` (global)
  must be on AND the client's `ClientPSASettings.portal_enabled` (or
  the future replacement signal) must be on. Default OFF.
- Anonymous submission lives behind another flag
  (`psa_anonymous_ticket_form_enabled`) plus rate-limiting via
  `django-ratelimit` (already a dep).

### Routes (planned)
- `/portal/login/`, `/portal/tickets/`, `/portal/tickets/new/`,
  `/portal/tickets/<num>/`, `/portal/tickets/<num>/reply/`,
  `/portal/tickets/<num>/close/`, `/portal/service-catalog/`,
  `/portal/kb/`, `/portal/assets/` (filtered to client),
  `/portal/calendar/`, `/portal/announcements/`.

---

## Workstream 8 — Integrations

> Especially with RMM tools, accounting (QuickBooks, Xero), Microsoft
> 365, and distributors.

### Existing — reuse, do NOT duplicate
- ✅ **PSA sync (third-party PSAs)** — `integrations.PSAConnection`
  already supports Alga, Autotask, ConnectWise Manage, Freshservice,
  HaloPSA, ITFlow, Kaseya BMS, RangerMSP, Syncro, Zendesk. Native PSA
  auto-opts-out clients on these.
- ✅ **RMM sync** — Atera, ConnectWise Automate, Datto, NinjaOne, Tactical
  RMM. Live in `integrations/`.
- ✅ **Microsoft 365 / Entra** — `integrations.M365Connection` (msal-based).
- ✅ **Network & cloud** — Unifi, Omada, Grandstream.

### Queued
- 🔵 **Distributor integrations** (under PSA) — pricing + stock + ordering:
  - Ingram Micro (Xvantage API)
  - Synnex/TD Synnex
  - D&H Distributing
  - ScanSource
  - Tech Data (now part of TD Synnex)
  - Pax8 (cloud distributor)
  - QBS Software
  - Westcoast
  - Each plugs into a generalised `integrations.DistributorConnection`
    model with provider-specific adapters in `integrations/distributors/`.
    Service catalog (`Workstream 1` — service catalog) becomes the
    consumer: a ticket for "new computer" can fetch live pricing from
    multiple distributors and let the tech pick.
- 🔵 **Accounting** — QuickBooks Online + Xero. Output-only (push invoice
  drafts, never read GL data); driven by Workstream 5.
- ⚪ **Webhook outbound** — Workstream 9 dependency.

---

## Workstream 9 — Automation & Workflows

> Rules-based actions, approvals, notifications.

### Status: 🟡 partial (the existing `processes/` app already covers a
> chunk of this)

### Existing
- `processes.Process` + `ProcessExecution` already runs templated
  workflows linked to a `psa_ticket`. Audit log fields exist.
- `core.Webhook` + `WebhookDelivery` already power outbound HTTP events.

### Planned for PSA
- **Workflow engine** wired to PSA-native triggers — extend `processes`,
  don't fork:
  - Triggers: `ticket_created`, `ticket_updated`, `status_changed`,
    `priority_changed`, `assignment_changed`, `client_replied`,
    `tech_replied`, `comment_added`, `calendar_event_created`,
    `SLA_warning`, `SLA_breach`, `ticket_idle`, `ticket_closed`,
    `ticket_reopened`, `rmm_alert_received`, `vault_context_opened`.
  - Actions: `assign_user`, `assign_queue`, `set_priority`,
    `set_status`, `send_email`, `send_sms`, `send_desktop_alert`,
    `create_calendar_event`, `create_reminder`, `request_approval`,
    `create_child_ticket`, `add_internal_note`, `link_asset`,
    `suggest_kb`, `escalate_to_manager`, `webhook_outbound`.
- All actions are audit-logged. All triggers respect tenant scoping.

---

## Permissions plan

PSA actions map to `accounts.RoleTemplate` booleans. Existing template
roles: Owner, Administrator, Editor, Help Desk, IT Manager, Documentation
Writer, Read-Only.

| Capability | Boolean field on RoleTemplate | Default by role |
|---|---|---|
| View tickets (own org) | (existing org membership) | every role |
| View all tickets cross-org | `psa_view_all` | Owner, Administrator |
| Create / comment | `psa_write` | Editor and above |
| Quick close / reopen | `psa_resolve` | Help Desk and above |
| Assign others | `psa_assign_others` | IT Manager and above |
| Manage canned replies | `psa_manage_canned` | Administrator |
| Manage SLA / queues / types / priorities | `psa_manage_config` | Administrator |
| Manage time entries (own) | `psa_time_log` | Help Desk and above |
| Approve time entries | `psa_time_approve` | IT Manager and above |
| Manage contracts / billing | `psa_billing` | Administrator |
| Configure global PSA settings | superuser only | superuser only |
| Manage distributor connections | `psa_distributor_admin` | Administrator |
| Run / configure workflows | `psa_workflow_admin` | Administrator |

Flagged the columns on `RoleTemplate` as we ship each workstream.
Existing decorators (`@require_write`, `@require_admin`, `@require_owner`)
gate the foundation; per-feature granularity comes online with the
matching workstream.

---

## Calendar / scheduling integration — already in place

Native PSA reuses `scheduling.ScheduledTask` for dispatch and reminders.
The existing model already carries:
- `recurrence` + `recurrence_interval_days` + `spawn_next_occurrence()` →
  recurring service tickets
- `alert_email` / `alert_sms` / `alert_before_hours` → reminder cadence
- `psa_ticket` FK → existing `integrations.PSATicket` (third-party). New
  native tickets use `Ticket.related_calendar_event` → `ScheduledTask`.

No duplicate calendar model. Workstream 4 schedules technicians on this
existing surface.

---

## Phasing

- **Now (shipped):** Phase 1 foundation, vault context, admin warnings,
  global filtered list, Phase 2a depth, Phase 2b watchers + canned replies.
- **Next session — Phase 2c:** ticket merge / split, recurring detection,
  hygiene scores, @mentions.
- **Phase 3:** SLA engine + workflow engine + email-to-ticket + alert API.
- **Phase 4:** client portal (with red-team isolation tests).
- **Phase 5:** time tracking + contracts + reports.
- **Phase 6:** distributor integrations (Ingram, Synnex, D&H, Pax8, …).
- **Phase 7:** accounting connectors (QBO, Xero) — output-only.

Update this doc whenever scope shifts.
