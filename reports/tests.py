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
