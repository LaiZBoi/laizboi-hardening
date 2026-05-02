"""Tests for portal app — Phase 12 v2 portal announcements."""
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalAnnouncementTests(TestCase):
    """v3.17.232: portal announcements rendered to portal users."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization, SystemSetting
        from portal.models import PortalAnnouncement
        from psa.models import ClientPSASettings

        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='PortalCo', slug='portal-co')
        cls.other_org = Organization.objects.create(name='OtherCo', slug='portal-other')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        ClientPSASettings.objects.create(organization=cls.other_org, portal_enabled=True)

        cls.user = User.objects.create_user('portal-user', 'p@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.outsider = User.objects.create_user('portal-outsider', 'o@x.com', 'pw')
        Membership.objects.create(user=cls.outsider, organization=cls.other_org,
                                   role=Role.READONLY, is_active=True)

        cls.active = PortalAnnouncement.objects.create(
            organization=cls.org, title='Maintenance window', body='Saturday 2am',
            severity='warning',
        )
        cls.inactive = PortalAnnouncement.objects.create(
            organization=cls.org, title='Old draft', body='Hidden',
            severity='info', is_active=False,
        )
        cls.expired = PortalAnnouncement.objects.create(
            organization=cls.org, title='Past notice', body='gone',
            severity='info',
            expires_at=timezone.now() - timedelta(days=1),
        )
        cls.other_org_ann = PortalAnnouncement.objects.create(
            organization=cls.other_org, title='OtherCo only', body='secret',
            severity='info',
        )
        cls.critical = PortalAnnouncement.objects.create(
            organization=cls.org, title='Critical breach', body='all hands',
            severity='danger', is_dismissable=False,
        )

    def _login(self, c, user):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_active_for_org_filters_inactive_and_expired(self):
        from portal.models import PortalAnnouncement
        active = list(PortalAnnouncement.active_for_org(self.org).values_list('title', flat=True))
        self.assertIn('Maintenance window', active)
        self.assertIn('Critical breach', active)
        self.assertNotIn('Old draft', active)
        self.assertNotIn('Past notice', active)

    def test_portal_home_shows_active_announcement(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Maintenance window')
        self.assertContains(r, 'Critical breach')
        self.assertNotContains(r, 'Old draft')
        self.assertNotContains(r, 'Past notice')

    def test_portal_home_does_not_leak_other_orgs_announcement(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        self.assertNotContains(r, 'OtherCo only')

    def test_dismiss_endpoint_filters_announcement_from_subsequent_renders(self):
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/portal/announcement/{self.active.pk}/dismiss/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['ok'], True)
        # Subsequent home render should not include the dismissed one,
        # but should still include the non-dismissable critical one.
        r = c.get('/portal/')
        self.assertNotContains(r, 'Maintenance window')
        self.assertContains(r, 'Critical breach')

    def test_dismiss_endpoint_rejects_non_dismissable(self):
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/portal/announcement/{self.critical.pk}/dismiss/')
        self.assertEqual(r.status_code, 400)

    def test_dismiss_endpoint_404s_cross_org(self):
        c = Client()
        self._login(c, self.user)
        # The user can't dismiss OtherCo's announcement.
        r = c.post(f'/portal/announcement/{self.other_org_ann.pk}/dismiss/')
        self.assertEqual(r.status_code, 404)

    def test_portal_renders_org_brand_color_when_set(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        body = r.content.decode('utf-8')
        # No color set → no override CSS.
        self.assertNotIn('--bs-primary:', body)
        self.org.portal_primary_color = '#ff7700'
        self.org.save(update_fields=['portal_primary_color'])
        r = c.get('/portal/')
        self.assertContains(r, '#ff7700')

    def test_critical_announcement_has_no_dismiss_button(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        body = r.content.decode('utf-8')
        # The critical announcement is rendered, but its alert should NOT
        # contain the dismiss-button class.
        self.assertIn('Critical breach', body)
        # Find the critical announcement's div (data-announcement-id) and
        # verify there's no js-dismiss-announcement inside it.
        import re
        match = re.search(
            r'data-announcement-id="' + str(self.critical.pk) + r'"(.*?)</div>',
            body, re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertNotIn('js-dismiss-announcement', match.group(1))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalThreadedRepliesTests(TestCase):
    """v3.17.237: threaded customer comments via parent_comment FK."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization, SystemSetting
        from psa.models import (
            ClientPSASettings, Queue, Ticket, TicketComment,
            TicketPriority, TicketStatus, TicketType,
        )
        from django.core.management import call_command
        call_command('psa_seed_defaults', verbosity=0)
        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='ThreadCo', slug='thread-co')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        cls.user = User.objects.create_user('thread-user', 't@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.ticket = Ticket.objects.create(
            organization=cls.org, subject='Thread test',
            queue=Queue.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            client_can_view=True,
        )
        cls.parent_comment = TicketComment.objects.create(
            ticket=cls.ticket, body='Top-level question',
            is_internal=False, source='portal', author=cls.user,
        )

    def _login(self, c):
        c.force_login(self.user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_post_reply_with_parent_id_sets_parent(self):
        from psa.models import TicketComment
        c = Client()
        self._login(c)
        r = c.post(f'/portal/t/{self.ticket.ticket_number}/reply/', data={
            'body': 'A reply to the question',
            'parent_id': str(self.parent_comment.id),
        })
        self.assertEqual(r.status_code, 302)
        reply = TicketComment.objects.filter(
            ticket=self.ticket, body='A reply to the question',
        ).first()
        self.assertIsNotNone(reply)
        self.assertEqual(reply.parent_comment_id, self.parent_comment.id)

    def test_post_reply_without_parent_id_creates_top_level(self):
        from psa.models import TicketComment
        c = Client()
        self._login(c)
        r = c.post(f'/portal/t/{self.ticket.ticket_number}/reply/', data={
            'body': 'Just a top-level reply',
        })
        self.assertEqual(r.status_code, 302)
        reply = TicketComment.objects.filter(body='Just a top-level reply').first()
        self.assertIsNone(reply.parent_comment_id)

    def test_invalid_parent_id_falls_back_to_top_level(self):
        from psa.models import TicketComment
        c = Client()
        self._login(c)
        c.post(f'/portal/t/{self.ticket.ticket_number}/reply/', data={
            'body': 'Bad parent test',
            'parent_id': '999999',
        })
        reply = TicketComment.objects.filter(body='Bad parent test').first()
        self.assertIsNone(reply.parent_comment_id)

    def test_ticket_detail_renders_thread_tree(self):
        from psa.models import TicketComment
        TicketComment.objects.create(
            ticket=self.ticket, body='nested reply',
            is_internal=False, source='portal', author=self.user,
            parent_comment=self.parent_comment,
        )
        c = Client()
        self._login(c)
        r = c.get(f'/portal/t/{self.ticket.ticket_number}/')
        self.assertEqual(r.status_code, 200)
        threads = r.context['threads']
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0].id, self.parent_comment.id)
        self.assertEqual(len(threads[0].replies_in_thread), 1)
        self.assertEqual(threads[0].replies_in_thread[0].body, 'nested reply')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalTicketEscalateTests(TestCase):
    """v3.17.236: portal-side escalation."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization, SystemSetting
        from psa.models import (
            ClientPSASettings, Queue, Ticket, TicketPriority,
            TicketStatus, TicketType,
        )
        from django.core.management import call_command
        call_command('psa_seed_defaults', verbosity=0)
        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='EscCo', slug='esc-co')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        cls.user = User.objects.create_user('esc-user', 'esc@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.ticket = Ticket.objects.create(
            organization=cls.org, subject='Servers slow',
            queue=Queue.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            client_can_view=True,
        )

    def _login(self, c):
        c.force_login(self.user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_escalate_sets_fields_and_creates_comment(self):
        from psa.models import Ticket, TicketComment
        c = Client()
        self._login(c)
        before = TicketComment.objects.filter(ticket=self.ticket).count()
        r = c.post(f'/portal/t/{self.ticket.ticket_number}/escalate/', data={
            'reason': 'Whole office offline since 9am',
        })
        self.assertEqual(r.status_code, 302)
        ticket = Ticket.objects.get(pk=self.ticket.pk)
        self.assertIsNotNone(ticket.escalated_at)
        self.assertEqual(ticket.escalated_by_id, self.user.id)
        self.assertEqual(ticket.escalation_reason, 'Whole office offline since 9am')
        # Public comment created with the [Escalated by client] tag.
        after = TicketComment.objects.filter(ticket=self.ticket).count()
        self.assertEqual(after, before + 1)
        last = TicketComment.objects.filter(ticket=self.ticket).order_by('-created_at').first()
        self.assertIn('Escalated by client', last.body)

    def test_escalate_rejects_empty_reason(self):
        from psa.models import Ticket
        c = Client()
        self._login(c)
        r = c.post(f'/portal/t/{self.ticket.ticket_number}/escalate/', data={
            'reason': '   ',
        })
        self.assertEqual(r.status_code, 302)
        ticket = Ticket.objects.get(pk=self.ticket.pk)
        self.assertIsNone(ticket.escalated_at)

    def test_second_escalate_updates_reason(self):
        from psa.models import Ticket
        c = Client()
        self._login(c)
        c.post(f'/portal/t/{self.ticket.ticket_number}/escalate/', data={'reason': 'first'})
        c.post(f'/portal/t/{self.ticket.ticket_number}/escalate/', data={'reason': 'second'})
        ticket = Ticket.objects.get(pk=self.ticket.pk)
        self.assertEqual(ticket.escalation_reason, 'second')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalTicketVoteTests(TestCase):
    """v3.17.235: portal user 'I'm affected too' vote on a ticket."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization, SystemSetting
        from psa.models import (
            ClientPSASettings, Queue, Ticket, TicketPriority,
            TicketStatus, TicketType,
        )
        from django.core.management import call_command
        call_command('psa_seed_defaults', verbosity=0)
        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='VoteCo', slug='vote-co')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        cls.user = User.objects.create_user('vote-user', 'vu@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.user2 = User.objects.create_user('vote-user2', 'vu2@x.com', 'pw')
        Membership.objects.create(user=cls.user2, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.ticket = Ticket.objects.create(
            organization=cls.org, subject='Email is down',
            queue=Queue.objects.first(),
            priority=TicketPriority.objects.first(),
            ticket_type=TicketType.objects.first(),
            status=TicketStatus.objects.filter(slug='new').first(),
            client_can_view=True,
        )

    def _login(self, c, user):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_vote_creates_ticketvote_row(self):
        from psa.models import TicketVote
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/portal/t/{self.ticket.ticket_number}/vote/')
        self.assertIn(r.status_code, [200, 302])
        self.assertEqual(TicketVote.objects.filter(ticket=self.ticket).count(), 1)

    def test_vote_toggles_off_on_second_post(self):
        from psa.models import TicketVote
        c = Client()
        self._login(c, self.user)
        c.post(f'/portal/t/{self.ticket.ticket_number}/vote/')
        c.post(f'/portal/t/{self.ticket.ticket_number}/vote/')
        self.assertEqual(TicketVote.objects.filter(ticket=self.ticket).count(), 0)

    def test_vote_count_aggregates_across_users(self):
        from psa.models import TicketVote
        c1 = Client()
        c2 = Client()
        self._login(c1, self.user)
        self._login(c2, self.user2)
        c1.post(f'/portal/t/{self.ticket.ticket_number}/vote/')
        c2.post(f'/portal/t/{self.ticket.ticket_number}/vote/')
        self.assertEqual(TicketVote.objects.filter(ticket=self.ticket).count(), 2)

    def test_ticket_detail_renders_vote_button(self):
        c = Client()
        self._login(c, self.user)
        r = c.get(f'/portal/t/{self.ticket.ticket_number}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "I'm affected")

    def test_xhr_vote_returns_json(self):
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/portal/t/{self.ticket.ticket_number}/vote/',
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['voted'])
        self.assertEqual(body['count'], 1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalKBEnhancementTests(TestCase):
    """v3.17.234: Customer-facing KB — featured + view counts."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization, SystemSetting
        from docs.models import Document
        from psa.models import ClientPSASettings
        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='KBCo', slug='kb-co')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        cls.user = User.objects.create_user('kb-user', 'kb@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.featured = Document.objects.create(
            organization=cls.org, title='How to file a ticket',
            slug='how-to-file', body='Steps...',
            is_published=True, is_client_visible=True,
            is_featured_in_portal=True,
        )
        cls.regular = Document.objects.create(
            organization=cls.org, title='Vacation FAQ',
            slug='vacation-faq', body='When out...',
            is_published=True, is_client_visible=True,
        )
        cls.popular = Document.objects.create(
            organization=cls.org, title='Reset your password',
            slug='reset-pw', body='Click...',
            is_published=True, is_client_visible=True,
            portal_view_count=42,
        )
        cls.staff_only = Document.objects.create(
            organization=cls.org, title='Staff only — runbook',
            slug='staff-runbook', body='internal',
            is_published=True, is_client_visible=False,
        )

    def _login(self, c):
        c.force_login(self.user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_kb_list_shows_featured_section(self):
        c = Client()
        self._login(c)
        r = c.get('/portal/kb/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Featured')
        self.assertContains(r, 'How to file a ticket')

    def test_kb_list_orders_by_views_below_featured(self):
        c = Client()
        self._login(c)
        r = c.get('/portal/kb/')
        ctx = r.context
        # Featured row not in 'articles'
        article_titles = [a.title for a in ctx['articles']]
        self.assertNotIn('How to file a ticket', article_titles)
        # Popular (42 views) ahead of regular (0 views)
        self.assertEqual(article_titles[0], 'Reset your password')

    def test_kb_list_excludes_staff_only_articles(self):
        c = Client()
        self._login(c)
        r = c.get('/portal/kb/')
        self.assertNotContains(r, 'Staff only')

    def test_kb_detail_increments_view_count(self):
        from docs.models import Document
        c = Client()
        self._login(c)
        before = Document.objects.get(pk=self.regular.pk).portal_view_count
        c.get(f'/portal/kb/{self.regular.slug}/')
        after = Document.objects.get(pk=self.regular.pk).portal_view_count
        self.assertEqual(after, before + 1)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalPreferencesTests(TestCase):
    """v3.17.233: portal notification preferences."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role, UserProfile
        from core.models import Organization, SystemSetting
        from psa.models import ClientPSASettings
        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='PrefsCo', slug='prefs-co')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        cls.user = User.objects.create_user('prefs-user', 'pp@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        UserProfile.objects.get_or_create(user=cls.user)

    def _login(self, c):
        c.force_login(self.user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_get_preferences_renders_three_switches(self):
        c = Client()
        self._login(c)
        r = c.get('/portal/preferences/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Reply to my tickets')
        self.assertContains(r, 'Status changes')
        self.assertContains(r, 'CSAT survey invitations')

    def test_post_persists_preferences(self):
        from accounts.models import UserProfile
        c = Client()
        self._login(c)
        r = c.post('/portal/preferences/', data={
            'reply': 'on',
            # status omitted → False
            'csat': 'on',
        })
        self.assertEqual(r.status_code, 302)
        from django.contrib.auth.models import User as _U
        fresh = _U.objects.get(pk=self.user.pk)
        self.assertTrue(fresh.profile.portal_notify_ticket_reply)
        self.assertFalse(fresh.profile.portal_notify_status_change)
        self.assertTrue(fresh.profile.portal_notify_csat_invite)
