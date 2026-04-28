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
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings


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
