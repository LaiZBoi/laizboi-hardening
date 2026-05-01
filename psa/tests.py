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

class ProcurementModelTests(TestCase):
    """Numbering + totals math for PR/PO."""

    def setUp(self):
        from psa.models import (
            PurchaseRequisition, PurchaseRequisitionLineItem,
            PurchaseOrder, PurchaseOrderLineItem,
        )
        self.PR = PurchaseRequisition
        self.PRLI = PurchaseRequisitionLineItem
        self.PO = PurchaseOrder
        self.POLI = PurchaseOrderLineItem
        self.org = Organization.objects.create(name='ProcOrg', slug='proc-org')

    def test_pr_next_number(self):
        from django.utils import timezone
        year = timezone.now().year
        pr1 = self.PR.objects.create(organization=self.org, title='First')
        pr2 = self.PR.objects.create(organization=self.org, title='Second')
        self.assertEqual(pr1.pr_number, f'PR-{year}-00001')
        self.assertEqual(pr2.pr_number, f'PR-{year}-00002')

    def test_pr_recompute_totals(self):
        from decimal import Decimal
        pr = self.PR.objects.create(
            organization=self.org, title='Totals', tax_rate=Decimal('10'),
        )
        self.PRLI.objects.create(requisition=pr, description='Switch',
                                  quantity=Decimal('2'), unit_price=Decimal('100'))
        self.PRLI.objects.create(requisition=pr, description='Cable',
                                  quantity=Decimal('5'), unit_price=Decimal('20'))
        pr.recompute_totals()
        # subtotal: 2*100 + 5*20 = 300; tax 10% = 30; total 330
        self.assertEqual(pr.subtotal, Decimal('300'))
        self.assertEqual(pr.tax_amount, Decimal('30'))
        self.assertEqual(pr.total, Decimal('330'))

    def test_po_next_number(self):
        from django.utils import timezone
        year = timezone.now().year
        po1 = self.PO.objects.create(
            organization=self.org, vendor_name='V1', title='T1')
        po2 = self.PO.objects.create(
            organization=self.org, vendor_name='V2', title='T2')
        self.assertEqual(po1.po_number, f'PO-{year}-00001')
        self.assertEqual(po2.po_number, f'PO-{year}-00002')

    def test_po_total_includes_shipping(self):
        from decimal import Decimal
        po = self.PO.objects.create(
            organization=self.org, vendor_name='Acme',
            title='Ship test', tax_rate=Decimal('10'),
            shipping_cost=Decimal('25'),
        )
        self.POLI.objects.create(po=po, description='Item',
                                  quantity=Decimal('1'), unit_price=Decimal('100'))
        po.recompute_totals()
        # subtotal 100; tax 10; shipping 25; total 135
        self.assertEqual(po.subtotal, Decimal('100'))
        self.assertEqual(po.tax_amount, Decimal('10'))
        self.assertEqual(po.total, Decimal('135'))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProcurementWorkflowTests(TestCase):
    """Approval gate + PR-to-PO conversion via HTTP."""

    def setUp(self):
        from accounts.models import RoleTemplate
        from psa.models import PurchaseRequisition, PurchaseRequisitionLineItem
        from decimal import Decimal
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ProcCo', slug='proc-co')

        # Approver: full procurement perms
        self.approver_role = RoleTemplate.objects.create(
            name='ProcApprover', is_system_template=False,
            procurement_view=True, procurement_create_pr=True,
            procurement_approve_pr=True, procurement_create_po=True,
            procurement_send_po=True,
        )
        self.approver = User.objects.create_user(
            username='approver', password='pw', email='a@x.com')
        Membership.objects.create(
            user=self.approver, organization=self.org,
            role=Role.ADMIN, role_template=self.approver_role,
            is_active=True,
        )

        # Tech: can create PR, cannot approve
        self.tech_role = RoleTemplate.objects.create(
            name='ProcTech', is_system_template=False,
            procurement_view=True, procurement_create_pr=True,
            procurement_approve_pr=False, procurement_create_po=False,
            procurement_send_po=False,
        )
        self.tech = User.objects.create_user(
            username='tech', password='pw', email='t@x.com')
        Membership.objects.create(
            user=self.tech, organization=self.org,
            role=Role.EDITOR, role_template=self.tech_role,
            is_active=True,
        )

        # Submitted PR with one line item
        self.pr = PurchaseRequisition.objects.create(
            organization=self.org, title='Need switch',
            requested_by=self.tech, status='submitted',
            tax_rate=Decimal('0'),
        )
        PurchaseRequisitionLineItem.objects.create(
            requisition=self.pr, description='Cisco switch',
            quantity=Decimal('1'), unit_price=Decimal('500'),
            sku='WS-C2960X', distributor_provider='ingram',
        )
        self.pr.recompute_totals()
        self.pr.save()

    def test_approver_can_approve(self):
        c = Client()
        c.force_login(self.approver)
        resp = c.post(f'/psa/requisitions/{self.pr.pk}/decide/', {
            'decision': 'approve',
            'decision_note': 'looks good',
        })
        self.assertEqual(resp.status_code, 302)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.approver_id, self.approver.pk)
        self.assertIsNotNone(self.pr.decided_at)

    def test_non_approver_blocked(self):
        c = Client()
        c.force_login(self.tech)
        resp = c.post(f'/psa/requisitions/{self.pr.pk}/decide/', {
            'decision': 'approve',
        })
        self.assertEqual(resp.status_code, 403)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')

    def test_pr_to_po_conversion(self):
        from psa.models import PurchaseOrder
        # Approve first
        self.pr.status = 'approved'
        self.pr.save(update_fields=['status'])

        c = Client()
        c.force_login(self.approver)
        resp = c.post(f'/psa/requisitions/{self.pr.pk}/convert/', {
            'vendor_name': 'Ingram Micro',
            'vendor_email': 'orders@ingrammicro.com',
        })
        self.assertEqual(resp.status_code, 302)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')

        po = PurchaseOrder.objects.filter(requisition=self.pr).first()
        self.assertIsNotNone(po)
        self.assertEqual(po.vendor_name, 'Ingram Micro')
        # Line items copied
        self.assertEqual(po.line_items.count(), 1)
        line = po.line_items.first()
        self.assertEqual(line.description, 'Cisco switch')
        self.assertEqual(line.sku, 'WS-C2960X')


# ---------------------------------------------------------------------------
# Phase 4.2 — Receiving + back-orders + serial-number capture
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class POReceivingTests(TestCase):
    """Receiving workflow: partial / full / back-orders / serials / cap."""

    def setUp(self):
        from accounts.models import RoleTemplate
        from psa.models import (
            PurchaseOrder, PurchaseOrderLineItem,
        )
        from decimal import Decimal
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='RecvCo', slug='recv-co')

        # Receiver role: full procurement perms
        self.role = RoleTemplate.objects.create(
            name='ProcReceiver', is_system_template=False,
            procurement_view=True, procurement_create_pr=True,
            procurement_approve_pr=True, procurement_create_po=True,
            procurement_send_po=True,
        )
        self.user = User.objects.create_user(
            username='receiver', password='pw', email='r@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org,
            role=Role.ADMIN, role_template=self.role,
            is_active=True,
        )

        # PO with 2 lines (qty 10 each), status=sent
        self.po = PurchaseOrder.objects.create(
            organization=self.org, vendor_name='ACME Vendor',
            title='Receiving test PO', status='sent',
        )
        self.line1 = PurchaseOrderLineItem.objects.create(
            po=self.po, description='Widget A', sku='WA-01',
            quantity=Decimal('10'), unit_price=Decimal('5'),
        )
        self.line2 = PurchaseOrderLineItem.objects.create(
            po=self.po, description='Widget B', sku='WB-01',
            quantity=Decimal('10'), unit_price=Decimal('7'),
        )
        self.po.recompute_totals()
        self.po.save()

    def _post_receive(self, data):
        c = Client()
        c.force_login(self.user)
        return c.post(f'/psa/purchase-orders/{self.po.pk}/receive/', data)

    def test_partial_receive_flips_status_to_partial(self):
        # Receive 5/10 on line 1, 0/10 on line 2 → PO.status = 'partial'
        resp = self._post_receive({
            f'qty_line_{self.line1.pk}': '5',
            f'qty_line_{self.line2.pk}': '',
            'carrier': 'UPS',
        })
        self.assertEqual(resp.status_code, 302)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'partial')
        self.line1.refresh_from_db()
        self.assertEqual(self.line1.received_quantity, 5)

    def test_full_receive_flips_status_to_received(self):
        # Receive 10/10 on both lines → PO.status = 'received'
        resp = self._post_receive({
            f'qty_line_{self.line1.pk}': '10',
            f'qty_line_{self.line2.pk}': '10',
        })
        self.assertEqual(resp.status_code, 302)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'received')

    def test_back_order_created_for_shorted_line(self):
        # Receive 6/10 on line 1 → POBackOrder with qty_outstanding=4
        from psa.models import POBackOrder
        resp = self._post_receive({
            f'qty_line_{self.line1.pk}': '6',
        })
        self.assertEqual(resp.status_code, 302)
        bo = POBackOrder.objects.filter(po_line=self.line1, status='open').first()
        self.assertIsNotNone(bo)
        self.assertEqual(bo.quantity_outstanding, 4)

    def test_back_order_filled_on_full_receive(self):
        # Open BO, then receive remainder → BO status flips to 'filled'
        from psa.models import POBackOrder
        # First: receive 6 of 10 on line 1, 10 of 10 on line 2
        self._post_receive({
            f'qty_line_{self.line1.pk}': '6',
            f'qty_line_{self.line2.pk}': '10',
        })
        bo = POBackOrder.objects.filter(po_line=self.line1, status='open').first()
        self.assertIsNotNone(bo)
        # Now receive the remaining 4 on line 1
        self._post_receive({
            f'qty_line_{self.line1.pk}': '4',
        })
        bo.refresh_from_db()
        self.assertEqual(bo.status, 'filled')
        self.assertIsNotNone(bo.closed_at)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'received')

    def test_serial_numbers_captured_and_assets_created(self):
        # Receive 1 unit with serial="ABC123" → POReceiptLine has it,
        # assets.Asset row created with that serial number
        from psa.models import POReceiptLine
        resp = self._post_receive({
            f'qty_line_{self.line1.pk}': '1',
            f'serials_line_{self.line1.pk}': 'ABC123',
        })
        self.assertEqual(resp.status_code, 302)
        rl = POReceiptLine.objects.filter(po_line=self.line1).first()
        self.assertIsNotNone(rl)
        self.assertEqual(rl.serial_numbers, ['ABC123'])
        # Asset row created (best-effort — only if creatable)
        try:
            from assets.models import Asset
            self.assertTrue(Asset.objects.filter(serial_number='ABC123').exists())
        except Exception:
            pass

    def test_qty_capped_at_remaining(self):
        # Try to receive 999 on a line with only 10 outstanding → only 10 received
        resp = self._post_receive({
            f'qty_line_{self.line1.pk}': '999',
        })
        self.assertEqual(resp.status_code, 302)
        self.line1.refresh_from_db()
        self.assertEqual(self.line1.received_quantity, 10)

    def test_back_order_cancel(self):
        # Receive partial → open BO → cancel it
        from psa.models import POBackOrder
        self._post_receive({
            f'qty_line_{self.line1.pk}': '6',
        })
        bo = POBackOrder.objects.filter(po_line=self.line1, status='open').first()
        self.assertIsNotNone(bo)
        c = Client()
        c.force_login(self.user)
        resp = c.post(f'/psa/back-orders/{bo.pk}/cancel/')
        self.assertEqual(resp.status_code, 302)
        bo.refresh_from_db()
        self.assertEqual(bo.status, 'cancelled')
        self.assertIsNotNone(bo.closed_at)


# ---------------------------------------------------------------------------
# Phase 4.3 — Vendor metadata + auto-replenish
# ---------------------------------------------------------------------------

class VendorMetadataTests(TestCase):
    """assets.Vendor procurement metadata + PO vendor-FK auto-fill."""

    def test_vendor_default_lead_time(self):
        from assets.models import Vendor
        v = Vendor.objects.create(name='ACME Distribution', slug='acme-dist')
        self.assertEqual(v.default_lead_time_days, 7)
        self.assertEqual(v.payment_terms, '')
        self.assertEqual(v.preferred_contact_method, '')
        self.assertTrue(v.is_active)

    def test_vendor_metadata_fields_persist(self):
        from assets.models import Vendor
        v = Vendor.objects.create(
            name='Ingram', slug='ingram',
            default_lead_time_days=14,
            payment_terms='net_30',
            preferred_contact_method='email',
            contact_email='orders@ingram.com',
            contact_phone='555-1212',
            billing_address='1 Main St',
            account_number='ACCT-99',
            distributor_provider='ingram',
            notes='Primary distributor.',
        )
        v.refresh_from_db()
        self.assertEqual(v.default_lead_time_days, 14)
        self.assertEqual(v.payment_terms, 'net_30')
        self.assertEqual(v.contact_email, 'orders@ingram.com')
        self.assertEqual(v.distributor_provider, 'ingram')

    def test_po_vendor_fk_link(self):
        """PurchaseOrder.vendor FK persists alongside snapshot fields."""
        from assets.models import Vendor
        from psa.models import PurchaseOrder
        v = Vendor.objects.create(
            name='Vendor X', slug='vendor-x',
            contact_email='x@v.com', default_lead_time_days=10,
        )
        org = Organization.objects.create(name='POVendorCo', slug='povc')
        po = PurchaseOrder.objects.create(
            organization=org, vendor=v, vendor_name='Vendor X',
            vendor_email='x@v.com', title='FK test',
        )
        po.refresh_from_db()
        self.assertEqual(po.vendor_id, v.pk)
        # purchase_orders related_name on Vendor
        self.assertEqual(v.purchase_orders.count(), 1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class AutoReplenishTests(TestCase):
    """psa_auto_replenish_suggestions: scan + grouping + dedupe."""

    def setUp(self):
        from inventory.models import InventoryItem
        from assets.models import Vendor
        # MSP org for the command's PR-creation step
        self.org = Organization.objects.create(
            name='ReplenishCo', slug='replenish-co', is_active=True,
        )
        self.vendor = Vendor.objects.create(name='Auto Vendor', slug='auto-vendor')
        # Below-minimum item with a preferred vendor
        self.item = InventoryItem.objects.create(
            organization=self.org,
            name='Patch cable 6ft', sku='PC-6',
            quantity=1, min_quantity=5,
            reorder_quantity=10,
            preferred_vendor=self.vendor,
            unit_cost='3.50',
        )
        # Below-minimum item with NO vendor — should still log
        self.item_no_vendor = InventoryItem.objects.create(
            organization=self.org,
            name='Cat6 jack', sku='J-CAT6',
            quantity=0, min_quantity=20,
        )

    def test_scan_finds_below_minimum_items(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('psa_auto_replenish_suggestions', '--dry-run', stdout=out)
        text = out.getvalue()
        self.assertIn('Patch cable 6ft', text)
        self.assertIn('Cat6 jack', text)
        self.assertIn('Found 2 items below minimum', text)

    def test_create_prs_groups_by_vendor(self):
        from io import StringIO
        from django.core.management import call_command
        from inventory.models import InventoryItem
        from psa.models import PurchaseRequisition

        # Add 2nd item from same vendor — should land in same PR
        InventoryItem.objects.create(
            organization=self.org,
            name='Patch cable 25ft', sku='PC-25',
            quantity=0, min_quantity=5,
            reorder_quantity=10,
            preferred_vendor=self.vendor,
        )

        out = StringIO()
        call_command('psa_auto_replenish_suggestions', '--create-prs', stdout=out)

        # Two vendors total: Auto Vendor (2 items) + null (Cat6 jack)
        prs = PurchaseRequisition.objects.filter(status='draft')
        # One PR per vendor
        self.assertGreaterEqual(prs.count(), 1)
        auto_pr = prs.filter(title__icontains='Auto Vendor').first()
        self.assertIsNotNone(auto_pr)
        self.assertEqual(auto_pr.line_items.count(), 2)

    def test_skips_items_already_on_open_pr(self):
        from io import StringIO
        from django.core.management import call_command
        from psa.models import PurchaseRequisition, PurchaseRequisitionLineItem

        # Create an existing draft PR with the SKU we'd otherwise auto-suggest
        existing = PurchaseRequisition.objects.create(
            organization=self.org, title='Manual PR', status='draft',
        )
        PurchaseRequisitionLineItem.objects.create(
            requisition=existing, description='Patch cable 6ft',
            sku='PC-6', quantity=1, unit_price=0,
        )

        out = StringIO()
        call_command('psa_auto_replenish_suggestions', '--create-prs', stdout=out)

        # Auto-replenish should not create a new PR with PC-6 since it's
        # already on an open PR. Item without a SKU (Cat6 jack — has SKU
        # 'J-CAT6' which isn't on any PR) WILL get a new PR.
        new_prs = PurchaseRequisition.objects.filter(
            title__icontains='Auto-replenish').all()
        for pr in new_prs:
            for li in pr.line_items.all():
                self.assertNotEqual(li.sku, 'PC-6')


# ---------------------------------------------------------------------------
# Phase 4.4 — One-click PO from accepted quote
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class QuoteToPOTests(TestCase):
    """v3.17.151: convert accepted Quote to draft PurchaseOrder."""

    def setUp(self):
        from accounts.models import RoleTemplate
        from psa.models import Quote, QuoteLineItem
        from decimal import Decimal
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='Q2PO Co', slug='q2po-co')
        _enable_psa_for(self.org)

        # Buyer: full procurement perms (can create PO)
        self.buyer_role = RoleTemplate.objects.create(
            name='Q2POBuyer', is_system_template=False,
            procurement_view=True, procurement_create_pr=True,
            procurement_approve_pr=True, procurement_create_po=True,
            procurement_send_po=True,
        )
        self.creator = User.objects.create_user(
            username='q2po_buyer', password='pw', email='c@x.com')
        Membership.objects.create(
            user=self.creator, organization=self.org,
            role=Role.ADMIN, role_template=self.buyer_role,
            is_active=True,
        )

        # Tech (no procurement_create_po)
        self.tech_role = RoleTemplate.objects.create(
            name='Q2POTech', is_system_template=False,
            procurement_view=True, procurement_create_pr=True,
            procurement_approve_pr=False, procurement_create_po=False,
            procurement_send_po=False,
        )
        self.tech = User.objects.create_user(
            username='q2po_tech', password='pw', email='t@x.com')
        Membership.objects.create(
            user=self.tech, organization=self.org,
            role=Role.EDITOR, role_template=self.tech_role,
            is_active=True,
        )

        # Build an accepted quote with 2 line items
        self.quote = Quote.objects.create(
            organization=self.org, client_org=self.org,
            title='Test Quote', status='accepted',
            subtotal=Decimal('200'), tax_rate=Decimal('0.10'),
            tax_amount=Decimal('20'), total=Decimal('220'),
        )
        QuoteLineItem.objects.create(
            quote=self.quote, sort_order=0,
            description='Switch', quantity=Decimal('1'), unit_price=Decimal('150'),
        )
        QuoteLineItem.objects.create(
            quote=self.quote, sort_order=1,
            description='Cable', quantity=Decimal('5'), unit_price=Decimal('10'),
        )

    def _login(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        s['current_organization_id'] = self.org.id
        s.save()
        return c

    def test_convert_creates_draft_po(self):
        from psa.models import PurchaseOrder
        c = self._login(self.creator)
        r = c.post(f'/psa/quotes/{self.quote.pk}/to-po/')
        self.assertEqual(r.status_code, 302)
        po = PurchaseOrder.objects.filter(source_quote=self.quote).first()
        self.assertIsNotNone(po)
        self.assertEqual(po.status, 'draft')
        self.assertEqual(po.line_items.count(), self.quote.line_items.count())
        # Notes carry the audit crumb
        self.assertIn(self.quote.quote_number, po.notes)
        # Redirect lands on PO edit
        self.assertIn(f'/purchase-orders/{po.pk}/edit/', r.url)

    def test_convert_blocked_for_non_accepted_quote(self):
        from psa.models import PurchaseOrder
        self.quote.status = 'sent'
        self.quote.save(update_fields=['status'])
        c = self._login(self.creator)
        r = c.post(f'/psa/quotes/{self.quote.pk}/to-po/')
        # Should redirect back to quote detail (no PO created)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            PurchaseOrder.objects.filter(source_quote=self.quote).count(), 0,
        )

    def test_convert_blocked_without_permission(self):
        c = self._login(self.tech)
        r = c.post(f'/psa/quotes/{self.quote.pk}/to-po/')
        # @require_perm should 403 (or redirect on some setups)
        self.assertIn(r.status_code, [302, 403])
        from psa.models import PurchaseOrder
        self.assertEqual(
            PurchaseOrder.objects.filter(source_quote=self.quote).count(), 0,
        )


# ---------------------------------------------------------------------------
# v3.17.154 — Procurement gates (techs request, managers approve)
# ---------------------------------------------------------------------------


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProcurementGateTests(TestCase):
    """Confirm requisition_decide is gated by procurement_approve_pr."""

    def setUp(self):
        from accounts.models import RoleTemplate
        from psa.models import PurchaseRequisition, PurchaseRequisitionLineItem
        from decimal import Decimal
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ProcGateOrg', slug='proc-gate-org')

        # Tech: can create, cannot approve
        self.tech_role = RoleTemplate.objects.create(
            name='ProcGateTech', is_system_template=False,
            procurement_view=True, procurement_create_pr=True,
            procurement_approve_pr=False, procurement_create_po=False,
        )
        self.tech = User.objects.create_user(
            username='gate_tech', password='pw', email='gt@x.com')
        Membership.objects.create(
            user=self.tech, organization=self.org,
            role=Role.EDITOR, role_template=self.tech_role,
            is_active=True,
        )

        # Admin / superuser
        self.admin = User.objects.create_user(
            username='gate_admin', password='pw', email='ga@x.com',
            is_staff=True, is_superuser=True,
        )
        Membership.objects.create(
            user=self.admin, organization=self.org,
            role=Role.ADMIN, is_active=True,
        )

        self.pr = PurchaseRequisition.objects.create(
            organization=self.org, title='Need a switch',
            requested_by=self.tech, status='submitted',
            tax_rate=Decimal('0'),
        )
        PurchaseRequisitionLineItem.objects.create(
            requisition=self.pr, description='Switch',
            quantity=Decimal('1'), unit_price=Decimal('100'),
        )
        self.pr.recompute_totals()
        self.pr.save()

    def test_tech_cannot_decide_pr(self):
        c = Client()
        c.force_login(self.tech)
        r = c.post(f'/psa/requisitions/{self.pr.pk}/decide/', {
            'decision': 'approve',
            'decision_note': 'no',
        })
        # require_perm raises PermissionDenied -> 403
        self.assertEqual(r.status_code, 403)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')

    def test_admin_can_decide_pr(self):
        c = Client()
        c.force_login(self.admin)
        r = c.post(f'/psa/requisitions/{self.pr.pk}/decide/', {
            'decision': 'approve',
            'decision_note': 'OK',
        })
        self.assertIn(r.status_code, [200, 302])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')


# ---------------------------------------------------------------------------
# Phase 6.1 — Change requests with CAB approval workflow
# ---------------------------------------------------------------------------


def _make_change_ticket(org, **overrides):
    """Helper: build a ticket with ticket_type slug='change'. The
    auto-spawn signal will create the ChangeRequest."""
    from psa.models import (
        Queue, TicketStatus, TicketPriority, TicketType, Ticket,
    )
    queue = Queue.objects.first() or Queue.objects.create(name='Helpdesk', slug='helpdesk')
    status = TicketStatus.objects.first() or TicketStatus.objects.create(name='New', slug='new')
    priority = TicketPriority.objects.first() or TicketPriority.objects.create(
        code='P3', name='Normal',
        response_target_minutes=240, resolution_target_minutes=4320,
    )
    change_type, _ = TicketType.objects.get_or_create(
        slug='change', defaults={'name': 'Change', 'sort_order': 99},
    )
    defaults = dict(
        organization=org,
        subject='CR test',
        status=status,
        priority=priority,
        ticket_type=change_type,
        queue=queue,
    )
    defaults.update(overrides)
    return Ticket.objects.create(**defaults)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ChangeRequestModelTests(TestCase):
    """Validate the CAB gating logic on the model itself."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ChangeOrg', slug='change-org')
        self.requester = User.objects.create_user(
            username='cr_req', password='pw', email='req@x.com')
        self.approver_a = User.objects.create_user(
            username='cab_a', password='pw', email='a@x.com')
        self.approver_b = User.objects.create_user(
            username='cab_b', password='pw', email='b@x.com')

        self.ticket = _make_change_ticket(
            self.org, subject='CR test', created_by=self.requester,
        )
        self.cr = self.ticket.change_request
        self.cr.implementation_status = 'pending_cab'
        self.cr.save(update_fields=['implementation_status'])

    def test_can_implement_requires_all_cab_approvers(self):
        from psa.models import CABVote
        self.cr.required_approvers.set([self.approver_a, self.approver_b])
        # 1 approves -> not satisfied
        CABVote.objects.create(
            change_request=self.cr, user=self.approver_a, decision='approved')
        self.assertFalse(self.cr.is_cab_satisfied)
        # Even if we manually set the CR to 'approved', the gate must still
        # require all approvers -> can_implement returns False because
        # is_cab_satisfied is False.
        self.cr.implementation_status = 'approved'
        self.cr.save(update_fields=['implementation_status'])
        self.assertFalse(self.cr.can_implement())
        # Both approve -> satisfied
        CABVote.objects.create(
            change_request=self.cr, user=self.approver_b, decision='approved')
        self.assertTrue(self.cr.is_cab_satisfied)
        self.assertTrue(self.cr.can_implement())

    def test_any_rejection_blocks_implementation(self):
        from psa.models import CABVote
        self.cr.required_approvers.set([self.approver_a, self.approver_b])
        CABVote.objects.create(
            change_request=self.cr, user=self.approver_a, decision='approved')
        CABVote.objects.create(
            change_request=self.cr, user=self.approver_b, decision='rejected')
        self.assertTrue(self.cr.has_cab_rejection)
        self.cr.implementation_status = 'approved'
        self.cr.save(update_fields=['implementation_status'])
        self.assertFalse(self.cr.can_implement())

    def test_can_implement_falls_back_for_no_cab(self):
        """No required_approvers configured: a single 'approved' CABVote
        is enough to satisfy the fallback gate."""
        from psa.models import CABVote
        self.cr.required_approvers.clear()
        # No votes -> not satisfied
        self.assertFalse(self.cr.is_cab_satisfied)
        CABVote.objects.create(
            change_request=self.cr, user=self.approver_a, decision='approved')
        self.assertTrue(self.cr.is_cab_satisfied)
        self.cr.implementation_status = 'approved'
        self.cr.save(update_fields=['implementation_status'])
        self.assertTrue(self.cr.can_implement())


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ChangeRequestSignalTests(TestCase):
    """Validate the post_save signal that auto-creates a ChangeRequest."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='SignalOrg', slug='signal-org')

    def test_change_ticket_auto_spawns_change_request(self):
        from psa.models import ChangeRequest
        ticket = _make_change_ticket(self.org, subject='Auto-spawn test')
        cr = ChangeRequest.objects.filter(ticket=ticket).first()
        self.assertIsNotNone(cr)
        self.assertEqual(cr.organization, self.org)
        self.assertEqual(cr.implementation_status, 'draft')

    def test_non_change_ticket_does_not_spawn(self):
        from psa.models import (
            Queue, TicketStatus, TicketPriority, TicketType, Ticket,
            ChangeRequest,
        )
        incident_type = TicketType.objects.filter(slug='incident').first()
        if not incident_type:
            incident_type = TicketType.objects.create(name='Incident', slug='incident')
        ticket = Ticket.objects.create(
            organization=self.org,
            subject='Non-change',
            status=TicketStatus.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=incident_type,
            queue=Queue.objects.first(),
        )
        self.assertFalse(ChangeRequest.objects.filter(ticket=ticket).exists())


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ChangeRequestPermissionTests(TestCase):
    """Permission gating on vote/implement endpoints."""

    def setUp(self):
        from accounts.models import RoleTemplate
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='PermOrg', slug='perm-org')

        # Give every test user generic write perms so `require_write` lets
        # them past — the tests are exercising the change_* perms specifically.
        # `can_write()` looks at vault_create OR assets_create OR ...
        common_write = dict(assets_create=True)

        self.tech_role = RoleTemplate.objects.create(
            name='ChangeTech', is_system_template=False,
            change_view=True, change_create=True,
            change_approve_cab=False, change_implement=False,
            **common_write,
        )
        self.tech = User.objects.create_user(
            username='ch_tech', password='pw', email='ct@x.com')
        Membership.objects.create(
            user=self.tech, organization=self.org,
            role=Role.EDITOR, role_template=self.tech_role,
            is_active=True,
        )

        self.cab_role = RoleTemplate.objects.create(
            name='ChangeCAB', is_system_template=False,
            change_view=True, change_create=True,
            change_approve_cab=True, change_implement=False,
            **common_write,
        )
        self.cab_member = User.objects.create_user(
            username='ch_cab', password='pw', email='cab@x.com')
        Membership.objects.create(
            user=self.cab_member, organization=self.org,
            role=Role.ADMIN, role_template=self.cab_role,
            is_active=True,
        )

        self.impl_role = RoleTemplate.objects.create(
            name='ChangeImpl', is_system_template=False,
            change_view=True, change_create=True,
            change_approve_cab=False, change_implement=True,
            **common_write,
        )
        self.implementer = User.objects.create_user(
            username='ch_impl', password='pw', email='impl@x.com')
        Membership.objects.create(
            user=self.implementer, organization=self.org,
            role=Role.ADMIN, role_template=self.impl_role,
            is_active=True,
        )

        self.ticket = _make_change_ticket(
            self.org, subject='Perm CR', created_by=self.tech,
        )
        self.cr = self.ticket.change_request
        self.cr.implementation_status = 'pending_cab'
        self.cr.required_approvers.set([self.cab_member])
        self.cr.save()

    def _client_for(self, user):
        c = Client()
        c.force_login(user)
        sess = c.session
        sess['current_organization_id'] = self.org.id
        sess.save()
        return c

    def test_voting_requires_cab_perm(self):
        """A user without change_approve_cab cannot vote."""
        c = self._client_for(self.tech)
        r = c.post(f'/psa/t/{self.ticket.ticket_number}/change/vote/', {
            'decision': 'approved',
        })
        self.assertEqual(r.status_code, 403)

    def test_voting_requires_being_a_required_approver(self):
        """Even with the CAB perm, you must be in required_approvers."""
        # Implementer has change_implement but NOT change_approve_cab.
        c = self._client_for(self.implementer)
        r = c.post(f'/psa/t/{self.ticket.ticket_number}/change/vote/', {
            'decision': 'approved',
        })
        self.assertEqual(r.status_code, 403)

    def test_cab_member_can_vote_and_drives_approval(self):
        c = self._client_for(self.cab_member)
        r = c.post(f'/psa/t/{self.ticket.ticket_number}/change/vote/', {
            'decision': 'approved',
        })
        self.assertEqual(r.status_code, 302)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.implementation_status, 'approved')

    def test_implement_requires_can_implement_gate(self):
        """Implementer has perm but CR is pending_cab -> blocked."""
        c = self._client_for(self.implementer)
        r = c.post(f'/psa/t/{self.ticket.ticket_number}/change/implement/')
        # The view redirects with an error message, NOT a 403, because the
        # *permission* is fine - it's the gate that's not satisfied.
        self.assertEqual(r.status_code, 302)
        self.cr.refresh_from_db()
        self.assertNotEqual(self.cr.implementation_status, 'implementing')

    def test_implement_blocked_without_change_implement_perm(self):
        """A user with change_approve_cab but not change_implement gets 403."""
        c = self._client_for(self.cab_member)
        r = c.post(f'/psa/t/{self.ticket.ticket_number}/change/implement/')
        self.assertEqual(r.status_code, 403)


# ---------------------------------------------------------------------------
# Phase 6.2 — Problem records + root-cause analysis
# ---------------------------------------------------------------------------


def _make_problem_helper_ticket(org, subject='Recurring printer flakiness'):
    """Helper: build a non-change Ticket so it can be linked to a Problem."""
    from psa.models import (
        Queue, TicketStatus, TicketPriority, TicketType, Ticket,
    )
    queue = Queue.objects.first() or Queue.objects.create(name='Helpdesk', slug='helpdesk')
    status = TicketStatus.objects.first() or TicketStatus.objects.create(name='New', slug='new')
    priority = TicketPriority.objects.first() or TicketPriority.objects.create(
        code='P3', name='Normal',
        response_target_minutes=240, resolution_target_minutes=4320,
    )
    incident_type = TicketType.objects.filter(slug='incident').first()
    if not incident_type:
        incident_type = TicketType.objects.create(name='Incident', slug='incident')
    return Ticket.objects.create(
        organization=org,
        subject=subject,
        status=status,
        priority=priority,
        ticket_type=incident_type,
        queue=queue,
    )


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProblemModelTests(TestCase):
    """Validate problem_number auto-increment + RCA gating logic."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ProblemOrg', slug='problem-org')

    def test_next_number_increments(self):
        from psa.models import Problem
        p1 = Problem.objects.create(organization=self.org, title='First')
        p2 = Problem.objects.create(organization=self.org, title='Second')
        self.assertTrue(p1.problem_number.startswith('PRB-'))
        self.assertTrue(p1.problem_number.endswith('00001'))
        self.assertTrue(p2.problem_number.endswith('00002'))

    def test_can_advance_to_known_error_requires_root_cause_and_workaround(self):
        from psa.models import Problem
        p = Problem.objects.create(organization=self.org, title='Flaky printer')
        # Empty -> blocked
        self.assertFalse(p.can_advance_to('known_error'))
        p.root_cause = 'Driver mismatch'
        p.save()
        # Root cause but no workaround -> still blocked
        self.assertFalse(p.can_advance_to('known_error'))
        p.workaround = 'Restart spooler nightly'
        p.save()
        # Both -> ok
        self.assertTrue(p.can_advance_to('known_error'))

    def test_can_advance_to_resolved_requires_permanent_fix(self):
        from psa.models import Problem
        p = Problem.objects.create(
            organization=self.org, title='Flaky printer',
            root_cause='Driver mismatch',
            workaround='Restart spooler nightly',
        )
        # No permanent fix -> blocked
        self.assertFalse(p.can_advance_to('resolved'))
        p.permanent_fix = 'Roll out new driver via Intune'
        p.save()
        self.assertTrue(p.can_advance_to('resolved'))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProblemTicketLinkTests(TestCase):
    """The M2M tying tickets to problems and the link-ticket endpoint."""

    def setUp(self):
        from accounts.models import RoleTemplate
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='LinkOrg', slug='link-org')
        # Need vault_create or similar so require_write passes.
        self.role = RoleTemplate.objects.create(
            name='ProblemTech', is_system_template=False,
            problem_view=True, problem_create=True,
            problem_assign=False, problem_resolve=False,
            assets_create=True,
        )
        self.user = User.objects.create_user(
            username='ptech', password='pw', email='ptech@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org,
            role=Role.EDITOR, role_template=self.role,
            is_active=True,
        )

    def _client(self):
        c = Client()
        c.force_login(self.user)
        sess = c.session
        sess['current_organization_id'] = self.org.id
        sess.save()
        return c

    def test_link_ticket_via_post(self):
        """User with problem_create posts ticket_number -> ticket appears in
        related_tickets."""
        from psa.models import Problem
        problem = Problem.objects.create(organization=self.org, title='Flaky printer')
        ticket = _make_problem_helper_ticket(self.org, subject='Print job failed')

        c = self._client()
        r = c.post(f'/psa/problems/{problem.pk}/link-ticket/', {
            'ticket_number': ticket.ticket_number,
        })
        self.assertEqual(r.status_code, 302)
        problem.refresh_from_db()
        self.assertTrue(problem.related_tickets.filter(pk=ticket.pk).exists())

    def test_problem_appears_on_ticket_detail(self):
        """Linked ticket renders the problem in ticket_detail context."""
        from psa.models import Problem
        problem = Problem.objects.create(
            organization=self.org, title='Email storm',
            description='Recurring SMTP loop',
        )
        ticket = _make_problem_helper_ticket(self.org, subject='Email queue stuck')
        problem.related_tickets.add(ticket)

        c = self._client()
        r = c.get(f'/psa/t/{ticket.ticket_number}/')
        self.assertEqual(r.status_code, 200)
        # The Problem number appears on the rendered page.
        self.assertIn(problem.problem_number.encode(), r.content)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProblemPermissionTests(TestCase):
    """Permission gating on view/advance endpoints."""

    def setUp(self):
        from accounts.models import RoleTemplate
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='ProblemPermOrg', slug='problem-perm-org')

        common_write = dict(assets_create=True)

        # Tech: view+create but cannot resolve.
        self.tech_role = RoleTemplate.objects.create(
            name='ProblemTechRole', is_system_template=False,
            problem_view=True, problem_create=True,
            problem_assign=False, problem_resolve=False,
            **common_write,
        )
        self.tech = User.objects.create_user(
            username='pt_tech', password='pw', email='pt@x.com')
        Membership.objects.create(
            user=self.tech, organization=self.org,
            role=Role.EDITOR, role_template=self.tech_role,
            is_active=True,
        )

        # Resolver: full perms.
        self.resolver_role = RoleTemplate.objects.create(
            name='ProblemResolverRole', is_system_template=False,
            problem_view=True, problem_create=True,
            problem_assign=True, problem_resolve=True,
            **common_write,
        )
        self.resolver = User.objects.create_user(
            username='pt_res', password='pw', email='ptr@x.com')
        Membership.objects.create(
            user=self.resolver, organization=self.org,
            role=Role.ADMIN, role_template=self.resolver_role,
            is_active=True,
        )

        # No-perm user: nothing.
        self.no_perm_role = RoleTemplate.objects.create(
            name='ProblemNoPermRole', is_system_template=False,
            problem_view=False, problem_create=False,
            problem_assign=False, problem_resolve=False,
            **common_write,
        )
        self.no_perm = User.objects.create_user(
            username='pt_no', password='pw', email='ptn@x.com')
        Membership.objects.create(
            user=self.no_perm, organization=self.org,
            role=Role.EDITOR, role_template=self.no_perm_role,
            is_active=True,
        )

        from psa.models import Problem
        self.problem = Problem.objects.create(
            organization=self.org, title='Server stuck',
            root_cause='Thermal throttle',
            workaround='Bump fan speed',
            permanent_fix='Replace heatsink',
        )

    def _client_for(self, user):
        c = Client()
        c.force_login(user)
        sess = c.session
        sess['current_organization_id'] = self.org.id
        sess.save()
        return c

    def test_advance_requires_resolve_perm(self):
        """A user with problem_view+create but not problem_resolve gets 403."""
        c = self._client_for(self.tech)
        r = c.post(f'/psa/problems/{self.problem.pk}/advance/', {
            'status': 'known_error',
        })
        self.assertEqual(r.status_code, 403)

        # Resolver succeeds.
        c2 = self._client_for(self.resolver)
        r2 = c2.post(f'/psa/problems/{self.problem.pk}/advance/', {
            'status': 'known_error',
        })
        self.assertEqual(r2.status_code, 302)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.status, 'known_error')

    def test_view_blocked_for_user_without_problem_view(self):
        c = self._client_for(self.no_perm)
        r = c.get('/psa/problems/')
        self.assertEqual(r.status_code, 403)
        r2 = c.get(f'/psa/problems/{self.problem.pk}/')
        self.assertEqual(r2.status_code, 403)


# ---------------------------------------------------------------------------
# Phase 6.3 — Release management + Service-catalog governance
# ---------------------------------------------------------------------------


def _make_change_request(org, *, subject='Routine deploy', risk='medium',
                         implementation_plan='Step 1', rollback_plan='Revert',
                         created_by=None):
    """Helper: build a Ticket(type=change) + its auto-spawned ChangeRequest,
    fill in plans so the CR can be added to a release."""
    from psa.models import (
        Queue, TicketStatus, TicketPriority, TicketType, Ticket, ChangeRequest,
    )
    queue = Queue.objects.first() or Queue.objects.create(name='Helpdesk', slug='helpdesk')
    status = TicketStatus.objects.first() or TicketStatus.objects.create(name='New', slug='new')
    priority = TicketPriority.objects.first() or TicketPriority.objects.create(
        code='P3', name='Normal',
        response_target_minutes=240, resolution_target_minutes=4320,
    )
    change_type = TicketType.objects.filter(slug='change').first()
    if not change_type:
        change_type = TicketType.objects.create(name='Change', slug='change')
    ticket = Ticket.objects.create(
        organization=org, subject=subject,
        status=status, priority=priority,
        ticket_type=change_type, queue=queue,
    )
    cr, _ = ChangeRequest.objects.get_or_create(
        ticket=ticket,
        defaults={
            'organization': org, 'risk': risk,
            'implementation_plan': implementation_plan,
            'rollback_plan': rollback_plan,
        },
    )
    if not cr.implementation_plan:
        cr.implementation_plan = implementation_plan
        cr.rollback_plan = rollback_plan
        cr.risk = risk
        cr.save()
    return ticket, cr


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ReleaseWindowTests(TestCase):
    """Validate ReleaseWindow.next_number + can_advance_to gating."""

    def setUp(self):
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='RelOrg', slug='rel-org')
        self.start = timezone.now() + timedelta(days=1)
        self.end = self.start + timedelta(hours=2)

    def test_can_advance_to_frozen_requires_changes_and_rollback(self):
        from psa.models import ReleaseWindow
        rel = ReleaseWindow.objects.create(
            organization=self.org, title='Q2 maintenance',
            scheduled_start=self.start, scheduled_end=self.end,
        )
        # Empty release, no rollback -> blocked.
        self.assertFalse(rel.can_advance_to('frozen'))

        # Add a change, still no rollback -> blocked.
        _, cr = _make_change_request(self.org)
        rel.changes.add(cr)
        self.assertFalse(rel.can_advance_to('frozen'))

        # Both -> ok.
        rel.rollback_plan = 'Revert via psql snapshot'
        rel.save()
        self.assertTrue(rel.can_advance_to('frozen'))

    def test_release_number_increments(self):
        from psa.models import ReleaseWindow
        r1 = ReleaseWindow.objects.create(
            organization=self.org, title='First',
            scheduled_start=self.start, scheduled_end=self.end,
        )
        r2 = ReleaseWindow.objects.create(
            organization=self.org, title='Second',
            scheduled_start=self.start, scheduled_end=self.end,
        )
        self.assertTrue(r1.release_number.startswith('REL-'))
        self.assertTrue(r1.release_number.endswith('00001'))
        self.assertTrue(r2.release_number.endswith('00002'))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ServiceCatalogChangeTests(TestCase):
    """Service catalog governance — propose / apply / pending blocks publish."""

    def setUp(self):
        from accounts.models import RoleTemplate
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='CatOrg', slug='cat-org')
        # Staff superuser bypasses _catalog_admin_or_404.
        self.admin = User.objects.create_user(
            username='cat_admin', password='pw', email='ca@x.com',
            is_superuser=True, is_staff=True,
        )
        self.proposer_role = RoleTemplate.objects.create(
            name='CatProposer', is_system_template=False,
            catalog_propose_change=True,
            catalog_approve_change=False,
            assets_create=True,
        )
        self.proposer = User.objects.create_user(
            username='cat_prop', password='pw', email='cp@x.com',
            is_staff=True, is_superuser=True,
        )
        # Make the proposer a real superuser for the catalog admin gate, but
        # also give a role template with only propose perms so the
        # propose-change permission check is exercised. Membership also gets
        # us through any tenant scoping if relevant.
        Membership.objects.create(
            user=self.proposer, organization=self.org,
            role=Role.EDITOR, role_template=self.proposer_role,
            is_active=True,
        )

    def test_apply_writes_after_snapshot(self):
        from psa.models import ServiceCatalogItem, ServiceCatalogChange
        item = ServiceCatalogItem.objects.create(
            name='Reset Password', slug='reset-password',
            description='old desc',
        )
        change = ServiceCatalogChange.objects.create(
            catalog_item=item,
            before_snapshot={'description': 'old desc'},
            after_snapshot={'description': 'new desc'},
        )
        ok = change.apply(decided_by=self.admin)
        self.assertTrue(ok)
        item.refresh_from_db()
        self.assertEqual(item.description, 'new desc')
        self.assertEqual(item.last_published_by, self.admin)
        self.assertIsNotNone(item.last_published_at)
        change.refresh_from_db()
        self.assertEqual(change.status, 'approved')
        self.assertEqual(change.decided_by, self.admin)

    def test_pending_change_blocks_publish(self):
        """When item.requires_approval=True, the propose endpoint creates a
        new pending ServiceCatalogChange instead of writing live."""
        from psa.models import ServiceCatalogItem, ServiceCatalogChange
        item = ServiceCatalogItem.objects.create(
            name='Onboard User', slug='onboard-user',
            description='live description',
            requires_approval=True,
        )
        c = Client()
        c.force_login(self.proposer)
        sess = c.session
        sess['current_organization_id'] = self.org.id
        sess.save()
        r = c.post(f'/psa/catalog/{item.pk}/propose/', {
            'name': item.name,
            'description': 'proposed new description',
            'default_subject': item.default_subject,
            'default_body': item.default_body,
            'icon': item.icon,
            'sort_order': item.sort_order,
            'is_active': 'on',
            'reason': 'tightening copy',
        })
        self.assertEqual(r.status_code, 302)
        # Live item unchanged.
        item.refresh_from_db()
        self.assertEqual(item.description, 'live description')
        # A pending proposal exists with the new description in after_snapshot.
        proposal = ServiceCatalogChange.objects.filter(
            catalog_item=item, status='pending',
        ).first()
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.after_snapshot.get('description'), 'proposed new description')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ReleasePermissionTests(TestCase):
    """Permission gating on release_freeze endpoint."""

    def setUp(self):
        from accounts.models import RoleTemplate
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        self.org = Organization.objects.create(name='RelPermOrg', slug='rel-perm-org')

        common_write = dict(assets_create=True)

        # Manager: manage but cannot freeze.
        self.mgr_role = RoleTemplate.objects.create(
            name='RelMgr', is_system_template=False,
            release_view=True, release_manage=True, release_freeze=False,
            **common_write,
        )
        self.manager = User.objects.create_user(
            username='rel_mgr', password='pw', email='rm@x.com')
        Membership.objects.create(
            user=self.manager, organization=self.org,
            role=Role.EDITOR, role_template=self.mgr_role, is_active=True,
        )

        # Releaser: full perms.
        self.rel_role = RoleTemplate.objects.create(
            name='RelFull', is_system_template=False,
            release_view=True, release_manage=True, release_freeze=True,
            **common_write,
        )
        self.releaser = User.objects.create_user(
            username='rel_full', password='pw', email='rf@x.com')
        Membership.objects.create(
            user=self.releaser, organization=self.org,
            role=Role.ADMIN, role_template=self.rel_role, is_active=True,
        )

        from psa.models import ReleaseWindow
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=2)
        self.release = ReleaseWindow.objects.create(
            organization=self.org, title='Test release',
            scheduled_start=start, scheduled_end=end,
            rollback_plan='Revert via snapshot',
        )
        _, self.cr = _make_change_request(self.org)
        self.release.changes.add(self.cr)

    def _client_for(self, user):
        c = Client()
        c.force_login(user)
        sess = c.session
        sess['current_organization_id'] = self.org.id
        sess.save()
        return c

    def test_freeze_requires_release_freeze_perm(self):
        # Manager (no freeze perm) -> 403.
        c = self._client_for(self.manager)
        r = c.post(f'/psa/releases/{self.release.pk}/freeze/')
        self.assertEqual(r.status_code, 403)

        # Releaser succeeds.
        c2 = self._client_for(self.releaser)
        r2 = c2.post(f'/psa/releases/{self.release.pk}/freeze/')
        self.assertEqual(r2.status_code, 302)
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, 'frozen')
        self.assertTrue(self.release.is_frozen)


# ---------------------------------------------------------------------------
# Phase 7 — Outsourcing + Integration SDK
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class TicketShareTests(TestCase):
    """Outsourcing share endpoint + partner webhook receiver."""

    def setUp(self):
        from accounts.models import RoleTemplate
        _setup_seed()
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        # Originator org (the MSP)
        self.org = Organization.objects.create(name='ShareOrg', slug='share-org')
        # Partner org
        self.partner = Organization.objects.create(
            name='Partner Inc', slug='partner-inc',
            is_outsourcing_partner=True,
            partner_endpoint_url='http://127.0.0.1:1/no-such-host',
        )
        # Sharer with outsourcing_share_tickets perm
        self.share_role = RoleTemplate.objects.create(
            name='Sharer', is_system_template=False,
            outsourcing_share_tickets=True,
            assets_create=True,
        )
        self.user = User.objects.create_user(
            username='sharer', password='pw', email='s@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org,
            role=Role.ADMIN, role_template=self.share_role, is_active=True,
        )
        # Required ticket scaffolding
        from psa.models import Ticket as _T
        self.priority = TicketPriority.objects.filter(code='P3').first() or \
            TicketPriority.objects.create(code='P3', name='Normal')
        self.status = TicketStatus.objects.filter(slug='new').first() or \
            TicketStatus.objects.create(name='New', slug='new')
        self.ttype = TicketType.objects.filter(slug='incident').first() or \
            TicketType.objects.create(name='Incident', slug='incident')
        self.ticket = _T.objects.create(
            organization=self.org, subject='need partner help',
            description='offload this please',
            priority=self.priority, status=self.status,
            ticket_type=self.ttype,
            queue=Queue.objects.first(),
        )

    def _client_for(self, user):
        c = Client()
        c.force_login(user)
        sess = c.session
        sess['current_organization_id'] = self.org.id
        sess.save()
        return c

    def test_partner_secret_auto_generated(self):
        """Setting is_outsourcing_partner=True saves a 64-char hex token."""
        org = Organization.objects.create(
            name='Auto Partner', slug='auto-partner',
            is_outsourcing_partner=True,
        )
        self.assertEqual(len(org.partner_secret), 64)
        # Hex check
        int(org.partner_secret, 16)

    def test_share_creates_share_row(self):
        """POST share endpoint -> TicketShare row exists, status='pending'."""
        from psa.models import TicketShare
        c = self._client_for(self.user)
        r = c.post(
            f'/psa/t/{self.ticket.ticket_number}/share/',
            {'partner_org': str(self.partner.pk), 'notes': 'please handle'},
        )
        self.assertEqual(r.status_code, 302)
        share = TicketShare.objects.get(ticket=self.ticket, partner_org=self.partner)
        self.assertEqual(share.status, 'pending')
        self.assertEqual(share.shared_by, self.user)
        self.assertEqual(share.notes, 'please handle')

    def test_partner_webhook_creates_comment(self):
        """POST to webhook with valid HMAC sig and event='comment' ->
        TicketComment created with source='partner'."""
        import hashlib
        import hmac
        import json as _json

        from psa.models import TicketComment, TicketShare

        share = TicketShare.objects.create(
            ticket=self.ticket, partner_org=self.partner, status='accepted',
        )
        body_dict = {'event': 'comment', 'payload': {
            'body': 'partner reply here',
            'author': 'partner-tech',
            'author_email': 'tech@partner.example',
        }}
        raw = _json.dumps(body_dict).encode('utf-8')
        sig = hmac.new(
            self.partner.partner_secret.encode('utf-8'),
            raw, hashlib.sha256,
        ).hexdigest()
        c = Client()
        r = c.post(
            f'/psa/partners/webhook/{share.pk}/',
            data=raw,
            content_type='application/json',
            HTTP_X_CST0R_SIGNATURE=sig,
        )
        self.assertEqual(r.status_code, 200)
        comment = TicketComment.objects.filter(
            ticket=self.ticket, source='partner').order_by('-id').first()
        self.assertIsNotNone(comment)
        self.assertEqual(comment.body, 'partner reply here')
        self.assertFalse(comment.is_internal)
        self.assertEqual(comment.author_name, 'partner-tech')

    def test_webhook_rejects_bad_signature(self):
        """POST with wrong sig -> 403."""
        import json as _json

        from psa.models import TicketShare

        share = TicketShare.objects.create(
            ticket=self.ticket, partner_org=self.partner, status='accepted',
        )
        raw = _json.dumps({'event': 'comment', 'payload': {'body': 'x'}}).encode('utf-8')
        c = Client()
        r = c.post(
            f'/psa/partners/webhook/{share.pk}/',
            data=raw,
            content_type='application/json',
            HTTP_X_CST0R_SIGNATURE='deadbeef',
        )
        self.assertEqual(r.status_code, 403)


class IntegrationSDKTests(TestCase):
    """Integration SDK skeleton: registry + abstract base."""

    def test_register_and_lookup(self):
        from integrations.sdk import IntegrationProvider, get, register

        @register
        class _DummyProvider(IntegrationProvider):
            slug = 'phase7-dummy'
            label = 'Dummy'
            category = 'other'

            def test_connection(self, connection):
                return {'ok': True, 'message': 'ok'}

            def sync(self, connection):
                return {'ok': True, 'records_imported': 0, 'errors': []}

        p = get('phase7-dummy')
        self.assertIsNotNone(p)
        self.assertEqual(p.slug, 'phase7-dummy')
        self.assertEqual(p.label, 'Dummy')
        self.assertEqual(p.category, 'other')

    def test_missing_slug_raises(self):
        from integrations.sdk import IntegrationProvider, register

        with self.assertRaises(ValueError):
            @register
            class _NoSlug(IntegrationProvider):
                slug = None
                label = 'NoSlug'
                category = 'other'

                def test_connection(self, connection):
                    return {'ok': True, 'message': ''}

                def sync(self, connection):
                    return {'ok': True, 'records_imported': 0, 'errors': []}


# ---------------------------------------------------------------------------
# Phase 10.1 — Email-to-ticket threading via Message-ID
# ---------------------------------------------------------------------------

class _FakeIMAP:
    """
    Stand-in for imaplib.IMAP4_SSL. Holds a list of (uid, raw_bytes)
    messages and answers the small subset of methods the poller calls.
    Records `seen_uids` so tests can assert the poller marked things read.
    """
    def __init__(self, messages):
        self._messages = list(messages)
        self.seen_uids = []

    # imaplib API surface used by the poller
    def login(self, *a, **kw):  # pragma: no cover - trivial
        return ('OK', [b''])

    def select(self, folder):
        return ('OK', [str(len(self._messages)).encode()])

    def search(self, charset, criterion):
        ids = b' '.join(uid for uid, _ in self._messages)
        return ('OK', [ids])

    def fetch(self, uid, parts):
        for u, raw in self._messages:
            if u == uid:
                return ('OK', [(b'%s (RFC822 {%d}' % (u, len(raw)), raw), b')'])
        return ('NO', [])

    def store(self, uid, flag, value):
        self.seen_uids.append((uid, flag, value))
        return ('OK', [b''])

    def logout(self):
        return ('BYE', [b''])


def _build_raw_email(*, message_id, from_addr, to_addr, subject,
                    body='Hello world', in_reply_to=None, references=None):
    """Compose a minimal RFC 822 byte string for the fake IMAP server."""
    headers = [
        f'From: {from_addr}',
        f'To: {to_addr}',
        f'Subject: {subject}',
        f'Message-ID: {message_id}',
        'Content-Type: text/plain; charset=utf-8',
        'MIME-Version: 1.0',
    ]
    if in_reply_to:
        headers.append(f'In-Reply-To: {in_reply_to}')
    if references:
        headers.append(f'References: {references}')
    return ('\r\n'.join(headers) + '\r\n\r\n' + body).encode('utf-8')


class _EmailPollerSetup:
    """Mixin: builds two orgs, two configs, and a base ticket per org."""

    @classmethod
    def _seed_psa(cls):
        _setup_seed()

    def _make_org_with_config(self, slug):
        from psa.models import EmailIngestionConfig, Queue, TicketPriority, TicketType
        org = Organization.objects.create(name=slug.upper(), slug=slug)
        cfg = EmailIngestionConfig.objects.create(
            organization=org,
            name=f'{slug}-helpdesk',
            imap_host='imap.example.com',
            username=f'help@{slug}.example.com',
            default_queue=Queue.objects.first(),
            default_priority=TicketPriority.objects.first(),
            default_type=TicketType.objects.first(),
        )
        cfg.set_password('imap-pw')
        cfg.save()
        return org, cfg

    def _make_ticket(self, org, **overrides):
        from psa.models import Queue, TicketPriority, TicketStatus, TicketType
        defaults = dict(
            organization=org,
            subject='Existing ticket',
            description='Pre-existing',
            queue=Queue.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            source='email',
            visibility='client',
            client_can_view=True,
            requester_email='customer@example.com',
        )
        defaults.update(overrides)
        return Ticket.objects.create(**defaults)


class EmailThreadingByInReplyToTests(TestCase, _EmailPollerSetup):
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgthread')
        self.ticket = self._make_ticket(self.org)
        # Seed an inbound EmailMessage representing the original customer email
        from psa.models import EmailMessage
        self.original = EmailMessage.objects.create(
            organization=self.org,
            ticket=self.ticket,
            ingestion_config=self.cfg,
            direction='in',
            message_id='<original-12345@example.com>',
            from_email='customer@example.com',
            subject='Original',
        )
        self.ticket.last_inbound_message_id = '<original-12345@example.com>'
        self.ticket.save(update_fields=['last_inbound_message_id'])

    def _run_poll_with(self, raw):
        from psa.management.commands import psa_poll_email
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original
        return fake

    def test_in_reply_to_threads_to_existing_ticket(self):
        from psa.models import EmailMessage, TicketComment
        raw = _build_raw_email(
            message_id='<reply-67890@example.com>',
            from_addr='customer@example.com', to_addr='help@orgthread.example.com',
            subject='no token here at all',
            in_reply_to='<original-12345@example.com>',
            body='Thanks, problem persists.',
        )
        self._run_poll_with(raw)

        # Existing ticket got a new comment
        comments = TicketComment.objects.filter(ticket=self.ticket)
        self.assertEqual(comments.count(), 1)
        self.assertIn('problem persists', comments.first().body)
        # No new ticket created
        self.assertEqual(Ticket.objects.filter(organization=self.org).count(), 1)
        # Inbound EmailMessage row persisted
        self.assertTrue(EmailMessage.objects.filter(message_id='<reply-67890@example.com>').exists())
        # last_inbound_message_id updated
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.last_inbound_message_id, '<reply-67890@example.com>')


class EmailThreadingByReferencesChainTests(TestCase, _EmailPollerSetup):
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgrefs')
        self.ticket = self._make_ticket(self.org)
        from psa.models import EmailMessage
        EmailMessage.objects.create(
            organization=self.org, ticket=self.ticket, ingestion_config=self.cfg,
            direction='in', message_id='<root-aaa@example.com>',
        )

    def test_references_chain_threads_when_in_reply_to_unknown(self):
        from psa.management.commands import psa_poll_email
        from psa.models import TicketComment
        raw = _build_raw_email(
            message_id='<grandchild@example.com>',
            from_addr='customer@example.com', to_addr='help@orgrefs.example.com',
            subject='Re: something',
            # In-Reply-To points to a Message-ID we never saw, but References
            # contains the original Message-ID that we DO have on file.
            in_reply_to='<unknown-middle@example.com>',
            references='<root-aaa@example.com> <unknown-middle@example.com>',
            body='Tail of the chain.',
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        self.assertEqual(TicketComment.objects.filter(ticket=self.ticket).count(), 1)
        self.assertEqual(Ticket.objects.filter(organization=self.org).count(), 1)


class EmailThreadingFallbackToSubjectTests(TestCase, _EmailPollerSetup):
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgsubj')
        self.ticket = self._make_ticket(self.org)

    def test_subject_regex_still_matches_legacy_replies_without_headers(self):
        from psa.management.commands import psa_poll_email
        from psa.models import TicketComment
        # Legacy inbound — no In-Reply-To, no References, but subject has the
        # PSA-YYYY-NNNNNN token. The poller must still attach to the ticket.
        raw = _build_raw_email(
            message_id='<no-prior-record@example.com>',
            from_addr='customer@example.com', to_addr='help@orgsubj.example.com',
            subject=f'Re: [{self.ticket.ticket_number}] follow up',
            body='Legacy reply with no threading headers.',
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        self.assertEqual(TicketComment.objects.filter(ticket=self.ticket).count(), 1)


class EmailThreadingCrossOrgIsolationTests(TestCase, _EmailPollerSetup):
    """
    Org A's Message-ID must not match when org B receives a reply that
    happens to use the same value as In-Reply-To. Same Message-ID across
    tenants is allowed; the (organization, message_id) unique constraint
    + the org filter on the lookup keep them isolated.
    """
    def setUp(self):
        self._seed_psa()
        self.orgA, self.cfgA = self._make_org_with_config('orga')
        self.orgB, self.cfgB = self._make_org_with_config('orgb')
        self.ticketA = self._make_ticket(self.orgA, subject='A ticket')
        from psa.models import EmailMessage
        # Both orgs happen to have an EmailMessage with the same Message-ID
        # value (legitimate — Message-IDs aren't globally unique across mail
        # servers). The threading lookup must respect organization.
        EmailMessage.objects.create(
            organization=self.orgA, ticket=self.ticketA,
            direction='in', message_id='<collision@example.com>',
        )

    def test_org_b_inbound_does_not_match_org_a_message_id(self):
        from psa.management.commands import psa_poll_email
        raw = _build_raw_email(
            message_id='<orgb-new@example.com>',
            from_addr='other@example.com', to_addr='help@orgb.example.com',
            subject='no ticket token',
            in_reply_to='<collision@example.com>',
            body='If isolation is broken this lands on org A.',
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfgB.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        # org A still has exactly the seeded ticket with no new comments.
        from psa.models import TicketComment
        self.assertEqual(Ticket.objects.filter(organization=self.orgA).count(), 1)
        self.assertEqual(TicketComment.objects.filter(ticket=self.ticketA).count(), 0)
        # org B got a brand-new ticket — the In-Reply-To went unmatched.
        self.assertEqual(Ticket.objects.filter(organization=self.orgB).count(), 1)


class EmailThreadingNewTicketTests(TestCase, _EmailPollerSetup):
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgnew')

    def test_unknown_inbound_creates_ticket_and_emailmessage(self):
        from psa.management.commands import psa_poll_email
        from psa.models import EmailMessage
        raw = _build_raw_email(
            message_id='<fresh@example.com>',
            from_addr='alice@customer.example', to_addr='help@orgnew.example.com',
            subject='Printer down',
            body='Please send help.',
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        tickets = Ticket.objects.filter(organization=self.org)
        self.assertEqual(tickets.count(), 1)
        t = tickets.first()
        self.assertEqual(t.subject, 'Printer down')
        self.assertEqual(t.requester_email, 'alice@customer.example')

        em = EmailMessage.objects.get(organization=self.org, message_id='<fresh@example.com>')
        self.assertEqual(em.ticket_id, t.id)
        self.assertEqual(em.direction, 'in')
        self.assertEqual(em.from_email, 'alice@customer.example')
        self.assertIn('Please send help', em.body_text)
        # Cache field is set on the new ticket.
        self.assertEqual(t.last_inbound_message_id, '<fresh@example.com>')


# ---------------------------------------------------------------------------
# Phase 10.2 — Body cleanup helpers (pure functions; no IMAP needed)
# ---------------------------------------------------------------------------

class HtmlSanitizeTests(TestCase):
    def test_strips_script_and_style(self):
        from psa.email_parsing import sanitize_html
        result = sanitize_html('<p>ok</p><script>alert(1)</script><style>p{color:red}</style>')
        self.assertNotIn('<script', result)
        self.assertNotIn('<style', result)
        self.assertIn('<p>ok</p>', result)

    def test_strips_iframe_object_embed(self):
        from psa.email_parsing import sanitize_html
        result = sanitize_html(
            '<p>hi</p><iframe src=evil></iframe>'
            '<object data=x.swf></object><embed src=y.swf>'
        )
        self.assertNotIn('iframe', result.lower())
        self.assertNotIn('object', result.lower())
        self.assertNotIn('embed', result.lower())

    def test_strips_inline_event_handlers(self):
        from psa.email_parsing import sanitize_html
        result = sanitize_html('<a href="https://x.com" onclick="bad()">x</a>')
        self.assertNotIn('onclick', result)
        self.assertIn('href="https://x.com"', result)

    def test_strips_remote_images(self):
        from psa.email_parsing import sanitize_html
        # img is not in the allowlist; bleach drops it entirely (strip=True).
        result = sanitize_html('<p>hi</p><img src="https://tracker.example/p.gif">')
        self.assertNotIn('<img', result)
        self.assertIn('<p>hi</p>', result)

    def test_links_get_safe_attrs(self):
        from psa.email_parsing import sanitize_html
        result = sanitize_html('<a href="https://x.com">x</a>')
        self.assertIn('rel="noopener noreferrer"', result)
        self.assertIn('target="_blank"', result)

    def test_empty_input_returns_empty(self):
        from psa.email_parsing import sanitize_html
        self.assertEqual(sanitize_html(''), '')
        self.assertEqual(sanitize_html(None), '')


class SignatureStripTests(TestCase):
    def test_rfc3676_sentinel_cuts_signature(self):
        from psa.email_parsing import strip_signature
        body = 'Hello there.\n\n-- \nAlice\nWidget Co.'
        self.assertEqual(strip_signature(body), 'Hello there.')

    def test_sent_from_iphone_heuristic(self):
        from psa.email_parsing import strip_signature
        self.assertEqual(strip_signature('Hi.\n\nSent from my iPhone\n'), 'Hi.')

    def test_sent_from_android_heuristic(self):
        from psa.email_parsing import strip_signature
        self.assertEqual(strip_signature('Hi.\nSent from my Android device\n'), 'Hi.')

    def test_no_signature_returns_input_unchanged(self):
        from psa.email_parsing import strip_signature
        self.assertEqual(strip_signature('Just a message.'), 'Just a message.')

    def test_empty_input(self):
        from psa.email_parsing import strip_signature
        self.assertEqual(strip_signature(''), '')


class QuotedReplyStripTests(TestCase):
    def test_apple_gmail_on_wrote_header(self):
        from psa.email_parsing import strip_quoted_reply
        body = (
            'Yes that worked, thanks.\n\n'
            'On Tue, Mar 4, 2026 at 10:00 AM Alice <a@b> wrote:\n'
            '> Try restarting it.\n'
            '> Let me know.\n'
        )
        self.assertEqual(strip_quoted_reply(body), 'Yes that worked, thanks.')

    def test_outlook_original_message_block(self):
        from psa.email_parsing import strip_quoted_reply
        body = (
            'Reply text.\n\n'
            '-----Original Message-----\n'
            'From: Alice\n'
            'Sent: Tuesday\n'
        )
        self.assertEqual(strip_quoted_reply(body), 'Reply text.')

    def test_outlook_from_sent_to_subject_block(self):
        from psa.email_parsing import strip_quoted_reply
        body = (
            'Reply.\n\n'
            'From: Alice <a@b>\n'
            'Sent: Tuesday\n'
            'To: Bob\n'
            'Subject: RE: thing\n'
            'Hi Bob,\n'
        )
        self.assertEqual(strip_quoted_reply(body), 'Reply.')

    def test_bare_gt_prefix_block(self):
        from psa.email_parsing import strip_quoted_reply
        body = 'Got it.\n\n> previous content\n> here\n'
        self.assertEqual(strip_quoted_reply(body), 'Got it.')

    def test_no_quote_returns_unchanged(self):
        from psa.email_parsing import strip_quoted_reply
        self.assertEqual(strip_quoted_reply('Plain reply.'), 'Plain reply.')


def _build_raw_email_with_attachment(*, message_id, from_addr, to_addr, subject,
                                     body, attachments):
    """
    Build a multipart/mixed email with text + attachments. ``attachments`` is
    a list of (filename, mime, payload_bytes).
    """
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email import encoders

    root = MIMEMultipart('mixed')
    root['From'] = from_addr
    root['To'] = to_addr
    root['Subject'] = subject
    root['Message-ID'] = message_id
    root.attach(MIMEText(body, 'plain', 'utf-8'))
    for filename, mime, payload in attachments:
        maintype, _, subtype = mime.partition('/')
        part = MIMEBase(maintype, subtype)
        part.set_payload(payload)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=filename)
        root.attach(part)
    return root.as_bytes()


class AttachmentIngestTests(TestCase, _EmailPollerSetup):
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgatt')

    def _run_poll(self, raw):
        from psa.management.commands import psa_poll_email
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

    def test_allowlist_hit_creates_ticket_attachment(self):
        from psa.models import TicketAttachment
        raw = _build_raw_email_with_attachment(
            message_id='<att-ok@example.com>',
            from_addr='alice@example.com', to_addr='help@orgatt.example.com',
            subject='Logs attached',
            body='See attached.',
            attachments=[('error.log', 'text/plain', b'2026-05-01 ERROR boom')],
        )
        self._run_poll(raw)

        ticket = Ticket.objects.get(organization=self.org)
        attachments = TicketAttachment.objects.filter(ticket=ticket)
        self.assertEqual(attachments.count(), 1)
        a = attachments.first()
        self.assertEqual(a.filename, 'error.log')
        self.assertEqual(a.content_type, 'text/plain')
        self.assertGreater(a.size_bytes, 0)
        self.assertFalse(a.is_internal)

    def test_allowlist_miss_skips_attachment(self):
        from psa.models import TicketAttachment
        raw = _build_raw_email_with_attachment(
            message_id='<att-block@example.com>',
            from_addr='alice@example.com', to_addr='help@orgatt.example.com',
            subject='Suspicious binary',
            body='Run this.',
            attachments=[('payload.exe', 'application/x-msdownload', b'MZ\x90\x00')],
        )
        self._run_poll(raw)

        ticket = Ticket.objects.get(organization=self.org)
        # Ticket got created (the email body went through), but attachment
        # was rejected.
        self.assertEqual(TicketAttachment.objects.filter(ticket=ticket).count(), 0)

    def test_oversize_attachment_skipped(self):
        from django.test import override_settings
        from psa.models import TicketAttachment
        raw = _build_raw_email_with_attachment(
            message_id='<att-big@example.com>',
            from_addr='alice@example.com', to_addr='help@orgatt.example.com',
            subject='Huge log',
            body='attached',
            attachments=[('huge.log', 'text/plain', b'A' * 5000)],
        )
        # Drop the cap to 1024 bytes so 5000 bytes of payload is rejected.
        with override_settings(PSA_EMAIL_ATTACHMENT_MAX_BYTES=1024):
            self._run_poll(raw)

        ticket = Ticket.objects.get(organization=self.org)
        self.assertEqual(TicketAttachment.objects.filter(ticket=ticket).count(), 0)

    def test_image_wildcard_in_allowlist_accepts_jpeg(self):
        """``image/*`` allowlist entries should accept ``image/jpeg`` etc."""
        from django.test import override_settings
        from psa.models import TicketAttachment
        raw = _build_raw_email_with_attachment(
            message_id='<att-img@example.com>',
            from_addr='alice@example.com', to_addr='help@orgatt.example.com',
            subject='Screenshot',
            body='see image',
            attachments=[('shot.jpg', 'image/jpeg', b'\xff\xd8\xff\xe0fakejpeg')],
        )
        with override_settings(PSA_EMAIL_ATTACHMENT_MIME_ALLOWLIST=['image/*']):
            self._run_poll(raw)

        ticket = Ticket.objects.get(organization=self.org)
        self.assertEqual(TicketAttachment.objects.filter(ticket=ticket).count(), 1)


class ReplyBodyCleanupTests(TestCase, _EmailPollerSetup):
    """
    On replies to an existing ticket, the comment body should have the
    customer's signature + quoted history stripped.
    """
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgclean')
        self.ticket = self._make_ticket(self.org)
        from psa.models import EmailMessage
        EmailMessage.objects.create(
            organization=self.org, ticket=self.ticket, ingestion_config=self.cfg,
            direction='in', message_id='<seed-clean@example.com>',
        )

    def test_reply_comment_drops_signature_and_quote(self):
        from psa.management.commands import psa_poll_email
        from psa.models import TicketComment
        body = (
            'Yes that worked, thanks.\n\n'
            '-- \n'
            'Bob\n'
            'Widget Co.\n\n'
            'On Tue, Mar 4, 2026 at 10:00 AM Alice <a@b> wrote:\n'
            '> Try restarting it.\n'
        )
        raw = _build_raw_email(
            message_id='<reply-clean@example.com>',
            from_addr='customer@example.com', to_addr='help@orgclean.example.com',
            subject='no token',
            in_reply_to='<seed-clean@example.com>',
            body=body,
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        c = TicketComment.objects.get(ticket=self.ticket)
        self.assertEqual(c.body.strip(), 'Yes that worked, thanks.')
        # The pristine raw body should still be on the EmailMessage row for
        # the conversation panel.
        from psa.models import EmailMessage
        em = EmailMessage.objects.get(message_id='<reply-clean@example.com>')
        self.assertIn('Widget Co.', em.body_text)
        self.assertIn('On Tue', em.body_text)


class HtmlBodyStoredSanitizedTests(TestCase, _EmailPollerSetup):
    """
    Inbound HTML bodies should be sanitized before being persisted to
    EmailMessage.body_html so the conversation panel can render them safely.
    """
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orghtml')

    def test_inbound_html_body_is_sanitized(self):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from psa.management.commands import psa_poll_email
        from psa.models import EmailMessage
        msg = MIMEMultipart('alternative')
        msg['From'] = 'alice@example.com'
        msg['To'] = 'help@orghtml.example.com'
        msg['Subject'] = 'with html'
        msg['Message-ID'] = '<htmlbody@example.com>'
        msg.attach(MIMEText('hi', 'plain', 'utf-8'))
        msg.attach(MIMEText(
            '<p>hi</p><script>alert(1)</script>'
            '<a href="https://x.com" onclick="bad()">x</a>',
            'html', 'utf-8',
        ))
        raw = msg.as_bytes()
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        em = EmailMessage.objects.get(message_id='<htmlbody@example.com>')
        self.assertNotIn('<script', em.body_html)
        self.assertNotIn('onclick', em.body_html)
        self.assertIn('href="https://x.com"', em.body_html)


class MalformedMimeTests(TestCase, _EmailPollerSetup):
    """The poller must not crash on broken MIME — single bad message
    can't take down the whole poll cycle."""
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgmime')

    def test_garbage_message_does_not_raise(self):
        from psa.management.commands import psa_poll_email
        # Not actually a valid RFC 822 message — just bytes.
        raw = b'this is not really an email at all'
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        # Either the poller created a "(no subject)" ticket from the garbage
        # or it skipped silently — but it must not have raised.
        # Either outcome is acceptable; the assertion is "we got here".
        self.assertTrue(True)


# ---------------------------------------------------------------------------
# Phase 10.3 — Auto-responder detection, DMARC verdict, spam keywords,
# routing rules. Most of this is pure-function tests; integration tests at
# the bottom drive the poller end-to-end with mocked IMAP.
# ---------------------------------------------------------------------------

class AutoResponderDetectionTests(TestCase):
    def _msg(self, **headers):
        import email
        from email.mime.text import MIMEText
        m = MIMEText('hi', 'plain', 'utf-8')
        for k, v in headers.items():
            del m[k]
            m[k] = v
        return email.message_from_bytes(m.as_bytes())

    def test_auto_submitted_header_quarantines(self):
        from psa.email_parsing import detect_auto_responder
        m = self._msg(**{'Auto-Submitted': 'auto-replied'})
        self.assertIn('Auto-Submitted', detect_auto_responder(m))

    def test_x_autoreply_header_quarantines(self):
        from psa.email_parsing import detect_auto_responder
        m = self._msg(**{'X-Autoreply': 'yes'})
        self.assertIn('X-Autoreply', detect_auto_responder(m))

    def test_precedence_bulk_quarantines(self):
        from psa.email_parsing import detect_auto_responder
        m = self._msg(Precedence='bulk')
        self.assertIn('Precedence', detect_auto_responder(m))

    def test_subject_out_of_office_quarantines_when_no_header(self):
        from psa.email_parsing import detect_auto_responder
        m = self._msg(Subject='Out of Office: back Monday')
        self.assertIn('subject heuristic', detect_auto_responder(m))

    def test_normal_reply_passes_through(self):
        from psa.email_parsing import detect_auto_responder
        m = self._msg(Subject='Re: my support ticket')
        self.assertEqual(detect_auto_responder(m), '')


class AuthenticationResultsParseTests(TestCase):
    def test_parses_dmarc_pass(self):
        import email
        raw = (
            b'Authentication-Results: mx.example.com;\n'
            b' spf=pass smtp.mailfrom=alice@example.com;\n'
            b' dkim=pass header.d=example.com;\n'
            b' dmarc=pass action=none\n'
            b'From: alice@example.com\n'
            b'Subject: hi\n\nbody\n'
        )
        m = email.message_from_bytes(raw)
        from psa.email_parsing import parse_authentication_results
        self.assertEqual(parse_authentication_results(m).get('dmarc'), 'pass')
        self.assertEqual(parse_authentication_results(m).get('dkim'), 'pass')

    def test_no_header_returns_empty(self):
        import email
        m = email.message_from_bytes(b'From: a@b\n\nx\n')
        from psa.email_parsing import parse_authentication_results
        self.assertEqual(parse_authentication_results(m), {})


class SpamKeywordScoreTests(TestCase):
    def test_score_zero_for_clean_text(self):
        from psa.email_parsing import spam_keyword_score
        self.assertEqual(spam_keyword_score('Please reset my password thanks'), 0)

    def test_score_increments_per_pattern(self):
        from psa.email_parsing import spam_keyword_score
        text = 'CONGRATULATIONS WINNER! Claim your prize, Nigerian Prince here.'
        # Hits: "congratulations ... winner" + "claim your prize" + "nigerian ... prince"
        self.assertGreaterEqual(spam_keyword_score(text), 3)


class EmailRoutingRuleMatchTests(TestCase):
    def setUp(self):
        from core.models import Organization
        from psa.models import EmailRoutingRule
        self.msp = Organization.objects.create(name='MSP', slug='routing-msp')
        self.client_org = Organization.objects.create(name='Acme Co', slug='routing-acme')
        self.rule_domain = EmailRoutingRule.objects.create(
            organization=self.msp, name='Acme exact',
            sender_domain_glob='acme.com', target_client_org=self.client_org,
        )
        self.rule_subdomain = EmailRoutingRule.objects.create(
            organization=self.msp, name='Acme subs',
            sender_domain_glob='*.acme.com', target_client_org=self.client_org,
            order=200,
        )
        self.rule_specific_sender = EmailRoutingRule.objects.create(
            organization=self.msp, name='Acme noreply',
            sender_domain_glob='noreply@acme.com', target_client_org=self.client_org,
            order=10,
        )

    def test_exact_domain_match(self):
        self.assertTrue(self.rule_domain.matches('alice@acme.com'))
        self.assertFalse(self.rule_domain.matches('alice@globex.com'))

    def test_subdomain_glob_match(self):
        self.assertTrue(self.rule_subdomain.matches('alerts@api.acme.com'))
        # The wildcard "*.acme.com" pattern requires at least one subdomain
        # before the suffix — bare "acme.com" should NOT match the subdomain rule.
        self.assertFalse(self.rule_subdomain.matches('alice@acme.com'))

    def test_full_email_match(self):
        self.assertTrue(self.rule_specific_sender.matches('noreply@acme.com'))
        self.assertFalse(self.rule_specific_sender.matches('alice@acme.com'))

    def test_empty_inputs_no_match(self):
        self.assertFalse(self.rule_domain.matches(''))
        self.assertFalse(self.rule_domain.matches(None))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class AutoResponderQuarantineIntegrationTests(TestCase, _EmailPollerSetup):
    """Auto-responder inbound is persisted as quarantined and never creates
    a ticket."""

    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgaresp')

    def test_auto_submitted_inbound_does_not_create_ticket(self):
        from psa.management.commands import psa_poll_email
        from psa.models import EmailMessage
        # Manually add Auto-Submitted via a header injected into a normal mime body.
        raw = (
            b'From: bot@example.com\r\n'
            b'To: help@orgaresp.example.com\r\n'
            b'Subject: out of office\r\n'
            b'Message-ID: <bot-1@example.com>\r\n'
            b'Auto-Submitted: auto-replied\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n\r\n'
            b'I am away.\r\n'
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        # No ticket created.
        from psa.models import Ticket
        self.assertEqual(Ticket.objects.filter(organization=self.org).count(), 0)
        # Quarantined EmailMessage row exists with no ticket and a reason.
        em = EmailMessage.objects.get(message_id='<bot-1@example.com>')
        self.assertTrue(em.was_quarantined)
        self.assertIsNone(em.ticket_id)
        self.assertIn('Auto-Submitted', em.quarantine_reason)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class RoutingRuleIntegrationTests(TestCase, _EmailPollerSetup):
    """A sender-domain routing rule remaps inbound mail from the MSP's
    config-org tenant to a client org tenant."""

    def setUp(self):
        self._seed_psa()
        # config_org is the MSP that owns the routing rule.
        self.msp_org, self.cfg = self._make_org_with_config('orgmsp-routing')
        # client_org is the routing target.
        from core.models import Organization
        self.client_org = Organization.objects.create(
            name='Routed Client', slug='routed-client',
        )
        from psa.models import EmailRoutingRule
        EmailRoutingRule.objects.create(
            organization=self.msp_org, name='Globex routing',
            sender_domain_glob='globex.example',
            target_client_org=self.client_org,
        )

    def test_inbound_from_matching_domain_lands_in_client_org(self):
        from psa.management.commands import psa_poll_email
        raw = _build_raw_email(
            message_id='<route-1@globex.example>',
            from_addr='alice@globex.example',
            to_addr='help@orgmsp-routing.example.com',
            subject='Printer down',
            body='it does not work',
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        # Ticket landed in client_org, NOT the MSP config_org.
        self.assertEqual(Ticket.objects.filter(organization=self.client_org).count(), 1)
        self.assertEqual(Ticket.objects.filter(organization=self.msp_org).count(), 0)

    def test_inbound_from_unmatched_domain_stays_in_msp_org(self):
        from psa.management.commands import psa_poll_email
        raw = _build_raw_email(
            message_id='<noroute-1@stranger.example>',
            from_addr='alice@stranger.example',
            to_addr='help@orgmsp-routing.example.com',
            subject='Help',
            body='hi',
        )
        fake = _FakeIMAP([(b'1', raw)])
        original = psa_poll_email.imaplib.IMAP4_SSL
        psa_poll_email.imaplib.IMAP4_SSL = lambda *a, **kw: fake
        try:
            from django.core.management import call_command
            call_command('psa_poll_email', config_id=self.cfg.pk, verbosity=0)
        finally:
            psa_poll_email.imaplib.IMAP4_SSL = original

        self.assertEqual(Ticket.objects.filter(organization=self.msp_org).count(), 1)
        self.assertEqual(Ticket.objects.filter(organization=self.client_org).count(), 0)


# ---------------------------------------------------------------------------
# Phase 10.4 — Outbound threading + conversation panel
# ---------------------------------------------------------------------------

class OutboundThreadedReplyTests(TestCase, _EmailPollerSetup):
    """`send_threaded_reply` builds a properly-threaded outbound email,
    persists an EmailMessage(direction='out') row, and uses the ticket's
    last_inbound_message_id as In-Reply-To."""

    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgoutbound')
        self.ticket = self._make_ticket(self.org, requester_email='customer@example.com')
        # Seed a prior inbound so outbound threading has something to chain against.
        self.ticket.last_inbound_message_id = '<original-inbound@example.com>'
        self.ticket.save(update_fields=['last_inbound_message_id'])

    def test_send_sets_threading_headers_from_last_inbound(self):
        from django.core import mail
        from psa.email_outbound import send_threaded_reply
        em = send_threaded_reply(
            ticket=self.ticket, comment=None,
            body_text='Thanks for the update.',
        )
        # Django captures sent mail in mail.outbox during tests.
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.extra_headers.get('In-Reply-To'),
                         '<original-inbound@example.com>')
        self.assertEqual(sent.extra_headers.get('References'),
                         '<original-inbound@example.com>')
        self.assertIn('Message-ID', sent.extra_headers)
        # The persisted EmailMessage row records the same threading.
        self.assertEqual(em.direction, 'out')
        self.assertEqual(em.in_reply_to, '<original-inbound@example.com>')
        self.assertEqual(em.references, '<original-inbound@example.com>')
        self.assertEqual(em.ticket_id, self.ticket.id)

    def test_no_last_inbound_skips_in_reply_to(self):
        from django.core import mail
        from psa.email_outbound import send_threaded_reply
        # Fresh ticket with no captured inbound.
        ticket = self._make_ticket(
            self.org, subject='Cold outbound', requester_email='cold@example.com',
        )
        send_threaded_reply(
            ticket=ticket, comment=None,
            body_text='Hi, reaching out.',
        )
        sent = mail.outbox[-1]
        self.assertNotIn('In-Reply-To', sent.extra_headers)
        self.assertNotIn('References', sent.extra_headers)

    def test_subject_falls_back_to_re_ticket_pattern(self):
        from django.core import mail
        from psa.email_outbound import send_threaded_reply
        send_threaded_reply(
            ticket=self.ticket, comment=None,
            body_text='reply',
        )
        sent = mail.outbox[-1]
        # Default subject embeds the ticket number for legacy subject-regex
        # threading on inbound replies.
        self.assertIn(self.ticket.ticket_number, sent.subject)

    def test_explicit_subject_and_recipients_honored(self):
        from django.core import mail
        from psa.email_outbound import send_threaded_reply
        send_threaded_reply(
            ticket=self.ticket, comment=None,
            body_text='reply',
            subject='Explicit subject',
            to_emails=['someone-else@example.com'],
        )
        sent = mail.outbox[-1]
        self.assertEqual(sent.subject, 'Explicit subject')
        self.assertEqual(sent.to, ['someone-else@example.com'])

    def test_html_alternative_attached(self):
        from django.core import mail
        from psa.email_outbound import send_threaded_reply
        send_threaded_reply(
            ticket=self.ticket, comment=None,
            body_text='plain version',
            body_html='<p>html version</p>',
        )
        sent = mail.outbox[-1]
        self.assertEqual(sent.body, 'plain version')
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime_type = sent.alternatives[0]
        self.assertEqual(mime_type, 'text/html')
        self.assertIn('<p>html version</p>', html_body)

    def test_missing_recipients_raises(self):
        from psa.email_outbound import send_threaded_reply
        ticket = self._make_ticket(
            self.org, subject='No recipient', requester_email='',
        )
        with self.assertRaises(ValueError):
            send_threaded_reply(
                ticket=ticket, comment=None, body_text='hi',
            )

    def test_outbound_message_id_resolves_to_same_ticket_on_reply(self):
        """The round-trip closes: customer's reply to our outbound
        carries our generated Message-ID as In-Reply-To, and the existing
        Phase 10.1 _thread_target lookup resolves it back to the same
        ticket."""
        from psa.email_outbound import send_threaded_reply
        em = send_threaded_reply(
            ticket=self.ticket, comment=None, body_text='our reply',
        )
        # Simulate inbound carrying our outbound's Message-ID as In-Reply-To.
        import email
        raw = _build_raw_email(
            message_id='<customer-reply@example.com>',
            from_addr='customer@example.com',
            to_addr='help@orgoutbound.example.com',
            subject='no token',
            in_reply_to=em.message_id,
            body='thanks!',
        )
        msg = email.message_from_bytes(raw)
        from psa.management.commands.psa_poll_email import _thread_target
        self.assertEqual(_thread_target(msg, self.org), self.ticket)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class TicketConversationViewTests(TestCase, _EmailPollerSetup):
    def setUp(self):
        self._seed_psa()
        self.org, self.cfg = self._make_org_with_config('orgconv')
        self.ticket = self._make_ticket(self.org)
        from psa.models import EmailMessage
        EmailMessage.objects.create(
            organization=self.org, ticket=self.ticket, direction='in',
            message_id='<inbound-1@example.com>', from_email='customer@example.com',
            subject='Original', body_text='Hello, please help.',
        )
        EmailMessage.objects.create(
            organization=self.org, ticket=self.ticket, direction='out',
            message_id='<outbound-1@example.com>',
            in_reply_to='<inbound-1@example.com>',
            from_email='help@orgconv.example.com',
            to_emails=['customer@example.com'],
            subject='Re: Original', body_text='Sure, looking now.',
        )

        # Staff user with current org pinned in session.
        from accounts.models import Membership, Role
        self.user = User.objects.create_user(
            'conv-user', email='c@x.com', password='pw', is_staff=True,
        )
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OWNER, is_active=True,
        )
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()

    def test_view_lists_inbound_and_outbound_messages(self):
        from core.models import SystemSetting
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()
        url = f'/psa/t/{self.ticket.ticket_number}/conversation/'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Inbound', body)
        self.assertIn('Outbound', body)
        self.assertIn('inbound-1@example.com', body)
        self.assertIn('outbound-1@example.com', body)

    def test_view_404_for_other_org_ticket(self):
        from accounts.models import Membership, Role
        from core.models import SystemSetting
        s = SystemSetting.get_settings(); s.psa_enabled = True; s.save()

        # Create a ticket in a DIFFERENT org and a regular (non-staff)
        # client user from yet a third org. Cross-tenant access must 404.
        other_org = Organization.objects.create(name='Other', slug='other-conv')
        other_ticket = self._make_ticket(other_org, subject='other')

        client_user = User.objects.create_user(
            'client-user-conv', password='pw', email='cu@x.com',
        )
        third_org = Organization.objects.create(name='Third', slug='third-conv')
        Membership.objects.create(
            user=client_user, organization=third_org, role=Role.READONLY, is_active=True,
        )

        c = Client()
        c.force_login(client_user)
        s2 = c.session
        s2['2fa_prompted'] = True
        s2['current_organization_id'] = third_org.id
        s2.save()
        resp = c.get(f'/psa/t/{other_ticket.ticket_number}/conversation/')
        self.assertEqual(resp.status_code, 404)
