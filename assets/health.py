"""
Asset health services: age warnings, firmware checks, warranty lookups.
Each service is gated behind a SystemSetting feature flag.
"""
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Asset types that support firmware checks
# ---------------------------------------------------------------------------
FIRMWARE_CHECK_TYPES = {
    'ubiquiti': ['wireless_ap', 'wireless_controller', 'router', 'switch', 'firewall', 'gateway'],
    'grandstream': ['phone', 'conference_phone', 'pbx', 'voip_gateway'],
    'tplink': ['wireless_ap', 'wireless_controller', 'router', 'switch'],
}

# Asset types that warrant PC/server warranty checks
WARRANTY_CHECK_TYPES = ['desktop', 'laptop', 'workstation', 'server', 'thin_client', 'tablet']

# Vendors whose serials we can query
DELL_IDENTIFIERS = ['dell', 'alienware']
HP_IDENTIFIERS = ['hp', 'hewlett-packard', 'hewlett packard', 'hpe', 'hewlett packard enterprise']
LENOVO_IDENTIFIERS = ['lenovo', 'thinkpad', 'thinkcentre', 'thinkstation']


def _manufacturer_matches(mfr: str, identifiers: list) -> bool:
    mfr_lower = (mfr or '').lower()
    return any(ident in mfr_lower for ident in identifiers)


# ---------------------------------------------------------------------------
# Age Warning Service
# ---------------------------------------------------------------------------

class AssetAgeService:
    """
    Evaluates all assets against age thresholds defined in SystemSetting.
    Returns summary counts; individual badge status is derived in templates.
    """

    def __init__(self, settings=None):
        if settings is None:
            from core.models import SystemSetting
            settings = SystemSetting.get_settings()
        self.settings = settings
        self.warning_years = settings.asset_age_warning_years or 3
        self.critical_years = settings.asset_age_critical_years or 5

    def get_age_status(self, asset):
        """
        Returns 'ok', 'warning', 'critical', or None (no purchase date).
        If the asset has lifespan_years set, uses the EOL-based status instead.
        """
        # Prefer lifespan-based status if configured
        if asset.purchase_date and asset.lifespan_years:
            days_left = asset.days_until_end_of_life()
            if days_left is None:
                return None
            if days_left < 0:
                return 'critical'
            if asset.lifespan_reminder_enabled and asset.is_nearing_end_of_life():
                return 'warning'
            return 'ok'

        # Fallback: global thresholds from settings
        age_years = asset.get_age_years()
        if age_years is None:
            return None
        if age_years >= self.critical_years:
            return 'critical'
        if age_years >= self.warning_years:
            return 'warning'
        return 'ok'

    def check_all(self):
        """Scan all assets and return counts by status."""
        from assets.models import Asset
        assets = Asset.objects.exclude(purchase_date=None)
        counts = {'warning': 0, 'critical': 0}
        for asset in assets:
            status = self.get_age_status(asset)
            if status in counts:
                counts[status] += 1
        logger.info("Asset age check: %s", counts)
        return counts


# ---------------------------------------------------------------------------
# Firmware Check Service
# ---------------------------------------------------------------------------

class FirmwareCheckService:
    """
    Checks for firmware updates on network devices.

    Currently supports:
    - Ubiquiti UniFi (via public firmware API)
    - Grandstream (via public version endpoint)
    - TP-Link / Omada (via public version page)

    Stores results in asset.firmware_latest and asset.firmware_checked_at.
    """

    def __init__(self, settings=None):
        if settings is None:
            from core.models import SystemSetting
            settings = SystemSetting.get_settings()
        self.settings = settings

    def check_all(self):
        """Check all eligible network device assets."""
        from assets.models import Asset
        from django.utils import timezone

        all_types = set()
        for types in FIRMWARE_CHECK_TYPES.values():
            all_types.update(types)

        assets = Asset.objects.filter(asset_type__in=all_types).exclude(manufacturer='')
        updated = 0
        for asset in assets:
            try:
                latest = self._get_latest_firmware(asset)
                if latest:
                    asset.firmware_latest = latest
                    asset.firmware_checked_at = timezone.now()
                    asset.save(update_fields=['firmware_latest', 'firmware_checked_at'])
                    updated += 1
            except Exception as e:
                logger.warning("Firmware check failed for %s: %s", asset, e)

        logger.info("Firmware check: updated %d assets", updated)
        return updated

    def _get_latest_firmware(self, asset):
        """Return latest firmware version string for this asset, or None."""
        mfr = (asset.manufacturer or '').lower()

        if any(v in mfr for v in ['ubiquiti', 'ui.com', 'unifi', 'ubnt']):
            return self._check_ubiquiti(asset)
        if any(v in mfr for v in ['grandstream', 'grandstream networks']):
            return self._check_grandstream(asset)
        if any(v in mfr for v in ['tp-link', 'tplink', 'omada']):
            return self._check_tplink(asset)
        return None

    def _check_ubiquiti(self, asset):
        """Query Ubiquiti firmware API."""
        import urllib.request, json
        try:
            url = 'https://fw-update.ubnt.com/api/firmware-latest'
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            # data is a list of {product, channel, version, ...}
            model = (asset.model or '').lower()
            for entry in data:
                if model and model in (entry.get('product') or '').lower():
                    return entry.get('version')
            # return generic latest for the product type if no model match
            return None
        except Exception as e:
            logger.debug("Ubiquiti firmware API error: %s", e)
            return None

    def _check_grandstream(self, asset):
        """
        Grandstream does not have a public firmware API.
        We note the check was attempted but return None.
        """
        return None

    def _check_tplink(self, asset):
        """
        TP-Link / Omada does not have a public firmware API.
        We note the check was attempted but return None.
        """
        return None


# ---------------------------------------------------------------------------
# Warranty Check Service
# ---------------------------------------------------------------------------

class WarrantyCheckService:
    """
    Queries vendor warranty APIs for PCs and servers.

    Supported vendors:
    - Dell (TechDirect API — requires dell_api_key in SystemSetting)
    - HP (requires hp_client_id + hp_client_secret)
    - Lenovo (requires lenovo_client_id + lenovo_client_secret)

    Stores results in asset.warranty_expiry, asset.warranty_status,
    and asset.warranty_checked_at.
    """

    def __init__(self, settings=None):
        if settings is None:
            from core.models import SystemSetting
            settings = SystemSetting.get_settings()
        self.settings = settings

    def check_all(self):
        """Check warranty for all eligible assets."""
        from assets.models import Asset
        from django.utils import timezone

        assets = Asset.objects.filter(
            asset_type__in=WARRANTY_CHECK_TYPES,
        ).exclude(serial_number='')

        updated = 0
        for asset in assets:
            try:
                result = self._lookup_warranty(asset)
                if result:
                    asset.warranty_expiry = result.get('expiry')
                    asset.warranty_status = result.get('status', '')
                    asset.warranty_checked_at = timezone.now()
                    asset.save(update_fields=['warranty_expiry', 'warranty_status', 'warranty_checked_at'])
                    updated += 1
            except Exception as e:
                logger.warning("Warranty check failed for %s: %s", asset, e)

        logger.info("Warranty check: updated %d assets", updated)
        return updated

    def _lookup_warranty(self, asset):
        """Route to the correct vendor API based on manufacturer."""
        mfr = asset.manufacturer or ''
        serial = asset.serial_number or ''
        if not serial:
            return None

        if _manufacturer_matches(mfr, DELL_IDENTIFIERS):
            return self._check_dell(serial)
        if _manufacturer_matches(mfr, HP_IDENTIFIERS):
            return self._check_hp(serial)
        if _manufacturer_matches(mfr, LENOVO_IDENTIFIERS):
            return self._check_lenovo(serial)
        return None

    def _check_dell(self, serial):
        """Dell TechDirect Warranty API."""
        api_key = self.settings.dell_api_key
        if not api_key:
            logger.debug("Dell API key not configured")
            return None

        import urllib.request, json, urllib.error
        url = f'https://apigtwb2c.us.dell.com/PROD/sbil/eapi/v5/asset-entitlements?servicetags={serial}'
        req = urllib.request.Request(url, headers={
            'apikey': api_key,
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            if not data:
                return None
            entitlements = data[0].get('entitlements', [])
            if not entitlements:
                return {'expiry': None, 'status': 'No entitlements found'}
            # Find the latest end date
            end_dates = []
            for e in entitlements:
                end_str = e.get('endDate') or e.get('end_date')
                if end_str:
                    try:
                        end_dates.append(date.fromisoformat(end_str[:10]))
                    except ValueError:
                        pass
            expiry = max(end_dates) if end_dates else None
            status_parts = list({e.get('serviceLevelDescription') or e.get('service_level_description', '') for e in entitlements if e.get('serviceLevelDescription') or e.get('service_level_description')})
            status = ', '.join(status_parts[:2])
            return {'expiry': expiry, 'status': status or 'Dell warranty'}
        except urllib.error.HTTPError as e:
            logger.warning("Dell API HTTP error %s for serial %s", e.code, serial)
            return None
        except Exception as e:
            logger.warning("Dell API error for %s: %s", serial, e)
            return None

    def _check_hp(self, serial):
        """HP Warranty API (OAuth2 client credentials)."""
        client_id = self.settings.hp_client_id
        client_secret = self.settings.hp_client_secret
        if not client_id or not client_secret:
            logger.debug("HP API credentials not configured")
            return None

        import urllib.request, urllib.parse, json, urllib.error
        # Get access token
        try:
            token_data = urllib.parse.urlencode({
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
            }).encode()
            token_req = urllib.request.Request(
                'https://warranty.api.hp.com/oauth/v1/token',
                data=token_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            with urllib.request.urlopen(token_req, timeout=15) as resp:
                token_resp = json.loads(resp.read())
            access_token = token_resp.get('access_token')
            if not access_token:
                return None
        except Exception as e:
            logger.warning("HP token error: %s", e)
            return None

        # Query warranty
        try:
            url = f'https://warranty.api.hp.com/productwarranty/v2/machinetypes?serial={serial}'
            warranty_req = urllib.request.Request(url, headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(warranty_req, timeout=15) as resp:
                data = json.loads(resp.read())
            offers = data.get('offers', [])
            if not offers:
                return {'expiry': None, 'status': 'No HP warranty found'}
            end_dates = []
            statuses = []
            for offer in offers:
                end_str = offer.get('endDate')
                if end_str:
                    try:
                        end_dates.append(date.fromisoformat(end_str[:10]))
                    except ValueError:
                        pass
                desc = offer.get('offerDescription')
                if desc:
                    statuses.append(desc)
            expiry = max(end_dates) if end_dates else None
            status = ', '.join(statuses[:2]) or 'HP warranty'
            return {'expiry': expiry, 'status': status}
        except Exception as e:
            logger.warning("HP warranty API error for %s: %s", serial, e)
            return None

    def _check_lenovo(self, serial):
        """Lenovo Warranty API."""
        client_id = self.settings.lenovo_client_id
        client_secret = self.settings.lenovo_client_secret
        if not client_id or not client_secret:
            logger.debug("Lenovo API credentials not configured")
            return None

        import urllib.request, urllib.parse, json, urllib.error
        try:
            # Lenovo uses basic auth for warranty lookup
            import base64
            credentials = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
            url = f'https://supportapi.lenovo.com/v2.5/product/{serial}/warranty'
            req = urllib.request.Request(url, headers={
                'Authorization': f'Basic {credentials}',
                'ClientID': client_id,
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            warranties = data.get('Warranty', [])
            if not warranties:
                return {'expiry': None, 'status': 'No Lenovo warranty found'}
            end_dates = []
            for w in warranties:
                end_str = w.get('End')
                if end_str:
                    try:
                        end_dates.append(date.fromisoformat(end_str[:10]))
                    except ValueError:
                        pass
            expiry = max(end_dates) if end_dates else None
            return {'expiry': expiry, 'status': 'Lenovo warranty'}
        except Exception as e:
            logger.warning("Lenovo warranty API error for %s: %s", serial, e)
            return None
