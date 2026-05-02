"""
Auto-split fragment of the legacy `psa/tests.py` (v3.17.192).
See `psa/tests/__init__.py` for the rationale.
"""
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Membership, Role
from audit.models import AuditLog
from core.models import Organization, SystemSetting
from psa.feature_flags import (
    is_psa_enabled,
    is_psa_enabled_for_client,
)
from psa.models import (
    ClientPSASettings,
    Queue,
    Ticket,
    TicketPriority,
    TicketStatus,
    TicketType,
)

from psa.tests._base import (
    TEST_MIDDLEWARE,
    _setup_seed,
    _enable_psa_global,
    _enable_psa_for,
)


class Phase3FeaturesTests(TestCase):
    """
    Phase 3: Projects, Recurring Tickets, Approvals, KB linking.

    Each feature gets a smoke test:
      * model creation works (unique-together / save() helpers fire)
      * the runner command creates a ticket and rolls next_run_at
      * the approval decide() helper updates status/decided_at
      * KB link enforces uniqueness
    """

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME Phase3', slug='acme-p3')
        self.user = User.objects.create_user('p3-tech', password='pw', email='p3@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import (
            Queue, TicketPriority, TicketType, TicketStatus,
        )
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_project_save_generates_slug(self):
        from psa.models import Project
        p = Project.objects.create(organization=self.org, name='ACME Onboarding 2026')
        self.assertTrue(p.slug.startswith('acme-onboarding-2026'))

    def test_project_completed_sets_completed_at(self):
        from psa.models import Project
        p = Project.objects.create(organization=self.org, name='Migration')
        self.assertIsNone(p.completed_at)
        p.status = 'completed'
        p.save()
        self.assertIsNotNone(p.completed_at)

    def test_recurring_advance_next_run_monthly(self):
        from psa.models import RecurringTicketSchedule
        from datetime import datetime
        s = RecurringTicketSchedule.objects.create(
            organization=self.org,
            name='Monthly patch',
            template_subject='Patch {{org}}',
            queue=self.queue, priority=self.priority, ticket_type=self.ttype,
            frequency='monthly', interval=1,
            next_run_at=timezone.make_aware(datetime(2026, 1, 15, 9, 0)),
        )
        s.advance_next_run()
        self.assertEqual(s.next_run_at.month, 2)
        self.assertEqual(s.next_run_at.day, 15)

    def test_recurring_runner_creates_ticket(self):
        from psa.models import RecurringTicketSchedule, Ticket
        from datetime import timedelta
        s = RecurringTicketSchedule.objects.create(
            organization=self.org,
            name='Daily backup check',
            template_subject='Daily backup verification',
            template_body='Verify the backup completed.',
            queue=self.queue, priority=self.priority, ticket_type=self.ttype,
            frequency='daily', interval=1,
            next_run_at=timezone.now() - timedelta(hours=1),  # overdue
        )
        before = Ticket.objects.filter(organization=self.org).count()
        call_command('psa_run_recurring_tickets', verbosity=0)
        after = Ticket.objects.filter(organization=self.org).count()
        self.assertEqual(after, before + 1)
        # next_run_at rolled forward past now
        s.refresh_from_db()
        self.assertGreater(s.next_run_at, timezone.now())
        # The created ticket links back to the schedule
        t = Ticket.objects.filter(recurring_schedule=s).first()
        self.assertIsNotNone(t)
        self.assertEqual(t.source, 'recurring')

    def test_approval_decide_updates_status_and_decided_at(self):
        from psa.models import PSAApproval
        a = PSAApproval.objects.create(
            organization=self.org, kind='time',
            object_type='psa.TicketTimeEntry', object_id=99,
            requested_by=self.user, request_comment='Need approval',
        )
        self.assertEqual(a.status, 'pending')
        a.decide(user=self.user, approved=True, comment='LGTM')
        self.assertEqual(a.status, 'approved')
        self.assertIsNotNone(a.decided_at)
        self.assertEqual(a.decision_comment, 'LGTM')

    def test_ticket_kb_link_uniqueness(self):
        from psa.models import Ticket, TicketKBLink
        from docs.models import Document
        ticket = Ticket.objects.create(
            organization=self.org, subject='TEST',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        article = Document.objects.create(
            title='How to reset password', body='...', is_global=True,
        )
        TicketKBLink.objects.create(ticket=ticket, article=article, linked_by=self.user)
        # Second create with same pair should fail unique_together
        with self.assertRaises(Exception):
            TicketKBLink.objects.create(ticket=ticket, article=article, linked_by=self.user)


class Phase4FeaturesTests(TestCase):
    """
    Phase 4: Customer Portal, Email Ingestion, Contracts.

    Smoke + correctness tests:
      * Contract.for_ticket returns the active contract for the client org.
      * Time entry save() increments hours_used_minutes via the contract hook.
      * Email config password round-trips encrypted (no plaintext on disk).
      * Portal queryset filters out staff-only tickets and internal comments.
    """

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME P4', slug='acme-p4')
        self.user = User.objects.create_user('p4-tech', password='pw', email='p4t@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_contract_for_ticket_returns_active(self):
        from psa.models import Contract, Ticket
        from datetime import date, timedelta
        c = Contract.objects.create(
            organization=self.org, client_org=self.org,
            name='Block', start_date=date.today() - timedelta(days=1),
            status='active', total_hours=10,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='X',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        self.assertEqual(Contract.for_ticket(t), c)

    def test_contract_for_ticket_skips_draft_and_expired(self):
        from psa.models import Contract, Ticket
        from datetime import date, timedelta
        Contract.objects.create(
            organization=self.org, client_org=self.org,
            name='Draft', start_date=date.today(),
            status='draft', total_hours=10,
        )
        Contract.objects.create(
            organization=self.org, client_org=self.org,
            name='Expired', start_date=date.today() - timedelta(days=400),
            end_date=date.today() - timedelta(days=10),
            status='active', total_hours=10,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='X',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        self.assertIsNone(Contract.for_ticket(t))

    def test_time_entry_save_increments_contract_hours(self):
        from psa.models import Contract, Ticket, TicketTimeEntry
        from datetime import date, timedelta
        c = Contract.objects.create(
            organization=self.org, client_org=self.org,
            name='Block', start_date=date.today() - timedelta(days=1),
            status='active', total_hours=10,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='Time',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        TicketTimeEntry.objects.create(
            ticket=t, user=self.user,
            started_at=timezone.now() - timedelta(minutes=45),
            ended_at=timezone.now(),
            duration_minutes=45,
        )
        c.refresh_from_db()
        self.assertEqual(c.hours_used_minutes, 45)
        self.assertEqual(c.hours_used, 0.75)

    def test_email_config_password_encryption(self):
        from psa.models import EmailIngestionConfig
        cfg = EmailIngestionConfig.objects.create(
            organization=self.org, name='Helpdesk',
            imap_host='imap.example.com', username='help@example.com',
            default_queue=self.queue, default_priority=self.priority,
            default_type=self.ttype,
        )
        cfg.set_password('s3cr3t-imap-pw')
        cfg.save()
        reloaded = EmailIngestionConfig.objects.get(pk=cfg.pk)
        self.assertEqual(reloaded.get_password(), 's3cr3t-imap-pw')
        self.assertNotIn('s3cr3t-imap-pw', reloaded.encrypted_password)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class CustomerPortalTests(TestCase):
    """
    Portal hard rules:
      1. 404 if PSA disabled globally.
      2. 404 if user has no active membership in a portal-enabled org.
      3. Tickets queryset filters to client_can_view=True only.
      4. Internal comments don't render in detail view.
    """

    def setUp(self):
        _setup_seed()
        from psa.models import (
            ClientPSASettings, Queue, Ticket, TicketComment,
            TicketPriority, TicketType, TicketStatus,
        )
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='Portal Co', slug='portal-co')
        self.user = User.objects.create_user('portal-user', password='pw', email='pu@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.READONLY, 'is_active': True},
        )
        ClientPSASettings.objects.update_or_create(
            organization=self.org, defaults={'enabled': True, 'portal_enabled': True},
        )
        q = Queue.objects.first()
        p = TicketPriority.objects.first()
        tt = TicketType.objects.first()
        st = TicketStatus.objects.filter(slug='new').first()
        # Public ticket — should be visible
        self.public = Ticket.objects.create(
            organization=self.org, subject='Public ticket',
            queue=q, priority=p, ticket_type=tt, status=st,
            client_can_view=True, visibility='client',
        )
        TicketComment.objects.create(ticket=self.public, body='public reply', is_internal=False)
        TicketComment.objects.create(ticket=self.public, body='STAFF SECRET', is_internal=True)
        # Staff-only ticket — should be hidden
        self.private = Ticket.objects.create(
            organization=self.org, subject='Internal ticket',
            queue=q, priority=p, ticket_type=tt, status=st,
            client_can_view=False, visibility='staff',
        )

    def test_portal_404_without_membership(self):
        # Use a NEW org with portal_enabled=False, then create a rando whose
        # auto-membership lands in that non-portal org.
        from psa.models import ClientPSASettings
        no_portal_org = Organization.objects.create(name='No Portal LLC', slug='no-portal')
        ClientPSASettings.objects.create(organization=no_portal_org, portal_enabled=False)
        # Disable portal_enabled on Portal Co so the default-membership
        # signal doesn't accidentally grant the rando user access.
        ClientPSASettings.objects.filter(organization=self.org).update(portal_enabled=False)

        c = Client()
        rando = User.objects.create_user('not-a-member', password='pw', email='nm@x.com')
        c.force_login(rando)
        resp = c.get('/portal/')
        self.assertEqual(resp.status_code, 404)

    def test_portal_lists_only_client_visible_tickets(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get('/portal/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(self.public.ticket_number, body)
        self.assertNotIn(self.private.ticket_number, body)

    def test_portal_detail_hides_internal_comments(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get(f'/portal/t/{self.public.ticket_number}/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('public reply', body)
        self.assertNotIn('STAFF SECRET', body)

    def test_portal_detail_404_for_staff_only_ticket(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get(f'/portal/t/{self.private.ticket_number}/')
        self.assertEqual(resp.status_code, 404)

    def test_portal_post_reply_creates_public_comment(self):
        from psa.models import TicketComment
        c = Client()
        c.force_login(self.user)
        before = TicketComment.objects.filter(ticket=self.public).count()
        resp = c.post(f'/portal/t/{self.public.ticket_number}/reply/',
                      {'body': 'I tried rebooting'})
        self.assertEqual(resp.status_code, 302)
        after = TicketComment.objects.filter(ticket=self.public).count()
        self.assertEqual(after, before + 1)
        comment = TicketComment.objects.filter(ticket=self.public).order_by('-created_at').first()
        self.assertFalse(comment.is_internal)
        self.assertEqual(comment.source, 'portal')
        self.assertEqual(comment.author, self.user)

    def test_portal_create_ticket(self):
        from psa.models import Ticket
        c = Client()
        c.force_login(self.user)
        before = Ticket.objects.filter(organization=self.org).count()
        resp = c.post('/portal/new/', {
            'subject': 'My laptop is on fire',
            'description': 'Smoke is coming out',
        })
        self.assertEqual(resp.status_code, 302)
        after = Ticket.objects.filter(organization=self.org).count()
        self.assertEqual(after, before + 1)
        new_ticket = Ticket.objects.filter(organization=self.org).order_by('-created_at').first()
        self.assertEqual(new_ticket.source, 'portal')
        self.assertTrue(new_ticket.client_can_view)


class Phase5QuotesExpensesTests(TestCase):
    """Phase 5: Quotes (with line items + accept→ticket) and Expenses."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME P5', slug='acme-p5')
        self.client_org = Organization.objects.create(name='Client P5', slug='client-p5')
        self.user = User.objects.create_user('p5-tech', password='pw', email='p5@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_quote_number_auto_assigned(self):
        from psa.models import Quote
        q = Quote.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Network upgrade',
        )
        from datetime import date
        self.assertTrue(q.quote_number.startswith(f'Q-{date.today().year}-'))

    def test_quote_recompute_totals_with_line_items(self):
        from psa.models import Quote, QuoteLineItem
        from decimal import Decimal
        q = Quote.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Setup', tax_rate=Decimal('0.10'),
        )
        QuoteLineItem.objects.create(quote=q, description='Switch', quantity=2, unit_price=500)
        QuoteLineItem.objects.create(quote=q, description='Labor', quantity=4, unit_price=100)
        q.recompute_totals()
        self.assertEqual(q.subtotal, Decimal('1400.00'))
        self.assertEqual(q.tax_amount, Decimal('140.00'))
        self.assertEqual(q.total, Decimal('1540.00'))

    def test_quote_accept_creates_ticket(self):
        from psa.models import Quote, Ticket
        q = Quote.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Project Z', description='Big project',
        )
        before = Ticket.objects.filter(organization=self.client_org).count()
        q.mark_accepted(user=self.user, create_ticket=True,
                        queue=self.queue, priority=self.priority,
                        ticket_type=self.ttype, status=self.status)
        after = Ticket.objects.filter(organization=self.client_org).count()
        self.assertEqual(after, before + 1)
        self.assertEqual(q.status, 'accepted')
        self.assertIsNotNone(q.accepted_at)
        self.assertIsNotNone(q.converted_ticket)

    def test_quote_accept_without_ticket_creation(self):
        from psa.models import Quote, Ticket
        q = Quote.objects.create(
            organization=self.org, client_org=self.client_org,
            title='No-ticket quote',
        )
        before = Ticket.objects.filter(organization=self.client_org).count()
        q.mark_accepted(user=self.user, create_ticket=False)
        after = Ticket.objects.filter(organization=self.client_org).count()
        self.assertEqual(after, before)
        self.assertIsNone(q.converted_ticket)

    def test_quote_convert_to_project_creates_tasks_per_line_item(self):
        # v3.17.213: accepting a quote with `create_project=True` spins up
        # a Project and one ProjectTask per line item.
        from psa.models import Quote, QuoteLineItem, Project, ProjectTask
        q = Quote.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Server refresh', description='Replace 4 hosts',
        )
        QuoteLineItem.objects.create(quote=q, description='Procure 4 servers', quantity=4, unit_price=2500, sort_order=0)
        QuoteLineItem.objects.create(quote=q, description='Rack & cable', quantity=8, unit_price=150, sort_order=1)
        QuoteLineItem.objects.create(quote=q, description='Migrate workloads', quantity=16, unit_price=200, sort_order=2)

        q.mark_accepted(user=self.user, create_ticket=False, create_project=True)

        self.assertEqual(q.status, 'accepted')
        self.assertIsNotNone(q.converted_project)
        proj = q.converted_project
        self.assertIsInstance(proj, Project)
        self.assertEqual(proj.organization, self.org)
        self.assertEqual(proj.client_org, self.client_org)
        self.assertEqual(proj.name, 'Server refresh')
        self.assertEqual(proj.tasks.count(), 3)
        titles = list(proj.tasks.order_by('sort_order').values_list('title', flat=True))
        self.assertEqual(titles, ['Procure 4 servers', 'Rack & cable', 'Migrate workloads'])

    def test_quote_convert_to_project_is_idempotent(self):
        # Calling convert_to_project twice should not create a second project.
        from psa.models import Quote, QuoteLineItem
        q = Quote.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Idempotent test',
        )
        QuoteLineItem.objects.create(quote=q, description='Item', quantity=1, unit_price=10)
        first = q.convert_to_project(user=self.user)
        second = q.convert_to_project(user=self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.tasks.count(), 1)

    def test_expense_creation_and_billable_flag(self):
        from psa.models import Ticket, TicketExpense
        t = Ticket.objects.create(
            organization=self.org, subject='Onsite',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        from datetime import date
        e = TicketExpense.objects.create(
            ticket=t, user=self.user,
            category='mileage', description='Drive to client site',
            amount=42.50, currency='USD',
            incurred_on=date.today(),
            is_billable=True, is_reimbursable=True,
        )
        self.assertEqual(t.expenses.count(), 1)
        self.assertTrue(e.is_billable)
        self.assertEqual(float(e.amount), 42.50)


class Phase6PolishTests(TestCase):
    """Phase 6 polish: ProjectTask + ticket-detail contract/expense surfaces."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME P6', slug='acme-p6')
        self.user = User.objects.create_user('p6', password='pw', email='p6@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_project_task_done_sets_completed_at(self):
        from psa.models import Project, ProjectTask
        p = Project.objects.create(organization=self.org, name='Migration')
        t = ProjectTask.objects.create(project=p, title='Plan it')
        self.assertIsNone(t.completed_at)
        t.status = 'done'
        t.save()
        self.assertIsNotNone(t.completed_at)

    def test_project_task_re_open_clears_completed_at(self):
        from psa.models import Project, ProjectTask
        p = Project.objects.create(organization=self.org, name='Reopen')
        t = ProjectTask.objects.create(project=p, title='Setup', status='done')
        self.assertIsNotNone(t.completed_at)
        t.status = 'in_progress'
        t.save()
        self.assertIsNone(t.completed_at)

    def test_project_task_milestone_flag_persists(self):
        from psa.models import Project, ProjectTask
        p = Project.objects.create(organization=self.org, name='Big project')
        t = ProjectTask.objects.create(project=p, title='Go-live', is_milestone=True)
        t.refresh_from_db()
        self.assertTrue(t.is_milestone)


class Phase7WorkflowTests(TestCase):
    """Phase 7: SLA matrix override + WorkflowRule engine + sample workflows."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME P7', slug='acme-p7')
        self.user = User.objects.create_user('p7', password='pw', email='p7@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.filter(code='P1').first() or TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_sla_matrix_overrides_priority_default(self):
        from psa.models import Contract, Ticket
        from psa.sla import compute_due_dates
        from datetime import date, timedelta
        # Contract pegs P1 response to 30 minutes (priority default is 240).
        Contract.objects.create(
            organization=self.org, client_org=self.org,
            name='Premium', start_date=date.today() - timedelta(days=1),
            status='active',
            sla_matrix={self.priority.code: {'response_minutes': 30, 'resolution_minutes': 120}},
        )
        t = Ticket.objects.create(
            organization=self.org, subject='SLA test',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        fr, res = compute_due_dates(t)
        # Response should be 30 min after creation, not 240.
        delta = (fr - t.created_at).total_seconds() / 60
        self.assertAlmostEqual(delta, 30, delta=1)
        delta_res = (res - t.created_at).total_seconds() / 60
        self.assertAlmostEqual(delta_res, 120, delta=1)

    def test_workflow_rule_set_priority_on_create(self):
        from psa.models import Ticket, TicketPriority, WorkflowRule
        p2 = TicketPriority.objects.filter(code='P2').first()
        WorkflowRule.objects.create(
            organization=self.org,
            name='Outage → P1',
            trigger='ticket_created',
            conditions={'subject_contains': 'outage'},
            actions=[{'type': 'set_priority', 'code': 'P1'}],
            is_active=True,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='Email outage',
            queue=self.queue, priority=p2,
            ticket_type=self.ttype, status=self.status,
        )
        t.refresh_from_db()
        self.assertEqual(t.priority.code, 'P1')

    def test_workflow_rule_skips_when_condition_false(self):
        from psa.models import Ticket, TicketPriority, WorkflowRule
        p3 = TicketPriority.objects.filter(code='P3').first()
        WorkflowRule.objects.create(
            organization=self.org,
            name='Outage → P1',
            trigger='ticket_created',
            conditions={'subject_contains': 'outage'},
            actions=[{'type': 'set_priority', 'code': 'P1'}],
            is_active=True,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='Friendly hello',
            queue=self.queue, priority=p3,
            ticket_type=self.ttype, status=self.status,
        )
        t.refresh_from_db()
        # Condition was false — priority unchanged.
        self.assertEqual(t.priority.code, 'P3')

    def test_workflow_rule_add_tag_action(self):
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=self.org,
            name='Tag VIP',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'add_tag', 'tag': 'vip'}],
            is_active=True,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='Anything',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        t.refresh_from_db()
        self.assertIn('vip', t.tags or [])

    def test_workflow_rule_inactive_does_not_fire(self):
        from psa.models import Ticket, WorkflowRule
        rule = WorkflowRule.objects.create(
            organization=self.org,
            name='Tag Inactive',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'add_tag', 'tag': 'should-not-appear'}],
            is_active=False,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='Test',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        t.refresh_from_db()
        self.assertNotIn('should-not-appear', t.tags or [])
        rule.refresh_from_db()
        self.assertEqual(rule.fire_count, 0)

    def test_workflow_rule_bad_action_recorded_on_rule(self):
        """A misconfigured action shouldn't break ticket save — error is
        captured on the rule row instead."""
        from psa.models import Ticket, WorkflowRule
        rule = WorkflowRule.objects.create(
            organization=self.org,
            name='Broken rule',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'bogus_action'}],
            is_active=True,
        )
        Ticket.objects.create(
            organization=self.org, subject='x',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        rule.refresh_from_db()
        self.assertIn('bogus_action', rule.last_error)

    def test_sample_workflows_seed_command(self):
        from psa.models import WorkflowRule
        from django.core.management import call_command
        before = WorkflowRule.objects.filter(organization=self.org).count()
        call_command('psa_seed_sample_workflows', '--org-id', str(self.org.id), verbosity=0)
        after = WorkflowRule.objects.filter(organization=self.org).count()
        self.assertGreater(after, before)


class Phase8BillingTests(TestCase):
    """Phase 8: Invoices, line items, payments, generate-from-ticket."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME P8', slug='acme-p8')
        self.client_org = Organization.objects.create(name='Client P8', slug='client-p8')
        self.user = User.objects.create_user('p8', password='pw', email='p8@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_invoice_auto_numbers(self):
        from psa.models import Invoice
        from datetime import date
        i = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='January work', invoice_date=date.today(),
        )
        self.assertTrue(i.invoice_number.startswith(f'INV-{date.today().year}-'))

    def test_invoice_recompute_totals_with_payments_updates_status(self):
        from psa.models import Invoice, InvoiceLineItem, Payment
        from datetime import date
        from decimal import Decimal
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Onboarding', invoice_date=date.today(), status='sent',
        )
        InvoiceLineItem.objects.create(invoice=inv, description='Setup', quantity=1, unit_price=200)
        InvoiceLineItem.objects.create(invoice=inv, description='Training', quantity=2, unit_price=100)
        inv.recompute_totals()
        self.assertEqual(inv.total, Decimal('400.00'))
        Payment.objects.create(invoice=inv, amount=Decimal('150.00'), paid_on=date.today(), method='ach')
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, Decimal('150.00'))
        self.assertEqual(inv.status, 'partial')
        Payment.objects.create(invoice=inv, amount=Decimal('250.00'), paid_on=date.today(), method='ach')
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, Decimal('400.00'))
        self.assertEqual(inv.status, 'paid')

    def test_invoice_balance_computed(self):
        from psa.models import Invoice, InvoiceLineItem, Payment
        from datetime import date
        from decimal import Decimal
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='X', invoice_date=date.today(), status='sent',
        )
        InvoiceLineItem.objects.create(invoice=inv, description='item', quantity=1, unit_price=500)
        inv.recompute_totals()
        Payment.objects.create(invoice=inv, amount=Decimal('200'), paid_on=date.today(), method='ach')
        inv.refresh_from_db()
        self.assertEqual(inv.balance, Decimal('300.00'))




# ---------------------------------------------------------------------------
# Phase 36 v2 — Pre-Invoice Approval Gate (v3.17.228)
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class InvoiceApprovalGateTests(TestCase):
    """Phase 36 v2: pre-invoice approval workflow."""

    def setUp(self):
        from accounts.models import Membership, Role
        _setup_seed()
        from core.models import SystemSetting
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='InvApprMSP', slug='inv-appr-msp')
        self.client_org = Organization.objects.create(name='InvApprClient', slug='inv-appr-c')
        self.admin = User.objects.create_user('inv-admin', password='pw', email='ia@x.com',
                                                is_superuser=True, is_staff=True)
        Membership.objects.update_or_create(
            user=self.admin, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )

    def test_flag_for_approval_above_total_threshold(self):
        from psa.models import Invoice
        from datetime import date
        from decimal import Decimal as _D
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Big invoice', invoice_date=date.today(),
            total=_D('15000'),
        )
        flagged = inv.flag_for_approval(total_threshold=10000)
        self.assertTrue(flagged)
        self.assertTrue(inv.requires_approval)
        self.assertIn('total', inv.approval_reason)
        self.assertIn('15000', inv.approval_reason)

    def test_flag_for_approval_below_total_threshold(self):
        from psa.models import Invoice
        from datetime import date
        from decimal import Decimal as _D
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Small invoice', invoice_date=date.today(),
            total=_D('500'),
        )
        flagged = inv.flag_for_approval(total_threshold=10000)
        self.assertFalse(flagged)
        self.assertFalse(inv.requires_approval)

    def test_flag_for_approval_above_overage_threshold(self):
        from psa.models import Invoice, Contract
        from datetime import date
        from decimal import Decimal as _D
        contract = Contract.objects.create(
            organization=self.org, client_org=self.client_org,
            name='Block-overage', contract_type='block_hours',
            status='active', start_date=date.today(),
            total_hours=_D('100'), hours_used_minutes=130 * 60,  # 130% used
        )
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Inv with overage', invoice_date=date.today(),
            total=_D('500'), source_contract=contract,
        )
        flagged = inv.flag_for_approval(total_threshold=10000, overage_pct_threshold=110)
        self.assertTrue(flagged)
        self.assertIn('Block-overage', inv.approval_reason)

    def test_approve_clears_gate_and_records_user(self):
        from psa.models import Invoice
        from datetime import date
        from decimal import Decimal as _D
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Pending', invoice_date=date.today(), total=_D('500'),
            requires_approval=True, approval_reason='manual',
        )
        inv.approve(user=self.admin)
        inv.refresh_from_db()
        self.assertFalse(inv.requires_approval)
        self.assertEqual(inv.approved_by_id, self.admin.id)
        self.assertIsNotNone(inv.approved_at)

    def test_invoice_approve_view_201(self):
        from psa.models import Invoice
        from datetime import date
        from decimal import Decimal as _D
        from django.test import Client
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='To approve', invoice_date=date.today(), total=_D('500'),
            requires_approval=True, approval_reason='over threshold',
        )
        c = Client()
        c.force_login(self.admin)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()
        r = c.post(f'/psa/invoices/{inv.pk}/approve/')
        self.assertEqual(r.status_code, 302)
        inv.refresh_from_db()
        self.assertFalse(inv.requires_approval)
        self.assertEqual(inv.approved_by_id, self.admin.id)

    def test_push_to_accounting_blocked_when_pending_approval(self):
        from psa.models import Invoice
        from datetime import date
        from decimal import Decimal as _D
        from django.test import Client
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Pending push', invoice_date=date.today(), total=_D('500'),
            requires_approval=True, approval_reason='hold',
        )
        c = Client()
        c.force_login(self.admin)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()
        # Should redirect with error and NOT touch accounting_external_id.
        r = c.post(f'/psa/invoices/{inv.pk}/push/')
        self.assertEqual(r.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.accounting_external_id, '')

    def test_request_approval_view_sets_flag(self):
        from psa.models import Invoice
        from datetime import date
        from decimal import Decimal as _D
        from django.test import Client
        inv = Invoice.objects.create(
            organization=self.org, client_org=self.client_org,
            title='Manual flag', invoice_date=date.today(), total=_D('500'),
        )
        self.assertFalse(inv.requires_approval)
        c = Client()
        c.force_login(self.admin)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()
        r = c.post(f'/psa/invoices/{inv.pk}/request-approval/',
                   data={'reason': 'Customer dispute pending'})
        self.assertEqual(r.status_code, 302)
        inv.refresh_from_db()
        self.assertTrue(inv.requires_approval)
        self.assertIn('Customer dispute', inv.approval_reason)
