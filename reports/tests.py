"""
Reports tests — Phase 3.1.

Cover the canonical query layer (`reports.queries`) and the new
Profitability-by-Client report view (URL gate, HTML render, CSV export).
"""
from django.conf import settings as django_settings
from django.test import TestCase, override_settings


# Strip the project-wide 2FA enforcement + Axes middleware so the test
# client can hit the views directly. Same pattern as `psa/tests.py`.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProfitabilityQueryTests(TestCase):
    """v3.17.139: canonical queries return correct shape and totals."""

    def setUp(self):
        from datetime import date, timedelta
        from django.contrib.auth.models import User
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            TicketTimeEntry, Invoice,
        )
        from django.core.management import call_command
        call_command('psa_seed_defaults', verbosity=0)
        self.org = Organization.objects.create(name='Profit Co', slug='profit-co')
        self.user = User.objects.create_user('alice', 'a@x.com', 'pw')
        self.t = Ticket.objects.create(
            organization=self.org, subject='X',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        self.today = date.today()
        TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=self.today, duration_minutes=120, is_billable=True,
        )
        Invoice.objects.create(
            organization=self.org, client_org=self.org,
            invoice_number='INV-2026-1', title='Test',
            invoice_date=self.today, due_date=self.today,
            total=500, amount_paid=200,
            status='partial', subtotal=500, tax_amount=0, currency='USD',
        )

    def test_hours_minutes_by_client(self):
        from reports.queries import hours_minutes_by_client
        rows = hours_minutes_by_client(self.today, self.today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['billable_minutes'], 120)
        self.assertEqual(rows[0]['client_id'], self.org.id)

    def test_revenue_by_client(self):
        from reports.queries import revenue_by_client
        rows = revenue_by_client(self.today, self.today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['invoiced'], 500.0)
        self.assertEqual(rows[0]['outstanding'], 300.0)

    def test_profitability_combines(self):
        from reports.queries import profitability_by_client
        rows = profitability_by_client(self.today, self.today, default_loaded_rate=60)
        self.assertEqual(len(rows), 1)
        # 2h × $60 = $120 cost; revenue $500; margin $380; pct 76%
        self.assertAlmostEqual(rows[0]['cost'], 120.0, places=1)
        self.assertAlmostEqual(rows[0]['margin'], 380.0, places=1)
        self.assertAlmostEqual(rows[0]['margin_pct'], 76.0, places=1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProfitabilityReportViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.staff = User.objects.create_user('admin1', 'a@x.com', 'pw',
                                              is_staff=True, is_superuser=True)
        self.regular = User.objects.create_user('reg', 'r@x.com', 'pw')

    def test_staff_can_view(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/profitability-by-client/')
        self.assertEqual(r.status_code, 200)

    def test_non_staff_redirected(self):
        self.client.force_login(self.regular)
        r = self.client.get('/reports/psa/profitability-by-client/')
        self.assertIn(r.status_code, [302, 403])

    def test_csv_export(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/profitability-by-client/?format=csv')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'].split(';')[0].strip(), 'text/csv')
        self.assertIn(b'Client', r.content)  # header row


# ---------------------------------------------------------------------------
# Phase 3.2 — TechCostRate + per-tech / contract / project profitability
# ---------------------------------------------------------------------------

class TechCostRateTests(TestCase):
    def test_rate_for_returns_default_when_no_rows(self):
        from datetime import date
        from django.contrib.auth.models import User
        from resourcing.models import TechCostRate
        from reports.queries import DEFAULT_LOADED_RATE
        u = User.objects.create_user('alice', 'a@x.com', 'pw')
        self.assertEqual(TechCostRate.rate_for(u, date.today()),
                         DEFAULT_LOADED_RATE)

    def test_rate_for_picks_most_recent_effective(self):
        from datetime import date
        from decimal import Decimal
        from django.contrib.auth.models import User
        from resourcing.models import TechCostRate
        u = User.objects.create_user('alice', 'a@x.com', 'pw')
        TechCostRate.objects.create(user=u, rate_per_hour=Decimal('50'),
                                    effective_from=date(2024, 1, 1))
        TechCostRate.objects.create(user=u, rate_per_hour=Decimal('70'),
                                    effective_from=date(2025, 6, 1))
        self.assertEqual(TechCostRate.rate_for(u, date(2025, 7, 1)),
                         Decimal('70'))
        self.assertEqual(TechCostRate.rate_for(u, date(2024, 6, 1)),
                         Decimal('50'))

    def test_rate_for_ignores_future_effective(self):
        from datetime import date
        from decimal import Decimal
        from django.contrib.auth.models import User
        from resourcing.models import TechCostRate
        from reports.queries import DEFAULT_LOADED_RATE
        u = User.objects.create_user('alice', 'a@x.com', 'pw')
        TechCostRate.objects.create(user=u, rate_per_hour=Decimal('99'),
                                    effective_from=date(2030, 1, 1))
        # Today < 2030, so falls back to default
        self.assertEqual(TechCostRate.rate_for(u, date(2026, 1, 1)),
                         DEFAULT_LOADED_RATE)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProfitabilityByTechTests(TestCase):
    """Phase 3.2 — per-tech profitability uses TechCostRate."""

    def setUp(self):
        from datetime import date
        from decimal import Decimal
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            TicketTimeEntry, Invoice,
        )
        from resourcing.models import TechCostRate
        call_command('psa_seed_defaults', verbosity=0)
        self.org = Organization.objects.create(name='ProfitTechCo', slug='profit-tech-co')
        self.user = User.objects.create_user('alice', 'a@x.com', 'pw')
        self.t = Ticket.objects.create(
            organization=self.org, subject='X',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        self.today = date.today()
        TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=self.today, duration_minutes=120, is_billable=True,
        )
        Invoice.objects.create(
            organization=self.org, client_org=self.org,
            invoice_number='INV-2026-T1', title='TestTech',
            invoice_date=self.today, due_date=self.today,
            total=300, amount_paid=0, status='sent',
            subtotal=300, tax_amount=0, currency='USD',
        )
        # Custom cost rate $50/hr
        TechCostRate.objects.create(user=self.user, rate_per_hour=Decimal('50'),
                                    effective_from=self.today)

    def test_per_tech_uses_cost_rate(self):
        from reports.queries import profitability_by_tech
        rows = profitability_by_tech(self.today, self.today)
        self.assertEqual(len(rows), 1)
        # 2 hours × $50/hr = $100 cost
        self.assertAlmostEqual(rows[0]['cost'], 100.0, places=1)
        self.assertEqual(rows[0]['tech_username'], 'alice')

    def test_cost_estimate_by_client_uses_per_tech_rate(self):
        from reports.queries import cost_estimate_by_client
        rows = cost_estimate_by_client(self.today, self.today)
        self.assertEqual(len(rows), 1)
        # 2h × $50 = $100 (not 2h × $60 placeholder)
        self.assertAlmostEqual(rows[0]['cost'], 100.0, places=1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProfitabilityByContractTests(TestCase):
    """Phase 3.2 — per-contract grouping via Contract.for_ticket."""

    def setUp(self):
        from datetime import date, timedelta
        from decimal import Decimal
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            TicketTimeEntry, Contract, Invoice,
        )
        call_command('psa_seed_defaults', verbosity=0)
        self.msp = Organization.objects.create(name='MSPInc', slug='msp-inc')
        self.client_org = Organization.objects.create(name='ClientCo', slug='client-co')
        self.user = User.objects.create_user('alice', 'a@x.com', 'pw')
        self.today = date.today()
        self.contract = Contract.objects.create(
            organization=self.msp, client_org=self.client_org,
            name='Block 50', contract_type='block_hours', status='active',
            start_date=self.today - timedelta(days=30),
            end_date=self.today + timedelta(days=30),
            total_hours=Decimal('50'), hourly_rate=Decimal('150'),
        )
        self.t = Ticket.objects.create(
            organization=self.client_org, subject='X',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=self.today, duration_minutes=60, is_billable=True,
        )
        Invoice.objects.create(
            organization=self.msp, client_org=self.client_org,
            source_contract=self.contract,
            invoice_number='INV-2026-C1', title='Contract billing',
            invoice_date=self.today, due_date=self.today,
            total=Decimal('500'), amount_paid=0, status='sent',
            subtotal=Decimal('500'), tax_amount=0, currency='USD',
        )

    def test_aggregates_per_contract(self):
        from reports.queries import profitability_by_contract
        rows = profitability_by_contract(self.today, self.today)
        # We expect at least one row matching our contract
        match = [r for r in rows if r['contract_id'] == self.contract.id]
        self.assertEqual(len(match), 1)
        r = match[0]
        self.assertEqual(r['client_name'], 'ClientCo')
        # Revenue includes the $500 invoice
        self.assertGreaterEqual(r['revenue'], 500.0)
        # Cost = 1h × default $60 = $60
        self.assertAlmostEqual(r['cost'], 60.0, places=1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProfitabilityByProjectTests(TestCase):
    """Phase 3.2 — per-project grouping via Ticket.project."""

    def setUp(self):
        from datetime import date
        from decimal import Decimal
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            TicketTimeEntry, Project, Invoice,
        )
        call_command('psa_seed_defaults', verbosity=0)
        self.msp = Organization.objects.create(name='MSPInc', slug='msp-inc-2')
        self.client_org = Organization.objects.create(name='ProjClient', slug='proj-client')
        self.user = User.objects.create_user('alice', 'a@x.com', 'pw')
        self.today = date.today()
        self.project = Project.objects.create(
            organization=self.msp, client_org=self.client_org,
            name='Migration', status='active',
        )
        self.t = Ticket.objects.create(
            organization=self.client_org, subject='Migration ticket',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
            project=self.project,
        )
        TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=self.today, duration_minutes=180, is_billable=True,
        )
        Invoice.objects.create(
            organization=self.msp, client_org=self.client_org,
            source_ticket=self.t,
            invoice_number='INV-2026-P1', title='Project billing',
            invoice_date=self.today, due_date=self.today,
            total=Decimal('900'), amount_paid=0, status='sent',
            subtotal=Decimal('900'), tax_amount=0, currency='USD',
        )

    def test_aggregates_per_project(self):
        from reports.queries import profitability_by_project
        rows = profitability_by_project(self.today, self.today)
        match = [r for r in rows if r['project_id'] == self.project.id]
        self.assertEqual(len(match), 1)
        r = match[0]
        self.assertEqual(r['project_name'], 'Migration')
        self.assertAlmostEqual(r['revenue'], 900.0, places=1)
        # 3h × $60 default = $180
        self.assertAlmostEqual(r['cost'], 180.0, places=1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProfitabilityPivotViewTests(TestCase):
    """Phase 3.2 — staff can hit the three new HTML/CSV report views."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.staff = User.objects.create_user('admin1', 'a@x.com', 'pw',
                                              is_staff=True, is_superuser=True)
        self.regular = User.objects.create_user('reg', 'r@x.com', 'pw')

    def test_by_tech_view_staff_html(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/profitability-by-tech/')
        self.assertEqual(r.status_code, 200)

    def test_by_tech_view_csv(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/profitability-by-tech/?format=csv')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'].split(';')[0].strip(), 'text/csv')
        self.assertIn(b'Tech', r.content)

    def test_by_contract_view_staff_html(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/profitability-by-contract/')
        self.assertEqual(r.status_code, 200)

    def test_by_project_view_staff_html(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/profitability-by-project/')
        self.assertEqual(r.status_code, 200)

    def test_by_tech_non_staff_redirected(self):
        self.client.force_login(self.regular)
        r = self.client.get('/reports/psa/profitability-by-tech/')
        self.assertIn(r.status_code, [302, 403])


# ---------------------------------------------------------------------------
# Phase 3.3 — Effective hourly rate + Revenue leakage
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class EffectiveRateTests(TestCase):
    """v3.17.141: effective_hourly_rate_by_client + by_tech."""

    def setUp(self):
        from datetime import date, timedelta
        from decimal import Decimal
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            TicketTimeEntry, Contract, Invoice,
        )
        from resourcing.models import TechCostRate
        call_command('psa_seed_defaults', verbosity=0)
        self.msp = Organization.objects.create(name='MSPRate', slug='msp-rate')
        self.client_org = Organization.objects.create(name='RateClient',
                                                      slug='rate-client')
        self.user = User.objects.create_user('alice', 'a@x.com', 'pw')
        self.today = date.today()
        # Active contract so attributed revenue uses $150/hr
        self.contract = Contract.objects.create(
            organization=self.msp, client_org=self.client_org,
            name='Rate Block', contract_type='block_hours', status='active',
            start_date=self.today - timedelta(days=30),
            end_date=self.today + timedelta(days=30),
            total_hours=Decimal('50'), hourly_rate=Decimal('150'),
        )
        self.t = Ticket.objects.create(
            organization=self.client_org, subject='Work',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        # 4 billable hours
        TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=self.today, duration_minutes=240, is_billable=True,
        )
        Invoice.objects.create(
            organization=self.msp, client_org=self.client_org,
            invoice_number='INV-RATE-1', title='Test',
            invoice_date=self.today, due_date=self.today,
            total=Decimal('600'), amount_paid=0, status='sent',
            subtotal=Decimal('600'), tax_amount=0, currency='USD',
        )
        # Cost rate $50/hr → effective $150 ÷ 50 cost = 300% realization
        # for the tech tab (which uses contract hourly_rate × hours = 600
        # attributed revenue ÷ 4h billable = $150 effective).
        TechCostRate.objects.create(user=self.user,
                                    rate_per_hour=Decimal('50'),
                                    effective_from=self.today)

    def test_effective_rate_basic(self):
        """4h billable + $600 invoiced → effective rate $150/hr."""
        from reports.queries import effective_hourly_rate_by_client
        rows = effective_hourly_rate_by_client(self.today, self.today)
        match = [r for r in rows if r['client_id'] == self.client_org.id]
        self.assertEqual(len(match), 1)
        r = match[0]
        self.assertEqual(r['billable_hours'], 4.0)
        self.assertAlmostEqual(r['revenue'], 600.0, places=1)
        self.assertAlmostEqual(r['effective_rate'], 150.0, places=1)

    def test_effective_rate_by_tech_has_realization(self):
        """Per-tech rate uses attributed revenue ÷ billable hours."""
        from reports.queries import effective_hourly_rate_by_tech
        rows = effective_hourly_rate_by_tech(self.today, self.today)
        self.assertGreaterEqual(len(rows), 1)
        r = rows[0]
        # Attributed revenue = 4h × $150 contract rate = $600
        # Effective rate = $600 / 4h = $150/hr
        # Cost rate = $50/hr
        # Realization % = 150 / 50 × 100 = 300%
        self.assertAlmostEqual(r['effective_rate'], 150.0, places=1)
        self.assertAlmostEqual(r['cost_rate'], 50.0, places=1)
        self.assertAlmostEqual(r['realization_pct'], 300.0, places=0)

    def test_effective_rate_zero_when_no_billable_hours(self):
        """Client with revenue but no time → effective_rate = 0."""
        from datetime import date as _date
        from decimal import Decimal
        from core.models import Organization
        from psa.models import Invoice
        from reports.queries import effective_hourly_rate_by_client
        ghost = Organization.objects.create(name='GhostCo', slug='ghost-co')
        Invoice.objects.create(
            organization=self.msp, client_org=ghost,
            invoice_number='INV-GHOST-1', title='G',
            invoice_date=self.today, due_date=self.today,
            total=Decimal('200'), amount_paid=0, status='sent',
            subtotal=Decimal('200'), tax_amount=0, currency='USD',
        )
        rows = effective_hourly_rate_by_client(self.today, self.today)
        match = [r for r in rows if r['client_id'] == ghost.id]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]['billable_hours'], 0)
        self.assertEqual(match[0]['effective_rate'], 0.0)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class RevenueLeakageTests(TestCase):
    """v3.17.141: revenue_leakage surfaces stale unbilled, expired blocks,
    stuck drafts."""

    def setUp(self):
        from datetime import date, timedelta
        from decimal import Decimal
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from django.utils import timezone
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            TicketTimeEntry, Contract, Invoice,
        )
        call_command('psa_seed_defaults', verbosity=0)
        self.msp = Organization.objects.create(name='MSPLeak', slug='msp-leak')
        self.client_org = Organization.objects.create(name='LeakClient',
                                                      slug='leak-client')
        self.user = User.objects.create_user('bob', 'b@x.com', 'pw')
        self.today = date.today()
        self.t = Ticket.objects.create(
            organization=self.client_org, subject='LeakTest',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        # Stale unbilled entry: 60 days ago, billable, 2h
        old_dt = timezone.now() - timedelta(days=60)
        TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=old_dt, ended_at=old_dt + timedelta(hours=2),
            duration_minutes=120, is_billable=True,
        )
        # Expired contract: 10h total, 0 used, $200/hr
        self.expired_contract = Contract.objects.create(
            organization=self.msp, client_org=self.client_org,
            name='Old Block', contract_type='block_hours', status='expired',
            start_date=self.today - timedelta(days=400),
            end_date=self.today - timedelta(days=30),
            total_hours=Decimal('10'),
            hours_used_minutes=0,
            hourly_rate=Decimal('200'),
        )
        # Stuck draft: 30 days old, $750
        self.draft_invoice = Invoice.objects.create(
            organization=self.msp, client_org=self.client_org,
            invoice_number='INV-STUCK-1', title='Stuck Draft',
            invoice_date=self.today - timedelta(days=30),
            due_date=self.today, status='draft',
            total=Decimal('750'), amount_paid=0,
            subtotal=Decimal('750'), tax_amount=0, currency='USD',
        )

    def test_stale_unbilled_picks_up_old_billable_entries(self):
        from reports.queries import revenue_leakage
        data = revenue_leakage(self.today, self.today, stale_days=30)
        match = [r for r in data['stale_unbilled']
                 if r['client_id'] == self.client_org.id]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]['entry_count'], 1)
        self.assertAlmostEqual(match[0]['hours_at_risk'], 2.0, places=1)
        self.assertGreater(match[0]['amount_at_risk'], 0)

    def test_stuck_drafts_are_old_drafts(self):
        from reports.queries import revenue_leakage
        data = revenue_leakage(self.today, self.today)
        match = [r for r in data['stuck_drafts']
                 if r['invoice_id'] == self.draft_invoice.id]
        self.assertEqual(len(match), 1)
        self.assertAlmostEqual(match[0]['amount'], 750.0, places=1)
        self.assertGreaterEqual(match[0]['days_stuck'], 14)

    def test_expired_block_with_unused_hours(self):
        from reports.queries import revenue_leakage
        data = revenue_leakage(self.today, self.today)
        match = [r for r in data['expired_blocks']
                 if r['contract_id'] == self.expired_contract.id]
        self.assertEqual(len(match), 1)
        self.assertAlmostEqual(match[0]['unused_hours'], 10.0, places=1)
        # 10h × $200 = $2000
        self.assertAlmostEqual(match[0]['unused_value'], 2000.0, places=1)

    def test_grand_total_sums(self):
        from reports.queries import revenue_leakage
        data = revenue_leakage(self.today, self.today)
        t = data['totals']
        self.assertAlmostEqual(
            t['grand_total'],
            t['stale'] + t['expired_blocks'] + t['stuck'],
            places=1,
        )
        # Should at least include our stuck $750 and expired $2000
        self.assertGreaterEqual(t['grand_total'], 2750.0)

    def test_billed_entries_are_excluded_from_stale(self):
        """An entry whose pk is referenced by a non-void InvoiceLineItem
        must not appear under stale_unbilled."""
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        from psa.models import TicketTimeEntry, Invoice, InvoiceLineItem
        from reports.queries import revenue_leakage

        # New billable entry 60 days ago that IS on an invoice
        old_dt = timezone.now() - timedelta(days=60)
        te = TicketTimeEntry.objects.create(
            ticket=self.t, user=self.user,
            started_at=old_dt, ended_at=old_dt + timedelta(hours=1),
            duration_minutes=60, is_billable=True,
        )
        inv = Invoice.objects.create(
            organization=self.msp, client_org=self.client_org,
            invoice_number='INV-BILLED-1', title='Billed',
            invoice_date=self.today, due_date=self.today,
            total=Decimal('150'), amount_paid=0, status='sent',
            subtotal=Decimal('150'), tax_amount=0, currency='USD',
        )
        InvoiceLineItem.objects.create(
            invoice=inv, description='Time', quantity=1,
            unit_price=Decimal('150'), source='time', source_id=str(te.pk),
        )

        data = revenue_leakage(self.today, self.today, stale_days=30)
        # Confirm only the original 2h-stale entry is counted, not the
        # new 1h billed one — the row for our client should still report
        # entry_count=1 (the un-invoiced one), not 2.
        match = [r for r in data['stale_unbilled']
                 if r['client_id'] == self.client_org.id]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]['entry_count'], 1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class EffectiveRateViewTests(TestCase):
    """Phase 3.3 view auth + CSV export."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.staff = User.objects.create_user('admin1', 'a@x.com', 'pw',
                                              is_staff=True, is_superuser=True)
        self.regular = User.objects.create_user('reg', 'r@x.com', 'pw')

    def test_renders_for_staff(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/effective-hourly-rate/')
        self.assertEqual(r.status_code, 200)

    def test_non_staff_redirected(self):
        self.client.force_login(self.regular)
        r = self.client.get('/reports/psa/effective-hourly-rate/')
        self.assertIn(r.status_code, [302, 403])

    def test_csv_export(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/effective-hourly-rate/?format=csv')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'].split(';')[0].strip(), 'text/csv')
        self.assertIn(b'Effective Rate', r.content)

    def test_tech_tab_csv_export(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/effective-hourly-rate/?format=csv&tab=tech')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Realization', r.content)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class RevenueLeakageViewTests(TestCase):
    """Phase 3.3 leakage view auth + CSV combined export."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.staff = User.objects.create_user('admin1', 'a@x.com', 'pw',
                                              is_staff=True, is_superuser=True)
        self.regular = User.objects.create_user('reg', 'r@x.com', 'pw')

    def test_renders_for_staff(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/revenue-leakage/')
        self.assertEqual(r.status_code, 200)

    def test_non_staff_redirected(self):
        self.client.force_login(self.regular)
        r = self.client.get('/reports/psa/revenue-leakage/')
        self.assertIn(r.status_code, [302, 403])

    def test_csv_export_combines_sections(self):
        self.client.force_login(self.staff)
        r = self.client.get('/reports/psa/revenue-leakage/?format=csv')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'].split(';')[0].strip(), 'text/csv')
        # The single-CSV combined export uses a Section column
        self.assertIn(b'Section', r.content)


# ---------------------------------------------------------------------------
# v3.17.142: dashboard widget registry + CRUD
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class WidgetSourceRegistryTests(TestCase):
    """v3.17.142: every registered data source returns a non-empty dict."""

    def test_all_sources_runnable(self):
        from reports.widget_sources import REGISTRY
        for name, fn in REGISTRY.items():
            result = fn({})
            self.assertIsInstance(result, dict, f'{name} must return dict')
            # error key is allowed (empty data); value/columns/labels need at
            # least one to be present
            allowed = {'value', 'columns', 'labels', 'error'}
            self.assertTrue(allowed & set(result.keys()),
                            f'{name} returned no recognizable shape: {result}')

    def test_unknown_source_returns_error(self):
        from reports.widget_sources import get_widget_data
        result = get_widget_data('nonsense_source_xyz', {})
        self.assertIn('error', result)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class DashboardWidgetCRUDTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from reports.models import Dashboard
        self.user = User.objects.create_user(
            'alice', 'a@x.com', 'pw',
            is_staff=True, is_superuser=True,
        )
        self.dash = Dashboard.objects.create(
            name='Test Dash', is_global=True, created_by=self.user,
        )

    def test_add_widget_via_post(self):
        self.client.force_login(self.user)
        r = self.client.post(
            f'/reports/dashboards/{self.dash.pk}/widgets/add/',
            {'title': 'Open Tickets', 'data_source': 'open_tickets_count'},
        )
        from reports.models import DashboardWidget
        self.assertEqual(
            DashboardWidget.objects.filter(dashboard=self.dash).count(), 1
        )


# ---------------------------------------------------------------------------
# Phase 3.4 — SLA trend report + Margin analytics by service line
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class SLATrendQueryTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from core.models import Organization
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
        )
        from django.core.management import call_command
        call_command('psa_seed_defaults', verbosity=0)
        self.org = Organization.objects.create(name='SLA Co', slug='sla-co')
        self.queue = Queue.objects.first()
        self.status_resolved = TicketStatus.objects.filter(is_terminal=True).first()
        self.p1 = TicketPriority.objects.filter(code='P1').first()
        self.tt = TicketType.objects.first()
        # Fixture: P1 ticket created 2 days ago; resolution_due 1 day ago;
        # closed today → resolution breach. Response landed before due.
        # NOTE: Ticket.created_at is auto_now_add=True, so we can't backdate
        # the create timestamp. The window we use in the assertion includes
        # today, so created_at=now still falls in the bucket.
        now = timezone.now()
        self.t = Ticket.objects.create(
            organization=self.org, subject='X',
            queue=self.queue, status=self.status_resolved,
            priority=self.p1, ticket_type=self.tt,
            resolution_due_at=now - timedelta(days=1),
            first_response_due_at=now + timedelta(hours=2),
            first_response_at=now + timedelta(hours=1),  # before due → on time
            closed_at=now,
        )

    def test_sla_trend_by_priority_buckets(self):
        from datetime import date, timedelta
        from reports.queries import sla_trend_by_priority
        result = sla_trend_by_priority(
            date.today() - timedelta(days=7), date.today(), bucket='week'
        )
        # P1 totals should reflect 1 ticket with 1 resolution breach,
        # 0 response breach.
        p1 = result['totals_by_priority']['P1']
        self.assertEqual(p1['tickets'], 1)
        self.assertEqual(p1['resolution_breaches'], 1)
        self.assertEqual(p1['response_breaches'], 0)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class MarginAnalyticsTests(TestCase):
    def test_dimension_grouping(self):
        from datetime import date
        from reports.queries import margin_analytics_by_service_line
        rows = margin_analytics_by_service_line(date.today(), date.today())
        # Empty period — list returns empty
        self.assertIsInstance(rows, list)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class SLATrendsViewTests(TestCase):
    def test_renders_for_staff(self):
        from django.contrib.auth.models import User
        u = User.objects.create_user('admin', 'a@x.com', 'pw',
                                     is_staff=True, is_superuser=True)
        self.client.force_login(u)
        r = self.client.get('/reports/psa/sla-trends/')
        self.assertEqual(r.status_code, 200)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class MarginAnalyticsViewTests(TestCase):
    def test_renders_for_staff(self):
        from django.contrib.auth.models import User
        u = User.objects.create_user('admin', 'a@x.com', 'pw',
                                     is_staff=True, is_superuser=True)
        self.client.force_login(u)
        r = self.client.get('/reports/psa/margin-analytics/')
        self.assertEqual(r.status_code, 200)
