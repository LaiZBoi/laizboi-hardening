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
        self._fp_diag: list = []
        self._tr_diag: list = []
        # Persistent session for legacy cookie-based API — preserves cookies across redirects
        self._legacy_session = requests.Session()
        self._legacy_session.headers.update({'Accept': 'application/json',
                                             'Content-Type': 'application/json'})
        if not verify_ssl:
            self._legacy_session.verify = False

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

    def _try_path(self, path: str, use_legacy: bool = False) -> tuple:
        """Returns (raw_json_or_None, status_str, body_snippet).
        Captures 200-with-error-body responses (some UniFi versions return these)."""
        auth = 'legacy' if use_legacy else 'api_key'
        try:
            raw = self._legacy_get(path) if use_legacy else self._get(path)
            # Some UniFi firmwares return HTTP 200 with an error envelope
            if isinstance(raw, dict):
                fake_status = raw.get('httpStatusCode') or raw.get('errorCode')
                if fake_status and int(fake_status) >= 400:
                    snippet = raw.get('message') or raw.get('error') or str(raw)[:120]
                    logger.warning(f"UniFi {auth} {path} → HTTP 200 with embedded error {fake_status}: {snippet}")
                    return None, f'200/{fake_status}', str(snippet)[:120]
            return raw, '200', ''
        except requests.exceptions.HTTPError as e:
            status = str(e.response.status_code) if e.response is not None else '?'
            snippet = ''
            try:
                snippet = (e.response.text or '')[:150]
            except Exception:
                pass
            logger.warning(f"UniFi {auth} {path} → {status}: {snippet[:80]}")
            return None, status, snippet
        except Exception as e:
            logger.warning(f"UniFi {auth} {path} → error: {e}")
            return None, 'err', str(e)[:100]

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
        """Log in to the legacy API using a persistent session (captures cookies across redirects)."""
        for login_path in ('/api/auth/login', '/api/login', '/proxy/network/api/auth/login'):
            try:
                resp = self._legacy_session.post(
                    f"{self.host}{login_path}",
                    json={'username': self.username, 'password': self.password},
                    timeout=10,
                )
                if resp.status_code == 200:
                    # Session now holds all cookies (including those from redirect chain)
                    self._session_cookie = self._legacy_session.cookies
                    try:
                        body = resp.json()
                        self._auth_token = body.get('token') or body.get('access_token') or ''
                        # CSRF token: check body first, then any cookie named csrf/TOKEN
                        csrf = (body.get('csrf_token') or body.get('csrfToken') or
                                body.get('X-Csrf-Token') or '')
                        if not csrf:
                            for cname in ('csrf_token', 'csrfToken', 'X-CSRF-Token', 'TOKEN'):
                                v = self._legacy_session.cookies.get(cname)
                                if v:
                                    csrf = v
                                    break
                        self._csrf_token = csrf
                        # Update session default headers with auth token if returned
                        if self._auth_token:
                            self._legacy_session.headers.update(
                                {'Authorization': f'Bearer {self._auth_token}'}
                            )
                    except Exception:
                        self._auth_token = ''
                        self._csrf_token = ''
                    logger.info(f"UniFi legacy login OK via {login_path} "
                                f"(token={'yes' if self._auth_token else 'no'}, "
                                f"csrf={'yes' if self._csrf_token else 'no'}, "
                                f"cookies={list(self._legacy_session.cookies.keys())})")
                    return True
                logger.debug(f"UniFi login {login_path} returned {resp.status_code}")
            except Exception as e:
                logger.debug(f"UniFi login attempt {login_path} failed: {e}")
                continue
        logger.warning("UniFi legacy login failed on all paths — check username/password")
        return False

    def _legacy_get(self, path: str) -> dict:
        """GET via legacy session using the persistent session (cookie + Bearer token)."""
        if not self._session_cookie:
            if not self._legacy_login():
                return {}
        headers = {}
        if self._csrf_token:
            headers['X-Csrf-Token'] = self._csrf_token
        resp = self._legacy_session.get(
            f"{self.host}{path}",
            headers=headers,
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

    def get_legacy_site_refs(self) -> list:
        """Fetch site short-names from legacy API as alternative refs. Returns list of names."""
        if not (self.username and self.password):
            return []
        try:
            data = self._legacy_get('/api/self/sites')
            return [s.get('name') for s in data.get('data', []) if s.get('name')]
        except Exception as e:
            logger.debug(f"UniFi get_legacy_site_refs failed: {e}")
            return []

    def probe_site_endpoints(self, site_ref: str) -> dict:
        """Probe potential endpoint roots to discover what the controller exposes.
        Returns dict of path → (status, body_snippet) for any that return 200."""
        probe_paths = [
            f'/proxy/network/v2/api/site/{site_ref}',
            f'/proxy/network/v2/api/site/{site_ref}/security',
            f'/proxy/network/v2/api/site/{site_ref}/firewall',
            f'/proxy/network/v2/api/site/{site_ref}/policies',
            f'/proxy/network/v2/api/site/{site_ref}/policy',
            f'/proxy/network/v2/api/site/{site_ref}/zone-policies',
            f'/proxy/network/v2/api/site/{site_ref}/zonepolicies',
            f'/proxy/network/v2/api/site/{site_ref}/traffic-rules',
            f'/proxy/network/v2/api/site/{site_ref}/config',
            f'/proxy/network/v2/api/site/{site_ref}/setting',
            f'/proxy/network/v2/api/site/{site_ref}/setting/super_mgmt',
            f'/proxy/network/api/s/{site_ref}/rest',
            f'/proxy/network/api/s/{site_ref}/stat/sysinfo',
        ]
        found = {}
        for path in probe_paths:
            raw, status, snippet = self._try_path(path)
            if status == '200':
                body = str(raw)[:200] if raw is not None else ''
                found[path] = body
                logger.info(f"UniFi probe 200: {path} → {body[:80]}")
        return found

    def get_traffic_rules(self, site_ref: str, site_id: str = '',
                          extra_refs: list = None) -> list:
        """Get Traffic Rules (UniFi OS 3.x+). Tries v2 API first, falls back to legacy REST."""
        self._tr_diag = []

        def _build_paths(ref):
            return [
                # Network 9.x/10.x security prefix (via proxy)
                f'/proxy/network/v2/api/site/{ref}/security/traffic-rules',
                f'/proxy/network/v2/api/site/{ref}/security/trafficrules',
                # Network 10.x alternate naming (hyphenated, no security prefix)
                f'/proxy/network/v2/api/site/{ref}/traffic-rules',
                f'/proxy/network/v2/api/site/{ref}/traffic_rules',
                # Legacy 7.x/8.x (via proxy) — confirmed 200 on Network 10.x (empty)
                f'/proxy/network/v2/api/site/{ref}/trafficrules',
                # Direct paths (UOS 4.x/5.x may expose Network app without /proxy prefix)
                f'/v2/api/site/{ref}/security/traffic-rules',
                f'/v2/api/site/{ref}/trafficrules',
            ]

        def _build_integration(ref):
            return [f'/proxy/network/integration/v1/sites/{ref}/trafficRules']

        refs = [site_ref]
        if site_id and site_id != site_ref:
            refs.append(site_id)
        for r in (extra_refs or []):
            if r and r not in refs:
                refs.append(r)

        paths_v2 = [p for r in refs for p in _build_paths(r)]
        paths_legacy_rest = [f'/proxy/network/api/s/{site_ref}/rest/trafficrule']
        paths_integration = [p for r in refs for p in _build_integration(r)]

        def _parse_rules(raw):
            if isinstance(raw, list):
                return raw
            for k in ('data', 'trafficRules', 'traffic_rules', 'rules', 'items'):
                if k in raw and isinstance(raw[k], list):
                    return raw[k]
            return []

        # API key attempts
        for path in paths_v2 + paths_integration + paths_legacy_rest:
            raw, status, snippet = self._try_path(path)
            diag = {'path': path, 'auth': 'api_key', 'status': status}
            if raw is not None:
                items = _parse_rules(raw)
                diag['count'] = len(items)
                if not items:
                    diag['keys'] = list(raw.keys()) if isinstance(raw, dict) else 'list'
                self._tr_diag.append(diag)
                if items:
                    logger.info(f"UniFi traffic rules (api_key) found via {path}: {len(items)}")
                    return items
            else:
                diag['snippet'] = snippet[:80]
                self._tr_diag.append(diag)

        # Legacy session cookie fallback
        if self.username and self.password:
            for path in paths_v2 + paths_legacy_rest:
                raw, status, snippet = self._try_path(path, use_legacy=True)
                diag = {'path': path, 'auth': 'legacy', 'status': status}
                if raw is not None:
                    items = _parse_rules(raw)
                    diag['count'] = len(items)
                    if not items:
                        diag['keys'] = list(raw.keys()) if isinstance(raw, dict) else 'list'
                    self._tr_diag.append(diag)
                    if items:
                        logger.info(f"UniFi traffic rules (legacy) found via {path}: {len(items)}")
                        return items
                else:
                    diag['snippet'] = snippet[:80]
                    self._tr_diag.append(diag)
        return []

    def get_firewall_policies(self, site_ref: str, site_id: str = '',
                              extra_refs: list = None) -> list:
        """Get zone-based Firewall Policies (UniFi OS 3.x+).
        UniFi 8.x renamed firewall/policies → firewall/zone-policies.
        UniFi Network 9.x/10.x moved to security/ prefix."""
        self._fp_diag = []

        def _build_paths(ref):
            return [
                # Network 9.x/10.x — security prefix
                f'/proxy/network/v2/api/site/{ref}/security/zone-policies',
                f'/proxy/network/v2/api/site/{ref}/security/policies',
                f'/proxy/network/v2/api/site/{ref}/security/firewall-policies',
                # Network 10.x alternate naming — no security/ prefix, various forms
                f'/proxy/network/v2/api/site/{ref}/zone-policies',
                f'/proxy/network/v2/api/site/{ref}/zonepolicies',
                f'/proxy/network/v2/api/site/{ref}/policies',
                f'/proxy/network/v2/api/site/{ref}/firewall-policies',
                f'/proxy/network/v2/api/site/{ref}/policy',
                # Legacy 7.x/8.x paths
                f'/proxy/network/v2/api/site/{ref}/firewall/zone-policies',
                f'/proxy/network/v2/api/site/{ref}/firewall/policies',
                # Direct paths (UOS 4.x/5.x)
                f'/v2/api/site/{ref}/security/zone-policies',
                f'/v2/api/site/{ref}/zone-policies',
            ]

        def _build_legacy_rest(ref):
            return [
                f'/proxy/network/api/s/{ref}/rest/firewallpolicy',
                # Some versions use 'firewallpolicies' (plural)
                f'/proxy/network/api/s/{ref}/rest/firewallpolicies',
            ]

        def _build_integration(ref):
            return [
                f'/proxy/network/integration/v1/sites/{ref}/firewallPolicies',
                f'/proxy/network/integration/v1/sites/{ref}/firewall/zone-policies',
            ]

        refs = [site_ref]
        if site_id and site_id != site_ref:
            refs.append(site_id)
        for r in (extra_refs or []):
            if r and r not in refs:
                refs.append(r)

        paths_v2 = [p for r in refs for p in _build_paths(r)]
        paths_legacy_rest = [p for r in refs for p in _build_legacy_rest(r)]
        paths_integration = [p for r in refs for p in _build_integration(r)]

        def _parse(raw):
            if isinstance(raw, list):
                return raw
            for key in ('data', 'policies', 'zonePolicies', 'zone_policies',
                        'firewallPolicies', 'firewall_policies', 'rules', 'items'):
                if key in raw and isinstance(raw[key], list):
                    return raw[key]
            return []

        # API key attempts
        for path in paths_v2 + paths_integration + paths_legacy_rest:
            raw, status, snippet = self._try_path(path)
            diag = {'path': path, 'auth': 'api_key', 'status': status}
            if raw is not None:
                items = _parse(raw)
                diag['count'] = len(items)
                if not items:
                    diag['keys'] = list(raw.keys()) if isinstance(raw, dict) else 'list'
                self._fp_diag.append(diag)
                if items:
                    logger.info(f"UniFi firewall policies (api_key) found via {path}: {len(items)}")
                    return items
            else:
                diag['snippet'] = snippet[:80]
                self._fp_diag.append(diag)

        # Legacy session cookie fallback
        if self.username and self.password:
            for path in paths_v2 + paths_legacy_rest:
                raw, status, snippet = self._try_path(path, use_legacy=True)
                diag = {'path': path, 'auth': 'legacy', 'status': status}
                if raw is not None:
                    items = _parse(raw)
                    diag['count'] = len(items)
                    if not items:
                        diag['keys'] = list(raw.keys()) if isinstance(raw, dict) else 'list'
                    self._fp_diag.append(diag)
                    if items:
                        logger.info(f"UniFi firewall policies (legacy) found via {path}: {len(items)}")
                        return items
                else:
                    diag['snippet'] = snippet[:80]
                    self._fp_diag.append(diag)
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

        # Fetch site short-names from the legacy API as extra fallback refs for v2 paths
        extra_refs = self.get_legacy_site_refs() if legacy_login_ok else []

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
            firewall_policies = self.get_firewall_policies(site_ref, site_id=site_id,
                                                           extra_refs=extra_refs)
            fp_diag = list(self._fp_diag)
            traffic_rules = self.get_traffic_rules(site_ref, site_id=site_id,
                                                   extra_refs=extra_refs)
            tr_diag = list(self._tr_diag)
            # If still empty, run broader endpoint probe to find what paths exist
            probe_results = {}
            if not firewall_policies and not traffic_rules:
                probe_results = self.probe_site_endpoints(site_ref)
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
                '_fp_diag': fp_diag,
                '_tr_diag': tr_diag,
                '_probe': probe_results,
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
        """Follow nextToken/nextPageToken pagination. Handles 'data', 'items', or bare-list responses."""
        results = []
        p = dict(params or {})
        while True:
            raw = self._get(path, params=p)
            # Site Manager API wraps results in 'data' but some endpoints use other keys
            if isinstance(raw, list):
                items = raw
            else:
                items = (raw.get('data') or raw.get('items') or raw.get('devices') or
                         raw.get('sites') or raw.get('hosts') or [])
            results.extend(items)
            next_token = raw.get('nextToken') or raw.get('nextPageToken') or '' if isinstance(raw, dict) else ''
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
        """List devices. When host_id given, tries the per-host endpoint first,
        then falls back to the flat /v1/devices endpoint with hostId filter."""
        if host_id:
            # Per-host path is most reliable; flat endpoint may ignore hostId param
            for path in (f'/v1/hosts/{host_id}/devices', '/v1/devices'):
                try:
                    params = None if 'hosts' in path else {'hostId': host_id}
                    results = self._get_all(path, params=params)
                    if results:
                        return results
                except Exception as e:
                    logger.debug(f"UniFi Cloud get_devices path {path} failed: {e}")
            # Also try plural 'hostIds' param variant
            try:
                results = self._get_all('/v1/devices', params={'hostIds': host_id})
                if results:
                    return results
            except Exception as e:
                logger.debug(f"UniFi Cloud get_devices hostIds param failed: {e}")
            return []
        try:
            return self._get_all('/v1/devices')
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
        # Fallback 1: flat /v1/devices endpoint
        if not devices:
            devices = self.get_devices()

        # Fallback 2: extract device inventory embedded in host.reportedState
        # (Site Manager API embeds device lists in the host object on some API scopes)
        if not devices:
            for host in hosts:
                hid = host.get('id') or ''
                state = host.get('reportedState') or {}
                for key in ('devices', 'networkDevices', 'network_devices'):
                    embedded = state.get(key) or []
                    if embedded:
                        for d in embedded:
                            if not d.get('hostId'):
                                d['hostId'] = hid
                        devices.extend(embedded)
                        break
            if devices:
                logger.debug(f"UniFi Cloud: found {len(devices)} devices via host.reportedState")

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
