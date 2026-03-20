"""
Microsoft 365 / Graph API provider.
Uses Azure AD app registration with client credentials OAuth2 flow.
Required Graph API permissions (application, not delegated):
  User.Read.All, Group.Read.All, Sites.Read.All, TeamSettings.Read.All,
  Directory.Read.All, Organization.Read.All
"""
import logging
import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
TOKEN_URL = 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'


class M365Provider:
    """Read-only Microsoft Graph API client using client credentials flow."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            TOKEN_URL.format(tenant_id=self.tenant_id),
            data={
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': 'https://graph.microsoft.com/.default',
            },
            timeout=15,
        )
        resp.raise_for_status()
        self._token = resp.json()['access_token']
        return self._token

    def _get(self, path: str, params: dict = None) -> dict:
        headers = {
            'Authorization': f'Bearer {self._get_token()}',
            'Accept': 'application/json',
            # Required for $search and advanced $filter queries (Graph API ignores for others)
            'ConsistencyLevel': 'eventual',
        }
        url = path if path.startswith('http') else f'{GRAPH_BASE}{path}'
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, path: str, params: dict = None) -> list:
        """Follow @odata.nextLink to page through all results."""
        results = []
        data = self._get(path, params)
        results.extend(data.get('value', []))
        while '@odata.nextLink' in data:
            data = self._get(data['@odata.nextLink'])
            results.extend(data.get('value', []))
        return results

    def test_connection(self) -> dict:
        try:
            data = self._get('/organization', params={'$select': 'displayName,id'})
            org_name = data.get('value', [{}])[0].get('displayName', 'Unknown')
            return {'success': True, 'message': f"Connected to tenant: {org_name}"}
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                if e.response.status_code == 401:
                    return {'success': False, 'error': 'Authentication failed — check tenant ID, client ID and secret.'}
                if e.response.status_code == 403:
                    return {'success': False, 'error': 'Forbidden — ensure the app has required Graph API permissions.'}
                try:
                    err = e.response.json().get('error', {})
                    return {'success': False, 'error': f"{err.get('code')}: {err.get('message')}"}
                except Exception:
                    pass
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_users(self) -> list:
        try:
            return self._get_all('/users', params={
                '$select': 'displayName,userPrincipalName,accountEnabled,assignedLicenses,jobTitle,department,mail',
                '$filter': 'accountEnabled eq true',
                '$top': '999',
            })
        except Exception as e:
            logger.warning(f"M365 get_users failed: {e}")
            return []

    def get_licenses(self) -> list:
        try:
            return self._get_all('/subscribedSkus', params={
                '$select': 'skuPartNumber,consumedUnits,prepaidUnits,skuId',
            })
        except Exception as e:
            logger.warning(f"M365 get_licenses failed: {e}")
            return []

    def get_shared_mailboxes(self) -> list:
        """Get shared mailboxes (users with mailbox but no assigned licenses)."""
        try:
            users = self._get_all('/users', params={
                '$select': 'displayName,mail,userPrincipalName,assignedLicenses',
                '$filter': "mail ne null",
                '$top': '999',
            })
            return [u for u in users if not u.get('assignedLicenses')]
        except Exception as e:
            logger.warning(f"M365 get_shared_mailboxes failed: {e}")
            return []

    def get_teams(self) -> list:
        try:
            return self._get_all('/groups', params={
                '$select': 'displayName,description,visibility,createdDateTime,mail',
                '$filter': "groupTypes/any(c:c eq 'Unified') and resourceProvisioningOptions/Any(x:x eq 'Team')",
                '$top': '999',
            })
        except Exception as e:
            logger.warning(f"M365 get_teams failed: {e}")
            return []

    def get_sharepoint_sites(self) -> list:
        try:
            return self._get_all('/sites', params={
                '$select': 'displayName,webUrl,description,createdDateTime',
                '$search': '*',
                '$count': 'true',
            })
        except Exception as e:
            logger.warning(f"M365 get_sharepoint_sites failed: {e}")
            return []

    def get_roles(self) -> list:
        try:
            roles = self._get_all('/directoryRoles', params={'$select': 'id,displayName,description'})
            result = []
            for role in roles:
                try:
                    members = self._get_all(f"/directoryRoles/{role['id']}/members", params={'$select': 'displayName,userPrincipalName'})
                    if members:
                        role['members'] = members
                        result.append(role)
                except Exception:
                    pass
            return result
        except Exception as e:
            logger.warning(f"M365 get_roles failed: {e}")
            return []

    def get_conditional_access_policies(self) -> list:
        """Get Conditional Access policies. Requires Policy.Read.All permission."""
        try:
            return self._get_all('/identity/conditionalAccess/policies', params={
                '$select': 'displayName,state,createdDateTime,modifiedDateTime,conditions,grantControls',
            })
        except Exception as e:
            logger.warning(f"M365 get_conditional_access_policies failed: {e}")
            return []

    def get_secure_score(self) -> dict:
        """Get latest Secure Score. Requires SecurityEvents.Read.All or SecurityActions.Read.All."""
        try:
            data = self._get('/security/secureScores', params={
                '$top': '1',
                '$select': 'currentScore,maxScore,percentageScore,createdDateTime,controlScores',
            })
            scores = data.get('value', [])
            return scores[0] if scores else {}
        except Exception as e:
            logger.warning(f"M365 get_secure_score failed: {e}")
            return {}

    def get_devices(self) -> list:
        """Get Entra ID registered/joined devices. Requires Device.Read.All."""
        try:
            return self._get_all('/devices', params={
                '$select': 'displayName,operatingSystem,operatingSystemVersion,trustType,approximateLastSignInDateTime,isCompliant,isManaged',
                '$top': '999',
            })
        except Exception as e:
            logger.warning(f"M365 get_devices failed: {e}")
            return []

    def get_sharepoint_usage(self) -> list:
        """Get SharePoint site storage via per-site drive quota. Requires Sites.Read.All."""
        results = []
        try:
            sites = self._get_all('/sites', params={
                '$search': '*',
                '$select': 'id,displayName,webUrl',
                '$count': 'true',
            })
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 403:
                return [{'_permission_error': True, 'required': 'Sites.Read.All'}]
            return []
        except Exception as e:
            logger.warning(f"M365 get_sharepoint_usage (sites) failed: {e}")
            return []

        for site in sites[:50]:  # cap to avoid too many requests
            try:
                drive = self._get(f"/sites/{site['id']}/drive",
                                  params={'$select': 'quota'})
                quota = drive.get('quota') or {}
                results.append({
                    'displayName': site.get('displayName') or '',
                    'siteUrl': site.get('webUrl') or '',
                    'storageUsedInBytes': quota.get('used') or 0,
                    'storageAllocatedInBytes': quota.get('total') or 0,
                    'ownerDisplayName': '',
                })
            except Exception:
                continue
        return results

    def get_defender_alerts(self) -> list:
        """Get recent Defender/security alerts. Requires SecurityAlert.Read.All."""
        try:
            return self._get_all('/security/alerts_v2', params={
                '$select': 'id,title,severity,status,createdDateTime,serviceSource,category,description',
                '$top': '100',
                '$orderby': 'createdDateTime desc',
            })
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            logger.warning(f"M365 get_defender_alerts failed (HTTP {code}): {e}")
            if code == 403:
                return [{'_permission_error': True, 'required': 'SecurityAlert.Read.All'}]
            return []
        except Exception as e:
            logger.warning(f"M365 get_defender_alerts failed: {e}")
            return []

    def sync(self) -> dict:
        """Pull all data and return structured summary."""
        return {
            'users': self.get_users(),
            'licenses': self.get_licenses(),
            'shared_mailboxes': self.get_shared_mailboxes(),
            'teams': self.get_teams(),
            'sharepoint_sites': self.get_sharepoint_sites(),
            'roles': self.get_roles(),
            'conditional_access_policies': self.get_conditional_access_policies(),
            'secure_score': self.get_secure_score(),
            'devices': self.get_devices(),
            'sharepoint_usage': self.get_sharepoint_usage(),
            'defender_alerts': self.get_defender_alerts(),
        }
