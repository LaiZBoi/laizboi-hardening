"""
QuickBooks Online (Intuit) accounting adapter.

OAuth2 with refresh-token rotation. Production-ready for invoice push;
customer matching falls back to creating a new Customer if the client_org
isn't already mapped (the response Id is then stored in the AccountingConnection
credentials as a per-client lookup table).

Docs:
  https://developer.intuit.com/app/developer/qbo/docs
  Auth:    https://appcenter.intuit.com/connect/oauth2
  Token:   https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer_authorization_code
  API:     https://quickbooks.api.intuit.com/v3/company/<realm_id>/...
"""
from __future__ import annotations

import json
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


logger = logging.getLogger('integrations.accounting.qbo')


class QuickBooksOnlineProvider(BaseAccountingProvider):
    provider_type = 'quickbooks_online'
    provider_name = 'QuickBooks Online'
    DEFAULT_BASE_URL = 'https://quickbooks.api.intuit.com'
    AUTHORIZE_URL = 'https://appcenter.intuit.com/connect/oauth2'
    TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer_authorization_code'
    DEFAULT_SCOPES = ['com.intuit.quickbooks.accounting']

    # ---- OAuth ------------------------------------------------------------

    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        creds = self.credentials
        client_id = creds.get('client_id') or ''
        if not client_id:
            raise AccountingAuthError('client_id not configured for this connection')
        params = {
            'client_id': client_id,
            'response_type': 'code',
            'scope': ' '.join(self.DEFAULT_SCOPES),
            'redirect_uri': redirect_uri,
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
                headers={'Accept': 'application/json'},
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': redirect_uri,
                },
                timeout=20,
            )
        except requests.RequestException as e:
            raise AccountingProviderError(f'QBO token exchange unreachable: {e}')
        if resp.status_code != 200:
            raise AccountingAuthError(f'QBO token exchange failed: {resp.status_code} {resp.text[:200]}')
        data = resp.json()
        self._save_tokens(
            access_token=data['access_token'],
            refresh_token=data.get('refresh_token'),
            expires_in=int(data.get('expires_in', 3600)),
            realm_id=realm_id or creds.get('realm_id') or '',
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
                headers={'Accept': 'application/json'},
                data={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
                timeout=20,
            )
        except requests.RequestException as e:
            raise AccountingProviderError(f'QBO token refresh unreachable: {e}')
        if resp.status_code != 200:
            raise AccountingAuthError(f'QBO token refresh failed: {resp.status_code}')
        data = resp.json()
        self._save_tokens(
            access_token=data['access_token'],
            refresh_token=data.get('refresh_token') or refresh_token,
            expires_in=int(data.get('expires_in', 3600)),
        )
        return data['access_token']

    # ---- API surface ------------------------------------------------------

    def _api(self, method: str, path: str, **kwargs) -> requests.Response:
        token = self.refresh_access_token()
        creds = self.credentials
        realm_id = creds.get('realm_id') or ''
        if not realm_id:
            raise AccountingAuthError('realm_id missing — re-run OAuth connect')
        url = f'{self.base_url}/v3/company/{realm_id}{path}'
        headers = kwargs.pop('headers', {})
        headers.setdefault('Authorization', f'Bearer {token}')
        headers.setdefault('Accept', 'application/json')
        if 'json' in kwargs:
            headers.setdefault('Content-Type', 'application/json')
        return requests.request(method, url, headers=headers, timeout=30, **kwargs)

    def _ensure_customer(self, client_org) -> str:
        """Find or create a QBO Customer matching client_org.name. Returns
        the QBO Customer Id (string)."""
        creds = self.credentials
        cust_map = creds.get('customer_map') or {}
        existing = cust_map.get(str(client_org.id))
        if existing:
            return existing

        # Search by display name
        from urllib.parse import quote
        q = quote(f"select * from Customer where DisplayName = '{client_org.name}'")
        resp = self._api('GET', f'/query?query={q}')
        if resp.status_code == 200:
            results = (resp.json().get('QueryResponse') or {}).get('Customer') or []
            if results:
                cust_id = str(results[0]['Id'])
                cust_map[str(client_org.id)] = cust_id
                self.connection.update_credentials(customer_map=cust_map)
                self.connection.save(update_fields=['encrypted_credentials', 'updated_at'])
                return cust_id

        # Create a new customer
        resp = self._api('POST', '/customer', json={'DisplayName': client_org.name[:100]})
        if resp.status_code not in (200, 201):
            raise AccountingProviderError(f'QBO create-customer failed: {resp.status_code} {resp.text[:200]}')
        cust_id = str(resp.json()['Customer']['Id'])
        cust_map[str(client_org.id)] = cust_id
        self.connection.update_credentials(customer_map=cust_map)
        self.connection.save(update_fields=['encrypted_credentials', 'updated_at'])
        return cust_id

    def push_invoice(self, invoice) -> Dict[str, Any]:
        from django.utils import timezone
        try:
            customer_id = self._ensure_customer(invoice.client_org)
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
            'CustomerRef': {'value': customer_id},
            'Line': [
                {
                    'DetailType': 'SalesItemLineDetail',
                    'Amount': float(li.line_total),
                    'Description': li.description[:1000],
                    'SalesItemLineDetail': {
                        'Qty': float(li.quantity),
                        'UnitPrice': float(li.unit_price),
                    },
                }
                for li in invoice.line_items.all()
            ],
            'TxnDate': invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            'DueDate': invoice.due_date.isoformat() if invoice.due_date else None,
            'CustomerMemo': {'value': (invoice.notes or invoice.title or '')[:1000]},
        }
        # Strip None values QBO doesn't accept
        body = {k: v for k, v in body.items() if v is not None}

        resp = self._api('POST', '/invoice', json=body)
        if resp.status_code not in (200, 201):
            err = f'HTTP {resp.status_code}: {resp.text[:500]}'
            invoice.last_push_error = err
            invoice.save(update_fields=['last_push_error', 'updated_at'])
            log_accounting_call(
                connection=self.connection, action='push_invoice',
                resource_type='invoice', resource_id=invoice.pk,
                success=False, http_status=resp.status_code,
                error_message=err,
                request_summary=f'invoice={invoice.invoice_number} lines={len(body.get("Line", []))}',
                response_summary=resp.text[:500],
            )
            return {'success': False, 'error': err}

        data = resp.json().get('Invoice') or {}
        invoice.accounting_provider = self.provider_type
        invoice.accounting_external_id = str(data.get('Id') or '')
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
            request_summary=f'invoice={invoice.invoice_number} lines={len(body.get("Line", []))}',
            response_summary=f'qbo_id={invoice.accounting_external_id}',
        )
        return {'success': True, 'invoice_id': invoice.accounting_external_id}

    def record_payment(self, payment) -> Dict[str, Any]:
        invoice = payment.invoice
        if not invoice.accounting_external_id:
            return {'skipped': True, 'reason': 'invoice not yet pushed'}
        creds = self.credentials
        customer_id = (creds.get('customer_map') or {}).get(str(invoice.client_org_id))
        if not customer_id:
            return {'skipped': True, 'reason': 'customer not mapped'}
        body = {
            'CustomerRef': {'value': customer_id},
            'TotalAmt': float(payment.amount),
            'TxnDate': payment.paid_on.isoformat(),
            'Line': [{
                'Amount': float(payment.amount),
                'LinkedTxn': [{
                    'TxnId': invoice.accounting_external_id,
                    'TxnType': 'Invoice',
                }],
            }],
        }
        resp = self._api('POST', '/payment', json=body)
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
        ext_id = resp.json().get('Payment', {}).get('Id') or ''
        log_accounting_call(
            connection=self.connection, action='record_payment',
            resource_type='payment', resource_id=payment.pk,
            external_id=str(ext_id),
            success=True, http_status=resp.status_code,
            request_summary=f'payment={payment.pk} amount={payment.amount} invoice={invoice.invoice_number}',
            response_summary=f'qbo_payment_id={ext_id}',
        )
        return {'success': True, 'payment_id': ext_id}
