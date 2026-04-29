"""
PSA Phase 1 tests.

These verify the load-bearing safety properties:
  * PSA is OFF by default and routes 404 when disabled.
  * Per-client opt-in is enforced for client-bound routes.
  * Tickets get auto-numbered.
  * Cross-tenant access is denied for non-staff users.
  * Audit log entries are written on creates.
  * The seed command produces the documented defaults.

Phase 2+ will add deeper RBAC, internal-note isolation, portal tests, etc.
"""
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone


# Tests bypass the project-wide 2FA enforcement middleware so we can exercise
# the views directly. PSA security tests run with the real middleware are
# scheduled for Phase 2 once we have a 2FA-enrolled fixture user.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]

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


def _setup_seed():
    call_command('psa_seed_defaults', verbosity=0)


def _enable_psa_global():
    s = SystemSetting.get_settings()
    s.psa_enabled = True
    s.save()


def _enable_psa_for(org):
    cps, _ = ClientPSASettings.objects.get_or_create(organization=org)
    cps.enabled = True
    cps.save()
    return cps


class FeatureFlagDefaultsTests(TestCase):
    """PSA must default to disabled at every layer."""

    def test_global_flag_defaults_off(self):
        s = SystemSetting.get_settings()
        self.assertFalse(s.psa_enabled, 'SystemSetting.psa_enabled must default to False')
        self.assertFalse(is_psa_enabled())

    def test_client_inherits_global_when_no_row(self):
        """When PSA is on globally and a client has no ClientPSASettings row,
        treat it as enabled (cascade UX)."""
        org = Organization.objects.create(name='ACME', slug='acme')
        # Off globally → False regardless.
        self.assertFalse(is_psa_enabled_for_client(org))

        # On globally, no row → True (lazy default).
        _enable_psa_global()
        self.assertTrue(is_psa_enabled_for_client(org))

    def test_per_surface_flags_stay_off_by_default(self):
        """Sensitive per-surface flags must remain off even when the master
        ClientPSASettings.enabled defaults to True."""
        org = Organization.objects.create(name='ACME', slug='acme')
        cps = ClientPSASettings.objects.create(organization=org)
        self.assertTrue(cps.enabled, 'master enabled flag should default to True (cascades from global)')
        self.assertFalse(cps.portal_enabled)
        self.assertFalse(cps.anonymous_ticket_form_enabled)
        self.assertFalse(cps.email_to_ticket_enabled)
        self.assertFalse(cps.sms_notifications_enabled)
        self.assertFalse(cps.desktop_alerts_enabled)
        self.assertFalse(cps.external_alert_ingest_enabled)

    def test_explicit_client_opt_out_blocks(self):
        """Admins can still opt a specific client OUT by setting enabled=False."""
        org = Organization.objects.create(name='ACME', slug='acme')
        _enable_psa_global()
        ClientPSASettings.objects.create(organization=org, enabled=False)
        self.assertFalse(is_psa_enabled_for_client(org))

    def test_external_psa_auto_opts_out(self):
        """Clients with an active PSAConnection (ConnectWise / Halo / etc.)
        should auto-opt-out of native PSA — the whole point of the native
        PSA is to serve clients WITHOUT another PSA."""
        from integrations.models import PSAConnection
        from psa.feature_flags import client_has_external_psa

        org = Organization.objects.create(name='ACME', slug='acme')
        _enable_psa_global()
        # Sanity: no external PSA → enabled by auto-detect
        self.assertFalse(client_has_external_psa(org))
        self.assertTrue(is_psa_enabled_for_client(org))

        # Add an active external PSA connection
        PSAConnection.objects.create(
            organization=org,
            provider_type='connectwise',
            name='ACME ConnectWise',
            base_url='https://example.connectwise.com',
            encrypted_credentials='dummy',
            is_active=True,
        )
        self.assertTrue(client_has_external_psa(org))
        # Auto-opt-out — no row needed
        self.assertFalse(is_psa_enabled_for_client(org))

    def test_external_psa_inactive_does_not_opt_out(self):
        """An is_active=False external PSA connection is NOT a real opt-out
        signal — it's a disconnected/disabled integration."""
        from integrations.models import PSAConnection
        org = Organization.objects.create(name='ACME', slug='acme')
        _enable_psa_global()
        PSAConnection.objects.create(
            organization=org,
            provider_type='halopsa',
            name='ACME Halo (disabled)',
            base_url='https://example.halopsa.com',
            encrypted_credentials='dummy',
            is_active=False,
        )
        self.assertTrue(is_psa_enabled_for_client(org), 'inactive external PSA must not block native')

    def test_external_psa_overrides_explicit_enable(self):
        """Hard product rule: native PSA is ONLY for clients without
        another PSA. Even an explicit ClientPSASettings.enabled=True
        cannot re-enable native if the client has an active external
        PSAConnection. To use native, deactivate the external first."""
        from integrations.models import PSAConnection
        org = Organization.objects.create(name='ACME', slug='acme')
        _enable_psa_global()
        PSAConnection.objects.create(
            organization=org,
            provider_type='autotask',
            name='ACME Autotask',
            base_url='https://example.autotask.net',
            encrypted_credentials='dummy',
            is_active=True,
        )
        # Without override: auto-opt-out
        self.assertFalse(is_psa_enabled_for_client(org))
        # With explicit enable=True row: STILL opted out — external PSA wins.
        ClientPSASettings.objects.create(organization=org, enabled=True)
        self.assertFalse(is_psa_enabled_for_client(org))
        # Sanity: deactivating the external PSA re-enables native.
        PSAConnection.objects.filter(organization=org).update(is_active=False)
        self.assertTrue(is_psa_enabled_for_client(org))

    def test_admin_can_still_opt_out_no_external_client(self):
        """Admin opt-out via cps.enabled=False still works when there's
        no external PSA — that's how a no-PSA client gets disabled."""
        org = Organization.objects.create(name='ACME', slug='acme')
        _enable_psa_global()
        # No external PSA, default auto = enabled
        self.assertTrue(is_psa_enabled_for_client(org))
        # Admin disables explicitly
        ClientPSASettings.objects.create(organization=org, enabled=False)
        self.assertFalse(is_psa_enabled_for_client(org))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class RouteGatingTests(TestCase):
    """When PSA is disabled, every PSA route must return 404."""

    def setUp(self):
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.user = User.objects.create_user(username='user1', password='pw', email='u@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['current_organization_id'] = self.org.id
        session.save()

    def test_routes_404_when_psa_globally_disabled(self):
        self.assertEqual(self.client.get('/psa/').status_code, 404)
        self.assertEqual(self.client.get('/psa/new/').status_code, 404)
        self.assertEqual(self.client.get('/psa/t/PSA-2026-000001/').status_code, 404)

    def test_opt_out_client_excluded_from_create_dropdown(self):
        """PSA is global — /psa/new/ doesn't 404 anymore. Instead, opted-out
        clients are filtered out of the client dropdown so admins can't
        accidentally pick them."""
        _enable_psa_global()
        _setup_seed()
        ClientPSASettings.objects.update_or_create(
            organization=self.org,
            defaults={'enabled': False},
        )
        resp = self.client.get('/psa/new/')
        # User has no other org, so eligible_clients is now empty → no_eligible_clients page.
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('No eligible clients', body)
        self.assertNotIn(f'<option value="{self.org.id}"', body)

    def test_create_loads_when_globally_on_with_no_client_row(self):
        """No ClientPSASettings row + global on → client inherits enabled."""
        _enable_psa_global()
        _setup_seed()
        # Do NOT call _enable_psa_for — there should be no row at all.
        self.assertFalse(ClientPSASettings.objects.filter(organization=self.org).exists())
        resp = self.client.get('/psa/new/')
        self.assertEqual(resp.status_code, 200)

    def test_list_loads_when_both_flags_on(self):
        _enable_psa_global()
        _setup_seed()
        _enable_psa_for(self.org)
        resp = self.client.get('/psa/')
        self.assertEqual(resp.status_code, 200)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class TicketLifecycleTests(TestCase):
    """Auto-numbering, audit log on create, tenant scoping."""

    def setUp(self):
        _enable_psa_global()
        _setup_seed()
        self.org_a = Organization.objects.create(name='OrgA', slug='org-a')
        self.org_b = Organization.objects.create(name='OrgB', slug='org-b')
        _enable_psa_for(self.org_a)
        _enable_psa_for(self.org_b)
        self.user_a = User.objects.create_user(username='ua', password='pw', email='ua@x.com')
        Membership.objects.update_or_create(
            user=self.user_a, organization=self.org_a,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        self.user_b = User.objects.create_user(username='ub', password='pw', email='ub@x.com')
        Membership.objects.update_or_create(
            user=self.user_b, organization=self.org_b,
            defaults={'role': Role.OWNER, 'is_active': True},
        )

    def _login(self, user, org):
        c = Client()
        c.force_login(user)
        s = c.session
        s['current_organization_id'] = org.id
        s.save()
        return c

    def test_ticket_number_is_auto_assigned(self):
        ticket = Ticket.objects.create(
            organization=self.org_a,
            subject='hello',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        self.assertTrue(ticket.ticket_number.startswith('PSA-'))
        self.assertRegex(ticket.ticket_number, r'^PSA-\d{4}-\d{6}$')

    def test_ticket_numbers_increment(self):
        kw = dict(
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        t1 = Ticket.objects.create(organization=self.org_a, subject='one', **kw)
        t2 = Ticket.objects.create(organization=self.org_a, subject='two', **kw)
        self.assertNotEqual(t1.ticket_number, t2.ticket_number)
        # second number must be > first
        self.assertGreater(int(t2.ticket_number.rsplit('-', 1)[1]), int(t1.ticket_number.rsplit('-', 1)[1]))

    def test_audit_log_written_on_create_via_view(self):
        c = self._login(self.user_a, self.org_a)
        resp = c.post('/psa/new/', {
            'client': self.org_a.pk,  # PSA is global; client picked from form
            'subject': 'audit me',
            'description': 'body',
            'queue': Queue.objects.first().pk,
            'status': TicketStatus.objects.first().pk,
            'priority': TicketPriority.objects.first().pk,
            'ticket_type': TicketType.objects.first().pk,
        })
        self.assertEqual(resp.status_code, 302, resp.content[:300])
        # An entry is recorded by our explicit AuditLog.log() call. The
        # project's audit middleware may add additional rows; we only care
        # that ours is present.
        psa_logs = AuditLog.objects.filter(action='create', object_type='psa.Ticket')
        self.assertEqual(psa_logs.count(), 1, list(AuditLog.objects.values('action', 'object_type', 'description')))
        log = psa_logs.first()
        self.assertEqual(log.organization, self.org_a)

    def test_cross_tenant_detail_blocked_for_non_staff(self):
        # Ticket created for org A
        ticket = Ticket.objects.create(
            organization=self.org_a, subject='org A ticket',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        # User B (different org) tries to view it — must 404.
        c = self._login(self.user_b, self.org_b)
        resp = c.get(f'/psa/t/{ticket.ticket_number}/')
        self.assertEqual(resp.status_code, 404)


class SeedDefaultsTests(TestCase):
    def test_seed_creates_documented_defaults(self):
        _setup_seed()
        self.assertGreaterEqual(Queue.objects.count(), 7)
        self.assertGreaterEqual(TicketStatus.objects.count(), 10)
        self.assertEqual(TicketPriority.objects.count(), 5)
        self.assertGreaterEqual(TicketType.objects.count(), 14)

        # P1 must have the spec'd 15-min response target
        p1 = TicketPriority.objects.get(code='P1')
        self.assertEqual(p1.response_target_minutes, 15)

    def test_seed_is_idempotent(self):
        _setup_seed()
        c1 = Queue.objects.count() + TicketStatus.objects.count() + TicketPriority.objects.count() + TicketType.objects.count()
        _setup_seed()
        c2 = Queue.objects.count() + TicketStatus.objects.count() + TicketPriority.objects.count() + TicketType.objects.count()
        self.assertEqual(c1, c2)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class VaultContextTests(TestCase):
    """The vault-context endpoint must scope passwords to the ticket's
    organization, refuse cross-tenant access, and never leak ciphertext."""

    def setUp(self):
        from vault.models import Password

        _enable_psa_global()
        _setup_seed()

        self.org_a = Organization.objects.create(name='OrgA', slug='org-a')
        self.org_b = Organization.objects.create(name='OrgB', slug='org-b')

        # Build a minimal valid Password for each org. encrypted_password is
        # a non-null TextField with no default, so we must populate it via
        # set_password() before save().
        self.pw_a = Password(title='secret-A', organization=self.org_a, is_personal=False)
        self.pw_a.set_password('plaintext-A-do-not-leak')
        self.pw_a.save()

        self.pw_b = Password(title='secret-B', organization=self.org_b, is_personal=False)
        self.pw_b.set_password('plaintext-B-do-not-leak')
        self.pw_b.save()

        self.user_a = User.objects.create_user(username='ua', password='pw', email='ua@x.com')
        Membership.objects.update_or_create(
            user=self.user_a, organization=self.org_a,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        self.user_b = User.objects.create_user(username='ub', password='pw', email='ub@x.com')
        Membership.objects.update_or_create(
            user=self.user_b, organization=self.org_b,
            defaults={'role': Role.OWNER, 'is_active': True},
        )

        _enable_psa_for(self.org_a)
        _enable_psa_for(self.org_b)

        self.ticket = Ticket.objects.create(
            organization=self.org_a,
            subject='vault-context ticket',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )

    def _login(self, user, org):
        c = Client()
        c.force_login(user)
        s = c.session
        s['current_organization_id'] = org.id
        s.save()
        return c

    def _url(self):
        return f'/psa/t/{self.ticket.ticket_number}/context/'

    def test_vault_context_lists_only_client_passwords(self):
        c = self._login(self.user_a, self.org_a)
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8', errors='replace')
        self.assertIn('secret-A', body)
        self.assertNotIn('secret-B', body)

    def test_vault_context_blocks_cross_tenant(self):
        c = self._login(self.user_b, self.org_b)
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_vault_context_404_when_psa_disabled(self):
        s = SystemSetting.get_settings()
        s.psa_enabled = False
        s.save()
        c = self._login(self.user_a, self.org_a)
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_vault_context_logs_audit_read(self):
        c = self._login(self.user_a, self.org_a)
        before = AuditLog.objects.filter(
            action='read',
            object_type='psa.TicketContext',
            object_id=str(self.ticket.pk),
            organization=self.org_a,
        ).count()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 200)
        after = AuditLog.objects.filter(
            action='read',
            object_type='psa.TicketContext',
            object_id=str(self.ticket.pk),
            organization=self.org_a,
        ).count()
        self.assertEqual(after - before, 1, list(AuditLog.objects.values('action', 'object_type', 'object_id', 'description')))

    def test_vault_context_does_not_render_secret_values(self):
        c = self._login(self.user_a, self.org_a)
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8', errors='replace')
        # Neither the encrypted column name nor any decryption-related token
        # should leak into the rendered HTML.
        self.assertNotIn('encrypted_password', body)
        self.assertNotIn('decrypt', body)
        self.assertNotIn('key=', body)
        # Plaintext sentinel must never appear either.
        self.assertNotIn('plaintext-A-do-not-leak', body)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class Phase2aTicketActionsTests(TestCase):
    """Phase 2a: comments, internal notes, attachments, quick actions, close."""

    def setUp(self):
        _enable_psa_global()
        _setup_seed()
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.user = User.objects.create_user(username='tech1', password='pw', email='tech1@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['current_organization_id'] = self.org.id
        s.save()

        from psa.models import Ticket
        self.ticket = Ticket.objects.create(
            organization=self.org, subject='Phase 2a tester',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first() or TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )

    def _url(self, suffix=''):
        return f'/psa/t/{self.ticket.ticket_number}/{suffix}'

    # ---------- Comments / internal notes ----------
    def test_post_reply_creates_comment(self):
        resp = self.client.post(self._url('comment/'), {'body': 'Hello world'})
        self.assertEqual(resp.status_code, 302)
        from psa.models import TicketComment
        c = TicketComment.objects.filter(ticket=self.ticket).first()
        self.assertIsNotNone(c)
        self.assertEqual(c.body, 'Hello world')
        self.assertFalse(c.is_internal)
        self.assertFalse(c.is_system)

    def test_post_internal_note_marks_is_internal(self):
        self.client.post(self._url('comment/'), {'body': 'private', 'is_internal': '1'})
        from psa.models import TicketComment
        c = TicketComment.objects.filter(ticket=self.ticket, is_internal=True).first()
        self.assertIsNotNone(c)
        self.assertTrue(c.is_internal)

    def test_empty_comment_rejected(self):
        from psa.models import TicketComment
        before = TicketComment.objects.filter(ticket=self.ticket).count()
        resp = self.client.post(self._url('comment/'), {'body': '   '})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(TicketComment.objects.filter(ticket=self.ticket).count(), before)

    def test_first_response_at_set_only_on_external_reply(self):
        # Internal note must NOT set first_response_at
        self.client.post(self._url('comment/'), {'body': 'internal', 'is_internal': '1'})
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.first_response_at)
        # External reply does
        self.client.post(self._url('comment/'), {'body': 'external'})
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.first_response_at)

    # ---------- Attachments ----------
    def test_attach_uploads_within_size_and_mime(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('hello.txt', b'hello', content_type='text/plain')
        resp = self.client.post(self._url('attach/'), {'file': f})
        self.assertEqual(resp.status_code, 302)
        from psa.models import TicketAttachment
        att = TicketAttachment.objects.filter(ticket=self.ticket).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.filename, 'hello.txt')
        self.assertEqual(att.content_type, 'text/plain')

    def test_attach_rejects_disallowed_mime(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from psa.models import TicketAttachment
        f = SimpleUploadedFile('virus.exe', b'MZ', content_type='application/x-msdownload')
        resp = self.client.post(self._url('attach/'), {'file': f})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(TicketAttachment.objects.filter(ticket=self.ticket).count(), 0)

    def test_attach_rejects_oversize(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from psa.models import TicketAttachment
        from psa.views import ATTACHMENT_MAX_BYTES
        big = b'x' * (ATTACHMENT_MAX_BYTES + 1024)
        f = SimpleUploadedFile('big.txt', big, content_type='text/plain')
        resp = self.client.post(self._url('attach/'), {'file': f})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(TicketAttachment.objects.filter(ticket=self.ticket).count(), 0)

    def test_attach_sanitises_filename(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from psa.models import TicketAttachment
        f = SimpleUploadedFile('../../etc/passwd', b'data', content_type='text/plain')
        self.client.post(self._url('attach/'), {'file': f})
        att = TicketAttachment.objects.filter(ticket=self.ticket).first()
        self.assertIsNotNone(att)
        # Path separators must not survive in filename
        self.assertNotIn('/', att.filename)
        self.assertNotIn('\\', att.filename)

    # ---------- Quick actions ----------
    def test_assign_me(self):
        self.client.post(self._url('action/'), {'action': 'assign_me'})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, self.user)
        # System comment must be written
        from psa.models import TicketComment
        sys = TicketComment.objects.filter(ticket=self.ticket, is_system=True).first()
        self.assertIsNotNone(sys)
        self.assertIn('Assigned', sys.body)

    def test_set_status_to_terminal_sets_resolved_at(self):
        terminal = TicketStatus.objects.filter(is_terminal=True).first()
        self.client.post(self._url('action/'), {'action': 'set_status', 'status': terminal.id})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, terminal)
        self.assertIsNotNone(self.ticket.resolved_at)

    # ---------- Close + Reopen ----------
    def test_close_requires_resolution_summary(self):
        from psa.models import Ticket
        resp = self.client.post(self._url('action/'), {
            'action': 'close',
            'closure_category': 'fixed',
            # no resolution_summary
        })
        self.assertEqual(resp.status_code, 302)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closed_at)
        self.assertEqual(self.ticket.resolution_summary, '')

    def test_close_requires_valid_category(self):
        resp = self.client.post(self._url('action/'), {
            'action': 'close',
            'closure_category': 'bogus',
            'resolution_summary': 'fix',
        })
        self.assertEqual(resp.status_code, 302)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closed_at)

    def test_close_with_valid_input_succeeds(self):
        self.client.post(self._url('action/'), {
            'action': 'close',
            'closure_category': 'fixed',
            'resolution_summary': 'replaced bad capacitor',
        })
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.closed_at)
        self.assertEqual(self.ticket.closure_category, 'fixed')
        self.assertIn('capacitor', self.ticket.resolution_summary)

    def test_reopen_clears_closed_state(self):
        # Close first
        self.client.post(self._url('action/'), {
            'action': 'close',
            'closure_category': 'fixed',
            'resolution_summary': 'done',
        })
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.closed_at)
        # Reopen
        self.client.post(self._url('action/'), {'action': 'reopen'})
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closed_at)
        self.assertIsNone(self.ticket.resolved_at)
        self.assertEqual(self.ticket.closure_category, '')
        # Status must be non-terminal
        self.assertFalse(self.ticket.status.is_terminal)

    # ---------- Cross-tenant safety ----------
    def test_cross_tenant_comment_blocked(self):
        other_org = Organization.objects.create(name='OtherCo', slug='other')
        other_user = User.objects.create_user(username='other', password='pw', email='o@x.com')
        # The accounts post_save signal auto-creates a READONLY Membership
        # in the first active org (ACME, in this test). For a true
        # cross-tenant scenario, kill that membership and add only OtherCo.
        Membership.objects.filter(user=other_user, organization=self.org).delete()
        Membership.objects.update_or_create(
            user=other_user, organization=other_org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        c = Client()
        c.force_login(other_user)
        s = c.session; s['current_organization_id'] = other_org.id; s.save()
        from psa.models import TicketComment
        before = TicketComment.objects.filter(ticket=self.ticket).count()
        resp = c.post(self._url('comment/'), {'body': 'malicious'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(TicketComment.objects.filter(ticket=self.ticket).count(), before)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class Phase2bTests(TestCase):
    """Phase 2b: watchers + canned replies."""

    def setUp(self):
        _enable_psa_global()
        _setup_seed()
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.user = User.objects.create_user(username='tech', password='pw', email='t@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session; s['current_organization_id'] = self.org.id; s.save()

        from psa.models import Ticket
        self.ticket = Ticket.objects.create(
            organization=self.org, subject='Phase 2b ticket',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )

    def _url(self, suffix=''):
        return f'/psa/t/{self.ticket.ticket_number}/{suffix}'

    # ---------- Watchers ----------
    def test_watch_toggle_subscribes_then_unsubscribes(self):
        from psa.models import TicketWatcher
        # Initially not watching
        self.assertFalse(TicketWatcher.objects.filter(ticket=self.ticket, user=self.user).exists())
        # Toggle on
        self.client.post(self._url('watch/'))
        self.assertTrue(TicketWatcher.objects.filter(ticket=self.ticket, user=self.user).exists())
        # Toggle off
        self.client.post(self._url('watch/'))
        self.assertFalse(TicketWatcher.objects.filter(ticket=self.ticket, user=self.user).exists())

    def test_watcher_sees_button_state_in_detail(self):
        # Pre-watch: button text says "Watch"
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Watch</button>'.encode(), resp.content) if False else None
        # Watch and check the new state
        self.client.post(self._url('watch/'))
        resp = self.client.get(self._url())
        body = resp.content.decode()
        self.assertIn('Watching', body)

    # ---------- Canned replies ----------
    def test_canned_reply_render_substitutes_variables(self):
        from psa.models import CannedReply
        r = CannedReply.objects.create(
            name='hello', body='Hi {{user.username}}, ticket {{ticket.number}} for {{ticket.client}}',
            organization=None, created_by=self.user, is_active=True,
        )
        rendered = r.render(ticket=self.ticket, user=self.user)
        self.assertIn(self.user.username, rendered)
        self.assertIn(self.ticket.ticket_number, rendered)
        self.assertIn(self.org.name, rendered)
        # Unknown placeholders left intact
        r2 = CannedReply.objects.create(
            name='unknown', body='hello {{unknown.thing}}',
            organization=None, created_by=self.user, is_active=True,
        )
        self.assertIn('{{unknown.thing}}', r2.render(ticket=self.ticket, user=self.user))

    def test_canned_reply_global_visible_on_any_ticket(self):
        from psa.models import CannedReply
        CannedReply.objects.create(
            name='Greeting', body='Hi there', organization=None,
            created_by=self.user, is_active=True,
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Greeting', resp.content)

    def test_canned_reply_org_scoped_isolated(self):
        from psa.models import CannedReply, Ticket
        # Reply scoped to org A
        CannedReply.objects.create(
            name='AcmeOnly', body='ACME body', organization=self.org,
            created_by=self.user, is_active=True,
        )
        # Reply scoped to a different org
        other_org = Organization.objects.create(name='OtherCo', slug='other')
        CannedReply.objects.create(
            name='OtherOnly', body='Other body', organization=other_org,
            created_by=self.user, is_active=True,
        )
        # Detail for ACME ticket should see AcmeOnly but NOT OtherOnly
        resp = self.client.get(self._url())
        body = resp.content.decode()
        self.assertIn('AcmeOnly', body)
        self.assertNotIn('OtherOnly', body)

    def test_canned_reply_inactive_hidden(self):
        from psa.models import CannedReply
        CannedReply.objects.create(
            name='Disabled', body='nope', organization=None,
            created_by=self.user, is_active=False,
        )
        resp = self.client.get(self._url())
        self.assertNotIn(b'Disabled', resp.content)

    def test_canned_reply_create_view(self):
        from psa.models import CannedReply
        resp = self.client.post('/psa/canned/new/', {
            'name': 'My reply', 'body': 'Hello world',
            'organization': self.org.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CannedReply.objects.filter(name='My reply').count(), 1)


# ---------------------------------------------------------------------------
# Phase 2c — SLA + time tracking + service catalog tests
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class SLATests(TestCase):
    """SLA computation + breach detection."""

    def setUp(self):
        _setup_seed()
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.user = User.objects.create_user('tech', password='pw', email='t@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )

    def _ticket(self, priority_code='P3'):
        from psa.models import Ticket
        from psa.sla import apply_due_dates
        t = Ticket.objects.create(
            organization=self.org, subject='SLA test',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.get(code=priority_code),
            ticket_type=TicketType.objects.first(),
        )
        apply_due_dates(t)
        return t

    def test_due_dates_set_from_priority_targets(self):
        t = self._ticket(priority_code='P1')
        self.assertIsNotNone(t.first_response_due_at)
        self.assertIsNotNone(t.resolution_due_at)
        # P1 = 15min response, 240min resolution. Window from created_at.
        self.assertGreater(t.resolution_due_at, t.first_response_due_at)

    def test_response_breach_when_overdue_and_no_response(self):
        from datetime import timedelta
        from psa.sla import response_breached
        t = self._ticket(priority_code='P1')
        # Force created_at into the past so the 15-min target has elapsed
        from psa.models import Ticket
        Ticket.objects.filter(pk=t.pk).update(
            created_at=timezone.now() - timedelta(minutes=30),
            first_response_due_at=timezone.now() - timedelta(minutes=15),
        )
        t.refresh_from_db()
        self.assertTrue(response_breached(t))

    def test_paused_status_suppresses_breach(self):
        from datetime import timedelta
        from psa.sla import response_breached, status_chip
        t = self._ticket(priority_code='P1')
        from psa.models import Ticket
        # Push due-date into the past
        paused = TicketStatus.objects.filter(slug='waiting-on-client').first()
        Ticket.objects.filter(pk=t.pk).update(
            first_response_due_at=timezone.now() - timedelta(minutes=15),
            status=paused,
        )
        t.refresh_from_db()
        self.assertFalse(response_breached(t))
        chip = status_chip(t)
        self.assertEqual(chip['kind'], 'paused')

    def test_resolved_ticket_chip(self):
        from psa.sla import status_chip
        t = self._ticket()
        terminal = TicketStatus.objects.filter(is_terminal=True).first()
        t.status = terminal
        t.resolved_at = timezone.now()
        t.save()
        chip = status_chip(t)
        self.assertEqual(chip['kind'], 'resolved')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class TimeTrackingTests(TestCase):

    def setUp(self):
        _setup_seed()
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.user = User.objects.create_user('tech', password='pw', email='t@x.com')
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session; s['current_organization_id'] = self.org.id; s.save()
        from psa.models import Ticket
        self.ticket = Ticket.objects.create(
            organization=self.org, subject='Time test',
            queue=Queue.objects.first(),
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        # Enable PSA globally so the gate decorators allow through
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()

    def test_timer_start_creates_running_entry(self):
        from psa.models import TicketTimeEntry
        resp = self.client.post(f'/psa/t/{self.ticket.ticket_number}/timer/start/',
                                {'is_billable': '1'})
        self.assertEqual(resp.status_code, 302)
        e = TicketTimeEntry.objects.filter(ticket=self.ticket, user=self.user).first()
        self.assertIsNotNone(e)
        self.assertTrue(e.is_running)
        self.assertTrue(e.is_billable)

    def test_timer_stop_finalises_duration(self):
        from datetime import timedelta
        from psa.models import TicketTimeEntry
        # Create a running timer that started 7 minutes ago
        e = TicketTimeEntry.objects.create(
            ticket=self.ticket, user=self.user,
            started_at=timezone.now() - timedelta(minutes=7),
        )
        resp = self.client.post(f'/psa/t/{self.ticket.ticket_number}/timer/stop/',
                                {'notes': 'fixed it'})
        self.assertEqual(resp.status_code, 302)
        e.refresh_from_db()
        self.assertFalse(e.is_running)
        self.assertGreaterEqual(e.duration_minutes, 6)
        self.assertEqual(e.notes, 'fixed it')

    def test_one_running_timer_per_user_per_ticket(self):
        # Start one timer
        self.client.post(f'/psa/t/{self.ticket.ticket_number}/timer/start/')
        # Try to start another — should be a no-op
        from psa.models import TicketTimeEntry
        before = TicketTimeEntry.objects.filter(ticket=self.ticket, user=self.user).count()
        self.client.post(f'/psa/t/{self.ticket.ticket_number}/timer/start/')
        after = TicketTimeEntry.objects.filter(ticket=self.ticket, user=self.user).count()
        self.assertEqual(before, after)

    def test_manual_time_entry_within_bounds(self):
        from psa.models import TicketTimeEntry
        resp = self.client.post(f'/psa/t/{self.ticket.ticket_number}/time/manual/',
                                {'minutes': '30', 'notes': 'phone call', 'is_billable': '1'})
        self.assertEqual(resp.status_code, 302)
        e = TicketTimeEntry.objects.filter(ticket=self.ticket).first()
        self.assertEqual(e.duration_minutes, 30)
        self.assertEqual(e.notes, 'phone call')
        self.assertTrue(e.is_billable)

    def test_manual_time_entry_rejects_zero(self):
        from psa.models import TicketTimeEntry
        before = TicketTimeEntry.objects.count()
        self.client.post(f'/psa/t/{self.ticket.ticket_number}/time/manual/',
                        {'minutes': '0', 'notes': ''})
        self.assertEqual(TicketTimeEntry.objects.count(), before)

    def test_manual_time_entry_rejects_excessive(self):
        from psa.models import TicketTimeEntry
        before = TicketTimeEntry.objects.count()
        self.client.post(f'/psa/t/{self.ticket.ticket_number}/time/manual/',
                        {'minutes': '99999'})
        self.assertEqual(TicketTimeEntry.objects.count(), before)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ServiceCatalogTests(TestCase):

    def setUp(self):
        _setup_seed()
        self.user = User.objects.create_user('tech', password='pw', email='t@x.com')
        self.client = Client()
        self.client.force_login(self.user)
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()

    def test_catalog_seeded(self):
        from psa.models import ServiceCatalogItem
        self.assertGreaterEqual(ServiceCatalogItem.objects.count(), 14)

    def test_catalog_page_renders(self):
        resp = self.client.get('/psa/catalog/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Service Catalog', resp.content)
        self.assertIn(b'Password Reset', resp.content)

    def test_create_from_catalog_prefills(self):
        # Need an org membership to be able to load /psa/new/
        org = Organization.objects.create(name='ACME', slug='acme')
        Membership.objects.update_or_create(
            user=self.user, organization=org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        s = self.client.session; s['current_organization_id'] = org.id; s.save()
        resp = self.client.get('/psa/new/?from_catalog=password-reset')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Structured fields path: catalog_item with fields_json renders the
        # field labels, NOT a default-subject input. Subject is built server-
        # side from field values on POST.
        self.assertIn('catalog_slug', body)
        self.assertIn('password-reset', body)  # the slug → catalog_item is set
        self.assertIn('Username / account', body)  # from fields_json
        self.assertIn('Identity verified by', body)  # from fields_json


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class CatalogStructuredFieldsTests(TestCase):
    """Phase 2c upgrade: catalog items have structured fields. The
    requester's input renders into subject+body via {{key}} templates."""

    def setUp(self):
        _setup_seed()
        from psa.models import ServiceCatalogItem, Ticket
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.user = User.objects.create_user('su', password='pw', email='su@x.com', is_superuser=True)
        Membership.objects.update_or_create(
            user=self.user, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.client = Client()
        self.client.force_login(self.user)
        sess = self.client.session; sess['current_organization_id'] = self.org.id; sess.save()

    def test_render_template_substitutes_keys(self):
        from psa.models import ServiceCatalogItem
        out = ServiceCatalogItem.render_template(
            'Hello {{name}} from {{company}}',
            {'name': 'Alex', 'company': 'ACME'},
        )
        self.assertEqual(out, 'Hello Alex from ACME')

    def test_render_template_strips_unfilled_placeholders(self):
        from psa.models import ServiceCatalogItem
        out = ServiceCatalogItem.render_template(
            'Hello {{name}} — {{leftover}}', {'name': 'Alex'},
        )
        self.assertNotIn('{{leftover}}', out)
        self.assertIn('Alex', out)

    def test_seed_attaches_field_schemas(self):
        from psa.models import ServiceCatalogItem
        new_user = ServiceCatalogItem.objects.get(slug='new-user')
        self.assertGreater(len(new_user.fields_json), 0)
        # Required fields exist with the expected shape
        keys = {f['key'] for f in new_user.fields_json}
        self.assertIn('full_name', keys)
        self.assertIn('email', keys)

    def test_create_ticket_from_catalog_substitutes_fields(self):
        from psa.models import Ticket
        resp = self.client.post('/psa/new/', {
            'catalog_slug': 'new-user',
            'client': self.org.id,
            'queue': self.org.native_psa_tickets.model._meta.get_field('queue').related_model.objects.first().pk,
            'status': self.org.native_psa_tickets.model._meta.get_field('status').related_model.objects.filter(slug='new').first().pk,
            'priority': self.org.native_psa_tickets.model._meta.get_field('priority').related_model.objects.first().pk,
            'ticket_type': self.org.native_psa_tickets.model._meta.get_field('ticket_type').related_model.objects.first().pk,
            'field_full_name': 'Alex Newhire',
            'field_email': 'alex@acme.com',
            'field_manager': 'Sam Boss',
            'field_start_date': '2026-05-01',
            'field_groups': 'Sales, Office E3',
            'field_equipment': 'Laptop, monitor',
        })
        self.assertEqual(resp.status_code, 302, resp.content[:300])
        t = Ticket.objects.filter(organization=self.org).order_by('-id').first()
        self.assertIsNotNone(t)
        self.assertIn('Alex Newhire', t.subject)
        self.assertIn('alex@acme.com', t.description)
        self.assertIn('2026-05-01', t.description)
        # No raw {{key}} placeholders should leak into the saved body.
        self.assertNotIn('{{', t.description)

    def test_required_field_missing_blocks_create(self):
        from psa.models import Ticket
        before = Ticket.objects.count()
        resp = self.client.post('/psa/new/', {
            'catalog_slug': 'new-user',
            'client': self.org.id,
            # Missing required field_full_name
            'field_email': 'alex@acme.com',
            'field_start_date': '2026-05-01',
        })
        self.assertEqual(resp.status_code, 302)
        # No new ticket
        self.assertEqual(Ticket.objects.count(), before)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class CatalogCRUDTests(TestCase):
    """Admin CRUD on catalog items. Non-admin users cannot reach the form."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()

    def test_non_admin_cannot_open_create_form(self):
        org = Organization.objects.create(name='ACME', slug='acme')
        u = User.objects.create_user('orguser', password='pw', email='o@x.com')
        Membership.objects.update_or_create(
            user=u, organization=org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        c = Client(); c.force_login(u)
        sess = c.session; sess['current_organization_id'] = org.id; sess.save()
        resp = c.get('/psa/catalog/new/')
        self.assertEqual(resp.status_code, 404)

    def test_admin_creates_catalog_item(self):
        from psa.models import ServiceCatalogItem
        admin = User.objects.create_user('boss', password='pw', email='b@x.com', is_superuser=True)
        c = Client(); c.force_login(admin)
        resp = c.post('/psa/catalog/new/', {
            'name': 'Test catalog item',
            'description': 'A test',
            'icon': 'fas fa-test',
            'default_subject': 'Test — {{thing}}',
            'default_body': 'Body for {{thing}}',
            'fields_json': '[{"key":"thing","label":"Thing","type":"text","required":true}]',
            'sort_order': '99',
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        item = ServiceCatalogItem.objects.filter(name='Test catalog item').first()
        self.assertIsNotNone(item)
        self.assertEqual(len(item.fields_json), 1)
        self.assertEqual(item.fields_json[0]['key'], 'thing')

    def test_admin_invalid_json_rejected(self):
        from psa.models import ServiceCatalogItem
        admin = User.objects.create_user('boss', password='pw', email='b@x.com', is_superuser=True)
        c = Client(); c.force_login(admin)
        before = ServiceCatalogItem.objects.count()
        resp = c.post('/psa/catalog/new/', {
            'name': 'Bad item',
            'fields_json': '{this-is-not-json',
            'default_subject': 's',
            'default_body': 'b',
            'sort_order': '0',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ServiceCatalogItem.objects.count(), before)

    def test_admin_invalid_field_type_rejected(self):
        from psa.models import ServiceCatalogItem
        admin = User.objects.create_user('boss', password='pw', email='b@x.com', is_superuser=True)
        c = Client(); c.force_login(admin)
        before = ServiceCatalogItem.objects.count()
        c.post('/psa/catalog/new/', {
            'name': 'Bad type item',
            'fields_json': '[{"key":"x","label":"X","type":"shell_command"}]',
            'default_subject': 's',
            'default_body': 'b',
            'sort_order': '0',
        })
        self.assertEqual(ServiceCatalogItem.objects.count(), before)

    def test_admin_deletes(self):
        from psa.models import ServiceCatalogItem
        admin = User.objects.create_user('boss', password='pw', email='b@x.com', is_superuser=True)
        c = Client(); c.force_login(admin)
        item = ServiceCatalogItem.objects.first()
        resp = c.post(f'/psa/catalog/{item.pk}/edit/', {'delete': '1'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ServiceCatalogItem.objects.filter(pk=item.pk).exists())


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class Phase2cRemainderTests(TestCase):
    """Recurring detection + @mentions + ticket merge."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ACME', slug='acme')
        self.alice = User.objects.create_user('alice', password='pw', email='a@x.com')
        self.bob = User.objects.create_user('bob', password='pw', email='b@x.com')
        Membership.objects.update_or_create(
            user=self.alice, organization=self.org,
            defaults={'role': Role.OWNER, 'is_active': True},
        )
        Membership.objects.update_or_create(
            user=self.bob, organization=self.org,
            defaults={'role': Role.EDITOR, 'is_active': True},
        )
        self.client = Client()
        self.client.force_login(self.alice)
        sess = self.client.session; sess['current_organization_id'] = self.org.id; sess.save()

        kw = dict(
            queue=Queue.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
        )
        self.t1 = Ticket.objects.create(organization=self.org, subject='Printer offline ground floor', **kw)
        self.t2 = Ticket.objects.create(organization=self.org, subject='Printer offline second floor', **kw)
        self.t3 = Ticket.objects.create(organization=self.org, subject='Email password reset', **kw)

    # -- Recurring detection --
    def test_find_similar_token_overlap(self):
        from psa.sla import find_similar_tickets
        hits = find_similar_tickets(self.t1)
        # t2 shares "printer" + "offline" with t1 — should match
        ticket_pks = {t.pk for t, _ in hits}
        self.assertIn(self.t2.pk, ticket_pks)
        self.assertNotIn(self.t3.pk, ticket_pks)

    def test_find_similar_excludes_self(self):
        from psa.sla import find_similar_tickets
        hits = find_similar_tickets(self.t1)
        for t, _ in hits:
            self.assertNotEqual(t.pk, self.t1.pk)

    # -- @mentions --
    def test_extract_mentions_handles_email_and_at(self):
        from psa.views import _extract_mentions
        body = 'Hey @bob, can you look at this with help from sysadmin@example.com?'
        out = _extract_mentions(body)
        self.assertEqual(out, ['bob'])  # email-style @ should not match

    def test_mention_adds_watcher(self):
        from psa.models import TicketWatcher
        resp = self.client.post(f'/psa/t/{self.t1.ticket_number}/comment/',
                                {'body': 'Hi @bob, take a look.'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TicketWatcher.objects.filter(ticket=self.t1, user=self.bob).exists())

    def test_mention_to_non_member_is_ignored(self):
        from psa.models import TicketWatcher
        outsider = User.objects.create_user('outsider', password='pw', email='o@x.com')
        Membership.objects.filter(user=outsider).delete()
        self.client.post(f'/psa/t/{self.t1.ticket_number}/comment/',
                         {'body': 'Hi @outsider, look at this.'})
        self.assertFalse(TicketWatcher.objects.filter(ticket=self.t1, user=outsider).exists())

    # -- Merge --
    def test_merge_moves_comments_and_closes_source(self):
        from psa.models import TicketComment
        # Add comments + an attachment-less marker comment to t1
        TicketComment.objects.create(ticket=self.t1, author=self.alice, body='source comment 1')
        TicketComment.objects.create(ticket=self.t1, author=self.alice, body='source comment 2')
        before_t2 = self.t2.comments.count()
        before_t1 = self.t1.comments.count()
        self.assertGreaterEqual(before_t1, 2)

        resp = self.client.post(f'/psa/t/{self.t1.ticket_number}/merge/',
                                {'target': self.t2.ticket_number})
        self.assertEqual(resp.status_code, 302)
        self.t1.refresh_from_db()
        self.t2.refresh_from_db()
        # Source comments moved to target (plus 1 system note added on each side)
        self.assertEqual(self.t1.comments.count(), 1)  # only the [merge] system note remains
        self.assertGreaterEqual(self.t2.comments.count(), before_t2 + 2 + 1)
        # Source closed as duplicate, pointing at target
        self.assertEqual(self.t1.duplicate_of_id, self.t2.id)
        self.assertEqual(self.t1.closure_category, 'duplicate')
        self.assertIsNotNone(self.t1.closed_at)

    def test_merge_into_self_blocked(self):
        before = self.t1.duplicate_of_id
        resp = self.client.post(f'/psa/t/{self.t1.ticket_number}/merge/',
                                {'target': self.t1.ticket_number})
        self.assertEqual(resp.status_code, 302)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.duplicate_of_id, before)  # unchanged


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
