"""
Xero accounting adapter.

OAuth2 with refresh-token rotation. Production-ready for invoice push.
Customer matching falls back to creating a new Contact when the
client_org isn't already mapped (the response ContactID is then stored
in the AccountingConnection credentials as a per-client lookup table).

Docs:
  https://developer.xero.com/documentation/guides/oauth2/auth-flow/
  Auth:    https://login.xero.com/identity/connect/authorize
  Token:   https://identity.xero.com/connect/token
  API:     https://api.xero.com/api.xro/2.0/...   (header `xero-tenant-id`)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from .base import (
    AccountingAuthError,
    AccountingProviderError,
    BaseAccountingProvider,
    log_accounting_call,
)


logger = logging.getLogger('integrations.accounting.xero')


class XeroProvider(BaseAccountingProvider):
    provider_type = 'xero'
    provider_name = 'Xero'
    DEFAULT_BASE_URL = 'https://api.xero.com'
    AUTHORIZE_URL = 'https://login.xero.com/identity/connect/authorize'
    TOKEN_URL = 'https://identity.xero.com/connect/token'
    DEFAULT_SCOPES = [
        'offline_access',
        'accounting.transactions',
        'accounting.contacts',
    ]

    # ---- OAuth ------------------------------------------------------------

    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        creds = self.credentials
        client_id = creds.get('client_id') or ''
        if not client_id:
            raise AccountingAuthError('client_id not configured for this connection')
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'scope': ' '.join(self.DEFAULT_SCOPES),
            'state': state,
        }
        return f'{self.AUTHORIZE_URL}?{urlencode(params)}'

    def handle_callback(self, *, code: str, redirect_uri: str,
                        realm_id: Optional[str] = None) -> None:
        creds = self.credentials
        client_id = creds.get('client_id') or ''
        client_secret = creds.get('client_secret') or ''
        if not client_id or not client_secret:
            raise AccountingAuthError('client_id / client_secret missing')
        try:
            resp = requests.post(
                self.TOKEN_URL,
                auth=(client_id, client_secret),
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': redirect_uri,
                },
                timeout=20,
            )
        except requests.RequestException as e:
            raise AccountingProviderError(f'Xero token exchange unreachable: {e}')
        if resp.status_code != 200:
            raise AccountingAuthError(f'Xero token exchange failed: {resp.status_code} {resp.text[:200]}')
        data = resp.json()

        # Discover tenant_id (required for every API call)
        try:
            tenant_resp = requests.get(
                'https://api.xero.com/connections',
                headers={'Authorization': f'Bearer {data["access_token"]}',
                         'Accept': 'application/json'},
                timeout=15,
            )
            tenants = tenant_resp.json() if tenant_resp.status_code == 200 else []
            tenant_id = tenants[0]['tenantId'] if tenants else ''
        except Exception:
            tenant_id = ''

        self._save_tokens(
            access_token=data['access_token'],
            refresh_token=data.get('refresh_token'),
            expires_in=int(data.get('expires_in', 1800)),
            tenant_id=tenant_id,
        )

    def refresh_access_token(self) -> str:
        if self._is_access_token_fresh():
            return self.credentials.get('access_token', '')
        creds = self.credentials
        client_id = creds.get('client_id') or ''
        client_secret = creds.get('client_secret') or ''
        refresh_token = creds.get('refresh_token') or ''
        if not (client_id and client_secret and refresh_token):
            raise AccountingAuthError('OAuth not yet completed — connect via /integrations/accounting/<id>/connect/')
        try:
            resp = requests.post(
                self.TOKEN_URL,
                auth=(client_id, client_secret),
                data={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
                timeout=20,
            )
        except requests.RequestException as e:
            raise AccountingProviderError(f'Xero token refresh unreachable: {e}')
        if resp.status_code != 200:
            raise AccountingAuthError(f'Xero token refresh failed: {resp.status_code}')
        data = resp.json()
        self._save_tokens(
            access_token=data['access_token'],
            refresh_token=data.get('refresh_token') or refresh_token,
            expires_in=int(data.get('expires_in', 1800)),
        )
        return data['access_token']

    # ---- API surface ------------------------------------------------------

    def _api(self, method: str, path: str, **kwargs) -> requests.Response:
        token = self.refresh_access_token()
        creds = self.credentials
        tenant_id = creds.get('tenant_id') or ''
        if not tenant_id:
            raise AccountingAuthError('tenant_id missing — re-run OAuth connect')
        url = f'{self.base_url}/api.xro/2.0{path}'
        headers = kwargs.pop('headers', {})
        headers.setdefault('Authorization', f'Bearer {token}')
        headers.setdefault('Accept', 'application/json')
        headers['Xero-tenant-id'] = tenant_id
        if 'json' in kwargs:
            headers.setdefault('Content-Type', 'application/json')
        return requests.request(method, url, headers=headers, timeout=30, **kwargs)

    def _ensure_contact(self, client_org) -> str:
        creds = self.credentials
        contact_map = creds.get('contact_map') or {}
        existing = contact_map.get(str(client_org.id))
        if existing:
            return existing

        # Search by name
        from urllib.parse import quote
        where = quote(f'Name=="{client_org.name}"')
        resp = self._api('GET', f'/Contacts?where={where}')
        if resp.status_code == 200:
            results = (resp.json() or {}).get('Contacts') or []
            if results:
                contact_id = results[0]['ContactID']
                contact_map[str(client_org.id)] = contact_id
                self.connection.update_credentials(contact_map=contact_map)
                self.connection.save(update_fields=['encrypted_credentials', 'updated_at'])
                return contact_id

        # Create a new contact
        resp = self._api('POST', '/Contacts', json={'Contacts': [{'Name': client_org.name[:255]}]})
        if resp.status_code not in (200, 201):
            raise AccountingProviderError(f'Xero create-contact failed: {resp.status_code} {resp.text[:200]}')
        body = resp.json() or {}
        contacts = body.get('Contacts') or []
        if not contacts:
            raise AccountingProviderError('Xero create-contact returned no rows')
        contact_id = contacts[0]['ContactID']
        contact_map[str(client_org.id)] = contact_id
        self.connection.update_credentials(contact_map=contact_map)
        self.connection.save(update_fields=['encrypted_credentials', 'updated_at'])
        return contact_id

    def push_invoice(self, invoice) -> Dict[str, Any]:
        from django.utils import timezone
        try:
            contact_id = self._ensure_contact(invoice.client_org)
        except Exception as exc:
            invoice.last_push_error = str(exc)[:500]
            invoice.save(update_fields=['last_push_error', 'updated_at'])
            log_accounting_call(
                connection=self.connection, action='push_invoice',
                resource_type='invoice', resource_id=invoice.pk,
                success=False, error_message=str(exc),
                request_summary=f'invoice={invoice.invoice_number}',
            )
            return {'success': False, 'error': str(exc)}

        body = {
            'Invoices': [{
                'Type': 'ACCREC',
                'Contact': {'ContactID': contact_id},
                'Date': invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                'DueDate': invoice.due_date.isoformat() if invoice.due_date else None,
                'LineItems': [
                    {
                        'Description': li.description[:4000],
                        'Quantity': float(li.quantity),
                        'UnitAmount': float(li.unit_price),
                    }
                    for li in invoice.line_items.all()
                ],
                'Status': 'AUTHORISED',
            }],
        }
        # Strip None
        body['Invoices'][0] = {k: v for k, v in body['Invoices'][0].items() if v is not None}

        resp = self._api('POST', '/Invoices', json=body)
        if resp.status_code not in (200, 201):
            err = f'HTTP {resp.status_code}: {resp.text[:500]}'
            invoice.last_push_error = err
            invoice.save(update_fields=['last_push_error', 'updated_at'])
            log_accounting_call(
                connection=self.connection, action='push_invoice',
                resource_type='invoice', resource_id=invoice.pk,
                success=False, http_status=resp.status_code,
                error_message=err,
                request_summary=f'invoice={invoice.invoice_number} lines={len(body["Invoices"][0].get("LineItems", []))}',
                response_summary=resp.text[:500],
            )
            return {'success': False, 'error': err}

        data = resp.json() or {}
        invoices = data.get('Invoices') or []
        ext_id = (invoices[0].get('InvoiceID') if invoices else '') or ''
        invoice.accounting_provider = self.provider_type
        invoice.accounting_external_id = str(ext_id)
        invoice.pushed_to_accounting_at = timezone.now()
        invoice.last_push_error = ''
        invoice.save(update_fields=[
            'accounting_provider', 'accounting_external_id',
            'pushed_to_accounting_at', 'last_push_error', 'updated_at'
        ])
        log_accounting_call(
            connection=self.connection, action='push_invoice',
            resource_type='invoice', resource_id=invoice.pk,
            external_id=invoice.accounting_external_id,
            success=True, http_status=resp.status_code,
            request_summary=f'invoice={invoice.invoice_number} lines={len(body["Invoices"][0].get("LineItems", []))}',
            response_summary=f'xero_id={invoice.accounting_external_id}',
        )
        return {'success': True, 'invoice_id': invoice.accounting_external_id}

    def record_payment(self, payment) -> Dict[str, Any]:
        invoice = payment.invoice
        if not invoice.accounting_external_id:
            return {'skipped': True, 'reason': 'invoice not yet pushed'}
        body = {
            'Payments': [{
                'Invoice': {'InvoiceID': invoice.accounting_external_id},
                'Amount': float(payment.amount),
                'Date': payment.paid_on.isoformat(),
                'Account': {'Code': payment.reference[:10] or '090'},  # let admin override
            }],
        }
        resp = self._api('POST', '/Payments', json=body)
        if resp.status_code not in (200, 201):
            err = f'HTTP {resp.status_code}: {resp.text[:200]}'
            log_accounting_call(
                connection=self.connection, action='record_payment',
                resource_type='payment', resource_id=payment.pk,
                success=False, http_status=resp.status_code,
                error_message=err,
                request_summary=f'payment={payment.pk} amount={payment.amount} invoice={invoice.invoice_number}',
                response_summary=resp.text[:500],
            )
            return {'success': False, 'error': err}
        log_accounting_call(
            connection=self.connection, action='record_payment',
            resource_type='payment', resource_id=payment.pk,
            success=True, http_status=resp.status_code,
            request_summary=f'payment={payment.pk} amount={payment.amount} invoice={invoice.invoice_number}',
            response_summary='ok',
        )
        return {'success': True}
