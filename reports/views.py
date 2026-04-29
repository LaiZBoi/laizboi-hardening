"""
Views for Reports and Analytics
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, FileResponse, JsonResponse
from django.db.models import Count, Q
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from .models import (
    Dashboard, DashboardWidget, ReportTemplate, GeneratedReport,
    ScheduledReport, AnalyticsEvent
)
from .generators import REPORT_GENERATORS
import csv as _csv
import json


def get_user_organizations(user):
    """Get all organizations the user is a member of"""
    from core.models import Organization
    if user.is_superuser:
        return Organization.objects.all()
    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True
    ).distinct()


def get_user_primary_organization(user):
    """Get user's primary organization (first active membership)"""
    membership = user.memberships.filter(is_active=True).select_related('organization').first()
    return membership.organization if membership else None


@login_required
def reports_home(request):
    """Reports and Analytics home page"""
    orgs = get_user_organizations(request.user)

    context = {
        'recent_reports': GeneratedReport.objects.filter(
            organization__in=orgs
        ).select_related('template', 'generated_by')[:10],
        'active_schedules': ScheduledReport.objects.filter(
            organization__in=orgs,
            is_active=True
        ).count(),
        'total_dashboards': Dashboard.objects.filter(
            Q(organization__in=orgs) | Q(is_global=True)
        ).count(),
        'templates_count': ReportTemplate.objects.filter(
            Q(organization__in=orgs) | Q(is_global=True)
        ).count(),
    }

    return render(request, 'reports/home.html', context)


@login_required
def dashboard_list(request):
    """List all available dashboards"""
    orgs = get_user_organizations(request.user)

    dashboards = Dashboard.objects.filter(
        Q(organization__in=orgs) | Q(is_global=True)
    ).prefetch_related('widgets')

    context = {
        'dashboards': dashboards,
    }

    return render(request, 'reports/dashboard_list.html', context)


@login_required
def dashboard_detail(request, pk):
    """View a specific dashboard.

    v3.17.142: each widget is rendered server-side via the
    `reports.widget_sources` registry so the template just emits HTML
    based on the prepared `widget.rendered` payload.
    """
    from .widget_sources import get_widget_data
    orgs = get_user_organizations(request.user)

    dashboard = get_object_or_404(
        Dashboard,
        Q(organization__in=orgs) | Q(organization__isnull=True),  # User's orgs or global
        pk=pk
    )

    widgets = list(dashboard.widgets.all())
    # Sort by position {x, y} when present, fall back to pk for stable order.
    def _pos_key(w):
        pos = w.position or {}
        return (pos.get('y', 0), pos.get('x', 0), w.pk)
    widgets.sort(key=_pos_key)

    # Render data for each widget. Errors are captured per-widget so a
    # single misbehaving data source can't blow up the whole page.
    has_chart = False
    for w in widgets:
        params = dict(w.query_params or {})
        params['user_id'] = request.user.id
        w.rendered = get_widget_data(w.data_source, params)
        # Pre-serialize chart payload so the template can drop it into a
        # <script type="application/json"> tag without re-encoding.
        if w.widget_type in ('chart_line', 'chart_bar', 'chart_pie'):
            has_chart = True
            # Escape `</` so the payload can't break out of <script> tags.
            w.rendered_json = json.dumps(w.rendered).replace('</', '<\\/')

    can_manage = (
        dashboard.created_by_id == request.user.id
        or request.user.is_staff
    )

    context = {
        'dashboard': dashboard,
        'widgets': widgets,
        'has_chart': has_chart,
        'can_manage': can_manage,
    }

    return render(request, 'reports/dashboard_detail.html', context)


@login_required
def dashboard_create(request):
    """Create a new dashboard"""
    org = get_user_primary_organization(request.user)
    if not org:
        messages.error(request, 'You must be a member of an organization to create dashboards.')
        return redirect('reports:home')

    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        is_default = request.POST.get('is_default') == 'on'

        dashboard = Dashboard.objects.create(
            name=name,
            description=description,
            is_default=is_default,
            organization=org,
            created_by=request.user
        )

        messages.success(request, f'Dashboard "{name}" created successfully.')
        return redirect('reports:dashboard_detail', pk=dashboard.pk)

    return render(request, 'reports/dashboard_form.html', {'action': 'Create'})


@login_required
def dashboard_edit(request, pk):
    """Edit an existing dashboard"""
    orgs = get_user_organizations(request.user)
    dashboard = get_object_or_404(
        Dashboard,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        dashboard.name = request.POST.get('name')
        dashboard.description = request.POST.get('description', '')
        dashboard.is_default = request.POST.get('is_default') == 'on'
        dashboard.save()

        messages.success(request, f'Dashboard "{dashboard.name}" updated successfully.')
        return redirect('reports:dashboard_detail', pk=dashboard.pk)

    context = {
        'dashboard': dashboard,
        'action': 'Edit'
    }

    return render(request, 'reports/dashboard_form.html', context)


@login_required
def dashboard_delete(request, pk):
    """Delete a dashboard"""
    orgs = get_user_organizations(request.user)
    dashboard = get_object_or_404(
        Dashboard,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        name = dashboard.name
        dashboard.delete()
        messages.success(request, f'Dashboard "{name}" deleted successfully.')
        return redirect('reports:dashboard_list')

    context = {'dashboard': dashboard}
    return render(request, 'reports/dashboard_confirm_delete.html', context)


# ---------------------------------------------------------------------------
# Dashboard widget CRUD (v3.17.142)
# ---------------------------------------------------------------------------

@login_required
def dashboard_widget_add(request, dashboard_pk):
    """Add a widget to a dashboard. Owner or staff only."""
    dashboard = get_object_or_404(Dashboard, pk=dashboard_pk)
    if dashboard.created_by_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    from .widget_sources import DATA_SOURCE_CHOICES
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        data_source = (request.POST.get('data_source') or '').strip()
        wt = next(
            (wt for k, _, wt in DATA_SOURCE_CHOICES if k == data_source),
            'metric',
        )
        DashboardWidget.objects.create(
            dashboard=dashboard, title=title or data_source,
            widget_type=wt, data_source=data_source,
            query_params={}, position={},
        )
        messages.success(request, 'Widget added.')
        return redirect('reports:dashboard_detail', pk=dashboard.pk)
    return render(request, 'reports/dashboard_widget_form.html', {
        'dashboard': dashboard,
        'data_source_choices': DATA_SOURCE_CHOICES,
        'action': 'Add',
    })


@login_required
def dashboard_widget_edit(request, pk):
    """Edit an existing widget. Owner or staff only."""
    widget = get_object_or_404(DashboardWidget, pk=pk)
    dashboard = widget.dashboard
    if dashboard.created_by_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    from .widget_sources import DATA_SOURCE_CHOICES
    if request.method == 'POST':
        widget.title = (request.POST.get('title') or '').strip() or widget.title
        new_ds = (request.POST.get('data_source') or '').strip()
        if new_ds:
            widget.data_source = new_ds
            widget.widget_type = next(
                (wt for k, _, wt in DATA_SOURCE_CHOICES if k == new_ds),
                widget.widget_type,
            )
        widget.save()
        messages.success(request, 'Widget updated.')
        return redirect('reports:dashboard_detail', pk=dashboard.pk)
    return render(request, 'reports/dashboard_widget_form.html', {
        'dashboard': dashboard, 'widget': widget,
        'data_source_choices': DATA_SOURCE_CHOICES,
        'action': 'Edit',
    })


@login_required
def dashboard_widget_delete(request, pk):
    """Delete a widget. Owner or staff only."""
    widget = get_object_or_404(DashboardWidget, pk=pk)
    dashboard = widget.dashboard
    if dashboard.created_by_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    if request.method == 'POST':
        widget.delete()
        messages.success(request, 'Widget deleted.')
        return redirect('reports:dashboard_detail', pk=dashboard.pk)
    return render(request, 'reports/dashboard_widget_confirm_delete.html', {
        'widget': widget, 'dashboard': dashboard,
    })


@login_required
def template_list(request):
    """List all report templates"""
    orgs = get_user_organizations(request.user)

    templates = ReportTemplate.objects.filter(
        Q(organization__in=orgs) | Q(is_global=True)
    ).select_related('created_by')

    # Group by report type
    templates_by_type = {}
    for template in templates:
        report_type = template.get_report_type_display()
        if report_type not in templates_by_type:
            templates_by_type[report_type] = []
        templates_by_type[report_type].append(template)

    context = {
        'templates': templates,
        'templates_by_type': templates_by_type,
    }

    return render(request, 'reports/template_list.html', context)


@login_required
def template_detail(request, pk):
    """View a report template"""
    orgs = get_user_organizations(request.user)

    template = get_object_or_404(
        ReportTemplate,
        Q(organization__in=orgs) | Q(organization__isnull=True),
        pk=pk
    )

    recent_reports = GeneratedReport.objects.filter(
        template=template,
        organization__in=orgs
    )[:10]

    context = {
        'template': template,
        'recent_reports': recent_reports,
    }

    return render(request, 'reports/template_detail.html', context)


@login_required
def template_create(request):
    """Create a new report template"""
    org = get_user_primary_organization(request.user)
    if not org and not request.user.is_staff:
        messages.error(request, 'You must be a member of an organization to create templates.')
        return redirect('reports:template_list')

    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        report_type = request.POST.get('report_type')
        query_template = request.POST.get('query_template', '')
        is_global = request.POST.get('is_global') == 'on' and request.user.is_staff

        template = ReportTemplate.objects.create(
            name=name,
            description=description,
            report_type=report_type,
            query_template=query_template,
            is_global=is_global,
            organization=org if not is_global else None,
            created_by=request.user
        )

        messages.success(request, f'Report template "{name}" created successfully.')
        return redirect('reports:template_detail', pk=template.pk)

    context = {
        'action': 'Create',
        'report_types': ReportTemplate.REPORT_TYPES,
    }

    return render(request, 'reports/template_form.html', context)


@login_required
def template_edit(request, pk):
    """Edit a report template"""
    orgs = get_user_organizations(request.user)
    template = get_object_or_404(
        ReportTemplate,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        template.name = request.POST.get('name')
        template.description = request.POST.get('description', '')
        template.report_type = request.POST.get('report_type')
        template.query_template = request.POST.get('query_template', '')
        template.is_global = request.POST.get('is_global') == 'on' and request.user.is_staff
        template.save()

        messages.success(request, f'Report template "{template.name}" updated successfully.')
        return redirect('reports:template_detail', pk=template.pk)

    context = {
        'template': template,
        'action': 'Edit',
        'report_types': ReportTemplate.REPORT_TYPES,
    }

    return render(request, 'reports/template_form.html', context)


@login_required
def template_delete(request, pk):
    """Delete a report template"""
    orgs = get_user_organizations(request.user)
    template = get_object_or_404(
        ReportTemplate,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        name = template.name
        template.delete()
        messages.success(request, f'Report template "{name}" deleted successfully.')
        return redirect('reports:template_list')

    context = {'template': template}
    return render(request, 'reports/template_confirm_delete.html', context)


@login_required
def generate_report(request, pk):
    """Generate a report from a template"""
    org = get_user_primary_organization(request.user)
    if not org:
        messages.error(request, 'You must be a member of an organization to generate reports.')
        return redirect('reports:template_list')

    orgs = get_user_organizations(request.user)
    template = get_object_or_404(
        ReportTemplate,
        Q(organization__in=orgs) | Q(organization__isnull=True),
        pk=pk
    )

    if request.method == 'POST':
        report_format = request.POST.get('format', 'pdf')
        parameters = {}

        # Create the report generation task
        report = GeneratedReport.objects.create(
            template=template,
            organization=org,
            generated_by=request.user,
            format=report_format,
            parameters=parameters,
            status='pending'
        )

        # In a real implementation, this would trigger a Celery task
        # For now, we'll mark it as completed immediately
        from .generators import REPORT_GENERATORS

        try:
            generator_class = REPORT_GENERATORS.get(template.report_type)
            if generator_class:
                generator = generator_class(org)
                data = generator.generate()

                # Store the data (in production, would generate actual file)
                report.status = 'completed'
                report.completed_at = timezone.now()
                report.save()

                messages.success(request, f'Report "{template.name}" generated successfully.')
            else:
                report.status = 'failed'
                report.error_message = 'No generator found for this report type'
                report.save()
                messages.error(request, 'Failed to generate report: No generator found.')
        except Exception as e:
            report.status = 'failed'
            report.error_message = str(e)
            report.save()
            messages.error(request, f'Failed to generate report: {str(e)}')

        return redirect('reports:generated_detail', pk=report.pk)

    context = {
        'template': template,
        'formats': GeneratedReport.FORMAT_CHOICES,
    }

    return render(request, 'reports/generate_form.html', context)


@login_required
def generated_list(request):
    """List all generated reports"""
    orgs = get_user_organizations(request.user)

    reports = GeneratedReport.objects.filter(
        organization__in=orgs
    ).select_related('template', 'generated_by').order_by('-created_at')

    context = {
        'reports': reports,
    }

    return render(request, 'reports/generated_list.html', context)


@login_required
def generated_detail(request, pk):
    """View a generated report"""
    orgs = get_user_organizations(request.user)
    report = get_object_or_404(
        GeneratedReport,
        pk=pk,
        organization__in=orgs
    )

    context = {
        'report': report,
    }

    return render(request, 'reports/generated_detail.html', context)


@login_required
def generated_download(request, pk):
    """Download a generated report"""
    orgs = get_user_organizations(request.user)
    report = get_object_or_404(
        GeneratedReport,
        pk=pk,
        organization__in=orgs
    )

    if report.file:
        return FileResponse(report.file.open('rb'), as_attachment=True)
    else:
        messages.error(request, 'Report file not found.')
        return redirect('reports:generated_detail', pk=pk)


@login_required
def generated_delete(request, pk):
    """Delete a generated report"""
    orgs = get_user_organizations(request.user)
    report = get_object_or_404(
        GeneratedReport,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        report.delete()
        messages.success(request, 'Report deleted successfully.')
        return redirect('reports:generated_list')

    context = {'report': report}
    return render(request, 'reports/generated_confirm_delete.html', context)


@login_required
def scheduled_list(request):
    """List all scheduled reports"""
    orgs = get_user_organizations(request.user)

    schedules = ScheduledReport.objects.filter(
        organization__in=orgs
    ).select_related('template', 'created_by').order_by('next_run')

    context = {
        'schedules': schedules,
    }

    return render(request, 'reports/scheduled_list.html', context)


@login_required
def scheduled_create(request):
    """Create a new scheduled report"""
    org = get_user_primary_organization(request.user)
    if not org:
        messages.error(request, 'You must be a member of an organization to create scheduled reports.')
        return redirect('reports:scheduled_list')

    orgs = get_user_organizations(request.user)

    if request.method == 'POST':
        name = request.POST.get('name')
        template_id = request.POST.get('template')
        frequency = request.POST.get('frequency')
        delivery_method = request.POST.get('delivery_method')
        recipients_str = request.POST.get('recipients', '')

        template = get_object_or_404(ReportTemplate, pk=template_id)
        recipients = [email.strip() for email in recipients_str.split(',') if email.strip()]

        # Calculate next run based on frequency
        next_run = timezone.now()
        if frequency == 'daily':
            next_run += timedelta(days=1)
        elif frequency == 'weekly':
            next_run += timedelta(weeks=1)
        elif frequency == 'monthly':
            next_run += timedelta(days=30)
        elif frequency == 'quarterly':
            next_run += timedelta(days=90)

        schedule = ScheduledReport.objects.create(
            name=name,
            template=template,
            organization=org,
            frequency=frequency,
            delivery_method=delivery_method,
            recipients=recipients,
            next_run=next_run,
            created_by=request.user
        )

        messages.success(request, f'Scheduled report "{name}" created successfully.')
        return redirect('reports:scheduled_list')

    templates = ReportTemplate.objects.filter(
        Q(organization__in=orgs) | Q(is_global=True)
    )

    context = {
        'action': 'Create',
        'templates': templates,
        'frequencies': ScheduledReport.FREQUENCY_CHOICES,
        'delivery_methods': ScheduledReport.DELIVERY_CHOICES,
    }

    return render(request, 'reports/scheduled_form.html', context)


@login_required
def scheduled_edit(request, pk):
    """Edit a scheduled report"""
    orgs = get_user_organizations(request.user)
    schedule = get_object_or_404(
        ScheduledReport,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        schedule.name = request.POST.get('name')
        template_id = request.POST.get('template')
        schedule.template = get_object_or_404(ReportTemplate, pk=template_id)
        schedule.frequency = request.POST.get('frequency')
        schedule.delivery_method = request.POST.get('delivery_method')
        recipients_str = request.POST.get('recipients', '')
        schedule.recipients = [email.strip() for email in recipients_str.split(',') if email.strip()]
        schedule.save()

        messages.success(request, f'Scheduled report "{schedule.name}" updated successfully.')
        return redirect('reports:scheduled_list')

    templates = ReportTemplate.objects.filter(
        Q(organization=schedule.organization) | Q(is_global=True)
    )

    context = {
        'schedule': schedule,
        'action': 'Edit',
        'templates': templates,
        'frequencies': ScheduledReport.FREQUENCY_CHOICES,
        'delivery_methods': ScheduledReport.DELIVERY_CHOICES,
        'recipients_str': ', '.join(schedule.recipients),
    }

    return render(request, 'reports/scheduled_form.html', context)


@login_required
def scheduled_delete(request, pk):
    """Delete a scheduled report"""
    orgs = get_user_organizations(request.user)
    schedule = get_object_or_404(
        ScheduledReport,
        pk=pk,
        organization__in=orgs
    )

    if request.method == 'POST':
        name = schedule.name
        schedule.delete()
        messages.success(request, f'Scheduled report "{name}" deleted successfully.')
        return redirect('reports:scheduled_list')

    context = {'schedule': schedule}
    return render(request, 'reports/scheduled_confirm_delete.html', context)


@login_required
def scheduled_toggle(request, pk):
    """Toggle a scheduled report active/inactive"""
    orgs = get_user_organizations(request.user)
    schedule = get_object_or_404(
        ScheduledReport,
        pk=pk,
        organization__in=orgs
    )

    schedule.is_active = not schedule.is_active
    schedule.save()

    status = 'enabled' if schedule.is_active else 'disabled'
    messages.success(request, f'Scheduled report "{schedule.name}" {status}.')

    return redirect('reports:scheduled_list')


@login_required
def analytics_overview(request):
    """Analytics overview dashboard"""
    orgs = get_user_organizations(request.user)

    # Get recent events
    recent_events = AnalyticsEvent.objects.filter(
        organization__in=orgs
    ).select_related('user')[:100]

    # Event counts by category
    events_by_category = AnalyticsEvent.objects.filter(
        organization__in=orgs,
        timestamp__gte=timezone.now() - timedelta(days=30)
    ).values('event_category').annotate(count=Count('id'))

    # Most active users
    active_users = AnalyticsEvent.objects.filter(
        organization__in=orgs,
        timestamp__gte=timezone.now() - timedelta(days=7)
    ).values('user__username').annotate(count=Count('id')).order_by('-count')[:10]

    context = {
        'recent_events': recent_events,
        'events_by_category': events_by_category,
        'active_users': active_users,
    }

    return render(request, 'reports/analytics_overview.html', context)


@login_required
def analytics_events(request):
    """Detailed analytics events list"""
    orgs = get_user_organizations(request.user)

    events = AnalyticsEvent.objects.filter(
        organization__in=orgs
    ).select_related('user').order_by('-timestamp')

    # Apply filters
    category = request.GET.get('category')
    if category:
        events = events.filter(event_category=category)

    event_name = request.GET.get('event')
    if event_name:
        events = events.filter(event_name__icontains=event_name)

    # Pagination
    events = events[:500]  # Limit to 500 most recent

    context = {
        'events': events,
        'categories': AnalyticsEvent.CATEGORY_CHOICES,
    }

    return render(request, 'reports/analytics_events.html', context)


# ---------------------------------------------------------------------------
# PSA reports — Workstream 6
# ---------------------------------------------------------------------------

@login_required
def psa_reports_list(request):
    """Catalog of PSA reports — click one to run it ad-hoc against the
    user's accessible orgs (or a specific picked client)."""
    from .generators import PSA_REPORT_DEFINITIONS
    return render(request, 'reports/psa_list.html', {
        'definitions': PSA_REPORT_DEFINITIONS,
    })


@login_required
def psa_report_run(request, report_type):
    """Run an ad-hoc PSA report. URL params:
       ?days=<n>&client=<org_id>
    Renders the result inline. CSV export via &format=csv."""
    from .generators import get_report_generator, PSA_REPORT_DEFINITIONS
    cls = get_report_generator(report_type)
    if cls is None or not report_type.startswith('psa_'):
        messages.error(request, 'Unknown PSA report.')
        return redirect('reports:psa_reports_list')

    # Tenant scope
    from core.models import Organization
    client_id = request.GET.get('client') or ''
    if request.user.is_superuser or getattr(request, 'is_staff_user', False):
        clients = Organization.objects.filter(is_active=True).order_by('name')
    else:
        ids = []
        if hasattr(request.user, 'memberships'):
            ids = list(request.user.memberships.filter(is_active=True).values_list('organization_id', flat=True))
        clients = Organization.objects.filter(id__in=ids, is_active=True).order_by('name')

    org = None
    if client_id:
        org = clients.filter(pk=client_id).first()
        if org is None:
            messages.error(request, "You don't have access to that client.")
            return redirect('reports:psa_reports_list')

    parameters = {'days': request.GET.get('days', 30)}
    generator = cls(organization=org, parameters=parameters)
    data = generator.generate()

    definition = next((d for d in PSA_REPORT_DEFINITIONS if d['type'] == report_type), None)

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        return _psa_report_csv(report_type, data)

    return render(request, 'reports/psa_run.html', {
        'definition': definition,
        'data': data,
        'clients': clients,
        'selected_org': org,
        'days': parameters['days'],
        'report_type': report_type,
    })


def _psa_report_csv(report_type, data):
    """Stream a CSV of the rows. Column set per report."""
    import csv as _csv
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{report_type}.csv"'
    writer = _csv.writer(response)

    if 'rows' in data and isinstance(data['rows'], list):
        rows = data['rows']
        if not rows:
            writer.writerow(['(no data)'])
            return response
        keys = list(rows[0].keys())
        writer.writerow(keys)
        for r in rows:
            writer.writerow([r.get(k, '') for k in keys])
        return response

    if 'by_queue' in data:  # PSATicketsByDimensionReport
        writer.writerow(['Dimension', 'Bucket', 'Count'])
        for r in data.get('by_queue', []):
            writer.writerow(['queue', r.get('queue__name'), r.get('count')])
        for r in data.get('by_type', []):
            writer.writerow(['type', r.get('ticket_type__name'), r.get('count')])
        for r in data.get('by_priority', []):
            writer.writerow(['priority', f"{r.get('priority__code', '')} {r.get('priority__name', '')}", r.get('count')])
        return response

    writer.writerow(['key', 'value'])
    for k, v in data.items():
        writer.writerow([k, v])
    return response


# ---------------------------------------------------------------------------
# Phase 3.1 — Profitability by Client (PSA)
# ---------------------------------------------------------------------------

def _is_staff_or_super(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def _parse_date(s, fallback):
    """Parse YYYY-MM-DD safely; fall back when missing or invalid."""
    if not s:
        return fallback
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return fallback


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_profitability_by_client(request):
    """
    Per-client profitability report — revenue, cost, margin over a
    user-chosen window. CSV via ?format=csv. Pulls from the canonical
    `reports.queries` layer.
    """
    from .queries import (
        profitability_by_client,
        DEFAULT_LOADED_RATE,
    )

    today = date.today()
    default_start = today - timedelta(days=30)
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    # Loaded rate override
    loaded_rate_raw = (request.GET.get('loaded_rate') or '').strip()
    loaded_rate = DEFAULT_LOADED_RATE
    if loaded_rate_raw:
        try:
            loaded_rate = Decimal(loaded_rate_raw)
        except (InvalidOperation, ValueError):
            loaded_rate = DEFAULT_LOADED_RATE

    rows = profitability_by_client(start_date, end_date,
                                   default_loaded_rate=loaded_rate)

    # Aggregates
    total_revenue = sum(r['revenue'] for r in rows)
    total_cost = sum(r['cost'] for r in rows)
    total_margin = total_revenue - total_cost
    blended_margin_pct = (total_margin / total_revenue * 100) if total_revenue else 0.0

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="profitability-by-client-'
            f'{start_date.isoformat()}-{end_date.isoformat()}.csv"'
        )
        writer = _csv.writer(response)
        writer.writerow(['Client', 'Hours', 'Revenue', 'Cost', 'Margin', 'Margin %'])
        for r in rows:
            writer.writerow([
                r['client_name'],
                f"{r['hours']:.2f}",
                f"{r['revenue']:.2f}",
                f"{r['cost']:.2f}",
                f"{r['margin']:.2f}",
                f"{r['margin_pct']:.1f}",
            ])
        # Totals row
        writer.writerow([
            'TOTAL', '',
            f"{total_revenue:.2f}", f"{total_cost:.2f}",
            f"{total_margin:.2f}", f"{round(blended_margin_pct, 1):.1f}",
        ])
        return response

    context = {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'loaded_rate': loaded_rate,
        'default_loaded_rate': DEFAULT_LOADED_RATE,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'blended_margin_pct': round(blended_margin_pct, 1),
    }
    return render(request, 'reports/psa_profitability_by_client.html', context)


# ---------------------------------------------------------------------------
# Phase 3.2 — Profitability by Tech / Contract / Project (PSA)
# ---------------------------------------------------------------------------

def _profitability_window(request):
    """Parse start/end/loaded_rate query params → tuple."""
    today = date.today()
    default_start = today - timedelta(days=30)
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    loaded_rate_raw = (request.GET.get('loaded_rate') or '').strip()
    from .queries import DEFAULT_LOADED_RATE
    loaded_rate = DEFAULT_LOADED_RATE
    if loaded_rate_raw:
        try:
            loaded_rate = Decimal(loaded_rate_raw)
        except (InvalidOperation, ValueError):
            loaded_rate = DEFAULT_LOADED_RATE
    return start_date, end_date, loaded_rate, DEFAULT_LOADED_RATE


def _csv_response(filename, header, rows, totals_row=None):
    """Helper: stream a CSV with a header, rows, and an optional totals row."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = _csv.writer(response)
    writer.writerow(header)
    for r in rows:
        writer.writerow(r)
    if totals_row:
        writer.writerow(totals_row)
    return response


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_profitability_by_tech(request):
    """Per-tech profitability — hours / cost / attributed revenue / margin /
    utilization %. CSV export via ?format=csv."""
    from .queries import profitability_by_tech

    start_date, end_date, loaded_rate, default_rate = _profitability_window(request)
    rows = profitability_by_tech(start_date, end_date,
                                 default_loaded_rate=loaded_rate)

    total_revenue = sum(r['attributed_revenue'] for r in rows)
    total_cost = sum(r['cost'] for r in rows)
    total_margin = total_revenue - total_cost
    blended_margin_pct = (total_margin / total_revenue * 100) if total_revenue else 0.0

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        return _csv_response(
            f'profitability-by-tech-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            ['Tech', 'Hours', 'Cost', 'Attributed Revenue', 'Margin', 'Margin %', 'Utilization %'],
            [[
                r['tech_username'],
                f"{r['hours']:.2f}",
                f"{r['cost']:.2f}",
                f"{r['attributed_revenue']:.2f}",
                f"{r['margin']:.2f}",
                f"{r['margin_pct']:.1f}",
                f"{r['utilization_pct']:.1f}",
            ] for r in rows],
            totals_row=[
                'TOTAL', '',
                f"{total_cost:.2f}", f"{total_revenue:.2f}",
                f"{total_margin:.2f}", f"{round(blended_margin_pct, 1):.1f}", '',
            ],
        )

    return render(request, 'reports/psa_profitability_by_tech.html', {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'loaded_rate': loaded_rate,
        'default_loaded_rate': default_rate,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'blended_margin_pct': round(blended_margin_pct, 1),
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_profitability_by_contract(request):
    """Per-contract profitability."""
    from .queries import profitability_by_contract

    start_date, end_date, loaded_rate, default_rate = _profitability_window(request)
    rows = profitability_by_contract(start_date, end_date,
                                     default_loaded_rate=loaded_rate)

    total_revenue = sum(r['revenue'] for r in rows)
    total_cost = sum(r['cost'] for r in rows)
    total_margin = total_revenue - total_cost
    blended_margin_pct = (total_margin / total_revenue * 100) if total_revenue else 0.0

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        return _csv_response(
            f'profitability-by-contract-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            ['Contract', 'Client', 'Hours', 'Revenue', 'Cost', 'Margin', 'Margin %'],
            [[
                r['contract_name'], r['client_name'],
                f"{r['hours']:.2f}", f"{r['revenue']:.2f}", f"{r['cost']:.2f}",
                f"{r['margin']:.2f}", f"{r['margin_pct']:.1f}",
            ] for r in rows],
            totals_row=[
                'TOTAL', '', '',
                f"{total_revenue:.2f}", f"{total_cost:.2f}",
                f"{total_margin:.2f}", f"{round(blended_margin_pct, 1):.1f}",
            ],
        )

    return render(request, 'reports/psa_profitability_by_contract.html', {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'loaded_rate': loaded_rate,
        'default_loaded_rate': default_rate,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'blended_margin_pct': round(blended_margin_pct, 1),
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_profitability_by_project(request):
    """Per-project profitability."""
    from .queries import profitability_by_project

    start_date, end_date, loaded_rate, default_rate = _profitability_window(request)
    rows = profitability_by_project(start_date, end_date,
                                    default_loaded_rate=loaded_rate)

    total_revenue = sum(r['revenue'] for r in rows)
    total_cost = sum(r['cost'] for r in rows)
    total_margin = total_revenue - total_cost
    blended_margin_pct = (total_margin / total_revenue * 100) if total_revenue else 0.0

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        return _csv_response(
            f'profitability-by-project-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            ['Project', 'Client', 'Hours', 'Revenue', 'Cost', 'Margin', 'Margin %'],
            [[
                r['project_name'], r['client_name'],
                f"{r['hours']:.2f}", f"{r['revenue']:.2f}", f"{r['cost']:.2f}",
                f"{r['margin']:.2f}", f"{r['margin_pct']:.1f}",
            ] for r in rows],
            totals_row=[
                'TOTAL', '', '',
                f"{total_revenue:.2f}", f"{total_cost:.2f}",
                f"{total_margin:.2f}", f"{round(blended_margin_pct, 1):.1f}",
            ],
        )

    return render(request, 'reports/psa_profitability_by_project.html', {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'loaded_rate': loaded_rate,
        'default_loaded_rate': default_rate,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'blended_margin_pct': round(blended_margin_pct, 1),
    })


# ---------------------------------------------------------------------------
# Phase 3.3 — Effective hourly rate + Revenue leakage (PSA)
# ---------------------------------------------------------------------------

def _median(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_effective_hourly_rate(request):
    """
    Effective hourly rate report — `revenue ÷ billable_hours` per client
    (default tab) or per tech. Two tabs share the same date-range picker.
    CSV export per tab via `?format=csv&tab=client|tech`.
    """
    from .queries import (
        effective_hourly_rate_by_client,
        effective_hourly_rate_by_tech,
    )

    today = date.today()
    default_start = today - timedelta(days=30)
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    tab = (request.GET.get('tab') or 'client').lower()
    if tab not in ('client', 'tech'):
        tab = 'client'

    client_rows = effective_hourly_rate_by_client(start_date, end_date)
    tech_rows = effective_hourly_rate_by_tech(start_date, end_date)

    # Summary stats are computed on the active tab's rates (excluding
    # zero-rates from rows with no billable hours so they don't drag
    # avg/median to zero).
    active_rows = client_rows if tab == 'client' else tech_rows
    rate_vals = [r['effective_rate'] for r in active_rows
                 if r['effective_rate'] > 0]
    avg_rate = round(sum(rate_vals) / len(rate_vals), 2) if rate_vals else 0.0
    highest = max(rate_vals) if rate_vals else 0.0
    lowest = min(rate_vals) if rate_vals else 0.0
    median = round(_median(rate_vals), 2)

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        if tab == 'tech':
            return _csv_response(
                f'effective-rate-by-tech-{start_date.isoformat()}-{end_date.isoformat()}.csv',
                ['Tech', 'Billable Hours', 'Attributed Revenue',
                 'Effective Rate ($/hr)', 'Cost Rate ($/hr)', 'Realization %'],
                [[
                    r['tech_username'],
                    f"{r['billable_hours']:.2f}",
                    f"{r['attributed_revenue']:.2f}",
                    f"{r['effective_rate']:.2f}",
                    f"{r['cost_rate']:.2f}",
                    f"{r['realization_pct']:.1f}",
                ] for r in tech_rows],
            )
        return _csv_response(
            f'effective-rate-by-client-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            ['Client', 'Revenue', 'Billable Hours', 'Non-billable Hours',
             'Effective Rate ($/hr)', 'Utilization Ratio'],
            [[
                r['client_name'],
                f"{r['revenue']:.2f}",
                f"{r['billable_hours']:.2f}",
                f"{r['nonbillable_hours']:.2f}",
                f"{r['effective_rate']:.2f}",
                f"{r['utilization_ratio']:.3f}",
            ] for r in client_rows],
        )

    return render(request, 'reports/psa_effective_hourly_rate.html', {
        'tab': tab,
        'client_rows': client_rows,
        'tech_rows': tech_rows,
        'start_date': start_date,
        'end_date': end_date,
        'avg_rate': avg_rate,
        'highest': highest,
        'lowest': lowest,
        'median': median,
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_revenue_leakage(request):
    """
    Revenue leakage report — three categories of "money you should have
    collected but didn't" on one page. Stale-days input + CSV export
    that combines all three sections with a section column.
    """
    from .queries import revenue_leakage

    today = date.today()
    default_start = today - timedelta(days=180)
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    try:
        stale_days = max(1, int(request.GET.get('stale_days') or 30))
    except (TypeError, ValueError):
        stale_days = 30

    data = revenue_leakage(start_date, end_date, stale_days=stale_days)

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="revenue-leakage-'
            f'{start_date.isoformat()}-{end_date.isoformat()}.csv"'
        )
        writer = _csv.writer(response)
        writer.writerow([
            'Section', 'Client', 'Detail', 'Quantity', 'Amount',
        ])
        for r in data['stale_unbilled']:
            writer.writerow([
                'Stale Unbilled', r['client_name'],
                f"oldest {r['oldest_at']}, {r['entry_count']} entries",
                f"{r['hours_at_risk']:.2f} h",
                f"{r['amount_at_risk']:.2f}",
            ])
        for r in data['expired_blocks']:
            writer.writerow([
                'Expired Block', r['client_name'], r['contract_name'],
                f"{r['unused_hours']:.2f} h unused",
                f"{r['unused_value']:.2f}",
            ])
        for r in data['stuck_drafts']:
            writer.writerow([
                'Stuck Draft', r['client_name'], r['invoice_number'],
                f"{r['days_stuck']} days stuck",
                f"{r['amount']:.2f}",
            ])
        writer.writerow([
            'TOTAL', '', '', '',
            f"{data['totals']['grand_total']:.2f}",
        ])
        return response

    return render(request, 'reports/psa_revenue_leakage.html', {
        'data': data,
        'start_date': start_date,
        'end_date': end_date,
        'stale_days': stale_days,
    })


# ---------------------------------------------------------------------------
# Phase 3.4 — SLA trends + Margin analytics by service line
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_sla_trends(request):
    """
    SLA breach trend report — per-priority response + resolution breach %
    over a bucketed window (day / week / month). Side panel lists the
    top-N clients by ticket volume with their breach %.
    CSV via ?format=csv&tab=summary|by_client.
    """
    from .queries import sla_trend_by_priority, sla_trend_by_client

    today = date.today()
    default_start = today - timedelta(days=89)  # last 90 days inclusive
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    bucket = (request.GET.get('bucket') or 'week').lower()
    if bucket not in ('day', 'week', 'month'):
        bucket = 'week'

    trend = sla_trend_by_priority(start_date, end_date, bucket=bucket)
    top_clients = sla_trend_by_client(start_date, end_date, top_n=10)

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        tab = (request.GET.get('tab') or 'summary').lower()
        if tab == 'by_client':
            return _csv_response(
                f'sla-trend-by-client-{start_date.isoformat()}-{end_date.isoformat()}.csv',
                ['Client', 'Tickets', 'Response Breaches', 'Resolution Breaches',
                 'Response %', 'Resolution %'],
                [[
                    r['client_name'], r['tickets'],
                    r['response_breaches'], r['resolution_breaches'],
                    f"{r['response_pct']:.1f}", f"{r['resolution_pct']:.1f}",
                ] for r in top_clients],
            )
        # Default: per-priority summary
        return _csv_response(
            f'sla-trend-summary-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            ['Priority', 'Tickets', 'Response Breaches', 'Resolution Breaches',
             'Response %', 'Resolution %'],
            [[
                p,
                trend['totals_by_priority'][p]['tickets'],
                trend['totals_by_priority'][p]['response_breaches'],
                trend['totals_by_priority'][p]['resolution_breaches'],
                f"{trend['totals_by_priority'][p]['response_pct']:.1f}",
                f"{trend['totals_by_priority'][p]['resolution_pct']:.1f}",
            ] for p in trend['priorities']],
        )

    # Pre-encode chart payloads as JSON for safe embedding in <script>.
    response_chart = {
        'labels': trend['buckets'],
        'series': [
            {
                'name': p,
                'data': [row['response_pct'] for row in trend['series'][p]],
            }
            for p in trend['priorities']
        ],
    }
    resolution_chart = {
        'labels': trend['buckets'],
        'series': [
            {
                'name': p,
                'data': [row['resolution_pct'] for row in trend['series'][p]],
            }
            for p in trend['priorities']
        ],
    }
    response_chart_json = json.dumps(response_chart).replace('</', '<\\/')
    resolution_chart_json = json.dumps(resolution_chart).replace('</', '<\\/')

    # Build a totals list ordered by the priorities list for the template.
    totals_rows = [
        {
            'priority': p,
            **trend['totals_by_priority'][p],
        }
        for p in trend['priorities']
    ]

    return render(request, 'reports/psa_sla_trends.html', {
        'start_date': start_date,
        'end_date': end_date,
        'bucket': bucket,
        'priorities': trend['priorities'],
        'totals_rows': totals_rows,
        'top_clients': top_clients,
        'response_chart_json': response_chart_json,
        'resolution_chart_json': resolution_chart_json,
        'has_buckets': bool(trend['buckets']),
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def psa_margin_analytics(request):
    """
    Margin grouped by service-line dimension — ticket_type / closure_category
    / queue. Bar chart + sortable table + CSV export.
    """
    from .queries import margin_analytics_by_service_line

    today = date.today()
    default_start = today - timedelta(days=30)
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    dimension = (request.GET.get('dimension') or 'ticket_type').lower()
    if dimension not in ('ticket_type', 'closure_category', 'queue'):
        dimension = 'ticket_type'

    rows = margin_analytics_by_service_line(start_date, end_date,
                                            dimension=dimension)

    total_revenue = sum(r['revenue'] for r in rows)
    total_cost = sum(r['cost'] for r in rows)
    total_margin = total_revenue - total_cost
    blended_margin_pct = (total_margin / total_revenue * 100) if total_revenue else 0.0

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        return _csv_response(
            f'margin-analytics-{dimension}-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            [dimension.replace('_', ' ').title(), 'Tickets', 'Hours',
             'Revenue', 'Cost', 'Margin', 'Margin %'],
            [[
                r['label'], r['tickets'], f"{r['hours']:.2f}",
                f"{r['revenue']:.2f}", f"{r['cost']:.2f}",
                f"{r['margin']:.2f}", f"{r['margin_pct']:.1f}",
            ] for r in rows],
            totals_row=[
                'TOTAL', '', '',
                f"{total_revenue:.2f}", f"{total_cost:.2f}",
                f"{total_margin:.2f}", f"{round(blended_margin_pct, 1):.1f}",
            ],
        )

    chart_payload = {
        'labels': [r['label'] for r in rows],
        'series': [
            {'name': 'Revenue', 'data': [r['revenue'] for r in rows]},
            {'name': 'Cost', 'data': [r['cost'] for r in rows]},
        ],
    }
    chart_json = json.dumps(chart_payload).replace('</', '<\\/')

    dimension_choices = [
        ('ticket_type', 'Ticket Type'),
        ('closure_category', 'Closure Category'),
        ('queue', 'Queue'),
    ]

    return render(request, 'reports/psa_margin_analytics.html', {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'dimension': dimension,
        'dimension_choices': dimension_choices,
        'chart_json': chart_json,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'blended_margin_pct': round(blended_margin_pct, 1),
    })
