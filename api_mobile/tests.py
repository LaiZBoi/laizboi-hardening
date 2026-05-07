"""
Tests for the mobile API auth endpoints (v3.17.346).
"""
from __future__ import annotations

import json

from django.conf import settings as django_settings
from django.contrib.auth.models import User
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


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class MobileAuthLoginTests(TestCase):
    """Verifies happy-path login + wrong-password rejection + missing fields."""

    def setUp(self):
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


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class MobileAuth2FAFlowTests(TestCase):
    """Users with 2FA must complete `/auth/mfa/` to get the API token."""

    def setUp(self):
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


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class MobileAuthTokenLifecycleTests(TestCase):
    """Logout revokes the token; refresh issues a new one; me returns profile."""

    def setUp(self):
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
