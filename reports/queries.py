"""
Canonical reporting queries.

One module. One source of truth for "what's the revenue for client X
this period". Every Phase 3 report — profitability, leakage, SLA
trends, dashboards — calls into this. Don't duplicate these queries
in views; ADD a new function here and wire to it.

Conventions:
- Periods are inclusive [start_date, end_date], date objects.
- Currency math uses Decimal throughout.
- Returns dicts of plain Python types (no querysets) so JSON export +
  template rendering both work.
"""
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, Q
from django.contrib.auth.models import User


# ---- Hours -----------------------------------------------------------------

def hours_minutes_by_client(start_date, end_date, organization=None):
    """
    Total billable + non-billable minutes by client_org for the period.
    Returns: list of {'client_id', 'client_name', 'billable_minutes',
    'nonbillable_minutes', 'total_minutes'} dicts.
    """
    from psa.models import TicketTimeEntry, Ticket
    qs = TicketTimeEntry.objects.filter(
        started_at__date__gte=start_date,
        started_at__date__lte=end_date,
    ).select_related('ticket__organization')
    if organization is not None:
        qs = qs.filter(ticket__organization=organization)

    out = {}  # client_id → row
    for te in qs:
        org = te.ticket.organization
        if not org:
            continue
        row = out.setdefault(org.id, {
            'client_id': org.id, 'client_name': org.name,
            'billable_minutes': 0, 'nonbillable_minutes': 0, 'total_minutes': 0,
        })
        mins = te.duration_minutes or 0
        if te.is_billable:
            row['billable_minutes'] += mins
        else:
            row['nonbillable_minutes'] += mins
        row['total_minutes'] += mins
    return sorted(out.values(), key=lambda r: r['total_minutes'], reverse=True)


def hours_minutes_by_tech(start_date, end_date, organization=None):
    """Same shape as `hours_minutes_by_client` but grouped by user."""
    from psa.models import TicketTimeEntry
    qs = TicketTimeEntry.objects.filter(
        started_at__date__gte=start_date,
        started_at__date__lte=end_date,
    ).select_related('user', 'ticket__organization')
    if organization is not None:
        qs = qs.filter(ticket__organization=organization)

    out = {}
    for te in qs:
        if not te.user_id:
            continue
        row = out.setdefault(te.user_id, {
            'tech_id': te.user_id,
            'tech_username': te.user.username,
            'billable_minutes': 0, 'nonbillable_minutes': 0, 'total_minutes': 0,
        })
        mins = te.duration_minutes or 0
        if te.is_billable:
            row['billable_minutes'] += mins
        else:
            row['nonbillable_minutes'] += mins
        row['total_minutes'] += mins
    return sorted(out.values(), key=lambda r: r['total_minutes'], reverse=True)


# ---- Revenue ---------------------------------------------------------------

def revenue_by_client(start_date, end_date, organization=None):
    """
    Sum of invoice line totals issued in the period, grouped by client.
    Counts only `sent / partial / paid` invoices (excludes draft + void).

    Returns: list of {'client_id', 'client_name', 'invoiced',
    'paid', 'outstanding'} dicts.
    """
    from psa.models import Invoice
    qs = Invoice.objects.filter(
        invoice_date__gte=start_date,
        invoice_date__lte=end_date,
        status__in=['sent', 'partial', 'paid', 'overdue'],
    ).select_related('client_org')
    if organization is not None:
        qs = qs.filter(organization=organization)

    out = {}
    for inv in qs:
        client = inv.client_org or inv.organization
        if not client:
            continue
        row = out.setdefault(client.id, {
            'client_id': client.id, 'client_name': client.name,
            'invoiced': Decimal('0'), 'paid': Decimal('0'),
        })
        row['invoiced'] += (inv.total or Decimal('0'))
        row['paid'] += (inv.amount_paid or Decimal('0'))

    for r in out.values():
        r['outstanding'] = r['invoiced'] - r['paid']
        # Cast to floats so JSON export works without Decimal serializer
        r['invoiced'] = float(r['invoiced'])
        r['paid'] = float(r['paid'])
        r['outstanding'] = float(r['outstanding'])
    return sorted(out.values(), key=lambda r: r['invoiced'], reverse=True)


# ---- Cost ------------------------------------------------------------------

# Placeholder loaded-rate per tech ($/hr). Phase 3.2 will move this onto
# UserProfile (or a TechCostRate model) so per-tech rates configure properly.
DEFAULT_LOADED_RATE = Decimal('60')


def cost_estimate_by_client(start_date, end_date, organization=None,
                            default_loaded_rate=None):
    """
    Coarse cost-of-delivery estimate: hours_used × loaded_rate.
    Until per-tech cost rates land in Phase 3.2, uses DEFAULT_LOADED_RATE.

    Returns: list of {'client_id', 'client_name', 'cost'} dicts.
    """
    rate = Decimal(str(default_loaded_rate or DEFAULT_LOADED_RATE))
    rows = hours_minutes_by_client(start_date, end_date, organization=organization)
    out = []
    for r in rows:
        hours = Decimal(r['total_minutes']) / Decimal(60)
        out.append({
            'client_id': r['client_id'],
            'client_name': r['client_name'],
            'hours': float(hours),
            'cost': float(hours * rate),
        })
    return out


# ---- Profitability ---------------------------------------------------------

def profitability_by_client(start_date, end_date, organization=None,
                            default_loaded_rate=None):
    """
    Combine revenue + cost into per-client profitability rows.
    Returns: list of {'client_id', 'client_name', 'revenue', 'cost',
    'margin', 'margin_pct', 'hours'} dicts, sorted by revenue desc.
    """
    rev = {r['client_id']: r for r in revenue_by_client(start_date, end_date, organization)}
    cost = {r['client_id']: r for r in cost_estimate_by_client(start_date, end_date, organization, default_loaded_rate)}
    client_ids = set(rev.keys()) | set(cost.keys())

    rows = []
    for cid in client_ids:
        r = rev.get(cid, {})
        c = cost.get(cid, {})
        revenue = r.get('invoiced', 0.0)
        cost_v = c.get('cost', 0.0)
        margin = revenue - cost_v
        margin_pct = (margin / revenue * 100) if revenue else 0.0
        rows.append({
            'client_id': cid,
            'client_name': r.get('client_name') or c.get('client_name') or '?',
            'revenue': revenue,
            'cost': cost_v,
            'margin': margin,
            'margin_pct': round(margin_pct, 1),
            'hours': c.get('hours', 0.0),
        })
    return sorted(rows, key=lambda r: r['revenue'], reverse=True)
