"""
Assets models - Devices, Contacts, and Relationships
"""
from django.db import models
from django.contrib.auth.models import User
from core.models import Organization, Tag, BaseModel
from core.utils import OrganizationManager


class Contact(BaseModel):
    """
    Contact/person associated with assets or organizations.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='contacts')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    title = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    objects = OrganizationManager()

    class Meta:
        db_table = 'contacts'
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['organization', 'last_name']),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class ContactRating(BaseModel):
    """
    Tech feedback rating for a contact. Multiple ratings accumulate over time —
    each submission is stored individually; the displayed score is the average.
    """
    RATING_CHOICES = [(1,'1'),(2,'2'),(3,'3'),(4,'4'),(5,'5')]

    contact   = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='ratings')
    rated_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='contact_ratings')
    rating    = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    feedback  = models.TextField(blank=True, help_text='Optional feedback note for this rating')

    class Meta:
        db_table = 'contact_ratings'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.contact} — {self.rating}★ by {self.rated_by}"


class ContactNote(BaseModel):
    """
    Freeform tech note attached to a contact. Any staff user can add;
    only the author can edit or delete their own notes.
    """
    contact    = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='tech_notes')
    author     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='contact_notes')
    body       = models.TextField()
    is_private = models.BooleanField(default=False, help_text='Private notes visible only to the author')

    class Meta:
        db_table = 'contact_notes'
        ordering = ['-created_at']

    def __str__(self):
        return f"Note on {self.contact} by {self.author}"


class Asset(BaseModel):
    """
    Asset/device with flexible JSON fields.
    """
    ASSET_TYPES = sorted([
        ('access_control', 'Access Control System'),
        ('appliance', 'Appliance'),
        ('av_receiver', 'AV Receiver'),
        ('backup_appliance', 'Backup Appliance'),
        ('badge_printer', 'Badge Printer'),
        ('biometric_scanner', 'Biometric Scanner'),
        ('bridge', 'Network Bridge'),
        ('card_reader', 'Card Reader'),
        ('conference_phone', 'Conference Phone'),
        ('console_server', 'Console Server'),
        ('copier', 'Copier/MFP'),
        ('desktop', 'Desktop Computer'),
        ('digital_signage', 'Digital Signage'),
        ('display', 'Display/Monitor'),
        ('door_controller', 'Door Controller'),
        ('dvr', 'Digital Video Recorder (DVR)'),
        ('environmental_monitor', 'Environmental Monitor'),
        ('fiber_panel', 'Fiber Patch Panel'),
        ('firewall', 'Firewall'),
        ('gateway', 'Gateway'),
        ('generator', 'Generator'),
        ('handheld', 'Handheld Scanner/Device'),
        ('hvac', 'HVAC System'),
        ('iot_device', 'IoT Device'),
        ('kvm', 'KVM Switch'),
        ('label_printer', 'Label Printer'),
        ('laptop', 'Laptop'),
        ('lighting_control', 'Lighting Control'),
        ('load_balancer', 'Load Balancer'),
        ('mobile', 'Mobile Device'),
        ('modem', 'Modem'),
        ('nas', 'Network Attached Storage (NAS)'),
        ('nvr', 'Network Video Recorder (NVR)'),
        ('other', 'Other'),
        ('paging_system', 'Paging System'),
        ('patch_panel', 'Patch Panel'),
        ('pbx', 'PBX System'),
        ('pda', 'PDA'),
        ('pdu', 'Power Distribution Unit (PDU)'),
        ('phone', 'IP Phone'),
        ('plotter', 'Plotter'),
        ('printer', 'Printer'),
        ('projector', 'Projector'),
        ('rack', 'Server Rack/Cabinet'),
        ('router', 'Router'),
        ('san', 'Storage Area Network (SAN)'),
        ('scanner', 'Scanner'),
        ('security_camera', 'Security Camera'),
        ('sensor', 'Sensor'),
        ('server', 'Server'),
        ('switch', 'Network Switch'),
        ('tablet', 'Tablet'),
        ('tape_drive', 'Tape Drive/Library'),
        ('terminal', 'Terminal'),
        ('thermostat', 'Smart Thermostat'),
        ('thin_client', 'Thin Client'),
        ('ups', 'UPS (Uninterruptible Power Supply)'),
        ('video_conferencing', 'Video Conferencing System'),
        ('voip_gateway', 'VoIP Gateway'),
        ('wireless_ap', 'Wireless Access Point'),
        ('wireless_controller', 'Wireless Controller'),
        ('workstation', 'Workstation'),
    ], key=lambda x: x[1])

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=255)
    asset_type = models.CharField(max_length=50, choices=ASSET_TYPES, default='other')
    asset_tag = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=255, blank=True)

    # Equipment model from vendor database (optional, auto-fills fields below)
    equipment_model = models.ForeignKey(
        'EquipmentModel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        help_text="Select equipment model from vendor database (auto-fills manufacturer/model)"
    )

    # Keep existing fields for backward compatibility
    manufacturer = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=255, blank=True)

    # Network fields
    hostname = models.CharField(max_length=255, blank=True, help_text='Network hostname/FQDN')
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text='IPv4 or IPv6 address')
    mac_address = models.CharField(max_length=17, blank=True, help_text='MAC address (e.g., 00:11:22:33:44:55)')

    # Operating system
    os_name = models.CharField(max_length=100, blank=True, help_text='Operating system (e.g., Windows Server 2022, Ubuntu 22.04, Cisco IOS)')
    os_version = models.CharField(max_length=100, blank=True, help_text='OS version or build number')

    # Hardware specs (for servers/workstations/laptops)
    cpu = models.CharField(max_length=200, blank=True, help_text='Processor (e.g., Intel Xeon E5-2680 v4)')
    ram_gb = models.PositiveIntegerField(null=True, blank=True, help_text='RAM in GB')
    storage = models.CharField(max_length=200, blank=True, help_text='Storage configuration (e.g., 2× 1TB SSD RAID-1)')

    # Rackmount fields
    is_rackmount = models.BooleanField(default=False, help_text='Is this asset rackmountable?')
    rack_units = models.PositiveIntegerField(null=True, blank=True, help_text='Height in rack units (U)')

    # Port configuration (for switches, routers, firewalls, patch panels)
    port_count = models.PositiveIntegerField(null=True, blank=True, help_text='Number of ports')
    ports = models.JSONField(default=list, blank=True, help_text='Port configuration data')
    vlans = models.JSONField(default=list, blank=True, help_text='VLAN configuration data')

    # Flexible fields stored as JSON
    custom_fields = models.JSONField(default=dict, blank=True)

    # Relations
    tags = models.ManyToManyField(Tag, blank=True, related_name='assets')
    primary_contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_assets')

    # Lifespan & Replacement Tracking
    purchase_date = models.DateField(null=True, blank=True, help_text='Date asset was purchased or deployed')
    lifespan_years = models.PositiveIntegerField(null=True, blank=True, help_text='Expected lifespan in years (e.g., 3-5 for servers, 5-7 for firewalls)')
    lifespan_reminder_enabled = models.BooleanField(default=False, help_text='Enable reminders for upcoming end-of-life')
    lifespan_reminder_months = models.PositiveIntegerField(default=6, help_text='Send reminder X months before end-of-life')

    # Firmware tracking (for network devices, APs, phones, etc.)
    firmware_version = models.CharField(max_length=100, blank=True, help_text='Currently installed firmware version')
    firmware_latest = models.CharField(max_length=100, blank=True, help_text='Latest available firmware version (populated by scheduler)')
    firmware_checked_at = models.DateTimeField(null=True, blank=True, help_text='When firmware was last checked')

    # Warranty tracking (for PCs, servers, network gear)
    warranty_expiry = models.DateField(null=True, blank=True, help_text='Warranty or support contract expiry date')
    warranty_status = models.CharField(max_length=100, blank=True, help_text='Warranty status description (e.g. Active, Expired, ProSupport)')
    warranty_checked_at = models.DateTimeField(null=True, blank=True, help_text='When warranty was last checked via vendor API')

    # Physical reorder flag (can be toggled from browser extension)
    needs_reorder = models.BooleanField(default=False, help_text='Flag asset as needing physical reorder/replacement')

    # Phase 13 v1 (v3.17.254) — warranty alert deduplication.
    # Stamped by the `assets_warranty_alerts` management command after
    # a digest is sent. Prevents the same alert from going out daily
    # while warranty is still in the warning window.
    last_warranty_alert_sent_at = models.DateTimeField(null=True, blank=True)

    # Phase 18 v2 (v3.17.252) — shared infrastructure inheritance.
    # When True, this asset is also visible to descendants of its
    # organization via `Asset.visible_to_org(child_org)`. Use for
    # truly-shared things like a holding company's main domain
    # controller that all subsidiaries depend on.
    is_shared_with_descendants = models.BooleanField(
        default=False,
        help_text='Make this asset visible to descendants of its '
                  'organization via the parent/child hierarchy. Use for '
                  'shared infrastructure consumed across multiple sites.',
    )

    # Metadata
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='assets_created')

    # Auto-generated profile document (linked when "Generate Profile" is clicked)
    profile_document = models.ForeignKey(
        'docs.Document',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='profiled_assets',
        help_text='Auto-generated profile document for this asset',
    )

    # AI-generated documentation (linked when "Generate AI Doc" is clicked)
    ai_document = models.ForeignKey(
        'docs.Document',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='ai_doc_assets',
        help_text='AI-generated documentation for this asset',
    )

    objects = OrganizationManager()

    class Meta:
        db_table = 'assets'
        ordering = ['name']
        indexes = [
            models.Index(fields=['organization', 'name']),
            models.Index(fields=['asset_type']),
        ]

    @classmethod
    def visible_to_org(cls, organization):
        """
        Phase 18 v2 (v3.17.252): assets visible to `organization`,
        including:
          * own + descendants (via OrganizationManager.for_organization)
          * ancestors' assets where `is_shared_with_descendants=True`
        """
        from django.db.models import Q
        from core.utils import descendant_org_ids, ancestor_org_ids
        own_set = descendant_org_ids(organization)
        ancestor_set = ancestor_org_ids(organization) - {organization.pk if organization else None}
        return cls.objects.filter(
            Q(organization_id__in=own_set) |
            (Q(organization_id__in=ancestor_set) & Q(is_shared_with_descendants=True))
        )

    def __str__(self):
        return f"{self.name} ({self.get_asset_type_display()})"

    def save(self, *args, **kwargs):
        """Auto-populate manufacturer/model from equipment_model if set."""
        if self.equipment_model:
            # Auto-fill from equipment model
            self.manufacturer = self.equipment_model.vendor.name
            self.model = self.equipment_model.model_name

            # Auto-fill rack info if available
            if self.equipment_model.is_rackmount:
                self.is_rackmount = True
                if self.equipment_model.rack_units:
                    self.rack_units = self.equipment_model.rack_units

        super().save(*args, **kwargs)

    def get_equipment_specs(self):
        """Get equipment specifications from linked model."""
        if self.equipment_model:
            return self.equipment_model.specifications
        return {}

    def get_equipment_image(self):
        """Get equipment image from linked model."""
        if self.equipment_model:
            return self.equipment_model.get_primary_image()
        return None

    def get_end_of_life_date(self):
        """Calculate end-of-life date based on purchase date and lifespan."""
        if self.purchase_date and self.lifespan_years:
            from dateutil.relativedelta import relativedelta
            return self.purchase_date + relativedelta(years=self.lifespan_years)
        return None

    def get_replacement_due_date(self):
        """Get the date when replacement reminder should be shown."""
        eol_date = self.get_end_of_life_date()
        if eol_date and self.lifespan_reminder_enabled:
            from dateutil.relativedelta import relativedelta
            return eol_date - relativedelta(months=self.lifespan_reminder_months)
        return None

    def is_nearing_end_of_life(self):
        """Check if asset is approaching end-of-life and should show reminder."""
        if not self.lifespan_reminder_enabled:
            return False

        reminder_date = self.get_replacement_due_date()
        if reminder_date:
            from datetime import date
            return date.today() >= reminder_date
        return False

    def days_until_end_of_life(self):
        """Get number of days until end-of-life."""
        eol_date = self.get_end_of_life_date()
        if eol_date:
            from datetime import date
            delta = eol_date - date.today()
            return delta.days
        return None

    def get_age_years(self):
        """Return asset age in years based on purchase_date, or None."""
        if not self.purchase_date:
            return None
        from datetime import date
        delta = date.today() - self.purchase_date
        return delta.days / 365.25

    def has_firmware_update(self):
        """True if a newer firmware version is known."""
        if not self.firmware_version or not self.firmware_latest:
            return False
        return self.firmware_version.strip() != self.firmware_latest.strip()

    def warranty_days_remaining(self):
        """Days until warranty expires, negative if already expired."""
        if not self.warranty_expiry:
            return None
        from datetime import date
        delta = self.warranty_expiry - date.today()
        return delta.days

    def dependency_chain(self, *, direction='downstream', max_depth=10):
        """Phase 16 v7 (v3.17.300): walk this asset's `depends`
        relationship graph and return the dependency chain as a list
        of `Asset` rows in BFS order.

        - `direction='downstream'` returns assets THIS asset depends on
          (transitively): "if I go down, what else takes the hit"
          (well, the reverse — if X depends on Y, X going down takes Y's
          place; we walk source→target edges of `relation_type='depends'`
          where this asset is the source).
        - `direction='upstream'` returns assets that depend on THIS one
          (walks the reverse direction): "if I go down, what else
          breaks?"

        Cycle-safe — visited set prevents infinite loops. Capped at
        `max_depth` levels to keep the response bounded.
        """
        from .models import Relationship
        if direction not in ('downstream', 'upstream'):
            raise ValueError('direction must be "downstream" or "upstream"')
        out = []
        seen = {self.pk}
        queue = [(self.pk, 0)]
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            if direction == 'downstream':
                rels = Relationship.objects.filter(
                    organization=self.organization,
                    source_type='asset', source_id=current,
                    target_type='asset', relation_type='depends',
                ).values_list('target_id', flat=True)
            else:
                rels = Relationship.objects.filter(
                    organization=self.organization,
                    target_type='asset', target_id=current,
                    source_type='asset', relation_type='depends',
                ).values_list('source_id', flat=True)
            for nbr_id in rels:
                if nbr_id in seen:
                    continue
                seen.add(nbr_id)
                queue.append((nbr_id, depth + 1))
        return list(Asset.objects.filter(
            pk__in=(seen - {self.pk}),
            organization=self.organization,
        ).order_by('name'))

    def lifecycle_score(self):
        """Phase 13 v6 (v3.17.263): composite 0-100 replacement-priority
        score. Higher score = stronger candidate for refresh.

        Components (weights chosen to keep total in [0, 100]):
          - Age fraction of lifespan_years        (0-50)
          - Warranty status                       (0-30)
          - Firmware-update available             (0-20)

        Assets without enough data to score one component just get 0
        for that component, so a freshly-imported asset with no
        purchase_date / warranty / firmware fields scores 0 — accurate.
        Returns a dict so the caller (report / API) can show the
        breakdown, not just the total.
        """
        from datetime import date as _d
        breakdown = {'age': 0, 'warranty': 0, 'firmware': 0}

        # Age component — 0 at 0% of lifespan, 50 at 100% (or beyond)
        age_years = self.get_age_years()
        if age_years is not None and self.lifespan_years:
            frac = min(1.0, age_years / float(self.lifespan_years))
            breakdown['age'] = int(round(frac * 50))

        # Warranty component
        # - already expired:        +30
        # - expiring within 90d:    +20
        # - expiring within 365d:   +10
        # - fresh:                  0
        days = self.warranty_days_remaining()
        if days is not None:
            if days < 0:
                breakdown['warranty'] = 30
            elif days <= 90:
                breakdown['warranty'] = 20
            elif days <= 365:
                breakdown['warranty'] = 10

        # Firmware component
        if self.has_firmware_update():
            breakdown['firmware'] = 20

        breakdown['total'] = sum(breakdown.values())
        return breakdown

    def is_warranty_expired(self):
        """True if warranty_expiry is set and in the past."""
        days = self.warranty_days_remaining()
        return days is not None and days < 0

    def is_warranty_expiring_soon(self, within_days=90):
        """True if warranty expires within `within_days` days."""
        days = self.warranty_days_remaining()
        return days is not None and 0 <= days <= within_days

    def has_ports(self):
        """Check if this asset type supports ports."""
        return self.asset_type in [
            'switch', 'router', 'firewall', 'load_balancer',
            'wireless_controller', 'patch_panel', 'fiber_panel',
            'wireless_ap', 'gateway', 'bridge', 'pbx'
        ]

    def get_port_count(self):
        """Get total number of ports."""
        if not self.ports:
            return 0
        return len(self.ports)

    def get_active_port_count(self):
        """Get number of active/enabled ports."""
        if not self.ports:
            return 0
        return len([p for p in self.ports if p.get('status') in ['active', 'in-use']])

    def get_ports_by_vlan(self, vlan_id):
        """Get all ports assigned to a specific VLAN."""
        if not self.ports:
            return []
        return [p for p in self.ports if p.get('vlan') == vlan_id]

    def initialize_ports(self, count, port_type='switch'):
        """Initialize port configuration based on asset type."""
        self.port_count = count
        self.ports = []

        if port_type in ['patch_panel', 'fiber_panel']:
            # Patch panel ports
            for i in range(1, count + 1):
                self.ports.append({
                    'port_number': i,
                    'label': f'Port {i}',
                    'destination': '',
                    'cable_type': 'Cat6',
                    'notes': '',
                    'status': 'available'
                })
        else:
            # Network equipment ports
            for i in range(1, count + 1):
                self.ports.append({
                    'port_number': i,
                    'description': f'Port {i}',
                    'type': 'access',
                    'vlan': '',
                    'speed': '',
                    'status': 'inactive'
                })


class Relationship(BaseModel):
    """
    Generic relationships between any objects (asset<->asset, asset<->doc, etc).
    """
    RELATION_TYPES = [
        ('related', 'Related To'),
        ('parent', 'Parent Of'),
        ('child', 'Child Of'),
        ('depends', 'Depends On'),
        ('documents', 'Documents'),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='relationships')

    # Source object
    source_type = models.CharField(max_length=50)  # e.g., 'asset', 'document', 'password'
    source_id = models.PositiveIntegerField()

    # Target object
    target_type = models.CharField(max_length=50)
    target_id = models.PositiveIntegerField()

    relation_type = models.CharField(max_length=50, choices=RELATION_TYPES, default='related')
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'relationships'
        unique_together = [['source_type', 'source_id', 'target_type', 'target_id', 'relation_type']]
        indexes = [
            models.Index(fields=['organization', 'source_type', 'source_id']),
            models.Index(fields=['organization', 'target_type', 'target_id']),
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_id} {self.relation_type} {self.target_type}:{self.target_id}"
"""
Flexible Asset Type System - Customizable asset tracking engine
Allows users to define their own asset types with custom fields
"""
from django.db import models
from django.contrib.auth.models import User
from core.models import Organization, Tag, BaseModel


class AssetType(BaseModel):
    """
    Defines a custom asset type with configurable fields.
    Examples: Servers, Workstations, Network Devices, Software Licenses, Vehicles, etc.
    """
    ICON_CHOICES = [
        ('fa-server', 'Server'),
        ('fa-desktop', 'Desktop'),
        ('fa-laptop', 'Laptop'),
        ('fa-network-wired', 'Network'),
        ('fa-mobile-alt', 'Mobile'),
        ('fa-database', 'Database'),
        ('fa-cloud', 'Cloud'),
        ('fa-key', 'License'),
        ('fa-hdd', 'Storage'),
        ('fa-print', 'Printer'),
        ('fa-phone', 'Phone'),
        ('fa-car', 'Vehicle'),
        ('fa-building', 'Building'),
        ('fa-plug', 'Equipment'),
        ('fa-box', 'Generic'),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='asset_types')
    name = models.CharField(max_length=100, help_text="e.g., Server, Workstation, Network Device")
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, choices=ICON_CHOICES, default='fa-box')
    color = models.CharField(max_length=7, default='#0d6efd', help_text="Hex color for UI display")

    # Behavior flags
    is_active = models.BooleanField(default=True)
    show_in_menu = models.BooleanField(default=True)

    # Auto-numbering for assets of this type
    auto_number_prefix = models.CharField(max_length=20, blank=True, help_text="e.g., SRV-, WKS-")
    auto_number_next = models.PositiveIntegerField(default=1)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='asset_types_created')

    class Meta:
        db_table = 'asset_types'
        unique_together = [['organization', 'slug']]
        ordering = ['name']

    def __str__(self):
        return f"{self.organization.slug}:{self.name}"

    def get_next_asset_number(self):
        """Generate next auto-numbered asset name."""
        if self.auto_number_prefix:
            number = f"{self.auto_number_prefix}{self.auto_number_next:04d}"
            self.auto_number_next += 1
            self.save(update_fields=['auto_number_next'])
            return number
        return None


class AssetTypeField(BaseModel):
    """
    Defines a custom field for an asset type.
    Supports various field types: text, number, date, dropdown, checkbox, etc.
    """
    FIELD_TYPES = [
        ('text', 'Text'),
        ('textarea', 'Textarea'),
        ('number', 'Number'),
        ('decimal', 'Decimal'),
        ('date', 'Date'),
        ('datetime', 'DateTime'),
        ('checkbox', 'Checkbox'),
        ('dropdown', 'Dropdown'),
        ('url', 'URL'),
        ('email', 'Email'),
        ('phone', 'Phone'),
        ('ip_address', 'IP Address'),
        ('mac_address', 'MAC Address'),
    ]

    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE, related_name='fields')
    name = models.CharField(max_length=100, help_text="Field label")
    slug = models.SlugField(max_length=100, help_text="Internal field name")
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default='text')
    help_text = models.CharField(max_length=255, blank=True)

    # Field configuration
    is_required = models.BooleanField(default=False)
    show_in_list = models.BooleanField(default=True, help_text="Show in asset list view")
    order = models.PositiveIntegerField(default=0, help_text="Display order")

    # For dropdown fields
    dropdown_options = models.JSONField(default=list, blank=True, help_text="List of options for dropdown fields")

    # Validation
    min_value = models.FloatField(null=True, blank=True, help_text="For number/decimal fields")
    max_value = models.FloatField(null=True, blank=True, help_text="For number/decimal fields")
    regex_pattern = models.CharField(max_length=255, blank=True, help_text="Regex validation pattern")

    class Meta:
        db_table = 'asset_type_fields'
        unique_together = [['asset_type', 'slug']]
        ordering = ['order', 'name']

    def __str__(self):
        return f"{self.asset_type.name}.{self.name}"


class FlexibleAsset(BaseModel):
    """
    A flexible asset instance based on an AssetType.
    Stores custom field values in JSON.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='flexible_assets')
    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE, related_name='assets')

    # Core fields (always present)
    name = models.CharField(max_length=255)
    asset_number = models.CharField(max_length=100, blank=True, help_text="Auto-generated or manual")

    # Custom field values stored as JSON
    # Format: {"field_slug": "value", "hostname": "server01", "ip_address": "192.168.1.10"}
    field_values = models.JSONField(default=dict)

    # Common metadata
    tags = models.ManyToManyField(Tag, blank=True, related_name='flexible_assets')
    notes = models.TextField(blank=True)

    # Ownership/tracking
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='flexible_assets_created')
    last_modified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='flexible_assets_modified')

    # Status
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'flexible_assets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'asset_type']),
            models.Index(fields=['asset_number']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        if self.asset_number:
            return f"{self.asset_number}: {self.name}"
        return self.name

    def get_field_value(self, field_slug, default=None):
        """Get value for a specific custom field."""
        return self.field_values.get(field_slug, default)

    def set_field_value(self, field_slug, value):
        """Set value for a specific custom field."""
        self.field_values[field_slug] = value

    def get_all_fields_with_values(self):
        """
        Get all field definitions with their current values.
        Returns list of dicts with field metadata and values.
        """
        fields = []
        for field in self.asset_type.fields.all():
            fields.append({
                'field': field,
                'value': self.get_field_value(field.slug),
            })
        return fields


# ============================================================================
# Hardware Vendor & Equipment Database
# ============================================================================

class Vendor(BaseModel):
    """
    Hardware vendor/manufacturer (Dell, HP, Cisco, etc).
    Global model - shared across all organizations.
    """
    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Vendor name (e.g., Dell, HP, Cisco)"
    )

    slug = models.SlugField(
        max_length=200,
        unique=True,
        help_text="URL-safe identifier"
    )

    website = models.URLField(
        blank=True,
        help_text="Vendor website URL"
    )

    support_url = models.URLField(
        blank=True,
        help_text="Vendor support/documentation URL"
    )

    support_phone = models.CharField(
        max_length=50,
        blank=True,
        help_text="Vendor support phone number"
    )

    description = models.TextField(
        blank=True,
        help_text="Brief description of vendor"
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Whether vendor is actively used"
    )

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional vendor metadata"
    )

    # ---- Procurement-specific vendor relationship metadata (Phase 4.3) ----
    PAYMENT_TERMS_CHOICES = [
        ('net_15', 'Net 15'),
        ('net_30', 'Net 30'),
        ('net_45', 'Net 45'),
        ('net_60', 'Net 60'),
        ('cod', 'Cash on Delivery'),
        ('prepaid', 'Prepaid'),
        ('credit_card', 'Credit Card'),
    ]
    CONTACT_METHOD_CHOICES = [
        ('email', 'Email'),
        ('phone', 'Phone'),
        ('portal', 'Vendor Portal'),
    ]

    default_lead_time_days = models.PositiveSmallIntegerField(
        default=7,
        help_text='Typical days from PO sent to delivery. Used for auto-replenish '
                  'expected_delivery_date.',
    )
    payment_terms = models.CharField(
        max_length=40, blank=True,
        choices=PAYMENT_TERMS_CHOICES,
    )
    preferred_contact_method = models.CharField(
        max_length=20, blank=True,
        choices=CONTACT_METHOD_CHOICES,
    )
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    billing_address = models.TextField(blank=True)
    account_number = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    distributor_provider = models.CharField(
        max_length=40, blank=True,
        help_text='Optional link to existing distributor integration: ingram / pax8 / synnex / etc.',
    )

    class Meta:
        db_table = 'vendors'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.name

    def get_logo_attachment(self):
        """Get vendor logo from Attachment model."""
        from files.models import Attachment
        return Attachment.objects.filter(
            entity_type='vendor',
            entity_id=self.id
        ).first()


class EquipmentModel(BaseModel):
    """
    Specific equipment model from a vendor (e.g., Dell PowerEdge R740).
    Global model - shared across all organizations.
    """
    EQUIPMENT_TYPES = [
        ('server', 'Server'),
        ('workstation', 'Workstation'),
        ('laptop', 'Laptop'),
        ('switch', 'Network Switch'),
        ('router', 'Router'),
        ('firewall', 'Firewall'),
        ('access_point', 'Wireless Access Point'),
        ('storage', 'Storage Device'),
        ('ups', 'UPS'),
        ('pdu', 'Power Distribution Unit'),
        ('patch_panel', 'Patch Panel'),
        ('kvm', 'KVM Switch'),
        ('phone', 'IP Phone'),
        ('camera', 'IP Camera'),
        ('other', 'Other'),
    ]

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name='equipment_models',
        help_text="Equipment manufacturer"
    )

    model_name = models.CharField(
        max_length=200,
        help_text="Marketing/display name (e.g., PowerEdge R740)"
    )

    model_number = models.CharField(
        max_length=200,
        blank=True,
        help_text="Specific model/part number (e.g., R740XD)"
    )

    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-safe identifier"
    )

    equipment_type = models.CharField(
        max_length=50,
        choices=EQUIPMENT_TYPES,
        help_text="Type of equipment"
    )

    description = models.TextField(
        blank=True,
        help_text="Equipment description"
    )

    # Rack mounting
    is_rackmount = models.BooleanField(
        default=False,
        help_text="Whether equipment is rackmountable"
    )

    rack_units = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Height in rack units (1U, 2U, etc.)"
    )

    # Physical specifications (JSONField for flexibility)
    specifications = models.JSONField(
        default=dict,
        blank=True,
        help_text="""Equipment specifications in JSON format"""
    )

    # Data sheet and documentation
    datasheet_url = models.URLField(
        blank=True,
        help_text="Link to vendor datasheet/spec sheet"
    )

    documentation_url = models.URLField(
        blank=True,
        help_text="Link to user manual/documentation"
    )

    # EOL tracking
    release_date = models.DateField(
        null=True,
        blank=True,
        help_text="Product release date"
    )

    eol_date = models.DateField(
        null=True,
        blank=True,
        help_text="End of life date"
    )

    eos_date = models.DateField(
        null=True,
        blank=True,
        help_text="End of support date"
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Whether model is actively used/sold"
    )

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional model-specific metadata"
    )

    class Meta:
        db_table = 'equipment_models'
        ordering = ['vendor__name', 'model_name']
        unique_together = [['vendor', 'model_name']]
        indexes = [
            models.Index(fields=['vendor', 'model_name']),
            models.Index(fields=['equipment_type']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.vendor.name} {self.model_name}"

    def get_display_name(self):
        """Full display name with vendor."""
        return str(self)

    def get_images(self):
        """Get all product images."""
        from files.models import Attachment
        return Attachment.objects.filter(
            entity_type='equipment_model',
            entity_id=self.id
        ).order_by('created_at')

    def get_primary_image(self):
        """Get first/primary product image."""
        return self.get_images().first()

    def has_port_configuration(self):
        """Check if equipment has port configuration."""
        return self.equipment_type in ['switch', 'router', 'firewall', 'patch_panel']


class NetworkPortConfiguration(BaseModel):
    """
    Port configuration for switches, routers, firewalls.
    Organization-scoped - can be customized per organization.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        help_text="Organization this configuration belongs to"
    )

    equipment_model = models.ForeignKey(
        EquipmentModel,
        on_delete=models.CASCADE,
        related_name='port_configurations',
        help_text="Equipment model this configuration is for"
    )

    # Can also be linked to specific asset instance
    asset = models.ForeignKey(
        'Asset',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='port_configurations',
        help_text="Specific asset instance (optional)"
    )

    configuration_name = models.CharField(
        max_length=200,
        help_text="Configuration name (e.g., 'Default', 'Production VLAN Setup')"
    )

    # Port definitions stored as JSON
    ports = models.JSONField(
        default=list,
        help_text="Port configuration array"
    )

    # VLAN definitions
    vlans = models.JSONField(
        default=list,
        blank=True,
        help_text="VLAN definitions"
    )

    notes = models.TextField(
        blank=True,
        help_text="Configuration notes"
    )

    is_template = models.BooleanField(
        default=False,
        help_text="Whether this is a template configuration"
    )

    objects = OrganizationManager()

    class Meta:
        db_table = 'network_port_configurations'
        ordering = ['equipment_model', 'configuration_name']
        indexes = [
            models.Index(fields=['equipment_model']),
            models.Index(fields=['asset']),
            models.Index(fields=['is_template']),
        ]

    def __str__(self):
        if self.asset:
            return f"{self.asset.name} - {self.configuration_name}"
        return f"{self.equipment_model} - {self.configuration_name}"

    def get_port_count(self):
        """Get total number of ports."""
        return len(self.ports)

    def get_active_port_count(self):
        """Get number of active/enabled ports."""
        return len([p for p in self.ports if p.get('status') == 'active'])

    def get_ports_by_vlan(self, vlan_id):
        """Get all ports assigned to a specific VLAN."""
        return [p for p in self.ports if p.get('vlan') == vlan_id]


# ---------------------------------------------------------------------------
# Phase 13 v4 (v3.17.261) — RMA tracking
# ---------------------------------------------------------------------------

class RMAReturn(BaseModel):
    """Return / replace lifecycle tracking. Optional links to a source
    PurchaseOrder (where the bad unit came from) and an Asset (the
    physical thing being returned). Status drives the timeline:
    open → sent → received_by_vendor → replaced/refunded → closed.
    Cancelled is also a terminal state."""
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('sent', 'Sent to vendor'),
        ('received_by_vendor', 'Received by vendor'),
        ('replaced', 'Replacement received'),
        ('refunded', 'Refunded'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ]
    TERMINAL_STATUSES = {'replaced', 'refunded', 'closed', 'cancelled'}

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        related_name='rma_returns',
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rma_returns',
        help_text='Optional — the physical unit being returned.',
    )
    purchase_order = models.ForeignKey(
        'psa.PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rma_returns',
        help_text='Optional — the PO the bad unit was sourced from.',
    )
    vendor = models.ForeignKey(
        Vendor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rma_returns',
    )
    rma_number = models.CharField(max_length=80, blank=True,
        help_text='Vendor-issued RMA / case number.')
    serial_number = models.CharField(max_length=120, blank=True)
    reason = models.CharField(max_length=200, blank=True,
        help_text='Short reason: DOA, defective, wrong-spec, etc.')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='open')
    opened_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    replacement_serial = models.CharField(max_length=120, blank=True,
        help_text='Serial of the replacement unit (when status=replaced).')
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text='Refund amount in PO currency (when status=refunded).')

    objects = OrganizationManager()

    class Meta:
        db_table = 'rma_returns'
        ordering = ['-opened_at']
        indexes = [
            models.Index(fields=['organization', '-opened_at']),
            models.Index(fields=['status']),
            models.Index(fields=['vendor']),
        ]

    def __str__(self):
        return f'RMA {self.rma_number or self.pk} ({self.get_status_display()})'

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def transition(self, new_status: str, *, when=None):
        """Move to ``new_status`` and stamp the matching timestamp. Raises
        ValueError if the transition is invalid. Caller is responsible for
        calling save()."""
        from django.utils import timezone
        if new_status not in dict(self.STATUS_CHOICES):
            raise ValueError(f'Unknown RMA status: {new_status}')
        ts = when or timezone.now()
        if new_status == 'sent':
            self.sent_at = ts
        elif new_status == 'received_by_vendor':
            self.received_at = ts
        elif new_status in self.TERMINAL_STATUSES:
            self.closed_at = ts
        self.status = new_status
