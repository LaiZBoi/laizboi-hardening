"""
Dashboard widget data sources.

Each entry:  data_source (str) → callable(params: dict) → result dict
Result shape depends on widget type but is always JSON-serializable.

For 'metric': {'value': float|str, 'unit': str, 'trend_pct': float|None,
               'trend_label': str, 'subtitle': str, 'icon': str, 'color': str}
For 'chart_line', 'chart_bar': {'labels': [str], 'series': [{'name', 'data': [num]}]}
For 'chart_pie': {'labels': [str], 'data': [num]}
For 'table', 'list': {'columns': [str], 'rows': [[any]]}
"""
from datetime import date, timedelta
from decimal import Decimal


def _last_n_days(n=30):
    today = date.today()
    return today - timedelta(days=n - 1), today


# ---- METRIC widgets --------------------------------------------------------

def revenue_this_period(params):
    from reports.queries import revenue_by_client
    days = int(params.get('days', 30))
    start, end = _last_n_days(days)
    rows = revenue_by_client(start, end)
    total = sum(r['invoiced'] for r in rows)
    return {
        'value': f'${total:,.0f}',
        'subtitle': f'Revenue invoiced (last {days}d)',
        'icon': 'fa-dollar-sign',
        'color': 'success',
    }


def open_tickets_count(params):
    from psa.models import Ticket
    n = Ticket.objects.filter(status__is_terminal=False).count()
    return {
        'value': str(n),
        'subtitle': 'Open tickets',
        'icon': 'fa-ticket',
        'color': 'info' if n < 50 else 'warning' if n < 100 else 'danger',
    }


def overdue_tickets_count(params):
    from psa.models import Ticket
    from django.utils import timezone
    n = Ticket.objects.filter(
        status__is_terminal=False,
        resolution_due_at__lt=timezone.now(),
    ).count()
    return {
        'value': str(n),
        'subtitle': 'SLA overdue',
        'icon': 'fa-triangle-exclamation',
        'color': 'success' if n == 0 else 'warning' if n < 5 else 'danger',
    }


def unbilled_hours(params):
    """Stale (>30d) billable time not yet invoiced."""
    from reports.queries import revenue_leakage
    leak = revenue_leakage(date.today() - timedelta(days=365), date.today())
    stale = leak['totals']['stale']
    return {
        'value': f'${stale:,.0f}',
        'subtitle': 'Stale unbilled time at risk',
        'icon': 'fa-faucet-drip',
        'color': 'danger' if stale > 5000 else 'warning' if stale > 0 else 'success',
    }


def active_techs(params):
    """Distinct techs who logged time in last 30 days."""
    from psa.models import TicketTimeEntry
    start, _ = _last_n_days(30)
    n = TicketTimeEntry.objects.filter(
        started_at__date__gte=start
    ).values('user_id').distinct().count()
    return {
        'value': str(n),
        'subtitle': 'Active techs (30d)',
        'icon': 'fa-users-gear',
        'color': 'primary',
    }


def avg_resolution_hours(params):
    """Average resolution time in hours over last 30d closed tickets."""
    from psa.models import Ticket
    from django.utils import timezone
    from datetime import timedelta as td
    cutoff = timezone.now() - td(days=30)
    closed = Ticket.objects.filter(
        closed_at__gte=cutoff, status__is_terminal=True,
    ).exclude(closed_at__isnull=True).exclude(created_at__isnull=True)
    total = 0
    cnt = 0
    for t in closed:
        delta = (t.closed_at - t.created_at).total_seconds() / 3600
        if delta > 0:
            total += delta
            cnt += 1
    avg = (total / cnt) if cnt else 0.0
    return {
        'value': f'{avg:.1f}h',
        'subtitle': 'Avg time-to-resolve (30d)',
        'icon': 'fa-clock',
        'color': 'info',
    }


# ---- TABLE widgets ---------------------------------------------------------

def top_clients_by_revenue(params):
    from reports.queries import revenue_by_client
    days = int(params.get('days', 30))
    limit = int(params.get('limit', 5))
    start, end = _last_n_days(days)
    rows = revenue_by_client(start, end)[:limit]
    return {
        'columns': ['Client', 'Invoiced', 'Outstanding'],
        'rows': [
            [r['client_name'], f'${r["invoiced"]:,.2f}', f'${r["outstanding"]:,.2f}']
            for r in rows
        ],
    }


def tickets_by_priority(params):
    from psa.models import Ticket
    rows = []
    for code in ['P1', 'P2', 'P3', 'P4', 'P5']:
        n = Ticket.objects.filter(
            status__is_terminal=False, priority__code=code
        ).count()
        rows.append([code, str(n)])
    return {'columns': ['Priority', 'Open'], 'rows': rows}


def my_assigned_tickets(params):
    """Caller-aware: filtered to params['user_id']."""
    from psa.models import Ticket
    uid = params.get('user_id')
    if not uid:
        return {'columns': ['Ticket', 'Subject', 'Priority'], 'rows': []}
    qs = Ticket.objects.filter(
        assigned_to_id=uid, status__is_terminal=False,
    ).select_related('priority').order_by('-resolution_due_at')[:8]
    rows = [
        [t.ticket_number, t.subject[:60], t.priority.code if t.priority_id else '']
        for t in qs
    ]
    return {'columns': ['Ticket', 'Subject', 'Priority'], 'rows': rows}


# ---- CHART widgets ---------------------------------------------------------

def revenue_trend_30d(params):
    """30-day revenue trend (1 bar per day)."""
    from psa.models import Invoice
    today = date.today()
    days = []
    for i in range(29, -1, -1):
        days.append(today - timedelta(days=i))
    labels = [d.strftime('%m/%d') for d in days]
    by_day = {d: 0.0 for d in days}
    invs = Invoice.objects.filter(
        invoice_date__gte=days[0], invoice_date__lte=today,
        status__in=['sent', 'partial', 'paid', 'overdue'],
    )
    for inv in invs:
        if inv.invoice_date in by_day:
            by_day[inv.invoice_date] += float(inv.total or 0)
    series = [{'name': 'Invoiced', 'data': [round(by_day[d], 2) for d in days]}]
    return {'labels': labels, 'series': series}


def tickets_opened_30d(params):
    from psa.models import Ticket
    today = date.today()
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    labels = [d.strftime('%m/%d') for d in days]
    counts = []
    for d in days:
        counts.append(Ticket.objects.filter(created_at__date=d).count())
    return {'labels': labels, 'series': [{'name': 'Tickets', 'data': counts}]}


def hours_split_pie(params):
    """Billable vs non-billable hours (last 30d)."""
    from reports.queries import hours_minutes_by_client
    start, end = _last_n_days(30)
    rows = hours_minutes_by_client(start, end)
    bill = sum(r['billable_minutes'] for r in rows) / 60.0
    nonbill = sum(r['nonbillable_minutes'] for r in rows) / 60.0
    return {'labels': ['Billable', 'Non-billable'], 'data': [round(bill, 1), round(nonbill, 1)]}


# ---- Registry --------------------------------------------------------------

REGISTRY = {
    # metric
    'revenue_this_period': revenue_this_period,
    'open_tickets_count': open_tickets_count,
    'overdue_tickets_count': overdue_tickets_count,
    'unbilled_hours': unbilled_hours,
    'active_techs': active_techs,
    'avg_resolution_hours': avg_resolution_hours,
    # table
    'top_clients_by_revenue': top_clients_by_revenue,
    'tickets_by_priority': tickets_by_priority,
    'my_assigned_tickets': my_assigned_tickets,
    # chart
    'revenue_trend_30d': revenue_trend_30d,
    'tickets_opened_30d': tickets_opened_30d,
    'hours_split_pie': hours_split_pie,
}

DATA_SOURCE_CHOICES = [
    # (key, label, default widget_type)
    ('revenue_this_period', 'Revenue this period (metric)', 'metric'),
    ('open_tickets_count', 'Open tickets count (metric)', 'metric'),
    ('overdue_tickets_count', 'SLA-overdue tickets (metric)', 'metric'),
    ('unbilled_hours', 'Unbilled hours at risk (metric)', 'metric'),
    ('active_techs', 'Active techs in 30d (metric)', 'metric'),
    ('avg_resolution_hours', 'Avg time to resolve (metric)', 'metric'),
    ('top_clients_by_revenue', 'Top clients by revenue (table)', 'table'),
    ('tickets_by_priority', 'Open tickets by priority (table)', 'table'),
    ('my_assigned_tickets', 'My assigned tickets (table)', 'table'),
    ('revenue_trend_30d', 'Revenue trend 30d (bar chart)', 'chart_bar'),
    ('tickets_opened_30d', 'Tickets opened 30d (line chart)', 'chart_line'),
    ('hours_split_pie', 'Billable vs non-billable (pie chart)', 'chart_pie'),
]


def get_widget_data(data_source: str, params: dict) -> dict:
    """Lookup + execute. Returns {'error': str} if data source unknown
    or the callable raises (so a single bad widget doesn't crash the
    whole dashboard render)."""
    fn = REGISTRY.get(data_source)
    if fn is None:
        return {'error': f'Unknown data source: {data_source}'}
    try:
        return fn(params or {})
    except Exception as exc:
        import logging
        logging.getLogger('reports.widgets').exception('widget %s failed', data_source)
        return {'error': str(exc)[:200]}
