"""
Baseline test coverage for the monitoring/ app.

Cron-driven (uptime checks fire on schedule) + externally-facing
(WebsiteMonitor.check_status makes outbound HTTP requests). Silent
failures here = invisible monitoring loss. Plus IPAM (Subnet,
IPAddress, VLAN) used by network ops.

Coverage areas:
  * `WebsiteMonitor` model basics + `is_ssl_expiring_soon` /
    `is_domain_expiring_soon` properties.
  * `Expiration` model — `is_expired`, `days_until_expiration`,
    `is_expiring_soon` properties.
  * IPAM constraints — `IPAddress` `(subnet, ip_address)` is unique
    so the same address can't be allocated twice in one subnet.
  * OrganizationManager filtering on multi-tenant models.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Organization
from monitoring.models import (
    Expiration,
    IPAddress,
    Subnet,
    VLAN,
    WebsiteMonitor,
)


# ---------------------------------------------------------------------------
# WebsiteMonitor — uptime + SSL/domain expiry tracking
# ---------------------------------------------------------------------------

class WebsiteMonitorModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='WMCo', slug='wm-co')

    def test_create_with_minimal_required_fields(self):
        wm = WebsiteMonitor.objects.create(
            organization=self.org, name='example.com', url='https://example.com',
        )
        self.assertEqual(wm.status, 'unknown')
        self.assertTrue(wm.is_enabled)
        self.assertEqual(wm.check_interval_minutes, 60)

    def test_str_includes_name_and_url(self):
        wm = WebsiteMonitor.objects.create(
            organization=self.org, name='example.com', url='https://example.com',
        )
        self.assertIn('example.com', str(wm))
        self.assertIn('https://example.com', str(wm))

    def test_ssl_expiring_soon_true_when_expiry_within_warning_window(self):
        wm = WebsiteMonitor.objects.create(
            organization=self.org, name='soon-ssl', url='https://soon.example',
            ssl_expires_at=timezone.now() + timedelta(days=10),
            ssl_warning_days=30,
        )
        self.assertTrue(wm.is_ssl_expiring_soon)

    def test_ssl_expiring_soon_false_when_far_from_expiry(self):
        wm = WebsiteMonitor.objects.create(
            organization=self.org, name='far-ssl', url='https://far.example',
            ssl_expires_at=timezone.now() + timedelta(days=180),
            ssl_warning_days=30,
        )
        self.assertFalse(wm.is_ssl_expiring_soon)

    def test_ssl_expiring_soon_false_when_expiry_unknown(self):
        # ssl_expires_at left null — the property must short-circuit to False
        # rather than raising on `None <= ...`.
        wm = WebsiteMonitor.objects.create(
            organization=self.org, name='unknown-ssl', url='https://unknown.example',
        )
        self.assertFalse(wm.is_ssl_expiring_soon)

    def test_domain_expiring_soon_uses_domain_warning_window(self):
        wm = WebsiteMonitor.objects.create(
            organization=self.org, name='dom', url='https://dom.example',
            domain_expires_at=timezone.now() + timedelta(days=45),
            domain_warning_days=60,
        )
        self.assertTrue(wm.is_domain_expiring_soon)

    def test_for_organization_filtering(self):
        org_b = Organization.objects.create(name='WMOther', slug='wm-other')
        WebsiteMonitor.objects.create(
            organization=self.org, name='a', url='https://a.example',
        )
        WebsiteMonitor.objects.create(
            organization=org_b, name='b', url='https://b.example',
        )
        for_a = list(WebsiteMonitor.objects.for_organization(self.org))
        self.assertEqual(len(for_a), 1)
        self.assertEqual(for_a[0].name, 'a')


# ---------------------------------------------------------------------------
# Expiration — generic expiry tracker for SSL / domain / license / etc.
# ---------------------------------------------------------------------------

class ExpirationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ExpCo', slug='exp-co')

    def test_str_format(self):
        future = timezone.now() + timedelta(days=30)
        e = Expiration.objects.create(
            organization=self.org, name='wildcard cert',
            expiration_type='ssl_cert', expires_at=future,
        )
        s = str(e)
        self.assertIn('wildcard cert', s)
        # Date portion is present (Y-M-D form).
        self.assertIn(future.strftime('%Y-%m-%d'), s)

    def test_is_expired_true_when_past(self):
        e = Expiration.objects.create(
            organization=self.org, name='gone',
            expiration_type='license',
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertTrue(e.is_expired)

    def test_is_expired_false_when_future(self):
        e = Expiration.objects.create(
            organization=self.org, name='good',
            expiration_type='license',
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.assertFalse(e.is_expired)

    def test_days_until_expiration_negative_when_expired(self):
        e = Expiration.objects.create(
            organization=self.org, name='gone',
            expiration_type='license',
            expires_at=timezone.now() - timedelta(days=5),
        )
        # `delta.days` truncates toward zero; for a 5-day-overdue expiry
        # the result is -5 (sometimes -6 depending on microseconds).
        self.assertLess(e.days_until_expiration, 0)

    def test_is_expiring_soon_only_when_within_warning_and_not_expired(self):
        # Within window — true.
        e_warn = Expiration.objects.create(
            organization=self.org, name='warn',
            expiration_type='license',
            expires_at=timezone.now() + timedelta(days=15),
            warning_days=30,
        )
        self.assertTrue(e_warn.is_expiring_soon)

        # Already expired — false (must be in [0, warning_days]).
        e_done = Expiration.objects.create(
            organization=self.org, name='done',
            expiration_type='license',
            expires_at=timezone.now() - timedelta(days=1),
            warning_days=30,
        )
        self.assertFalse(e_done.is_expiring_soon)

        # Outside window — false.
        e_far = Expiration.objects.create(
            organization=self.org, name='far',
            expiration_type='license',
            expires_at=timezone.now() + timedelta(days=180),
            warning_days=30,
        )
        self.assertFalse(e_far.is_expiring_soon)


# ---------------------------------------------------------------------------
# IPAM — VLAN, Subnet, IPAddress
# ---------------------------------------------------------------------------

class IPAMConstraintTests(TestCase):
    """The unique_together on (subnet, ip_address) is the dedupe contract
    that prevents the same address being allocated twice in one subnet."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='IPAMCo', slug='ipam-co')
        cls.subnet = Subnet.objects.create(
            organization=cls.org, name='LAN', network='192.168.1.0/24',
        )

    def test_same_ip_in_same_subnet_rejected(self):
        IPAddress.objects.create(subnet=self.subnet, ip_address='192.168.1.10')
        with self.assertRaises(IntegrityError), transaction.atomic():
            IPAddress.objects.create(subnet=self.subnet, ip_address='192.168.1.10')

    def test_same_ip_in_different_subnet_allowed(self):
        IPAddress.objects.create(subnet=self.subnet, ip_address='192.168.1.10')
        other_subnet = Subnet.objects.create(
            organization=self.org, name='DMZ', network='10.0.0.0/24',
        )
        # Same IP-string in a different subnet must NOT raise — the unique
        # is per-subnet, not global.
        IPAddress.objects.create(subnet=other_subnet, ip_address='192.168.1.10')

    def test_default_status_is_available(self):
        ip = IPAddress.objects.create(subnet=self.subnet, ip_address='192.168.1.20')
        self.assertEqual(ip.status, 'available')

    def test_str_with_hostname_includes_both(self):
        ip = IPAddress.objects.create(
            subnet=self.subnet, ip_address='192.168.1.30', hostname='gateway',
        )
        s = str(ip)
        self.assertIn('192.168.1.30', s)
        self.assertIn('gateway', s)

    def test_str_without_hostname_is_just_ip(self):
        ip = IPAddress.objects.create(subnet=self.subnet, ip_address='192.168.1.40')
        self.assertEqual(str(ip), '192.168.1.40')


class SubnetModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='SubnetCo', slug='subnet-co')

    def test_str_includes_name_and_network(self):
        s = Subnet.objects.create(
            organization=self.org, name='LAN', network='192.168.1.0/24',
        )
        self.assertEqual(str(s), 'LAN (192.168.1.0/24)')

    def test_default_dns_servers_is_empty_list(self):
        s = Subnet.objects.create(
            organization=self.org, name='LAN', network='192.168.1.0/24',
        )
        self.assertEqual(s.dns_servers, [])

    def test_for_organization_filtering(self):
        org_b = Organization.objects.create(name='SubnetOther', slug='subnet-other')
        Subnet.objects.create(organization=self.org, name='A', network='10.0.0.0/24')
        Subnet.objects.create(organization=org_b, name='B', network='10.0.1.0/24')
        for_a = list(Subnet.objects.for_organization(self.org))
        self.assertEqual(len(for_a), 1)
        self.assertEqual(for_a[0].name, 'A')


class VLANModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='VLANCo', slug='vlan-co')

    def test_create_basic(self):
        v = VLAN.objects.create(
            organization=self.org, vlan_id=100, name='Voice',
        )
        self.assertEqual(v.vlan_id, 100)
        self.assertEqual(v.name, 'Voice')

    def test_str_includes_id_and_name(self):
        v = VLAN.objects.create(
            organization=self.org, vlan_id=200, name='Guest',
        )
        s = str(v)
        self.assertIn('200', s)
        self.assertIn('Guest', s)
