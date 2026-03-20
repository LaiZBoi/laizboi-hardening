"""
UniFi Network Application API provider.
Authentication:
  - Primary:  X-API-Key header (official API v1, UniFi OS 3.x+)
  - Optional: username + password (session cookie, legacy API for WLANs/VLANs/clients)
Reference: https://help.ui.com/hc/en-us/articles/30076656117655
"""
import logging
import urllib3
import requests

logger = logging.getLogger(__name__)


class UnifiProvider:
    """Read-only UniFi Network Application API client."""

    def __init__(self, host: str, api_key: str, verify_ssl: bool = False,
                 username: str = '', password: str = ''):
        self.host = host.rstrip('/')
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.username = username
        self.password = password
        self._session_cookie = None

        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': api_key,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ------------------------------------------------------------------
    # Official API v1 (X-API-Key)
    # ------------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> dict:
        url = f"{self.host}{path}"
        resp = self.session.get(url, verify=self.verify_ssl, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def test_connection(self) -> dict:
        """Test credentials by fetching the site list via official API."""
        try:
            data = self._get('/proxy/network/integration/v1/sites')
            sites = data.get('data', [])
            msg = f"Connected. Found {len(sites)} site(s)."
            if self.username and self.password:
                legacy_ok = self._legacy_login()
                msg += f" Legacy API ({'OK — WLANs/VLANs enabled' if legacy_ok else 'failed — check username/password'})."
            return {'success': True, 'message': msg}
        except requests.exceptions.SSLError:
            return {'success': False, 'error': 'SSL error — try disabling SSL verification for self-signed certificates.'}
        except requests.exceptions.ConnectionError as e:
            return {'success': False, 'error': f'Cannot reach host: {e}'}
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                return {'success': False, 'error': 'Authentication failed — check your API key.'}
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_sites(self) -> list:
        try:
            data = self._get('/proxy/network/integration/v1/sites')
            return data.get('data', [])
        except Exception as e:
            logger.warning(f"UniFi get_sites failed: {e}")
            return []

    def get_devices(self, site_id: str) -> list:
        """site_id must be the siteId UUID from the official API."""
        if not site_id:
            return []
        try:
            data = self._get(f'/proxy/network/integration/v1/sites/{site_id}/devices')
            return data.get('data', [])
        except Exception as e:
            logger.warning(f"UniFi get_devices({site_id}) failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Legacy API (session cookie — needs username + password)
    # ------------------------------------------------------------------

    def _legacy_login(self) -> bool:
        """Log in to the legacy API and store session cookie."""
        try:
            # Try UniFi OS path first
            for login_path in ('/api/auth/login', '/api/login'):
                try:
                    resp = requests.post(
                        f"{self.host}{login_path}",
                        json={'username': self.username, 'password': self.password},
                        verify=self.verify_ssl,
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        self._session_cookie = resp.cookies
                        return True
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"UniFi legacy login failed: {e}")
        return False

    def _legacy_get(self, path: str) -> dict:
        """GET via legacy session-cookie API."""
        if not self._session_cookie:
            if not self._legacy_login():
                return {}
        resp = requests.get(
            f"{self.host}{path}",
            cookies=self._session_cookie,
            verify=self.verify_ssl,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_wlans(self, site_ref: str) -> list:
        """Get wireless networks. site_ref = internalReference (e.g. 'default').
        Requires username/password (legacy API)."""
        if not (self.username and self.password):
            return []
        try:
            data = self._legacy_get(f'/proxy/network/api/s/{site_ref}/rest/wlanconf')
            return data.get('data', [])
        except Exception as e:
            logger.warning(f"UniFi get_wlans({site_ref}) failed: {e}")
            return []

    def get_vlans(self, site_ref: str) -> list:
        """Get network/VLAN config. site_ref = internalReference (e.g. 'default').
        Requires username/password (legacy API)."""
        if not (self.username and self.password):
            return []
        try:
            data = self._legacy_get(f'/proxy/network/api/s/{site_ref}/rest/networkconf')
            return data.get('data', [])
        except Exception as e:
            logger.warning(f"UniFi get_vlans({site_ref}) failed: {e}")
            return []

    def get_firewall_rules(self, site_ref: str) -> list:
        """Get firewall rules. site_ref = internalReference (e.g. 'default').
        Requires username/password (legacy API)."""
        if not (self.username and self.password):
            return []
        try:
            data = self._legacy_get(f'/proxy/network/api/s/{site_ref}/rest/firewallrule')
            return data.get('data', [])
        except Exception as e:
            logger.warning(f"UniFi get_firewall_rules({site_ref}) failed: {e}")
            return []

    def get_traffic_rules(self, site_ref: str, site_id: str = '') -> list:
        """Get Traffic Rules (UniFi OS 3.x+). Tries v2 API first, falls back to legacy REST.
        Requires username/password."""
        if not (self.username and self.password):
            return []
        # Try v2 API with UUID first (most reliable on OS 3.x), then short name, then legacy REST
        paths = []
        if site_id:
            paths.append(f'/proxy/network/v2/api/site/{site_id}/trafficrules')
        paths += [
            f'/proxy/network/v2/api/site/{site_ref}/trafficrules',
            f'/proxy/network/api/s/{site_ref}/rest/trafficrule',
        ]
        for path in paths:
            try:
                raw = self._legacy_get(path)
                items = raw if isinstance(raw, list) else raw.get('data', raw.get('trafficRules', []))
                if items:
                    return items
            except Exception:
                continue
        return []

    def get_firewall_policies(self, site_ref: str, site_id: str = '') -> list:
        """Get zone-based Firewall Policies (UniFi OS 3.x+). Requires username/password."""
        if not (self.username and self.password):
            return []
        paths = []
        if site_id:
            paths.append(f'/proxy/network/v2/api/site/{site_id}/firewall/policies')
        paths += [
            f'/proxy/network/v2/api/site/{site_ref}/firewall/policies',
            f'/proxy/network/api/s/{site_ref}/rest/firewallpolicy',
        ]
        for path in paths:
            try:
                raw = self._legacy_get(path)
                items = raw if isinstance(raw, list) else raw.get('data', raw.get('policies', []))
                if items:
                    return items
            except Exception:
                continue
        return []

    def get_device_serials(self, site_ref: str) -> dict:
        """Get MAC→serial mapping from legacy stat/device endpoint.
        Requires username/password."""
        if not (self.username and self.password):
            return {}
        try:
            data = self._legacy_get(f'/proxy/network/api/s/{site_ref}/stat/device')
            result = {}
            for d in data.get('data', []):
                mac = d.get('mac', '')
                serial = d.get('serial', '')
                if mac and serial:
                    result[mac.lower()] = serial
            return result
        except Exception as e:
            logger.warning(f"UniFi get_device_serials({site_ref}) failed: {e}")
            return {}

    def get_client_count(self, site_ref: str) -> int:
        """Get active client count. Requires username/password (legacy API)."""
        if not (self.username and self.password):
            return 0
        try:
            data = self._legacy_get(f'/proxy/network/api/s/{site_ref}/stat/sta')
            return len(data.get('data', []))
        except Exception as e:
            logger.warning(f"UniFi get_client_count({site_ref}) failed: {e}")
            return 0

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync(self) -> dict:
        """Pull all data and return structured summary."""
        sites = self.get_sites()
        has_legacy = bool(self.username and self.password)
        result = {'sites': [], 'has_legacy_data': has_legacy}

        for site in sites:
            # Official API uses 'siteId' (UUID); legacy API uses 'internalReference' (short name)
            site_id = site.get('siteId') or site.get('id') or ''
            site_ref = site.get('internalReference') or site.get('name') or 'default'
            site_name = (site.get('meta') or {}).get('desc') or site.get('name') or site_ref

            devices = self.get_devices(site_id)
            serials = self.get_device_serials(site_ref)
            # Merge serial into each device by MAC
            for d in devices:
                mac = (d.get('macAddress') or d.get('mac') or '').lower()
                if mac and mac in serials and not d.get('serial'):
                    d['serial'] = serials[mac]
            wlans = self.get_wlans(site_ref)
            vlans = self.get_vlans(site_ref)
            firewall_rules = self.get_firewall_rules(site_ref)
            firewall_policies = self.get_firewall_policies(site_ref, site_id=site_id)
            traffic_rules = self.get_traffic_rules(site_ref, site_id=site_id)
            client_count = self.get_client_count(site_ref)

            type_counts = {}
            for d in devices:
                dtype = d.get('type', 'unknown')
                type_counts[dtype] = type_counts.get(dtype, 0) + 1

            result['sites'].append({
                'id': site_id,
                'name': site_name,
                'devices': devices,
                'device_type_counts': type_counts,
                'wlans': wlans,
                'vlans': vlans,
                'firewall_rules': firewall_rules,
                'firewall_policies': firewall_policies,
                'traffic_rules': traffic_rules,
                'client_count': client_count,
            })
        return result
