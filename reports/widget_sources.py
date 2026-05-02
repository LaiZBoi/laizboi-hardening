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

from django.db import models


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
    from django.utils import timezone
    qs = Ticket.objects.filter(status__is_terminal=False)
    cat = (params or {}).get('category') or 'all'
    label = 'Open tickets'
    if cat == 'unassigned':
        qs = qs.filter(assigned_to__isnull=True)
        label = 'Open tickets — unassigned'
    elif cat == 'overdue':
        qs = qs.filter(resolution_due_at__lt=timezone.now())
        label = 'Open tickets — SLA overdue'
    elif cat == 'priority_high':
        qs = qs.filter(priority__code__in=['P1', 'P2'])
        label = 'Open tickets — P1/P2'
    n = qs.count()
    return {
        'value': str(n),
        'subtitle': label,
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
    """Open-ticket breakdown — grouping is category-driven (priority,
    queue, or assigned tech). Name kept for backwards-compat with
    saved widgets; column header changes per category."""
    from psa.models import Ticket
    from django.db.models import Count
    cat = (params or {}).get('category') or 'priority'
    base = Ticket.objects.filter(status__is_terminal=False)
    if cat == 'queue':
        agg = base.values('queue__name').annotate(n=Count('id')).order_by('-n')[:8]
        rows = [[r['queue__name'] or '—', str(r['n'])] for r in agg]
        return {'columns': ['Queue', 'Open'], 'rows': rows or [['—', '0']]}
    if cat == 'assigned_tech':
        agg = base.values('assigned_to__username').annotate(n=Count('id')).order_by('-n')[:8]
        rows = [[r['assigned_to__username'] or 'Unassigned', str(r['n'])] for r in agg]
        return {'columns': ['Tech', 'Open'], 'rows': rows or [['—', '0']]}
    rows = []
    for code in ['P1', 'P2', 'P3', 'P4', 'P5']:
        n = base.filter(priority__code=code).count()
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
    """30-day revenue trend. Category-aware (daily / weekly / cumulative)."""
    from psa.models import Invoice
    today = date.today()
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    by_day = {d: 0.0 for d in days}
    invs = Invoice.objects.filter(
        invoice_date__gte=days[0], invoice_date__lte=today,
        status__in=['sent', 'partial', 'paid', 'overdue'],
    )
    for inv in invs:
        if inv.invoice_date in by_day:
            by_day[inv.invoice_date] += float(inv.total or 0)

    cat = (params or {}).get('category') or 'daily'
    if cat == 'weekly':
        # Group into 5 buckets of ~6 days. Labels = end date of bucket.
        buckets = []
        labels = []
        chunk = 6
        for i in range(0, len(days), chunk):
            window = days[i:i + chunk]
            if not window:
                continue
            buckets.append(round(sum(by_day[d] for d in window), 2))
            labels.append(window[-1].strftime('%m/%d'))
        return {'labels': labels, 'series': [{'name': 'Invoiced (weekly)', 'data': buckets}]}
    if cat == 'cumulative':
        running = 0.0
        cum = []
        for d in days:
            running += by_day[d]
            cum.append(round(running, 2))
        return {
            'labels': [d.strftime('%m/%d') for d in days],
            'series': [{'name': 'Invoiced (running total)', 'data': cum}],
        }
    # daily — original behavior
    labels = [d.strftime('%m/%d') for d in days]
    series = [{'name': 'Invoiced', 'data': [round(by_day[d], 2) for d in days]}]
    return {'labels': labels, 'series': series}


def tickets_opened_30d(params):
    """30-day ticket trend. Category-aware (opened / closed / net)."""
    from psa.models import Ticket
    today = date.today()
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    labels = [d.strftime('%m/%d') for d in days]
    cat = (params or {}).get('category') or 'opened'
    if cat == 'closed':
        counts = [
            Ticket.objects.filter(closed_at__date=d).count()
            for d in days
        ]
        return {'labels': labels, 'series': [{'name': 'Closed', 'data': counts}]}
    if cat == 'net':
        opened = [Ticket.objects.filter(created_at__date=d).count() for d in days]
        closed = [Ticket.objects.filter(closed_at__date=d).count() for d in days]
        net = [o - c for o, c in zip(opened, closed)]
        return {
            'labels': labels,
            'series': [
                {'name': 'Opened', 'data': opened},
                {'name': 'Closed', 'data': closed},
                {'name': 'Net (backlog Δ)', 'data': net},
            ],
        }
    counts = [Ticket.objects.filter(created_at__date=d).count() for d in days]
    return {'labels': labels, 'series': [{'name': 'Opened', 'data': counts}]}


def hours_split_pie(params):
    """Billable vs non-billable hours (last 30d)."""
    from reports.queries import hours_minutes_by_client
    start, end = _last_n_days(30)
    rows = hours_minutes_by_client(start, end)
    bill = sum(r['billable_minutes'] for r in rows) / 60.0
    nonbill = sum(r['nonbillable_minutes'] for r in rows) / 60.0
    return {'labels': ['Billable', 'Non-billable'], 'data': [round(bill, 1), round(nonbill, 1)]}


def sla_breach_trend(params):
    """30d response-breach trend per priority (line chart, top 3 priorities only)."""
    from reports.queries import sla_trend_by_priority
    end = date.today()
    start = end - timedelta(days=29)
    data = sla_trend_by_priority(start, end, bucket='day')
    labels = data['buckets']
    series = []
    for p in ['P1', 'P2', 'P3']:  # only top 3 priorities for the widget
        rows = data['series'].get(p, [])
        series.append({'name': p, 'data': [r['response_pct'] for r in rows]})
    return {'labels': labels, 'series': series}


# ---- v3.17.147 — Client-health widgets -------------------------------------

def at_risk_clients(params):
    """Client health table — category filters which slice to show.

    `worst` (default): top 5 by lowest score regardless of category.
    `trouble_only`: only clients in the Trouble bucket.
    `at_risk_only`: only clients in the At-Risk bucket.
    """
    from reports.queries import client_health_scores_all
    cat = (params or {}).get('category') or 'worst'
    rows = client_health_scores_all()
    if cat == 'trouble_only':
        rows = [r for r in rows if r['category'] == 'trouble'][:8]
    elif cat == 'at_risk_only':
        rows = [r for r in rows if r['category'] == 'at_risk'][:8]
    else:
        rows = rows[:5]
    table_rows = [
        [r['client_name'], r['score'], r['category'].replace('_', ' ').title()]
        for r in rows
    ]
    return {'columns': ['Client', 'Health', 'Status'], 'rows': table_rows or [['—', '', '']]}


def client_health_breakdown(params):
    """Pie chart: Healthy / At-Risk / Trouble counts."""
    from reports.queries import client_health_scores_all
    rows = client_health_scores_all()
    counts = {'Healthy': 0, 'At-Risk': 0, 'Trouble': 0}
    for r in rows:
        if r['category'] == 'healthy':
            counts['Healthy'] += 1
        elif r['category'] == 'at_risk':
            counts['At-Risk'] += 1
        else:
            counts['Trouble'] += 1
    return {'labels': list(counts.keys()), 'data': list(counts.values())}


# ---- Phase 5.3 — Recent sales activity -----------------------------------

def recent_sales_activity(params):
    """Last 10 sales activities across the entire MSP. Useful for sales mgr dashboard."""
    try:
        from crm.models import SalesActivity
        rows = []
        for a in SalesActivity.objects.select_related(
            'lead', 'opportunity', 'client_org', 'user',
        ).order_by('-occurred_at')[:10]:
            target = (
                a.lead.company_name if a.lead_id else (
                    a.opportunity.name if a.opportunity_id else (
                        a.client_org.name if a.client_org_id else '?'
                    )
                )
            )
            rows.append([
                a.occurred_at.strftime('%m/%d %H:%M'),
                a.get_activity_type_display(),
                target[:30],
                (a.user.username if a.user_id else 'anon'),
            ])
        return {'columns': ['When', 'Type', 'Target', 'Who'], 'rows': rows}
    except Exception:
        return {'columns': [], 'rows': []}


# ---- Phase 4.3 — Auto-replenish ------------------------------------------

def low_stock_items(params):
    """Top N items below minimum stock — grouped by name only."""
    rows = []
    try:
        from inventory.models import InventoryItem
        for it in InventoryItem.objects.filter(
            quantity__lte=models.F('min_quantity')
        ).exclude(min_quantity=0)[:10]:
            rows.append([str(it), str(it.quantity), str(it.min_quantity)])
    except Exception:
        pass
    return {'columns': ['Item', 'In stock', 'Minimum'], 'rows': rows}


# ---- Phase 9 security alerts ----------------------------------------------

def security_alerts_24h(params):
    """Count of new security alerts in the last 24h, broken down by severity."""
    from datetime import timedelta
    from django.utils import timezone
    try:
        from security_alerts.models import SecurityAlert
        cutoff = timezone.now() - timedelta(hours=24)
        rows = []
        for sev in ['critical', 'high', 'medium', 'low', 'info']:
            n = SecurityAlert.objects.filter(severity=sev, status='new', seen_at__gte=cutoff).count()
            if n:
                rows.append([sev.upper(), str(n)])
        return {'columns': ['Severity', 'New (24h)'], 'rows': rows or [['—', '0']]}
    except Exception:
        return {'columns': [], 'rows': []}


def security_alerts_open_critical(params):
    """Single metric: count of open security alerts. Category-aware."""
    try:
        from security_alerts.models import SecurityAlert
        from datetime import timedelta
        from django.utils import timezone
        cat = (params or {}).get('category') or 'critical_high'
        qs = SecurityAlert.objects.all()
        label = 'Open critical / high security alerts'
        if cat == 'critical_high':
            qs = qs.filter(severity__in=['critical', 'high'], status='new')
        elif cat == 'critical_only':
            qs = qs.filter(severity='critical', status='new')
            label = 'Open critical alerts'
        elif cat == 'all_open':
            qs = qs.filter(status='new')
            label = 'All open alerts'
        elif cat == 'last_24h':
            qs = qs.filter(seen_at__gte=timezone.now() - timedelta(hours=24))
            label = 'Alerts seen (last 24h)'
        else:
            qs = qs.filter(severity__in=['critical', 'high'], status='new')
        n = qs.count()
        return {
            'value': str(n),
            'subtitle': label,
            'icon': 'fa-shield-halved',
            'color': 'danger' if n > 0 else 'success',
        }
    except Exception:
        return {'value': '0', 'subtitle': 'Security', 'icon': 'fa-shield-halved', 'color': 'secondary'}


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
    'at_risk_clients': at_risk_clients,
    # chart
    'revenue_trend_30d': revenue_trend_30d,
    'tickets_opened_30d': tickets_opened_30d,
    'hours_split_pie': hours_split_pie,
    'sla_breach_trend': sla_breach_trend,
    'client_health_breakdown': client_health_breakdown,
    # phase 4.3
    'low_stock_items': low_stock_items,
    # phase 5.3
    'recent_sales_activity': recent_sales_activity,
    # phase 9
    'security_alerts_24h': security_alerts_24h,
    'security_alerts_open_critical': security_alerts_open_critical,
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
    ('at_risk_clients', 'At-risk clients (table)', 'table'),
    ('revenue_trend_30d', 'Revenue trend 30d (bar chart)', 'chart_bar'),
    ('tickets_opened_30d', 'Tickets opened 30d (line chart)', 'chart_line'),
    ('hours_split_pie', 'Billable vs non-billable (pie chart)', 'chart_pie'),
    ('sla_breach_trend', 'SLA breach trend 30d (line chart)', 'chart_line'),
    ('client_health_breakdown', 'Client health breakdown (pie)', 'chart_pie'),
    ('low_stock_items', 'Low stock items (table)', 'table'),
    ('recent_sales_activity', 'Recent sales activity (table)', 'table'),
    ('security_alerts_24h', 'Security alerts last 24h by severity (table)', 'table'),
    ('security_alerts_open_critical', 'Open critical/high alerts (metric)', 'metric'),
]


# v3.17.217: per-widget selectable categories. Each entry maps a
# data_source key to a list of {value, label, default?} items the wallboard
# template renders as a dropdown next to the widget title; the chosen value
# is passed to the source as `params['category']` and the source branches.
CATEGORIES = {
    'open_tickets_count': [
        {'value': 'all', 'label': 'All open', 'default': True},
        {'value': 'unassigned', 'label': 'Unassigned'},
        {'value': 'overdue', 'label': 'SLA overdue'},
        {'value': 'priority_high', 'label': 'P1 / P2 only'},
    ],
    'security_alerts_open_critical': [
        {'value': 'critical_high', 'label': 'Critical + High', 'default': True},
        {'value': 'critical_only', 'label': 'Critical only'},
        {'value': 'all_open', 'label': 'All open'},
        {'value': 'last_24h', 'label': 'Last 24h'},
    ],
    'tickets_by_priority': [
        {'value': 'priority', 'label': 'By priority', 'default': True},
        {'value': 'queue', 'label': 'By queue'},
        {'value': 'assigned_tech', 'label': 'By tech'},
    ],
    'tickets_opened_30d': [
        {'value': 'opened', 'label': 'Opened', 'default': True},
        {'value': 'closed', 'label': 'Closed'},
        {'value': 'net', 'label': 'Opened vs closed (net Δ)'},
    ],
    'revenue_trend_30d': [
        {'value': 'daily', 'label': 'Daily', 'default': True},
        {'value': 'weekly', 'label': 'Weekly buckets'},
        {'value': 'cumulative', 'label': 'Cumulative (running total)'},
    ],
    'at_risk_clients': [
        {'value': 'worst', 'label': 'Top 5 worst', 'default': True},
        {'value': 'trouble_only', 'label': 'Trouble only'},
        {'value': 'at_risk_only', 'label': 'At-Risk only'},
    ],
}


def get_categories(data_source):
    return CATEGORIES.get(data_source)


def is_valid_category(data_source, value):
    cats = CATEGORIES.get(data_source) or []
    return any(c['value'] == value for c in cats)


def default_category(data_source):
    cats = CATEGORIES.get(data_source) or []
    for c in cats:
        if c.get('default'):
            return c['value']
    return cats[0]['value'] if cats else None


# v3.17.220: wallboard-type templates. Each template defines a starter set
# of widgets; selecting one on the Create form pre-populates the new board
# so the user lands on a useful screen instead of an empty grid.
#
# Each item:
#   key (str)            — internal id; passed in the create form POST
#   label (str)          — human-readable
#   description (str)    — one-liner shown next to the option
#   widgets (list)       — list of {data_source, title, widget_type}
WALLBOARD_TEMPLATES = [
    {
        'key': 'custom',
        'label': 'Custom (empty)',
        'description': 'Start with no widgets — add them yourself.',
        'widgets': [],
    },
    {
        'key': 'operations',
        'label': 'Operations overview',
        'description': 'Open tickets, overdue, security alerts, ticket trend — the all-purpose NOC TV.',
        'widgets': [
            {'data_source': 'open_tickets_count', 'title': 'Open tickets', 'widget_type': 'metric'},
            {'data_source': 'overdue_tickets_count', 'title': 'SLA overdue', 'widget_type': 'metric'},
            {'data_source': 'security_alerts_open_critical', 'title': 'Security alerts', 'widget_type': 'metric'},
            {'data_source': 'active_techs', 'title': 'Active techs (30d)', 'widget_type': 'metric'},
            {'data_source': 'tickets_opened_30d', 'title': 'Tickets opened — 30d', 'widget_type': 'chart_line'},
            {'data_source': 'tickets_by_priority', 'title': 'Open tickets by priority', 'widget_type': 'table'},
        ],
    },
    {
        'key': 'tickets',
        'label': 'Tickets / Service Desk',
        'description': 'Service-desk-focused — queue load, dispatch view, ticket flow.',
        'widgets': [
            {'data_source': 'open_tickets_count', 'title': 'Open tickets', 'widget_type': 'metric'},
            {'data_source': 'overdue_tickets_count', 'title': 'SLA overdue', 'widget_type': 'metric'},
            {'data_source': 'avg_resolution_hours', 'title': 'Avg resolution time', 'widget_type': 'metric'},
            {'data_source': 'tickets_by_priority', 'title': 'Open tickets by priority', 'widget_type': 'table'},
            {'data_source': 'tickets_opened_30d', 'title': 'Opened vs closed (30d)', 'widget_type': 'chart_line'},
            {'data_source': 'sla_breach_trend', 'title': 'SLA breach trend', 'widget_type': 'chart_line'},
        ],
    },
    {
        'key': 'alerts',
        'label': 'Security & Alerts',
        'description': 'Critical alerts, recent fires, vulnerable surface area.',
        'widgets': [
            {'data_source': 'security_alerts_open_critical', 'title': 'Open critical alerts', 'widget_type': 'metric'},
            {'data_source': 'security_alerts_24h', 'title': 'New alerts (24h) by severity', 'widget_type': 'table'},
        ],
    },
    {
        'key': 'sales',
        'label': 'Sales / Revenue',
        'description': 'Revenue this period, 30d trend, recent activity.',
        'widgets': [
            {'data_source': 'revenue_this_period', 'title': 'Revenue this period', 'widget_type': 'metric'},
            {'data_source': 'unbilled_hours', 'title': 'Unbilled hours at risk', 'widget_type': 'metric'},
            {'data_source': 'revenue_trend_30d', 'title': 'Revenue trend (30d)', 'widget_type': 'chart_bar'},
            {'data_source': 'top_clients_by_revenue', 'title': 'Top clients by revenue', 'widget_type': 'table'},
            {'data_source': 'recent_sales_activity', 'title': 'Recent sales activity', 'widget_type': 'table'},
        ],
    },
    {
        'key': 'health',
        'label': 'Client health',
        'description': 'At-risk clients + health breakdown + SLA pressure.',
        'widgets': [
            {'data_source': 'client_health_breakdown', 'title': 'Client health (pie)', 'widget_type': 'chart_pie'},
            {'data_source': 'at_risk_clients', 'title': 'At-risk clients', 'widget_type': 'table'},
            {'data_source': 'sla_breach_trend', 'title': 'SLA breach trend', 'widget_type': 'chart_line'},
        ],
    },
]


def get_template(key):
    for t in WALLBOARD_TEMPLATES:
        if t['key'] == key:
            return t
    return None


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
