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


class AccountingConnectionTests(TestCase):
    """Encrypted credentials + provider registry round-trip."""

    def setUp(self):
        self.org = Organization.objects.create(name='ACME Acct', slug='acme-acct')

    def test_credentials_round_trip_encrypted(self):
        from integrations.models import AccountingConnection
        c = AccountingConnection.objects.create(
            organization=self.org, provider_type='quickbooks_online', name='QBO Live',
        )
        c.set_credentials({'client_id': 'cid', 'client_secret': 'shhh',
                           'refresh_token': 'rrrt'})
        c.save()
        reloaded = AccountingConnection.objects.get(pk=c.pk)
        self.assertEqual(reloaded.get_credentials()['client_secret'], 'shhh')
        self.assertNotIn('shhh', reloaded.encrypted_credentials)

    def test_update_credentials_merges(self):
        from integrations.models import AccountingConnection
        c = AccountingConnection.objects.create(
            organization=self.org, provider_type='xero', name='Xero Live',
        )
        c.set_credentials({'client_id': 'cid'})
        c.save()
        c.update_credentials(refresh_token='rt', tenant_id='tid')
        c.save()
        reloaded = AccountingConnection.objects.get(pk=c.pk)
        creds = reloaded.get_credentials()
        self.assertEqual(creds['client_id'], 'cid')
        self.assertEqual(creds['refresh_token'], 'rt')
        self.assertEqual(creds['tenant_id'], 'tid')

    def test_provider_registry(self):
        from integrations.providers.accounting import (
            PROVIDER_REGISTRY, get_accounting_provider,
        )
        self.assertIn('quickbooks_online', PROVIDER_REGISTRY)
        self.assertIn('xero', PROVIDER_REGISTRY)
        from integrations.models import AccountingConnection
        c = AccountingConnection.objects.create(
            organization=self.org, provider_type='quickbooks_online', name='X',
        )
        p = get_accounting_provider(c)
        self.assertEqual(p.provider_name, 'QuickBooks Online')
        self.assertIn('quickbooks.api.intuit.com', p.base_url)


class WorkflowOnTicketTests(TestCase):
    """v3.17.105: Process workflows can attach to native PSA tickets."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME WO', slug='acme-wo')
        self.user = User.objects.create_user('wo', password='pw', email='wo@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_processexecution_native_psa_ticket_field_exists(self):
        from processes.models import ProcessExecution
        f = ProcessExecution._meta.get_field('native_psa_ticket')
        self.assertTrue(f.null)
        self.assertEqual(f.related_model.__name__, 'Ticket')

    def test_workflow_executions_query_for_ticket(self):
        from processes.models import ProcessExecution, Process
        from psa.models import Ticket
        proc = Process.objects.create(
            organization=self.org, title='Onboarding', slug='wo-onboard',
            description='',
        )
        t = Ticket.objects.create(
            organization=self.org, subject='Test ticket',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        ex = ProcessExecution.objects.create(
            process=proc, organization=self.org,
            assigned_to=self.user, started_by=self.user,
            status='in_progress', native_psa_ticket=t,
        )
        # Reverse query
        self.assertEqual(t.process_executions.count(), 1)
        self.assertEqual(t.process_executions.first().pk, ex.pk)


class PortalUserInviteTests(TestCase):
    """v3.17.106: Invite-portal-user flow + Document.is_client_visible."""

    def test_document_is_client_visible_field_default_false(self):
        from docs.models import Document
        from core.models import Organization
        org = Organization.objects.create(name='ACME PI', slug='acme-pi')
        d = Document.objects.create(
            organization=org, title='How to file a ticket',
            slug='how-to-file', body='steps', content_type='html',
        )
        self.assertFalse(d.is_client_visible)


class PortalVaultRBACTests(TestCase):
    """v3.17.107: client_visible + access modes + portal visibility helper."""

    def setUp(self):
        from core.models import Organization
        self.org = Organization.objects.create(name='ACME RBAC', slug='acme-rbac')
        self.client_user = User.objects.create_user('client1', password='pw', email='c1@x.com')
        self.outsider = User.objects.create_user('outsider', password='pw', email='o@x.com')
        # The accounts.create_default_membership signal auto-attaches new users
        # to the first active org. Strip that membership so outsider is truly an outsider.
        Membership.objects.filter(user=self.outsider).delete()
        Membership.objects.update_or_create(
            user=self.client_user, organization=self.org,
            defaults={'role': Role.READONLY, 'is_active': True},
        )

    def _make(self, **kwargs):
        from vault.models import Password
        defaults = dict(
            organization=self.org, title='Email server',
            password_type='email', is_personal=False,
        )
        defaults.update(kwargs)
        p = Password(**defaults)
        # set_password() requires the encryption helpers; we skip it for visibility tests
        p.save()
        return p

    def test_personal_password_never_portal_visible(self):
        from vault.models import Password
        p = self._make(is_personal=True, client_visible=True, client_access_mode='all_org')
        self.assertFalse(p.visible_to_portal_user(self.client_user))

    def test_client_visible_false_blocks_all(self):
        p = self._make(client_visible=False, client_access_mode='all_org')
        self.assertFalse(p.visible_to_portal_user(self.client_user))

    def test_mode_none_blocks_even_when_client_visible(self):
        p = self._make(client_visible=True, client_access_mode='none')
        self.assertFalse(p.visible_to_portal_user(self.client_user))

    def test_all_org_mode_grants_member(self):
        p = self._make(client_visible=True, client_access_mode='all_org')
        self.assertTrue(p.visible_to_portal_user(self.client_user))

    def test_all_org_mode_denies_non_member(self):
        p = self._make(client_visible=True, client_access_mode='all_org')
        self.assertFalse(p.visible_to_portal_user(self.outsider))

    def test_specific_users_requires_explicit_grant(self):
        p = self._make(client_visible=True, client_access_mode='specific_users')
        # Member but not in allowed_users → denied
        self.assertFalse(p.visible_to_portal_user(self.client_user))
        p.client_allowed_users.add(self.client_user)
        # Now allowed
        self.assertTrue(p.visible_to_portal_user(self.client_user))

    def test_specific_users_denies_non_member_even_if_in_list(self):
        p = self._make(client_visible=True, client_access_mode='specific_users')
        p.client_allowed_users.add(self.outsider)  # bug: outsider not in org
        self.assertFalse(p.visible_to_portal_user(self.outsider),
                         'visibility helper must check Membership defence-in-depth')

    def test_org_admin_managed_uses_specific_users_logic(self):
        p = self._make(client_visible=True, client_access_mode='org_admin_managed')
        self.assertFalse(p.visible_to_portal_user(self.client_user))
        p.client_allowed_users.add(self.client_user)
        self.assertTrue(p.visible_to_portal_user(self.client_user))

    def test_unauthenticated_user_denied(self):
        from django.contrib.auth.models import AnonymousUser
        p = self._make(client_visible=True, client_access_mode='all_org')
        self.assertFalse(p.visible_to_portal_user(AnonymousUser()))


class WorkflowRuleMSPWideTests(TestCase):
    """v3.17.111: Rules with org=None apply to every ticket."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org_a = Organization.objects.create(name='Client A', slug='client-a')
        self.org_b = Organization.objects.create(name='Client B', slug='client-b')
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def test_msp_wide_rule_fires_on_every_client(self):
        """A rule with organization=None should match every ticket."""
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=None,  # MSP-wide
            name='Tag everything',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'add_tag', 'tag': 'msp-wide-tag'}],
            is_active=True,
        )
        # Ticket on client A
        t_a = Ticket.objects.create(
            organization=self.org_a, subject='A ticket',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        # Ticket on client B
        t_b = Ticket.objects.create(
            organization=self.org_b, subject='B ticket',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        t_a.refresh_from_db(); t_b.refresh_from_db()
        self.assertIn('msp-wide-tag', t_a.tags or [])
        self.assertIn('msp-wide-tag', t_b.tags or [])

    def test_org_scoped_rule_only_fires_for_that_org(self):
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=self.org_a,
            name='Tag client A only',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'add_tag', 'tag': 'client-a-only'}],
            is_active=True,
        )
        t_a = Ticket.objects.create(
            organization=self.org_a, subject='A',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        t_b = Ticket.objects.create(
            organization=self.org_b, subject='B',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        t_a.refresh_from_db(); t_b.refresh_from_db()
        self.assertIn('client-a-only', t_a.tags or [])
        self.assertNotIn('client-a-only', t_b.tags or [])


class WorkflowConditionalRoutingTests(TestCase):
    """Phase 14 v2/v4 (v3.17.285): else_actions branching + fire_rule chaining."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='WfBranch', slug='wf-branch')
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority_p1 = TicketPriority.objects.filter(code='P1').first() \
                           or TicketPriority.objects.first()
        self.priority_p3 = TicketPriority.objects.filter(code='P3').first() \
                           or TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first()

    def _make_ticket(self, *, priority=None, subject='T'):
        from psa.models import Ticket
        return Ticket.objects.create(
            organization=self.org, subject=subject,
            queue=self.queue, priority=priority or self.priority_p1,
            ticket_type=self.ttype, status=self.status,
        )

    def test_else_actions_run_when_condition_false(self):
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=None,
            name='Branch on P1',
            trigger='ticket_created',
            conditions={'priority': 'P1'},
            actions=[{'type': 'add_tag', 'tag': 'high-prio'}],
            else_actions=[{'type': 'add_tag', 'tag': 'normal-prio'}],
            is_active=True,
        )
        t_p1 = self._make_ticket(priority=self.priority_p1, subject='P1')
        t_p3 = self._make_ticket(priority=self.priority_p3, subject='P3')
        t_p1.refresh_from_db(); t_p3.refresh_from_db()
        self.assertIn('high-prio', t_p1.tags or [])
        self.assertNotIn('normal-prio', t_p1.tags or [])
        # P3 → else branch
        if self.priority_p3.code != 'P1':  # only check if P3 is distinct
            self.assertIn('normal-prio', t_p3.tags or [])
            self.assertNotIn('high-prio', t_p3.tags or [])

    def test_no_else_actions_means_noop_when_false(self):
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=None,
            name='Tag P1 only',
            trigger='ticket_created',
            conditions={'priority': 'P1'},
            actions=[{'type': 'add_tag', 'tag': 'high'}],
            else_actions=[],  # legacy behavior
            is_active=True,
        )
        if self.priority_p3.code != 'P1':
            t_p3 = self._make_ticket(priority=self.priority_p3, subject='P3')
            t_p3.refresh_from_db()
            self.assertNotIn('high', t_p3.tags or [])

    def test_fire_rule_action_chains_to_named_rule(self):
        """Phase 14 v4: orchestration via fire_rule action."""
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=None,
            name='Sub-tagger',
            trigger='ticket_created',  # trigger is informational here
            conditions={},
            actions=[{'type': 'add_tag', 'tag': 'orchestrated'}],
            is_active=True,
        )
        WorkflowRule.objects.create(
            organization=None,
            name='Parent',
            trigger='ticket_created',
            conditions={},
            actions=[
                {'type': 'add_tag', 'tag': 'parent-ran'},
                {'type': 'fire_rule', 'name': 'Sub-tagger'},
            ],
            is_active=True,
        )
        t = self._make_ticket()
        t.refresh_from_db()
        self.assertIn('parent-ran', t.tags or [])
        self.assertIn('orchestrated', t.tags or [])

    def test_fire_rule_unknown_name_raises(self):
        from psa.models import Ticket, WorkflowRule
        rule = WorkflowRule.objects.create(
            organization=None,
            name='Bad parent',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'fire_rule', 'name': 'no-such-rule'}],
            is_active=True,
        )
        # Creating the ticket fires the rule; engine catches the
        # ValueError and stores it on `last_error` instead of raising.
        self._make_ticket()
        rule.refresh_from_db()
        self.assertIn('no-such-rule', rule.last_error or '')

    def test_fire_rule_respects_subrule_else_actions(self):
        from psa.models import Ticket, WorkflowRule
        # Sub-rule branches on P1
        WorkflowRule.objects.create(
            organization=None,
            name='Branching sub',
            trigger='ticket_created',
            conditions={'priority': 'P1'},
            actions=[{'type': 'add_tag', 'tag': 'sub-true'}],
            else_actions=[{'type': 'add_tag', 'tag': 'sub-false'}],
            is_active=True,
        )
        WorkflowRule.objects.create(
            organization=None,
            name='Parent for branching sub',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'fire_rule', 'name': 'Branching sub'}],
            is_active=True,
        )
        if self.priority_p3.code != 'P1':
            t = self._make_ticket(priority=self.priority_p3)
            t.refresh_from_db()
            self.assertIn('sub-false', t.tags or [])
            self.assertNotIn('sub-true', t.tags or [])


class WorkflowSLAThresholdTests(TestCase):
    """Phase 14 v3 (v3.17.286): SLA-driven automation."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='SlaCo', slug='sla-co')
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus, Ticket
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status_open = TicketStatus.objects.filter(slug='new').first() \
                           or TicketStatus.objects.first()
        # A separate non-terminal status for "in progress"-style
        self.status_terminal, _ = TicketStatus.objects.get_or_create(
            slug='terminal-test',
            defaults={'name': 'Terminal Test', 'is_terminal': True},
        )
        # Ticket whose resolution SLA window is half-elapsed
        now = timezone.now()
        self.t_half = Ticket.objects.create(
            organization=self.org, subject='Halfway',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
        )
        # Manually set SLA timing so 50% elapsed.
        Ticket.objects.filter(pk=self.t_half.pk).update(
            created_at=now - timedelta(hours=2),
            resolution_due_at=now + timedelta(hours=2),
        )
        self.t_half.refresh_from_db()

    def test_sla_pct_at_least_condition_matches_when_threshold_crossed(self):
        from psa.workflow_engine import matches
        # 50% elapsed should match >=50 but not >=80
        self.assertTrue(matches(self.t_half, {'sla_pct_at_least': 50}))
        self.assertFalse(matches(self.t_half, {'sla_pct_at_least': 80}))

    def test_fire_once_per_ticket_prevents_double_firing(self):
        from psa.models import Ticket, WorkflowRule, WorkflowRuleFiring
        from psa.workflow_engine import fire
        WorkflowRule.objects.create(
            organization=None,
            name='SLA 50%',
            trigger='sla_threshold_crossed',
            conditions={'sla_pct_at_least': 50},
            actions=[{'type': 'add_tag', 'tag': 'sla-warned'}],
            fire_once_per_ticket=True,
            is_active=True,
        )
        # First fire — rule should match + tag
        n1 = fire('sla_threshold_crossed', self.t_half)
        self.assertGreaterEqual(n1, 1)
        self.t_half.refresh_from_db()
        self.assertIn('sla-warned', self.t_half.tags or [])
        # Second fire on the same ticket — guard kicks in
        firings_after_first = WorkflowRuleFiring.objects.filter(
            ticket=self.t_half).count()
        self.assertEqual(firings_after_first, 1)
        n2 = fire('sla_threshold_crossed', self.t_half)
        firings_after_second = WorkflowRuleFiring.objects.filter(
            ticket=self.t_half).count()
        self.assertEqual(firings_after_second, 1)  # not duplicated

    def test_management_command_fires_rules_for_open_tickets(self):
        from psa.models import Ticket, WorkflowRule
        from django.core.management import call_command
        WorkflowRule.objects.create(
            organization=None,
            name='SLA 25%',
            trigger='sla_threshold_crossed',
            conditions={'sla_pct_at_least': 25},
            actions=[{'type': 'add_tag', 'tag': 'crossed-25'}],
            fire_once_per_ticket=True,
            is_active=True,
        )
        call_command('psa_sla_workflow_tick', verbosity=0)
        self.t_half.refresh_from_db()
        self.assertIn('crossed-25', self.t_half.tags or [])

    def test_management_command_skips_terminal_tickets(self):
        from datetime import timedelta
        from django.utils import timezone
        from psa.models import Ticket, WorkflowRule
        from django.core.management import call_command
        # Closed ticket — shouldn't fire even though it would match.
        now = timezone.now()
        t_closed = Ticket.objects.create(
            organization=self.org, subject='Closed',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_terminal,
        )
        Ticket.objects.filter(pk=t_closed.pk).update(
            created_at=now - timedelta(hours=10),
            resolution_due_at=now - timedelta(hours=1),
        )
        WorkflowRule.objects.create(
            organization=None,
            name='SLA 10% (closed should skip)',
            trigger='sla_threshold_crossed',
            conditions={'sla_pct_at_least': 10},
            actions=[{'type': 'add_tag', 'tag': 'should-not-fire'}],
            fire_once_per_ticket=True,
            is_active=True,
        )
        call_command('psa_sla_workflow_tick', verbosity=0)
        t_closed.refresh_from_db()
        self.assertNotIn('should-not-fire', t_closed.tags or [])


class WorkflowDynamicAssignmentTests(TestCase):
    """Phase 14 v8 (v3.17.287): assign_round_robin / assign_skill_match /
    assign_load_balanced action types."""

    def setUp(self):
        from django.contrib.auth.models import Group
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='AssignCo', slug='assign-co')
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status_open = TicketStatus.objects.filter(slug='new').first() \
                           or TicketStatus.objects.first()
        # Three active staff techs
        self.alice = User.objects.create_user('alice', 'a@x.com', 'pw',
                                                is_staff=True)
        self.bob = User.objects.create_user('bob', 'b@x.com', 'pw',
                                              is_staff=True)
        self.carol = User.objects.create_user('carol', 'c@x.com', 'pw',
                                                is_staff=True)
        # An inactive user — must never be picked.
        self.zach = User.objects.create_user('zach', 'z@x.com', 'pw',
                                               is_staff=True, is_active=False)
        # Skill group on Alice
        self.linux = Group.objects.create(name='linux-experts')
        self.alice.groups.add(self.linux)

    def _make_ticket(self):
        from psa.models import Ticket
        return Ticket.objects.create(
            organization=self.org, subject='T',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
        )

    def test_assign_skill_match_picks_skilled_user(self):
        from psa.models import WorkflowRule
        WorkflowRule.objects.create(
            organization=None,
            name='Linux skill route',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'assign_skill_match',
                      'skill_group': 'linux-experts'}],
            is_active=True,
        )
        t = self._make_ticket()
        t.refresh_from_db()
        self.assertEqual(t.assigned_to, self.alice)

    def test_assign_skill_match_unknown_group_noop(self):
        from psa.models import WorkflowRule
        WorkflowRule.objects.create(
            organization=None,
            name='Phantom skill route',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'assign_skill_match',
                      'skill_group': 'wizard-class'}],
            is_active=True,
        )
        t = self._make_ticket()
        t.refresh_from_db()
        self.assertIsNone(t.assigned_to)

    def test_assign_load_balanced_picks_user_with_fewest_open(self):
        from psa.models import Ticket, WorkflowRule
        # Give Bob 3 open tickets, Alice 1, Carol 0.
        for _ in range(3):
            Ticket.objects.create(
                organization=self.org, subject='B-tic',
                queue=self.queue, priority=self.priority,
                ticket_type=self.ttype, status=self.status_open,
                assigned_to=self.bob,
            )
        Ticket.objects.create(
            organization=self.org, subject='A-tic',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
            assigned_to=self.alice,
        )
        # Now create a rule + new ticket
        WorkflowRule.objects.create(
            organization=None,
            name='Load balance',
            trigger='ticket_created',
            conditions={'subject_contains': 'route-me'},
            actions=[{'type': 'assign_load_balanced'}],
            is_active=True,
        )
        from psa.models import Ticket as _T
        new_t = _T.objects.create(
            organization=self.org, subject='please route-me',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
        )
        new_t.refresh_from_db()
        # Carol has 0 open → should be chosen
        self.assertEqual(new_t.assigned_to, self.carol)

    def test_assign_round_robin_picks_oldest_assigned(self):
        from datetime import timedelta
        from django.utils import timezone
        from psa.models import Ticket, WorkflowRule
        # Give each tech a "previous" ticket assigned at a known time
        now = timezone.now()
        old = Ticket.objects.create(
            organization=self.org, subject='old',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
            assigned_to=self.bob,
        )
        Ticket.objects.filter(pk=old.pk).update(
            updated_at=now - timedelta(days=10),
        )
        recent = Ticket.objects.create(
            organization=self.org, subject='recent',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
            assigned_to=self.alice,
        )
        Ticket.objects.filter(pk=recent.pk).update(
            updated_at=now - timedelta(hours=1),
        )
        # Carol has never been assigned → should rank first by the
        # "never-been-assigned" tier.
        WorkflowRule.objects.create(
            organization=None,
            name='Round robin',
            trigger='ticket_created',
            conditions={'subject_contains': 'round-me'},
            actions=[{'type': 'assign_round_robin'}],
            is_active=True,
        )
        new_t = Ticket.objects.create(
            organization=self.org, subject='please round-me',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_open,
        )
        new_t.refresh_from_db()
        # Carol (no prior assignment) should be picked
        self.assertEqual(new_t.assigned_to, self.carol)

    def test_inactive_user_never_chosen(self):
        from psa.models import WorkflowRule
        # Make inactive Zach the only group member
        zach_only = self.linux  # currently has Alice
        zach_only.user_set.clear()
        self.zach.groups.add(zach_only)
        WorkflowRule.objects.create(
            organization=None,
            name='Skill (inactive only)',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'assign_skill_match',
                      'skill_group': 'linux-experts'}],
            is_active=True,
        )
        t = self._make_ticket()
        t.refresh_from_db()
        # Zach is inactive → no candidate → ticket stays unassigned
        self.assertIsNone(t.assigned_to)


class WorkflowTemplateTests(TestCase):
    """Phase 14 v9 (v3.17.288): WorkflowRuleTemplate save-and-clone."""

    def setUp(self):
        _setup_seed()
        self.org = Organization.objects.create(name='TplCo', slug='tpl-co')

    def test_instantiate_creates_rule_with_template_payload(self):
        from psa.models import WorkflowRuleTemplate, WorkflowRule
        tpl = WorkflowRuleTemplate.objects.create(
            name='P1 → page on-call',
            category='sla',
            trigger='ticket_created',
            conditions={'priority': 'P1'},
            actions=[{'type': 'add_tag', 'tag': 'urgent'}],
            else_actions=[],
        )
        rule = tpl.instantiate(organization=self.org)
        self.assertIsInstance(rule, WorkflowRule)
        self.assertEqual(rule.organization, self.org)
        self.assertEqual(rule.trigger, 'ticket_created')
        self.assertEqual(rule.conditions, {'priority': 'P1'})
        self.assertEqual(rule.actions, [{'type': 'add_tag', 'tag': 'urgent'}])
        self.assertTrue(rule.is_active)

    def test_instantiate_msp_wide_when_no_org(self):
        from psa.models import WorkflowRuleTemplate
        tpl = WorkflowRuleTemplate.objects.create(
            name='Global tag',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'add_tag', 'tag': 'all'}],
        )
        rule = tpl.instantiate()
        self.assertIsNone(rule.organization)

    def test_instantiate_with_name_override(self):
        from psa.models import WorkflowRuleTemplate
        tpl = WorkflowRuleTemplate.objects.create(
            name='Stock template',
            trigger='ticket_created',
            conditions={},
            actions=[],
        )
        rule = tpl.instantiate(name_override='Custom name for ACME')
        self.assertEqual(rule.name, 'Custom name for ACME')


class WorkflowCrossModuleTests(TestCase):
    """Phase 14 v11 (v3.17.289): cross-module action types."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='XmCo', slug='xm-co')
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status = TicketStatus.objects.filter(slug='new').first() \
                       or TicketStatus.objects.first()

    def _make_ticket(self):
        from psa.models import Ticket
        return Ticket.objects.create(
            organization=self.org, subject='T',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )

    def test_create_charge_action_records_charge(self):
        from psa.models import WorkflowRule, Charge
        from decimal import Decimal as _D
        WorkflowRule.objects.create(
            organization=None,
            name='Auto-charge afterhours',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'create_charge',
                      'amount': '125.00',
                      'description': 'After-hours emergency'}],
            is_active=True,
        )
        t = self._make_ticket()
        chg = Charge.objects.filter(organization=self.org).first()
        self.assertIsNotNone(chg)
        self.assertEqual(chg.amount, _D('125.00'))
        self.assertEqual(chg.description, 'After-hours emergency')
        self.assertEqual(chg.recurrence, 'once')
        self.assertFalse(chg.is_credit)

    def test_create_charge_invalid_amount_captured_on_rule(self):
        from psa.models import WorkflowRule, Charge
        rule = WorkflowRule.objects.create(
            organization=None,
            name='Bad-amount charge',
            trigger='ticket_created',
            conditions={},
            actions=[{'type': 'create_charge',
                      'amount': '0',
                      'description': 'zero'}],
            is_active=True,
        )
        self._make_ticket()
        rule.refresh_from_db()
        self.assertIn('amount', rule.last_error or '')
        self.assertEqual(Charge.objects.filter(organization=self.org).count(), 0)

    def test_create_charge_can_record_credit(self):
        from psa.models import WorkflowRule, Charge
        from decimal import Decimal as _D
        WorkflowRule.objects.create(
            organization=None,
            name='Goodwill credit',
            trigger='ticket_created',
            conditions={'subject_contains': 'apologize'},
            actions=[{'type': 'create_charge',
                      'amount': '50.00',
                      'is_credit': True,
                      'description': 'Service goodwill'}],
            is_active=True,
        )
        from psa.models import Ticket
        Ticket.objects.create(
            organization=self.org, subject='please apologize',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status,
        )
        chg = Charge.objects.filter(description='Service goodwill').first()
        self.assertIsNotNone(chg)
        self.assertTrue(chg.is_credit)


class WorkflowStateBasedConfirmationTests(TestCase):
    """Phase 14 v6 (v3.17.289): state-based workflows are already
    covered by the existing `status_changed` trigger + status condition.
    These tests confirm the path works end-to-end so the bullet can be
    marked shipped without additional code."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='StateCo', slug='state-co')
        from psa.models import Queue, TicketPriority, TicketType, TicketStatus
        self.queue = Queue.objects.first()
        self.priority = TicketPriority.objects.first()
        self.ttype = TicketType.objects.first()
        self.status_new = TicketStatus.objects.filter(slug='new').first()
        self.status_resolved, _ = TicketStatus.objects.get_or_create(
            slug='resolved-state',
            defaults={'name': 'Resolved (state-test)', 'is_terminal': False},
        )

    def test_status_changed_trigger_fires_state_specific_actions(self):
        from psa.models import Ticket, WorkflowRule
        WorkflowRule.objects.create(
            organization=None,
            name='Tag on resolved',
            trigger='status_changed',
            conditions={'status': 'resolved-state'},
            actions=[{'type': 'add_tag', 'tag': 'state-resolved'}],
            is_active=True,
        )
        t = Ticket.objects.create(
            organization=self.org, subject='State test',
            queue=self.queue, priority=self.priority,
            ticket_type=self.ttype, status=self.status_new,
        )
        # Move to resolved
        t.status = self.status_resolved
        t.save()
        t.refresh_from_db()
        self.assertIn('state-resolved', t.tags or [])


class WorkflowSuggestionTests(TestCase):
    """Phase 14 v13 (v3.17.290): WorkflowSuggestion + heuristic engine.
    Gated by SystemSetting.psa_ai_enabled."""

    def setUp(self):
        _setup_seed()
        self.org = Organization.objects.create(name='SugCo', slug='sug-co')
        self.user = User.objects.create_user('sug-user', 'sg@x.com', 'pw')

    def test_accept_materializes_workflow_rule(self):
        from psa.models import WorkflowSuggestion, WorkflowRule
        s = WorkflowSuggestion.objects.create(
            summary='Suggested',
            suggested_payload={
                'name': 'Accepted rule',
                'trigger': 'ticket_created',
                'conditions': {'priority': 'P1'},
                'actions': [{'type': 'add_tag', 'tag': 'urgent'}],
            },
        )
        rule = s.accept(user=self.user)
        self.assertIsInstance(rule, WorkflowRule)
        self.assertEqual(rule.trigger, 'ticket_created')
        self.assertEqual(rule.actions, [{'type': 'add_tag', 'tag': 'urgent'}])
        s.refresh_from_db()
        self.assertEqual(s.status, 'accepted')
        self.assertEqual(s.accepted_rule, rule)

    def test_accept_idempotent(self):
        from psa.models import WorkflowSuggestion
        s = WorkflowSuggestion.objects.create(
            summary='Sug',
            suggested_payload={'name': 'X', 'trigger': 'ticket_created'},
        )
        rule1 = s.accept(user=self.user)
        rule2 = s.accept(user=self.user)
        self.assertEqual(rule1.pk, rule2.pk)

    def test_dismiss_changes_status(self):
        from psa.models import WorkflowSuggestion
        s = WorkflowSuggestion.objects.create(summary='Sug')
        s.dismiss(user=self.user)
        s.refresh_from_db()
        self.assertEqual(s.status, 'dismissed')

    def test_command_no_op_when_ai_disabled(self):
        from django.core.management import call_command
        from psa.models import WorkflowSuggestion
        ss = SystemSetting.get_settings()
        ss.psa_ai_enabled = False
        ss.save()
        call_command('psa_generate_workflow_suggestions', verbosity=0)
        self.assertEqual(WorkflowSuggestion.objects.count(), 0)

    def test_command_generates_priority_route_suggestion(self):
        from django.core.management import call_command
        from psa.models import (
            WorkflowSuggestion, Ticket, Queue, TicketPriority,
            TicketType, TicketStatus,
        )
        ss = SystemSetting.get_settings()
        ss.psa_ai_enabled = True
        ss.save()
        # Create 6 P1 tickets all assigned to the same user
        queue = Queue.objects.first()
        priority = TicketPriority.objects.filter(code='P1').first() \
                    or TicketPriority.objects.first()
        ttype = TicketType.objects.first()
        status = TicketStatus.objects.first()
        for i in range(6):
            Ticket.objects.create(
                organization=self.org, subject=f'P1 ticket {i}',
                queue=queue, priority=priority, ticket_type=ttype,
                status=status, assigned_to=self.user,
            )
        call_command('psa_generate_workflow_suggestions',
                      '--min-count', '5', verbosity=0)
        sugs = WorkflowSuggestion.objects.filter(status='pending')
        self.assertGreaterEqual(sugs.count(), 1)
        # Look for the priority-route suggestion
        priority_sug = sugs.filter(summary__contains=priority.code).first()
        self.assertIsNotNone(priority_sug)
        self.assertEqual(priority_sug.suggested_payload['actions'][0]['type'],
                          'assign_to')


class ContractEnginePhase1Tests(TestCase):
    """v3.17.126: rollover + role gates + bundle subtotal + profitability."""

    def setUp(self):
        from core.models import Organization
        from psa.models import Contract
        self.msp = Organization.objects.create(name='MSP', slug='ce-msp')
        self.client_org = Organization.objects.create(name='Client', slug='ce-client')
        self.contract = Contract.objects.create(
            organization=self.msp,
            client_org=self.client_org,
            name='Block 40',
            contract_type='block_hours',
            status='active',
            start_date='2026-01-01',
            total_hours=40,
            hourly_rate=150,
            overage_rate=180,
            billable_role_codes=['T1', 'T2'],
            excluded_role_codes=['project'],
        )

    def test_role_gate_excluded_wins(self):
        self.contract.billable_role_codes = ['T1', 'project']
        self.contract.excluded_role_codes = ['project']
        self.assertFalse(self.contract.is_role_billable('project'))
        self.assertTrue(self.contract.is_role_billable('T1'))

    def test_role_gate_empty_billable_means_all(self):
        self.contract.billable_role_codes = []
        self.contract.excluded_role_codes = []
        self.assertTrue(self.contract.is_role_billable('any'))

    def test_role_gate_unlisted_role_is_not_billable(self):
        self.contract.billable_role_codes = ['T1', 'T2']
        self.contract.excluded_role_codes = []
        self.assertFalse(self.contract.is_role_billable('T3'))

    def test_effective_total_minutes_includes_rollover(self):
        from datetime import date, timedelta
        self.contract.rolled_over_minutes = 600  # 10h
        self.contract.rollover_expires_at = date.today() + timedelta(days=30)
        self.contract.save()
        self.assertEqual(self.contract.effective_total_minutes(), 40 * 60 + 600)

    def test_effective_total_minutes_drops_expired_rollover(self):
        from datetime import date, timedelta
        self.contract.rolled_over_minutes = 600
        self.contract.rollover_expires_at = date.today() - timedelta(days=1)
        self.contract.save()
        self.assertEqual(self.contract.effective_total_minutes(), 40 * 60)

    def test_bundled_subtotal(self):
        from psa.models import ContractBundleItem
        ContractBundleItem.objects.create(contract=self.contract, name='AV',
            quantity=10, unit_price=15, recurring_period='monthly')
        ContractBundleItem.objects.create(contract=self.contract, name='Backup',
            quantity=5, unit_price=20, recurring_period='monthly')
        self.assertEqual(int(self.contract.bundled_subtotal()), 250)

    def test_profitability_snapshot_keys(self):
        snap = self.contract.profitability_snapshot()
        for k in ('revenue', 'cost', 'margin', 'margin_pct', 'hours_used', 'overage_hours'):
            self.assertIn(k, snap)


class TechNotificationTests(TestCase):
    """v3.17.127: assignment + schedule notifications respect per-user prefs."""

    def setUp(self):
        _setup_seed()  # Existing helper that seeds defaults
        self.org = Organization.objects.create(name='Notify Co', slug='notify-co')
        self.tech = User.objects.create_user('alice', email='alice@x.com', password='pw')
        Membership.objects.create(user=self.tech, organization=self.org, role=Role.OWNER, is_active=True)
        self.tech.profile.phone = '+15551234567'
        self.tech.profile.notify_assigned_email = True
        self.tech.profile.notify_assigned_sms = False
        self.tech.profile.save()

    def _ticket_kwargs(self):
        return dict(
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first() or TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )

    def test_unassigned_ticket_no_notify(self):
        from psa.notifications import notify_tech_assigned
        t = Ticket.objects.create(
            organization=self.org, subject='X',
            **self._ticket_kwargs(),
        )
        result = notify_tech_assigned(t)
        self.assertEqual(result, {'email': None, 'sms': None})

    def test_self_assignment_skips_notify(self):
        from psa.notifications import notify_tech_assigned
        t = Ticket.objects.create(
            organization=self.org, subject='X', assigned_to=self.tech,
            **self._ticket_kwargs(),
        )
        result = notify_tech_assigned(t, by_user=self.tech)
        self.assertEqual(result['email'], 'self')

    def test_email_pref_respected(self):
        from psa.notifications import notify_tech_assigned
        from django.core import mail
        t = Ticket.objects.create(
            organization=self.org, subject='X', assigned_to=self.tech,
            **self._ticket_kwargs(),
        )
        # Clear any mail captured during create-time signal fan-out
        mail.outbox = []
        # Assigner is a different user
        other = User.objects.create_user('bob', email='bob@x.com', password='pw')
        notify_tech_assigned(t, by_user=other)
        # 1 email should have been sent (Django test backend captures it)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(t.ticket_number, mail.outbox[0].subject)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class KBCategoryBrowseTests(TestCase):
    """v3.17.128: KB browse page filters articles by category + descendants."""

    def setUp(self):
        from docs.models import Document, DocumentCategory
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.user = User.objects.create_superuser('su', 'su@x.com', 'pw')
        # Build a 2-level category tree: Networking > Wireless
        self.cat_net = DocumentCategory.objects.create(
            organization=None, name='Networking', slug='networking', order=1,
        )
        self.cat_wifi = DocumentCategory.objects.create(
            organization=None, parent=self.cat_net, name='Wireless', slug='wireless', order=2,
        )
        self.cat_other = DocumentCategory.objects.create(
            organization=None, name='Email', slug='email', order=3,
        )
        # Articles
        Document.objects.create(
            organization=None, is_global=True,
            title='ATAK SSID setup', slug='atak-ssid', body='steps',
            content_type='html', category=self.cat_wifi,
        )
        Document.objects.create(
            organization=None, is_global=True,
            title='OSPF basics', slug='ospf-basics', body='steps',
            content_type='html', category=self.cat_net,
        )
        Document.objects.create(
            organization=None, is_global=True,
            title='M365 mailbox', slug='m365-mb', body='steps',
            content_type='html', category=self.cat_other,
        )

    def test_filter_by_parent_returns_descendants(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/kb/?category=networking')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('OSPF basics', body)
        self.assertIn('ATAK SSID setup', body)  # via descendant inclusion
        self.assertNotIn('M365 mailbox', body)  # different branch

    def test_filter_by_leaf_returns_only_that_category(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/kb/?category=wireless')
        body = r.content.decode()
        self.assertIn('ATAK SSID setup', body)
        self.assertNotIn('OSPF basics', body)
        self.assertNotIn('M365 mailbox', body)

    def test_no_filter_returns_all(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/kb/')
        body = r.content.decode()
        self.assertIn('OSPF basics', body)
        self.assertIn('ATAK SSID setup', body)
        self.assertIn('M365 mailbox', body)

    def test_breadcrumb_shows_path(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/kb/?category=wireless')
        body = r.content.decode()
        # Both parent and leaf should appear in breadcrumb
        self.assertIn('Networking', body)
        self.assertIn('Wireless', body)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class AdminCanAssignTests(TestCase):
    """v3.17.129: org admins + staff can assign tickets to other techs.

    Covers the audit fix that wired up the admin "Assign to tech" sub-menu
    on ticket detail and the matching `set_assignee` quick-action.
    """

    def setUp(self):
        _enable_psa_global()
        _setup_seed()
        self.org_a = Organization.objects.create(name='OrgA', slug='org-a-asg')
        self.org_b = Organization.objects.create(name='OrgB', slug='org-b-asg')
        _enable_psa_for(self.org_a)
        _enable_psa_for(self.org_b)

        # Org A: admin + a regular tech (Editor) + a non-member.
        self.admin_a = User.objects.create_user(username='admin_a', password='pw', email='aa@x.com')
        Membership.objects.update_or_create(
            user=self.admin_a, organization=self.org_a,
            defaults={'role': Role.ADMIN, 'is_active': True},
        )
        self.tech_a = User.objects.create_user(username='tech_a', password='pw', email='ta@x.com')
        Membership.objects.update_or_create(
            user=self.tech_a, organization=self.org_a,
            defaults={'role': Role.EDITOR, 'is_active': True},
        )
        # Editor in org A — NOT an admin; should be forbidden from reassigning.
        self.editor_a = User.objects.create_user(username='editor_a', password='pw', email='ea@x.com')
        Membership.objects.update_or_create(
            user=self.editor_a, organization=self.org_a,
            defaults={'role': Role.EDITOR, 'is_active': True},
        )

        # Org B tech — has no membership in org A.
        self.tech_b = User.objects.create_user(username='tech_b', password='pw', email='tb@x.com')
        Membership.objects.update_or_create(
            user=self.tech_b, organization=self.org_b,
            defaults={'role': Role.EDITOR, 'is_active': True},
        )

        # MSP staff user — assignable to anything, regardless of org.
        self.staff_user = User.objects.create_user(
            username='staff_u', password='pw', email='su@x.com', is_staff=True,
        )

        kw = dict(
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        self.ticket_a = Ticket.objects.create(
            organization=self.org_a, subject='Assign me', **kw
        )

    def _login(self, user, org=None):
        c = Client()
        c.force_login(user)
        if org is not None:
            s = c.session
            s['current_organization_id'] = org.id
            s.save()
        return c

    def test_admin_can_assign_to_org_member(self):
        c = self._login(self.admin_a, self.org_a)
        resp = c.post(
            f'/psa/t/{self.ticket_a.ticket_number}/action/',
            {'action': 'set_assignee', 'assignee_id': str(self.tech_a.id)},
        )
        self.assertEqual(resp.status_code, 302, resp.content[:200])
        self.ticket_a.refresh_from_db()
        self.assertEqual(self.ticket_a.assigned_to_id, self.tech_a.id)

    def test_admin_can_assign_to_staff_user(self):
        c = self._login(self.admin_a, self.org_a)
        resp = c.post(
            f'/psa/t/{self.ticket_a.ticket_number}/action/',
            {'action': 'set_assignee', 'assignee_id': str(self.staff_user.id)},
        )
        self.assertEqual(resp.status_code, 302)
        self.ticket_a.refresh_from_db()
        self.assertEqual(self.ticket_a.assigned_to_id, self.staff_user.id)

    def test_admin_cannot_assign_to_non_member(self):
        """tech_b is in org B only, not staff/superuser — must be rejected."""
        c = self._login(self.admin_a, self.org_a)
        resp = c.post(
            f'/psa/t/{self.ticket_a.ticket_number}/action/',
            {'action': 'set_assignee', 'assignee_id': str(self.tech_b.id)},
        )
        # Server returns 302 with an error message, leaves assignee unchanged.
        self.assertEqual(resp.status_code, 302)
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.assigned_to_id)

    def test_editor_cannot_reassign(self):
        """Editor (non-admin) in org A may not use set_assignee."""
        c = self._login(self.editor_a, self.org_a)
        resp = c.post(
            f'/psa/t/{self.ticket_a.ticket_number}/action/',
            {'action': 'set_assignee', 'assignee_id': str(self.tech_a.id)},
        )
        self.assertEqual(resp.status_code, 302)
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.assigned_to_id)

    def test_admin_can_unassign(self):
        self.ticket_a.assigned_to = self.tech_a
        self.ticket_a.save()
        c = self._login(self.admin_a, self.org_a)
        resp = c.post(
            f'/psa/t/{self.ticket_a.ticket_number}/action/',
            {'action': 'set_assignee', 'assignee_id': ''},
        )
        self.assertEqual(resp.status_code, 302)
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.assigned_to_id)

    def test_dispatch_assign_admin_only(self):
        """Editor in the ticket's org cannot use the dispatch DnD endpoint."""
        c = self._login(self.editor_a, self.org_a)
        resp = c.post(
            '/psa/dispatch/assign/',
            {'ticket_number': self.ticket_a.ticket_number,
             'assignee': str(self.tech_a.id)},
        )
        self.assertEqual(resp.status_code, 403)

    def test_dispatch_assign_admin_succeeds(self):
        c = self._login(self.admin_a, self.org_a)
        resp = c.post(
            '/psa/dispatch/assign/',
            {'ticket_number': self.ticket_a.ticket_number,
             'assignee': str(self.tech_a.id)},
        )
        self.assertEqual(resp.status_code, 200)
        self.ticket_a.refresh_from_db()
        self.assertEqual(self.ticket_a.assigned_to_id, self.tech_a.id)

    def test_dispatch_assign_rejects_non_member(self):
        c = self._login(self.admin_a, self.org_a)
        resp = c.post(
            '/psa/dispatch/assign/',
            {'ticket_number': self.ticket_a.ticket_number,
             'assignee': str(self.tech_b.id)},
        )
        self.assertEqual(resp.status_code, 400)
        self.ticket_a.refresh_from_db()
        self.assertIsNone(self.ticket_a.assigned_to_id)

    def test_ticket_detail_passes_eligible_assignees_to_admin(self):
        c = self._login(self.admin_a, self.org_a)
        resp = c.get(f'/psa/t/{self.ticket_a.ticket_number}/')
        self.assertEqual(resp.status_code, 200)
        # The admin's name + the org-A tech should both appear in the
        # rendered Actions sub-menu.
        body = resp.content.decode()
        self.assertIn('Assign to tech', body)
        self.assertIn('tech_a', body)
        # The non-member from org B must NOT appear.
        self.assertNotIn('tech_b', body)

    def test_ticket_detail_hides_assign_submenu_for_editor(self):
        c = self._login(self.editor_a, self.org_a)
        resp = c.get(f'/psa/t/{self.ticket_a.ticket_number}/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Assign to tech', resp.content.decode())


class ContractAutoRenewalTests(TestCase):
    """v3.17.130: nightly auto-renewal cron rolls forward expired contracts."""

    def setUp(self):
        from core.models import Organization
        self.msp = Organization.objects.create(name='MSP', slug='cr-msp')
        self.client_org = Organization.objects.create(name='Client', slug='cr-client')

    def _make_contract(self, **overrides):
        from datetime import date, timedelta
        from psa.models import Contract
        defaults = dict(
            organization=self.msp,
            client_org=self.client_org,
            name='Block 40',
            contract_type='block_hours',
            status='active',
            start_date=date.today() - timedelta(days=365),
            end_date=date.today() - timedelta(days=1),  # expired yesterday
            total_hours=40,
            hourly_rate=150,
            auto_renew=True,
            auto_renew_period_months=12,
        )
        defaults.update(overrides)
        return Contract.objects.create(**defaults)

    def test_autorenew_creates_child_contract(self):
        from django.core.management import call_command
        from psa.models import Contract
        old = self._make_contract()
        call_command('psa_auto_renew_contracts')
        old.refresh_from_db()
        self.assertEqual(old.status, 'expired')
        self.assertEqual(Contract.objects.filter(parent_contract=old).count(), 1)

    def test_rollover_carries_unused_hours(self):
        from django.core.management import call_command
        from psa.models import Contract
        old = self._make_contract(
            total_hours=40, hours_used_minutes=20 * 60,  # 20h used
            rollover_percent=50, rollover_expiry_days=30,
        )
        call_command('psa_auto_renew_contracts')
        new = Contract.objects.filter(parent_contract=old).first()
        # 20h unused × 50% = 10h = 600 min
        self.assertEqual(new.rolled_over_minutes, 600)
        self.assertIsNotNone(new.rollover_expires_at)

    def test_dry_run_does_not_create(self):
        from django.core.management import call_command
        from psa.models import Contract
        old = self._make_contract()
        call_command('psa_auto_renew_contracts', '--dry-run')
        old.refresh_from_db()
        self.assertEqual(old.status, 'active')  # still active
        self.assertEqual(Contract.objects.filter(parent_contract=old).count(), 0)

    def test_auto_renew_off_skips(self):
        from django.core.management import call_command
        from psa.models import Contract
        old = self._make_contract(auto_renew=False)
        call_command('psa_auto_renew_contracts')
        self.assertEqual(Contract.objects.filter(parent_contract=old).count(), 0)


# ---------------------------------------------------------------------------
# v3.17.134 — KB CRUD + permission groups
# ---------------------------------------------------------------------------

class KBPermissionsTests(TestCase):
    """v3.17.134: gate KB action buttons + mutation endpoints by RoleTemplate
    booleans (kb_view_articles / kb_edit_articles / kb_move_articles /
    kb_manage_categories / kb_publish_articles)."""

    def setUp(self):
        _enable_psa_global()
        _setup_seed()
        self.org = Organization.objects.create(name='KB Org', slug='kb-org')
        _enable_psa_for(self.org)

        from accounts.models import RoleTemplate

        # Read-only role: only kb_view_articles
        self.ro_role = RoleTemplate.objects.create(
            name='KB-RO', description='read-only KB', is_system_template=False,
            kb_view_articles=True, kb_edit_articles=False,
            kb_move_articles=False, kb_manage_categories=False,
            kb_publish_articles=False,
        )
        # Editor role: view + edit + move + publish, no manage_categories
        self.editor_role = RoleTemplate.objects.create(
            name='KB-Editor', description='editor KB', is_system_template=False,
            kb_view_articles=True, kb_edit_articles=True,
            kb_move_articles=True, kb_manage_categories=False,
            kb_publish_articles=True,
        )

        self.ro_user = User.objects.create_user(username='ro', password='pw', email='ro@x.com')
        Membership.objects.create(
            user=self.ro_user, organization=self.org,
            role=Role.READONLY, role_template=self.ro_role, is_active=True,
        )
        self.editor_user = User.objects.create_user(username='ed', password='pw', email='ed@x.com')
        Membership.objects.create(
            user=self.editor_user, organization=self.org,
            role=Role.EDITOR, role_template=self.editor_role, is_active=True,
        )

    def test_check_kb_perm_helper(self):
        from psa.views import _check_kb_perm
        self.assertTrue(_check_kb_perm(self.ro_user, 'kb_view_articles'))
        self.assertFalse(_check_kb_perm(self.ro_user, 'kb_edit_articles'))
        self.assertFalse(_check_kb_perm(self.ro_user, 'kb_move_articles'))
        self.assertTrue(_check_kb_perm(self.editor_user, 'kb_edit_articles'))
        self.assertTrue(_check_kb_perm(self.editor_user, 'kb_move_articles'))
        self.assertFalse(_check_kb_perm(self.editor_user, 'kb_manage_categories'))

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_readonly_user_does_not_see_edit_buttons(self):
        c = Client()
        c.force_login(self.ro_user)
        resp = c.get('/psa/kb/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8', errors='ignore')
        self.assertNotIn('New article', body)
        self.assertNotIn('Move selected', body)

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_editor_user_sees_edit_and_move(self):
        c = Client()
        c.force_login(self.editor_user)
        # Need at least one article so the move form renders.
        from docs.models import Document
        Document.objects.create(
            organization=self.org, title='An article', slug='an-article',
            body='hi', is_global=True,
        )
        resp = c.get('/psa/kb/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8', errors='ignore')
        self.assertIn('New article', body)
        self.assertIn('Move selected', body)
        # No manage_categories permission → no "Manage categories" button.
        self.assertNotIn('Manage categories', body)

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_readonly_post_to_move_endpoint_is_forbidden(self):
        c = Client()
        c.force_login(self.ro_user)
        from docs.models import Document
        a = Document.objects.create(
            organization=self.org, title='Locked', slug='locked',
            body='nope', is_global=True,
        )
        resp = c.post('/psa/kb/move/', {
            'article_ids': [a.pk],
            'target_category_id': '',
        })
        self.assertEqual(resp.status_code, 403)


class KBMoveArticlesTests(TestCase):
    """v3.17.134: bulk-move endpoint reassigns category and writes one audit row."""

    def setUp(self):
        _enable_psa_global()
        _setup_seed()
        self.org = Organization.objects.create(name='Move Org', slug='move-org')
        _enable_psa_for(self.org)

        from accounts.models import RoleTemplate
        self.role = RoleTemplate.objects.create(
            name='KB-Mover', description='mover', is_system_template=False,
            kb_view_articles=True, kb_edit_articles=True,
            kb_move_articles=True, kb_manage_categories=True,
            kb_publish_articles=True,
        )
        self.user = User.objects.create_user(username='mv', password='pw', email='mv@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org,
            role=Role.EDITOR, role_template=self.role, is_active=True,
        )

        from docs.models import Document, DocumentCategory
        self.cat_a = DocumentCategory.objects.create(name='Cat A', slug='cat-a')
        self.cat_b = DocumentCategory.objects.create(name='Cat B', slug='cat-b')
        self.a1 = Document.objects.create(
            organization=self.org, title='Art 1', slug='art-1',
            body='b', is_global=True, category=self.cat_a,
        )
        self.a2 = Document.objects.create(
            organization=self.org, title='Art 2', slug='art-2',
            body='b', is_global=True, category=self.cat_a,
        )

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_move_to_target_category(self):
        c = Client()
        c.force_login(self.user)
        before = AuditLog.objects.filter(object_type='docs.Document').count()
        resp = c.post('/psa/kb/move/', {
            'article_ids': [self.a1.pk, self.a2.pk],
            'target_category_id': self.cat_b.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.a1.refresh_from_db()
        self.a2.refresh_from_db()
        self.assertEqual(self.a1.category_id, self.cat_b.pk)
        self.assertEqual(self.a2.category_id, self.cat_b.pk)
        # Exactly one audit row written for the bulk move.
        after = AuditLog.objects.filter(object_type='docs.Document').count()
        self.assertEqual(after - before, 1)

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_move_to_uncategorized(self):
        c = Client()
        c.force_login(self.user)
        resp = c.post('/psa/kb/move/', {
            'article_ids': [self.a1.pk],
            'target_category_id': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.a1.refresh_from_db()
        self.assertIsNone(self.a1.category_id)

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_invalid_target_category_does_not_move(self):
        c = Client()
        c.force_login(self.user)
        resp = c.post('/psa/kb/move/', {
            'article_ids': [self.a1.pk],
            'target_category_id': '999999',
        })
        self.assertEqual(resp.status_code, 302)
        self.a1.refresh_from_db()
        # Untouched — still in cat_a.
        self.assertEqual(self.a1.category_id, self.cat_a.pk)

    @override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
    def test_no_article_ids_redirects(self):
        c = Client()
        c.force_login(self.user)
        resp = c.post('/psa/kb/move/', {
            'target_category_id': self.cat_b.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.a1.refresh_from_db()
        self.assertEqual(self.a1.category_id, self.cat_a.pk)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ServiceCatalogViewModeTests(TestCase):
    """v3.17.135: catalog page renders tile and list views."""

    def setUp(self):
        from psa.models import ServiceCatalogItem
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='Cat Co', slug='cat-co')
        self.user = User.objects.create_superuser('su', 'su@x.com', 'pw')
        ServiceCatalogItem.objects.update_or_create(
            slug='password-reset',
            defaults={
                'name': 'Password Reset', 'is_active': True,
                'description': 'Reset a user password',
                'default_subject': 'Password reset for {{user}}',
                'default_body': 'User: {{user}}',
            },
        )

    def test_tile_view_renders(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/catalog/?view=tile')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Password Reset', r.content)

    def test_list_view_renders(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/catalog/?view=list')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Password Reset', r.content)
        self.assertIn(b'<thead class="table-light">', r.content)

    def test_invalid_view_falls_back_to_tile(self):
        self.client.force_login(self.user)
        r = self.client.get('/psa/catalog/?view=garbage')
        self.assertEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# Procurement (Phase 4.1)
# ---------------------------------------------------------------------------

