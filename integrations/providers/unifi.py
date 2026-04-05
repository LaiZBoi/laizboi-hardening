"""
UniFi Network Application API provider.
Authentication:
  - Primary:  X-API-Key header (official API v1, UniFi OS 3.x+)
  - Optional: username + password (session cookie, legacy API for WLANs/VLANs/clients)
Reference: https://help.ui.com/hc/en-us/articles/30076656117655

UniFi Site Manager (cloud) provider.
Authentication:
  - API key from https://account.ui.com → API Keys
  - Base URL: https://api.ui.com
Reference: https://developer.ui.com/site-manager/v1.0.0/gettingstarted
"""
import logging
import urllib3
import requests

logger = logging.getLogger(__name__)

SITE_MANAGER_BASE = 'https://api.ui.com'


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
        self._auth_token = ''
        self._csrf_token = ''

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
        """Log in to the legacy API and store session cookie/token/csrf."""
        for login_path in ('/api/auth/login', '/api/login', '/proxy/network/api/auth/login'):
            try:
                resp = requests.post(
                    f"{self.host}{login_path}",
                    json={'username': self.username, 'password': self.password},
                    verify=self.verify_ssl,
                    timeout=10,
                )
                if resp.status_code == 200:
                    self._session_cookie = resp.cookies
                    # UniFi OS 3.x/4.x returns token and csrf_token in JSON body
                    try:
                        body = resp.json()
                        self._auth_token = body.get('token') or body.get('access_token') or ''
                        self._csrf_token = (body.get('csrf_token') or body.get('csrfToken') or
                                            resp.cookies.get('TOKEN') or '')
                    except Exception:
                        self._auth_token = ''
                        self._csrf_token = ''
                    logger.debug(f"UniFi legacy login OK via {login_path}")
                    return True
                logger.debug(f"UniFi login {login_path} returned {resp.status_code}")
            except Exception as e:
                logger.debug(f"UniFi login attempt {login_path} failed: {e}")
                continue
        logger.warning("UniFi legacy login failed on all paths — check username/password")
        return False

    def _legacy_get(self, path: str) -> dict:
        """GET via legacy session-cookie API (with token/CSRF header support for UniFi OS 3.x/4.x)."""
        if not self._session_cookie:
            if not self._legacy_login():
                return {}
        headers = {}
        if getattr(self, '_auth_token', ''):
            headers['Authorization'] = f'Bearer {self._auth_token}'
        if getattr(self, '_csrf_token', ''):
            headers['X-Csrf-Token'] = self._csrf_token
        resp = requests.get(
            f"{self.host}{path}",
            cookies=self._session_cookie,
            headers=headers,
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
        """Get Traffic Rules (UniFi OS 3.x+). Tries v2 API first, falls back to legacy REST."""
        # Build path list — try UUID first, then short name, then legacy REST
        paths_v2 = []
        if site_id:
            paths_v2.append(f'/proxy/network/v2/api/site/{site_id}/trafficrules')
        paths_v2.append(f'/proxy/network/v2/api/site/{site_ref}/trafficrules')
        paths_legacy = [f'/proxy/network/api/s/{site_ref}/rest/trafficrule']

        # Try v2 paths with API key first (no username/password required)
        for path in paths_v2:
            try:
                raw = self._get(path)
                items = (raw if isinstance(raw, list) else
                         next((raw[k] for k in ('data', 'trafficRules', 'traffic_rules', 'rules')
                               if k in raw and isinstance(raw[k], list)), []))
                if items:
                    logger.debug(f"UniFi traffic rules (API key) found via {path}: {len(items)} items")
                    return items
            except Exception as e:
                logger.debug(f"UniFi traffic rules (API key) path {path} failed: {e}")

        # Fallback: legacy session cookie auth
        if self.username and self.password:
            for path in paths_v2 + paths_legacy:
                try:
                    raw = self._legacy_get(path)
                    items = (raw if isinstance(raw, list) else
                             next((raw[k] for k in ('data', 'trafficRules', 'traffic_rules', 'rules')
                                   if k in raw and isinstance(raw[k], list)), []))
                    if items:
                        logger.debug(f"UniFi traffic rules (legacy) found via {path}: {len(items)} items")
                        return items
                except Exception as e:
                    logger.debug(f"UniFi traffic rules (legacy) path {path} failed: {e}")
        return []

    def get_firewall_policies(self, site_ref: str, site_id: str = '') -> list:
        """Get zone-based Firewall Policies (UniFi OS 3.x+).
        UniFi 8.x renamed the endpoint from /firewall/policies to /firewall/zone-policies."""
        paths_v2 = []
        if site_id:
            paths_v2.append(f'/proxy/network/v2/api/site/{site_id}/firewall/zone-policies')
            paths_v2.append(f'/proxy/network/v2/api/site/{site_id}/firewall/policies')
        paths_v2 += [
            f'/proxy/network/v2/api/site/{site_ref}/firewall/zone-policies',
            f'/proxy/network/v2/api/site/{site_ref}/firewall/policies',
        ]
        paths_legacy = [f'/proxy/network/api/s/{site_ref}/rest/firewallpolicy']

        def _parse(raw):
            if isinstance(raw, list):
                return raw
            # UniFi returns different wrapper keys across versions
            for key in ('data', 'policies', 'zonePolicies', 'zone_policies',
                        'firewallPolicies', 'firewall_policies', 'rules'):
                if key in raw and isinstance(raw[key], list):
                    return raw[key]
            return []

        # Try v2 paths with API key first
        for path in paths_v2:
            try:
                items = _parse(self._get(path))
                if items:
                    logger.debug(f"UniFi firewall policies (API key) found via {path}: {len(items)} items")
                    return items
            except Exception as e:
                logger.debug(f"UniFi firewall policies (API key) path {path} failed: {e}")

        # Fallback: legacy session cookie auth
        if self.username and self.password:
            for path in paths_v2 + paths_legacy:
                try:
                    items = _parse(self._legacy_get(path))
                    if items:
                        logger.debug(f"UniFi firewall policies (legacy) found via {path}: {len(items)} items")
                        return items
                except Exception as e:
                    logger.debug(f"UniFi firewall policies (legacy) path {path} failed: {e}")
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
        legacy_login_ok = self._legacy_login() if has_legacy else False
        result = {'sites': [], 'has_legacy_data': has_legacy, 'legacy_login_ok': legacy_login_ok}

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


class UnifiCloudProvider:
    """
    UniFi Site Manager cloud API client.
    Uses the api.ui.com REST API with an X-API-Key generated at account.ui.com.
    Reference: https://developer.ui.com/site-manager/v1.0.0/gettingstarted
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': api_key,
            'Accept': 'application/json',
        })

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{SITE_MANAGER_BASE}{path}"
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, path: str, params: dict = None) -> list:
        """Follow nextToken/nextPageToken pagination."""
        results = []
        p = dict(params or {})
        while True:
            data = self._get(path, params=p)
            items = data.get('data', [])
            results.extend(items)
            next_token = data.get('nextToken') or data.get('nextPageToken') or ''
            if not next_token or not items:
                break
            p['nextToken'] = next_token
        return results

    def test_connection(self) -> dict:
        """Test API key by fetching the hosts list."""
        try:
            data = self._get('/v1/hosts')
            hosts = data.get('data', [])
            return {'success': True, 'message': f"Connected to UniFi Site Manager. Found {len(hosts)} host(s)."}
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                return {'success': False, 'error': 'Authentication failed — check your API key.'}
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_hosts(self) -> list:
        """List all hosts (controllers/gateways) visible to this API key."""
        try:
            return self._get_all('/v1/hosts')
        except Exception as e:
            logger.warning(f"UniFi Cloud get_hosts failed: {e}")
            return []

    def get_sites(self) -> list:
        """List all sites across all hosts."""
        try:
            return self._get_all('/v1/sites')
        except Exception as e:
            logger.warning(f"UniFi Cloud get_sites failed: {e}")
            return []

    def get_devices(self, host_id: str = '') -> list:
        """List all devices; optionally filter by hostId."""
        try:
            params = {'hostId': host_id} if host_id else None
            return self._get_all('/v1/devices', params=params)
        except Exception as e:
            logger.warning(f"UniFi Cloud get_devices failed: {e}")
            return []

    def sync(self) -> dict:
        """Pull all cloud data and return a structured summary compatible with UnifiProvider.sync()."""
        hosts = self.get_hosts()
        sites = self.get_sites()
        # Fetch devices per-host first (more reliable than the flat /v1/devices endpoint
        # which may require explicit hostId or return empty on some API key scopes).
        devices = []
        for host in hosts:
            hid = host.get('id') or ''
            if hid:
                host_devices = self.get_devices(host_id=hid)
                for d in host_devices:
                    if not d.get('hostId'):
                        d['hostId'] = hid
                devices.extend(host_devices)
        # Fallback to flat endpoint if per-host yielded nothing
        if not devices:
            devices = self.get_devices()

        host_map = {h.get('id') or h.get('hostId', ''): h for h in hosts}
        assigned_device_ids = set()
        site_list = []

        for site in sites:
            site_id = site.get('siteId') or site.get('id') or ''
            host_id = site.get('hostId') or ''
            meta = site.get('meta') or {}
            site_name = (meta.get('desc') or meta.get('name') or meta.get('displayName') or
                         site.get('displayName') or site.get('name') or
                         site.get('desc') or site.get('description') or site_id)
            host = host_map.get(host_id) or {}
            host_name = (host.get('reportedState', {}).get('hostname') or
                         host.get('name') or host.get('displayName') or
                         host.get('hostname') or host_id)
            if not site_name or site_name.strip().lower() in ('default', 'default site') or site_name == site_id:
                site_name = host_name or site_id

            # Match devices: exact siteId match, then fallback to hostId when device has no siteId
            site_devices = [d for d in devices
                            if d.get('siteId') == site_id
                            or (not d.get('siteId') and host_id and d.get('hostId') == host_id)]
            for d in site_devices:
                assigned_device_ids.add(d.get('deviceId') or d.get('id') or id(d))

            type_counts = {}
            for d in site_devices:
                dtype = d.get('productType') or d.get('type', 'unknown')
                type_counts[dtype] = type_counts.get(dtype, 0) + 1

            site_list.append({
                'id': site_id,
                'name': site_name,
                'host_id': host_id,
                'host_name': host_name,
                'devices': site_devices,
                'device_type_counts': type_counts,
                'wlans': [],
                'vlans': [],
                'firewall_rules': [],
                'firewall_policies': [],
                'traffic_rules': [],
                'client_count': 0,
            })

        # Fallback: if sites returned no devices (siteId mismatch or empty sites list),
        # group all devices directly under their host so nothing is silently dropped.
        unassigned = [d for d in devices
                      if (d.get('deviceId') or d.get('id') or id(d)) not in assigned_device_ids]
        if unassigned:
            by_host = {}
            for d in unassigned:
                hid = d.get('hostId') or 'unknown'
                by_host.setdefault(hid, []).append(d)
            for hid, hdevices in by_host.items():
                host = host_map.get(hid) or {}
                host_name = (host.get('reportedState', {}).get('hostname') or
                             host.get('name') or host.get('displayName') or hid)
                # Find an existing site_list entry for this host to merge into, or create one
                existing = next((s for s in site_list if s.get('host_id') == hid), None)
                if existing:
                    existing['devices'].extend(hdevices)
                    for d in hdevices:
                        dtype = d.get('productType') or d.get('type', 'unknown')
                        existing['device_type_counts'][dtype] = existing['device_type_counts'].get(dtype, 0) + 1
                else:
                    type_counts = {}
                    for d in hdevices:
                        dtype = d.get('productType') or d.get('type', 'unknown')
                        type_counts[dtype] = type_counts.get(dtype, 0) + 1
                    site_list.append({
                        'id': hid,
                        'name': host_name,
                        'host_id': hid,
                        'host_name': host_name,
                        'devices': hdevices,
                        'device_type_counts': type_counts,
                        'wlans': [],
                        'vlans': [],
                        'firewall_rules': [],
                        'firewall_policies': [],
                        'traffic_rules': [],
                        'client_count': 0,
                    })

        return {
            'mode': 'cloud',
            'hosts': hosts,
            'sites': site_list,
            'has_legacy_data': False,
            'legacy_login_ok': False,
        }
