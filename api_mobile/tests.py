"""
Tests for the mobile API auth endpoints (v3.17.346) and the
dashboard / organizations endpoints (v3.17.347).
"""
from __future__ import annotations

import json

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token

from accounts.models import Membership, Role
from core.models import Organization

# Strip 2FA + Axes middleware so the test client doesn't get bounced by
# the redirect middleware on every endpoint that's behind it.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]

# Disable DRF rate-throttling for tests — cumulative login attempts across
# multiple test classes sharing one IP otherwise trip the 10/hour login
# throttle and the test client gets 429 on `setUp` logins.
NO_THROTTLE_REST = dict(django_settings.REST_FRAMEWORK, DEFAULT_THROTTLE_CLASSES=[])


def _clear_throttle_cache():
    """Reset the DRF throttle cache between tests."""
    try:
        cache.clear()
    except Exception:
        pass


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type='application/json')


def _auth_get(client, path, token):
    return client.get(path, HTTP_AUTHORIZATION=f'Token {token}')


def _auth_post(client, path, token, payload=None):
    body = json.dumps(payload) if payload is not None else ''
    return client.post(
        path, data=body, content_type='application/json',
        HTTP_AUTHORIZATION=f'Token {token}',
    )


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False, REST_FRAMEWORK=NO_THROTTLE_REST)
class MobileAuthLoginTests(TestCase):
    """Verifies happy-path login + wrong-password rejection + missing fields."""

    def setUp(self):
        _clear_throttle_cache()
        self.org = Organization.objects.create(name='OrgA-Mobile', slug='orga-mobile')
        self.user = User.objects.create_user('mobileuser', password='hunter2', email='m@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OWNER, is_active=True,
        )
        self.client = Client()

    def test_login_success_returns_token(self):
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'mobileuser', 'password': 'hunter2',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn('token', body)
        self.assertEqual(body['user']['username'], 'mobileuser')
        self.assertEqual(body['user']['organization_id'], self.org.id)
        # Token is real
        self.assertTrue(Token.objects.filter(key=body['token']).exists())

    def test_login_wrong_password_rejected(self):
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'mobileuser', 'password': 'WRONG',
        })
        self.assertEqual(resp.status_code, 401)

    def test_login_missing_fields_400(self):
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {'username': 'x'})
        self.assertEqual(resp.status_code, 400)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False, REST_FRAMEWORK=NO_THROTTLE_REST)
class MobileAuth2FAFlowTests(TestCase):
    """Users with 2FA must complete `/auth/mfa/` to get the API token."""

    def setUp(self):
        _clear_throttle_cache()
        self.user = User.objects.create_user('mfauser', password='hunter2')
        # Mark profile two_factor_enabled — that's how `user_has_2fa_enabled`
        # detects without a real TOTP device row.
        if hasattr(self.user, 'profile'):
            self.user.profile.two_factor_enabled = True
            self.user.profile.save(update_fields=['two_factor_enabled'])
        self.client = Client()

    def test_login_returns_mfa_required(self):
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'mfauser', 'password': 'hunter2',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body.get('mfa_required'))
        self.assertIn('mfa_token', body)
        self.assertNotIn('token', body)

    def test_mfa_with_bad_token_rejected(self):
        resp = _post(self.client, '/api/mobile/v1/auth/mfa/', {
            'mfa_token': 'no-such-token', 'code': '123456',
        })
        self.assertEqual(resp.status_code, 401)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False, REST_FRAMEWORK=NO_THROTTLE_REST)
class MobileAuthTokenLifecycleTests(TestCase):
    """Logout revokes the token; refresh issues a new one; me returns profile."""

    def setUp(self):
        _clear_throttle_cache()
        self.user = User.objects.create_user('luser', password='hunter2', email='l@x.com')
        self.client = Client()
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'luser', 'password': 'hunter2',
        })
        self.token = resp.json()['token']

    def test_me_unauthenticated_blocked(self):
        c = Client()
        resp = c.get('/api/mobile/v1/auth/me/')
        self.assertIn(resp.status_code, (401, 403))

    def test_me_authenticated_returns_profile(self):
        resp = _auth_get(self.client, '/api/mobile/v1/auth/me/', self.token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['user']['username'], 'luser')

    def test_logout_revokes_token(self):
        resp = _auth_post(self.client, '/api/mobile/v1/auth/logout/', self.token)
        self.assertEqual(resp.status_code, 200)
        # Subsequent call with the old token now fails
        resp2 = _auth_get(self.client, '/api/mobile/v1/auth/me/', self.token)
        self.assertIn(resp2.status_code, (401, 403))

    def test_token_refresh_rotates(self):
        resp = _auth_post(self.client, '/api/mobile/v1/auth/refresh/', self.token)
        self.assertEqual(resp.status_code, 200)
        new_token = resp.json()['token']
        self.assertNotEqual(new_token, self.token)
        # Old token revoked
        resp2 = _auth_get(self.client, '/api/mobile/v1/auth/me/', self.token)
        self.assertIn(resp2.status_code, (401, 403))
        # New token works
        resp3 = _auth_get(self.client, '/api/mobile/v1/auth/me/', new_token)
        self.assertEqual(resp3.status_code, 200)


# v3.17.347 — dashboard + organizations endpoints
@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False, REST_FRAMEWORK=NO_THROTTLE_REST)
class MobileDashboardOrgsTests(TestCase):
    """Dashboard counts + organization list + detail are org-scoped."""

    def setUp(self):
        _clear_throttle_cache()
        self.org_a = Organization.objects.create(name='OrgA-Dash', slug='orga-dash')
        self.org_b = Organization.objects.create(name='OrgB-Dash', slug='orgb-dash')
        self.user_a = User.objects.create_user('dashuser', password='hunter2')
        Membership.objects.create(
            user=self.user_a, organization=self.org_a, role=Role.OWNER, is_active=True,
        )
        # user_a is NOT in org_b
        self.client = Client()
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'dashuser', 'password': 'hunter2',
        })
        self.token = resp.json()['token']

    def test_dashboard_requires_auth(self):
        c = Client()
        self.assertIn(c.get('/api/mobile/v1/dashboard/').status_code, (401, 403))

    def test_dashboard_returns_counts(self):
        resp = _auth_get(self.client, '/api/mobile/v1/dashboard/', self.token)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ('open_tickets', 'critical_tickets', 'expiring_soon',
                    'offline_monitors', 'recent_assets', 'security_alerts_open',
                    'organization_count'):
            self.assertIn(key, body)
        self.assertEqual(body['organization_count'], 1)

    def test_org_list_requires_auth(self):
        c = Client()
        self.assertIn(c.get('/api/mobile/v1/organizations/').status_code, (401, 403))

    def test_org_list_returns_only_user_orgs(self):
        resp = _auth_get(self.client, '/api/mobile/v1/organizations/', self.token)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        ids = [o['id'] for o in body['results']]
        self.assertIn(self.org_a.id, ids)
        self.assertNotIn(self.org_b.id, ids)
        self.assertEqual(body['count'], 1)

    def test_org_detail_returns_my_org(self):
        url = f'/api/mobile/v1/organizations/{self.org_a.id}/'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['name'], 'OrgA-Dash')

    def test_org_detail_cross_org_blocked(self):
        url = f'/api/mobile/v1/organizations/{self.org_b.id}/'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 404)


# v3.17.348 — assets endpoints
@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False, REST_FRAMEWORK=NO_THROTTLE_REST)
class MobileAssetsTests(TestCase):
    """Asset list + detail respect org-scoping; search + filters work."""

    def setUp(self):
        _clear_throttle_cache()
        from assets.models import Asset
        self.org_a = Organization.objects.create(name='OrgA-Asset', slug='orga-asset')
        self.org_b = Organization.objects.create(name='OrgB-Asset', slug='orgb-asset')
        self.user_a = User.objects.create_user('assetuser', password='hunter2')
        Membership.objects.create(
            user=self.user_a, organization=self.org_a, role=Role.OWNER, is_active=True,
        )
        self.asset_a = Asset.objects.create(
            organization=self.org_a, name='ServerA', hostname='srv-a.local',
            asset_type='server',
        )
        self.asset_b = Asset.objects.create(
            organization=self.org_b, name='ServerB', hostname='srv-b.local',
            asset_type='workstation',
        )
        self.client = Client()
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'assetuser', 'password': 'hunter2',
        })
        self.token = resp.json()['token']

    def test_asset_list_scoped_to_user_orgs(self):
        resp = _auth_get(self.client, '/api/mobile/v1/assets/', self.token)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        names = [a['name'] for a in body['results']]
        self.assertIn('ServerA', names)
        self.assertNotIn('ServerB', names)

    def test_asset_detail_returns_my_asset(self):
        url = f'/api/mobile/v1/assets/{self.asset_a.id}/'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['hostname'], 'srv-a.local')

    def test_asset_detail_cross_org_blocked(self):
        url = f'/api/mobile/v1/assets/{self.asset_b.id}/'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 404)

    def test_asset_list_search_by_hostname(self):
        url = '/api/mobile/v1/assets/?search=srv-a'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()['results']), 1)

    def test_asset_list_filter_by_type(self):
        from assets.models import Asset
        Asset.objects.create(
            organization=self.org_a, name='LaptopA', asset_type='laptop',
        )
        url = '/api/mobile/v1/assets/?type=server'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 200)
        types = {a['asset_type'] for a in resp.json()['results']}
        self.assertEqual(types, {'server'})

    def test_asset_list_requires_auth(self):
        c = Client()
        self.assertIn(c.get('/api/mobile/v1/assets/').status_code, (401, 403))


# v3.17.349 — tickets endpoints
@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False, REST_FRAMEWORK=NO_THROTTLE_REST)
class MobileTicketsTests(TestCase):
    """Ticket list/detail/create/patch/comment respect org scope."""

    def setUp(self):
        _clear_throttle_cache()
        from psa.models import (
            Ticket, TicketStatus, TicketPriority, TicketType, Queue,
        )
        self.org_a = Organization.objects.create(name='OrgA-Tk', slug='orga-tk')
        self.org_b = Organization.objects.create(name='OrgB-Tk', slug='orgb-tk')
        self.user_a = User.objects.create_user('tkuser', password='hunter2')
        Membership.objects.create(
            user=self.user_a, organization=self.org_a, role=Role.OWNER, is_active=True,
        )
        # Seed minimal PSA workflow rows
        self.status_new = TicketStatus.objects.create(name='New', slug='new', sort_order=1)
        self.status_closed = TicketStatus.objects.create(
            name='Closed', slug='closed', sort_order=99, is_terminal=True,
        )
        self.priority = TicketPriority.objects.create(code='P3', name='Normal')
        self.priority_p1 = TicketPriority.objects.create(code='P1', name='Critical')
        self.ttype = TicketType.objects.create(name='Incident', slug='incident')
        self.queue = Queue.objects.create(name='Default', slug='default')
        self.ticket_a = Ticket.objects.create(
            organization=self.org_a, subject='OrgA ticket',
            status=self.status_new, priority=self.priority,
            ticket_type=self.ttype, queue=self.queue,
        )
        self.ticket_b = Ticket.objects.create(
            organization=self.org_b, subject='OrgB ticket',
            status=self.status_new, priority=self.priority,
            ticket_type=self.ttype, queue=self.queue,
        )
        self.client = Client()
        resp = _post(self.client, '/api/mobile/v1/auth/login/', {
            'username': 'tkuser', 'password': 'hunter2',
        })
        self.token = resp.json()['token']

    def test_ticket_list_scoped_to_user_orgs(self):
        resp = _auth_get(self.client, '/api/mobile/v1/tickets/', self.token)
        self.assertEqual(resp.status_code, 200)
        ids = [t['id'] for t in resp.json()['results']]
        self.assertIn(self.ticket_a.id, ids)
        self.assertNotIn(self.ticket_b.id, ids)

    def test_ticket_detail_returns_my_ticket_with_comments(self):
        url = f'/api/mobile/v1/tickets/{self.ticket_a.id}/'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['subject'], 'OrgA ticket')
        self.assertIn('comments', body)

    def test_ticket_detail_cross_org_blocked(self):
        url = f'/api/mobile/v1/tickets/{self.ticket_b.id}/'
        resp = _auth_get(self.client, url, self.token)
        self.assertEqual(resp.status_code, 404)

    def test_ticket_create(self):
        resp = _auth_post(self.client, '/api/mobile/v1/tickets/', self.token, {
            'organization_id': self.org_a.id,
            'subject': 'New ticket from mobile',
            'description': 'Help.',
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['subject'], 'New ticket from mobile')

    def test_ticket_create_cross_org_rejected(self):
        resp = _auth_post(self.client, '/api/mobile/v1/tickets/', self.token, {
            'organization_id': self.org_b.id,  # not accessible
            'subject': 'Bad attempt',
        })
        self.assertEqual(resp.status_code, 403)

    def test_ticket_patch_status(self):
        url = f'/api/mobile/v1/tickets/{self.ticket_a.id}/'
        resp = self.client.patch(
            url, data=json.dumps({'status_id': self.status_closed.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token}',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'Closed')

    def test_ticket_add_comment(self):
        url = f'/api/mobile/v1/tickets/{self.ticket_a.id}/comments/'
        resp = _auth_post(self.client, url, self.token, {
            'body': 'On site now', 'is_internal': True,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['body'], 'On site now')
        self.assertTrue(resp.json()['is_internal'])

    def test_ticket_list_requires_auth(self):
        c = Client()
        self.assertIn(c.get('/api/mobile/v1/tickets/').status_code, (401, 403))
