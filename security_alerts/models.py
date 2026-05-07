"""
security_alerts/models.py — Phase 9 security event ingestion.

EDR / AV / Firewall vendors all alert independently. This app
aggregates alerts from all configured vendors, surfaces them on the
PSA dashboard, and lets techs triage from one screen.
"""
from django.conf import settings as django_settings
from django.db import models


class SecurityVendorConnection(models.Model):
    """A configured connection to a security vendor (EDR / AV / firewall)."""

    PROVIDER_CATEGORIES = [
        ('edr', 'Endpoint Detection & Response'),
        ('av', 'Antivirus'),
        ('firewall', 'Firewall'),
    ]
    PROVIDER_CHOICES = [
        # EDR
        ('crowdstrike_falcon', 'CrowdStrike Falcon'),
        ('sentinelone', 'SentinelOne Singularity'),
        ('defender', 'Microsoft Defender for Endpoint'),
        ('sophos_central', 'Sophos Central'),
        ('huntress', 'Huntress'),
        ('threatlocker', 'ThreatLocker'),
        # AV
        ('bitdefender', 'Bitdefender GravityZone'),
        ('webroot', 'Webroot'),
        ('malwarebytes', 'Malwarebytes'),
        ('eset', 'ESET'),
        # Firewall
        ('fortinet', 'Fortinet FortiGate'),
        ('palo_alto', 'Palo Alto'),
        ('sonicwall', 'SonicWall'),
        ('meraki_mx', 'Cisco Meraki MX'),
        ('sophos_xg', 'Sophos XG'),
        ('pfsense', 'pfSense'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='security_vendor_connections',
        help_text='MSP tenant that owns the connection.',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        related_name='security_vendor_connections_for_client',
        null=True, blank=True,
        help_text='Optionally pin the connection to a specific client. '
                  'Leave blank for an MSP-wide connection.',
    )
    name = models.CharField(max_length=120)
    provider = models.CharField(max_length=40, choices=PROVIDER_CHOICES)
    category = models.CharField(max_length=20, choices=PROVIDER_CATEGORIES)

    # Encrypted credentials — store as JSON blob using existing AES-GCM helper if available
    credentials_encrypted = models.TextField(blank=True,
        help_text='AES-GCM-encrypted JSON blob of provider-specific creds '
                  '(API key / token / username+password / etc).')
    base_url = models.URLField(blank=True,
        help_text='Provider tenant URL (e.g. https://api.crowdstrike.com).')
    webhook_token = models.CharField(max_length=64, blank=True,
        help_text='Random token for the inbound webhook URL path.')
    webhook_secret = models.CharField(max_length=64, blank=True,
        help_text='HMAC shared secret for verifying inbound webhook payloads.')

    # Polling
    poll_interval_minutes = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True)
    sync_enabled = models.BooleanField(default=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=40, blank=True,
        help_text='ok / error / running.')
    last_error = models.TextField(blank=True)

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'security_vendor_connections'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'is_active']),
            models.Index(fields=['provider']),
            models.Index(fields=['client_org']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_provider_display()})'

    def save(self, *args, **kwargs):
        if not self.webhook_token:
            import secrets
            self.webhook_token = secrets.token_urlsafe(32)[:64]
        if not self.webhook_secret:
            import secrets
            self.webhook_secret = secrets.token_hex(32)
        super().save(*args, **kwargs)


class SecurityAlert(models.Model):
    """A security alert ingested from a vendor."""
    SEVERITY_CHOICES = [
        ('info', 'Informational'),
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    STATUS_CHOICES = [
        ('new', 'New'),
        ('acknowledged', 'Acknowledged'),
        ('dismissed', 'Dismissed'),
        ('resolved', 'Resolved'),
    ]

    connection = models.ForeignKey(
        SecurityVendorConnection, on_delete=models.CASCADE,
        related_name='alerts',
        null=True, blank=True,
        help_text='Source vendor connection (poller / vendor webhook). '
                  'Null when the alert was ingested via a SIEM endpoint.',
    )
    siem_endpoint = models.ForeignKey(
        'security_alerts.SIEMWebhookEndpoint', on_delete=models.SET_NULL,
        related_name='alerts', null=True, blank=True,
        help_text='Phase 23 v3.17.337: source SIEM webhook endpoint when '
                  'the alert came in via /security/siem/webhook/.',
    )
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='security_alerts',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        related_name='security_alerts_for_client',
        null=True, blank=True,
    )

    external_id = models.CharField(max_length=200,
        help_text='Vendor-side unique ID — used for dedupe.')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='medium')
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    asset_hint = models.CharField(max_length=200, blank=True,
        help_text='Free-form hostname / IP / device name from the alert.')
    raw_payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    seen_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    auto_ticket = models.ForeignKey(
        'psa.Ticket', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='security_alerts',
        help_text='If a rule auto-created a ticket from this alert, link to it.',
    )

    class Meta:
        db_table = 'security_alerts'
        ordering = ['-seen_at']
        unique_together = [
            ['connection', 'external_id'],
            ['siem_endpoint', 'external_id'],
        ]
        indexes = [
            models.Index(fields=['organization', 'status', '-seen_at']),
            models.Index(fields=['client_org', 'severity']),
            models.Index(fields=['severity', 'status']),
            models.Index(fields=['siem_endpoint', '-seen_at']),
        ]

    def __str__(self):
        return f'[{self.severity.upper()}] {self.title[:80]}'

    @property
    def acknowledge_minutes(self):
        if not self.acknowledged_at:
            return None
        return int((self.acknowledged_at - self.seen_at).total_seconds() / 60)


class SecurityAlertRule(models.Model):
    """
    Auto-action rule on incoming alerts. Patterns match on
    (connection / category / severity); action is 'create_ticket' for now.
    Mirrors the workflow_rules engine pattern.
    """
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='security_alert_rules',
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)

    # Match clauses — empty = match-all
    match_provider = models.CharField(max_length=40, blank=True)
    match_category = models.CharField(max_length=20, blank=True)
    match_severity_min = models.CharField(
        max_length=20, blank=True,
        choices=SecurityAlert.SEVERITY_CHOICES,
    )
    match_client_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        related_name='+', null=True, blank=True,
    )

    # Action
    action = models.CharField(max_length=40, default='create_ticket',
        choices=[('create_ticket', 'Create PSA Ticket')])
    ticket_queue_id = models.IntegerField(null=True, blank=True)
    ticket_priority_code = models.CharField(max_length=10, blank=True,
        help_text='P1 / P2 / P3 / P4 / P5 — overrides severity-derived default.')
    ticket_assignee = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', null=True, blank=True,
    )

    # Suppression window — don't auto-ticket between these hours (per Phase 9.4)
    suppress_start_hour = models.IntegerField(null=True, blank=True,
        help_text='0-23 in MSP tenant\'s timezone. Blank = no suppression.')
    suppress_end_hour = models.IntegerField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'security_alert_rules'
        ordering = ['priority', 'pk']

    def __str__(self):
        return f'{self.name} (priority {self.priority})'


class SIEMWebhookEndpoint(models.Model):
    """
    Phase 23 v3.17.337 — generic SIEM/Syslog/CEF webhook endpoint.

    Lets a customer point a SIEM (Splunk HEC, Elastic, Graylog, generic
    syslog forwarder, anything that can POST CEF-ish JSON or raw CEF) at
    `/security/siem/webhook/<token>/`. The token authenticates the source;
    optional HMAC adds cryptographic integrity. Inbound events are mapped
    to `SecurityAlert` rows so the existing triage UI / rules / playbooks
    just work.

    Unlike `SecurityVendorConnection` (which models a polled SaaS API),
    this endpoint is purely inbound — no creds, no polling. Multiple
    endpoints per org are fine (one per SIEM source).
    """

    FORMAT_CHOICES = [
        ('cef', 'CEF (Common Event Format)'),
        ('json', 'Generic JSON'),
        ('syslog', 'Syslog (RFC 5424)'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='siem_webhook_endpoints',
        help_text='MSP tenant that owns the endpoint.',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        related_name='siem_webhook_endpoints_for_client',
        null=True, blank=True,
        help_text='Optionally pin the endpoint to a specific client.',
    )
    name = models.CharField(max_length=120)
    expected_format = models.CharField(
        max_length=20, choices=FORMAT_CHOICES, default='cef',
    )
    token = models.CharField(max_length=64, unique=True,
        help_text='Per-endpoint token used in the URL path. Auto-generated.')
    hmac_secret = models.CharField(max_length=64, blank=True,
        help_text='If set, inbound POSTs MUST send an X-Cst0r-Signature '
                  'header containing hex-encoded HMAC-SHA256(secret, body).')
    require_hmac = models.BooleanField(
        default=False,
        help_text='When True, requests without a valid signature are rejected '
                  'with HTTP 403. When False, signature is verified IF '
                  'sent but optional.',
    )
    default_severity = models.CharField(
        max_length=20, choices=SecurityAlert.SEVERITY_CHOICES,
        default='medium',
        help_text='Severity used when the inbound payload omits one.',
    )

    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    received_count = models.PositiveIntegerField(default=0)

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'siem_webhook_endpoints'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'is_active']),
            models.Index(fields=['token']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_expected_format_display()})'

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(32)[:64]
        if not self.hmac_secret:
            import secrets
            self.hmac_secret = secrets.token_hex(32)
        super().save(*args, **kwargs)
