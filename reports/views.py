"""
Views for Reports and Analytics
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, FileResponse, JsonResponse
from django.db.models import Count, Q, Sum
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from accounts.permission_utils import user_has_perm, require_perm
from .models import (
    Dashboard, DashboardWidget, ReportTemplate, GeneratedReport,
    ScheduledReport, AnalyticsEvent, Wallboard, WallboardWidget,
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
@require_perm('reports_view_dashboards')
def reports_home(request):
    """Reports and Analytics home page"""
    orgs = get_user_organizations(request.user)

    perm = {
        'view_dashboards': True,  # we got past require_perm above
        'view_financial': user_has_perm(request.user, 'reports_view_financial'),
        'view_sla': user_has_perm(request.user, 'reports_view_sla'),
        'view_capacity': user_has_perm(request.user, 'reports_view_capacity'),
        'manage_dashboards': user_has_perm(request.user, 'reports_manage_dashboards'),
        'manage_scheduled': user_has_perm(request.user, 'reports_manage_scheduled'),
        'crm_view_forecast': user_has_perm(request.user, 'crm_view_forecast'),
    }

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
        'perm': perm,
    }

    return render(request, 'reports/home.html', context)


@login_required
@require_perm('reports_view_dashboards')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
@require_perm('reports_manage_dashboards')
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
        # For now, we'll generate synchronously and write to the FileField.
        from django.core.files.base import ContentFile
        from .generators import generate_report as _generate_report

        try:
            filename, bytes_data = _generate_report(
                template,
                output_format=report_format,
                organization=org,
                parameters=parameters,
            )
            report.file.save(filename, ContentFile(bytes_data), save=False)
            report.file_size = len(bytes_data) if bytes_data else 0
            report.status = 'completed'
            report.completed_at = timezone.now()
            report.save()

            messages.success(request, f'Report "{template.name}" generated successfully.')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_view_dashboards')
def generated_download(request, pk):
    """View / download a generated report. PDFs render inline in a new
    window (the calling template uses target=_blank); CSV / XLSX / etc.
    download as attachments."""
    orgs = get_user_organizations(request.user)
    report = get_object_or_404(
        GeneratedReport,
        pk=pk,
        organization__in=orgs
    )

    if not report.file:
        messages.error(request, 'Report file not found.')
        return redirect('reports:generated_detail', pk=pk)

    fmt = (report.format or '').lower()
    is_pdf = fmt == 'pdf' or report.file.name.lower().endswith('.pdf')
    response = FileResponse(report.file.open('rb'), as_attachment=not is_pdf)
    if is_pdf:
        # Force inline rendering in browser
        filename = report.file.name.split('/')[-1]
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        response['Content-Type'] = 'application/pdf'
    return response


@login_required
@require_perm('reports_view_dashboards')
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
@require_perm('reports_manage_scheduled')
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
@require_perm('reports_manage_scheduled')
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
@require_perm('reports_manage_scheduled')
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
@require_perm('reports_manage_scheduled')
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
@require_perm('reports_manage_scheduled')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_view_dashboards')
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
@require_perm('reports_view_financial')
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
@require_perm('reports_view_financial')
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
@require_perm('reports_view_financial')
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
@require_perm('reports_view_financial')
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
@require_perm('reports_view_financial')
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
@require_perm('reports_view_financial')
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
@require_perm('reports_view_sla')
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
@require_perm('reports_view_financial')
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


# ---------------------------------------------------------------------------
# Phase 3.6 wave A — Wallboard + Executive scorecard
# ---------------------------------------------------------------------------

@login_required
@require_perm('reports_view_dashboards')
def wallboard(request):
    """TV-ready wallboard view. Refreshes via JS poll to /reports/wallboard/data/."""
    try:
        refresh = int(request.GET.get('refresh') or 30)
    except (TypeError, ValueError):
        refresh = 30
    refresh = max(5, min(refresh, 600))
    return render(request, 'reports/wallboard.html', {
        'refresh_interval_seconds': refresh,
    })


@login_required
@require_perm('reports_view_dashboards')
def wallboard_data(request):
    """JSON endpoint polled by the wallboard page every N seconds."""
    from psa.models import Ticket
    from django.contrib.auth.models import User

    today = timezone.now().date()

    # Mega-tile counts
    open_qs = Ticket.objects.filter(status__is_terminal=False)
    sla_overdue = open_qs.filter(resolution_due_at__lt=timezone.now()).count()
    unassigned = open_qs.filter(assigned_to__isnull=True).count()
    p1 = open_qs.filter(priority__code='P1').count()
    opened_today = Ticket.objects.filter(created_at__date=today).count()
    closed_today = Ticket.objects.filter(
        closed_at__date=today, status__is_terminal=True
    ).count()
    open_total = open_qs.count()

    # Tile color logic — green/amber/red banding
    def color(n, low, high):
        if n < low:
            return 'success'
        if n < high:
            return 'warning'
        return 'danger'

    tiles = [
        {'label': 'Open Tickets', 'value': open_total,
         'color': color(open_total, 50, 100), 'icon': 'fa-ticket'},
        {'label': 'SLA Overdue', 'value': sla_overdue,
         'color': color(sla_overdue, 1, 5), 'icon': 'fa-triangle-exclamation'},
        {'label': 'Unassigned', 'value': unassigned,
         'color': color(unassigned, 5, 15), 'icon': 'fa-circle-question'},
        {'label': 'P1 Open', 'value': p1,
         'color': color(p1, 1, 3), 'icon': 'fa-fire'},
        {'label': 'Opened Today', 'value': opened_today,
         'color': 'info', 'icon': 'fa-arrow-up'},
        {'label': 'Closed Today', 'value': closed_today,
         'color': 'success', 'icon': 'fa-check'},
    ]

    # Recent tickets ticker (5 most recent)
    recent = (
        Ticket.objects
        .select_related('priority', 'organization')
        .order_by('-created_at')[:5]
    )
    recent_data = []
    for t in recent:
        pcode = t.priority.code if t.priority_id else ''
        priority_color = {
            'P1': 'danger', 'P2': 'warning', 'P3': 'info',
            'P4': 'secondary', 'P5': 'light',
        }.get(pcode, 'secondary')
        recent_data.append({
            'ticket_number': t.ticket_number,
            'subject': (t.subject or '')[:80],
            'priority': pcode,
            'priority_color': priority_color,
            'organization': t.organization.name if t.organization_id else '—',
            'created_ago_min': int(
                (timezone.now() - t.created_at).total_seconds() / 60
            ),
        })

    # On-shift techs — uses UserProfile.is_working_now()
    techs_on_shift = []
    for u in User.objects.filter(
        is_active=True, is_staff=True
    ).exclude(username='AnonymousUser'):
        profile = getattr(u, 'profile', None)
        if profile is None:
            continue
        try:
            if profile.is_working_now():
                techs_on_shift.append({'username': u.username})
        except Exception:
            # Be defensive — never let a bad profile break the wallboard.
            continue

    return JsonResponse({
        'tiles': tiles,
        'recent_tickets': recent_data,
        'techs_on_shift': techs_on_shift,
        'as_of': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@login_required
@require_perm('reports_view_financial')
def exec_scorecard(request):
    """Rolling 30d executive scorecard for the owner.

    Single-page MSP KPI summary — hero cards + trend chart + top-clients
    / top-techs tables + service-line margin pie. Print-friendly.
    """
    from datetime import date as _date, timedelta as _td
    from psa.models import Ticket, TicketTimeEntry, Invoice
    from .queries import (
        revenue_by_client, hours_minutes_by_client, hours_minutes_by_tech,
        sla_trend_by_priority, margin_analytics_by_service_line,
    )

    today = _date.today()
    start_30 = today - _td(days=29)
    prior_start = today - _td(days=59)
    prior_end = start_30 - _td(days=1)

    # --- Hero KPIs ----------------------------------------------------------
    rev_30 = sum(r['invoiced'] for r in revenue_by_client(start_30, today))
    rev_prior = sum(r['invoiced'] for r in revenue_by_client(prior_start, prior_end))
    rev_trend_pct = ((rev_30 - rev_prior) / rev_prior * 100) if rev_prior else 0.0

    hrs_rows = hours_minutes_by_client(start_30, today)
    total_billable_min = sum(r['billable_minutes'] for r in hrs_rows)
    total_billable_hrs = total_billable_min / 60.0
    realized_rate = (rev_30 / total_billable_hrs) if total_billable_hrs else 0.0

    open_tickets = Ticket.objects.filter(status__is_terminal=False).count()

    # SLA breach %
    sla = sla_trend_by_priority(start_30, today, bucket='month')
    total_tix = sum(t['tickets'] for t in sla['totals_by_priority'].values())
    total_breach = sum(t['resolution_breaches']
                       for t in sla['totals_by_priority'].values())
    sla_breach_pct = (total_breach / total_tix * 100) if total_tix else 0.0

    # MTTR — avg close time over 30d
    closed = Ticket.objects.filter(
        closed_at__gte=timezone.now() - timedelta(days=30),
        status__is_terminal=True,
    ).exclude(closed_at__isnull=True)
    mttr_secs = 0
    cnt = 0
    for t in closed:
        mttr_secs += (t.closed_at - t.created_at).total_seconds()
        cnt += 1
    mttr_hours = (mttr_secs / cnt / 3600) if cnt else 0.0

    active_clients = (
        Ticket.objects
        .filter(created_at__date__gte=start_30)
        .values('organization').distinct().count()
    )

    # Tech utilization (avg actual hrs / target hrs across all techs with targets)
    from resourcing.models import BillableTarget
    util_pcts = []
    for bt in BillableTarget.objects.filter(is_active=True).select_related('user'):
        target_hrs = float(bt.target_hours_per_week) * 4.3  # ~30 days
        actual_min = TicketTimeEntry.objects.filter(
            user=bt.user, started_at__date__gte=start_30,
        ).aggregate(s=Sum('duration_minutes'))['s'] or 0
        actual_hrs = actual_min / 60.0
        if target_hrs:
            util_pcts.append((actual_hrs / target_hrs) * 100)
    avg_util = sum(util_pcts) / len(util_pcts) if util_pcts else 0.0

    kpis = [
        {'label': 'Revenue (30d)', 'value': f'${rev_30:,.0f}',
         'trend_pct': round(rev_trend_pct, 1), 'icon': 'fa-dollar-sign'},
        {'label': 'Billable Hours', 'value': f'{total_billable_hrs:,.0f}h',
         'trend_pct': None, 'icon': 'fa-clock'},
        {'label': 'Realized Rate', 'value': f'${realized_rate:,.0f}/hr',
         'trend_pct': None, 'icon': 'fa-tachometer-alt'},
        {'label': 'Open Tickets', 'value': str(open_tickets),
         'trend_pct': None, 'icon': 'fa-ticket'},
        {'label': 'SLA Breach %', 'value': f'{sla_breach_pct:.1f}%',
         'trend_pct': None, 'icon': 'fa-triangle-exclamation'},
        {'label': 'Avg MTTR', 'value': f'{mttr_hours:.1f}h',
         'trend_pct': None, 'icon': 'fa-stopwatch'},
        {'label': 'Active Clients', 'value': str(active_clients),
         'trend_pct': None, 'icon': 'fa-building'},
        {'label': 'Tech Utilization', 'value': f'{avg_util:.0f}%',
         'trend_pct': None, 'icon': 'fa-users-gear'},
    ]

    # Top 5 clients by revenue
    top_clients = sorted(
        revenue_by_client(start_30, today),
        key=lambda r: r['invoiced'], reverse=True,
    )[:5]
    # Top 5 techs by billable hours
    top_techs = sorted(
        hours_minutes_by_tech(start_30, today),
        key=lambda r: r['billable_minutes'], reverse=True,
    )[:5]
    for r in top_techs:
        r['billable_hours'] = round(r['billable_minutes'] / 60.0, 1)

    # Margin by service line — top 5 + Other
    margin_rows = margin_analytics_by_service_line(
        start_30, today, dimension='ticket_type'
    )
    pie_data = []
    for r in margin_rows[:5]:
        pie_data.append({'label': r['label'], 'value': r['margin']})
    if len(margin_rows) > 5:
        other_total = sum(r['margin'] for r in margin_rows[5:])
        pie_data.append({'label': 'Other', 'value': other_total})

    # 30d daily revenue + ticket counts for the dual-line chart
    days = [start_30 + timedelta(days=i) for i in range(30)]
    rev_by_day = {d: 0.0 for d in days}
    for inv in Invoice.objects.filter(
        invoice_date__gte=start_30, invoice_date__lte=today,
        status__in=['sent', 'partial', 'paid', 'overdue'],
    ):
        if inv.invoice_date in rev_by_day:
            rev_by_day[inv.invoice_date] += float(inv.total or 0)
    tix_by_day = {d: 0 for d in days}
    for t in Ticket.objects.filter(
        created_at__date__gte=start_30, created_at__date__lte=today,
    ):
        d = t.created_at.date()
        if d in tix_by_day:
            tix_by_day[d] += 1

    chart = {
        'labels': [d.strftime('%m/%d') for d in days],
        'revenue': [round(rev_by_day[d], 2) for d in days],
        'tickets': [tix_by_day[d] for d in days],
    }
    chart_json = json.dumps(chart).replace('</', '<\\/')
    pie_json = json.dumps(pie_data).replace('</', '<\\/')

    return render(request, 'reports/exec_scorecard.html', {
        'kpis': kpis,
        'top_clients': top_clients,
        'top_techs': top_techs,
        'pie_data': pie_data,
        'pie_json': pie_json,
        'chart': chart,
        'chart_json': chart_json,
        'as_of': timezone.now(),
        'window_start': start_30,
        'window_end': today,
    })


# ---------------------------------------------------------------------------
# Phase 3.6 wave B — Client-health score report (v3.17.147)
# ---------------------------------------------------------------------------

@login_required
@require_perm('reports_view_financial')
def psa_client_health(request):
    """Composite client-health score report. CSV via ?format=csv."""
    from .queries import client_health_scores_all
    rows = client_health_scores_all()
    summary = {
        'healthy': sum(1 for r in rows if r['category'] == 'healthy'),
        'at_risk': sum(1 for r in rows if r['category'] == 'at_risk'),
        'trouble': sum(1 for r in rows if r['category'] == 'trouble'),
        'total': len(rows),
    }
    if (request.GET.get('format') or '').lower() == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="client_health.csv"'
        w = _csv.writer(resp)
        w.writerow(['Client', 'Score', 'Category',
                    'SLA', 'Velocity', 'Aging', 'Engagement', 'NPS',
                    'Tickets 30d', 'Over 60d ($)'])
        for r in rows:
            c = r['components']
            m = r['metrics']
            w.writerow([
                r['client_name'], r['score'], r['category'],
                c['sla'], c['velocity'], c['aging'], c['engagement'], c['nps'],
                m['total_tickets_30d'], m['over_60_days'],
            ])
        return resp
    return render(request, 'reports/psa_client_health.html', {
        'rows': rows, 'summary': summary,
    })


# ---------------------------------------------------------------------------
# Phase 5.2 — CRM sales funnel report
# ---------------------------------------------------------------------------

@login_required
@require_perm('crm_view_forecast')
def crm_sales_funnel(request):
    """
    Visual funnel from Leads → Qualified → Opportunities → Proposal → Closed
    Won, with stage-to-stage conversion rates. Date-range selector via
    ?days=7|30|90|YTD; defaults to 30.
    """
    from .queries import sales_funnel
    today = date.today()
    days_raw = (request.GET.get('days') or '30').strip()
    if days_raw.upper() == 'YTD':
        days_label = 'YTD'
        start = date(today.year, 1, 1)
        days = (today - start).days + 1
    else:
        try:
            days = max(1, int(days_raw))
        except (TypeError, ValueError):
            days = 30
        days_label = str(days)
        start = today - timedelta(days=days - 1)
    funnel = sales_funnel(start, today)
    return render(request, 'reports/crm_sales_funnel.html', {
        'funnel': funnel,
        'days': days,
        'days_label': days_label,
        'start': start,
        'end': today,
    })


# ---------------------------------------------------------------------------
# Phase 9.4 — Security alert MTTA report
# ---------------------------------------------------------------------------

@login_required
@require_perm('reports_view_sla')
def security_alert_mtta_report(request):
    """
    Mean Time To Acknowledge for security alerts in a date window,
    bucketed per (client × vendor). CSV via ?format=csv.
    """
    from .queries import security_alert_mtta

    today = date.today()
    default_start = today - timedelta(days=29)
    start_date = _parse_date(request.GET.get('start'), default_start)
    end_date = _parse_date(request.GET.get('end'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    rows = security_alert_mtta(start_date, end_date)

    fmt = (request.GET.get('format') or 'html').lower()
    if fmt == 'csv':
        return _csv_response(
            f'security-alert-mtta-{start_date.isoformat()}-{end_date.isoformat()}.csv',
            ['Client', 'Vendor', 'Count', 'Avg MTTA (min)', 'Unacked'],
            [[
                r['client_name'], r['vendor'], r['count'],
                f"{r['avg_mtta_minutes']:.1f}" if r['avg_mtta_minutes'] is not None else '—',
                r['unack_count'],
            ] for r in rows],
        )

    return render(request, 'reports/security_alert_mtta.html', {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
    })


# ---------------------------------------------------------------------------
# v3.17.211 — Configurable wallboards
# ---------------------------------------------------------------------------

def _user_can_see_wallboards(user, organization):
    """
    Wallboards are visible to:
      - superuser / staff users — always (any org board, plus globals).
      - any other user — only their own active-membership orgs.

    `organization=None` means a v3.17.216 "global" board: visible
    only to staff/superuser. Plain org members never see globals.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, 'is_staff', False):
        return True
    if organization is None:
        return False
    return user.memberships.filter(
        organization=organization, is_active=True,
    ).exists()


@login_required
@require_perm('reports_view_dashboards')
def wallboard_list(request):
    """
    List wallboards. Org-scoped boards are filtered to the user's accessible
    orgs; global boards (organization=NULL, v3.17.216) are added for
    staff/superusers only.
    """
    orgs = get_user_organizations(request.user)
    user = request.user
    is_staff_like = user.is_superuser or getattr(user, 'is_staff', False)
    qs = Wallboard.objects.filter(
        Q(organization__in=orgs) | (Q(organization__isnull=True) if is_staff_like else Q(pk__in=[]))
    ).select_related('organization')
    return render(request, 'reports/wallboard_list.html', {
        'wallboards': qs.order_by('organization__name', 'order', 'name'),
        'can_create_global': is_staff_like,
    })


@login_required
@require_perm('reports_view_dashboards')
def wallboard_view(request, pk):
    """
    Render a single wallboard full-screen.

    The page meta-refreshes every `refresh_seconds` for whole-page updates.
    Per-widget refresh_seconds overrides aren't enforced server-side (one
    refresh applies to the whole rendered page); they're informational
    until the JS-side per-widget refresher ships in a future sub-phase.
    """
    from .widget_sources import get_widget_data

    board = get_object_or_404(Wallboard, pk=pk)
    if not _user_can_see_wallboards(request.user, board.organization):
        from django.http import Http404
        raise Http404('Wallboard not found')

    rendered_widgets = []
    has_chart = False
    for w in board.widgets.order_by('order', 'created_at'):
        data = get_widget_data(w.data_source, w.query_params or {})
        if w.widget_type in ('chart_line', 'chart_bar', 'chart_pie'):
            has_chart = True
        rendered_widgets.append({
            'widget': w,
            'data': data,
            'data_json': json.dumps(data, default=str),
        })

    return render(request, 'reports/wallboard_view.html', {
        'wallboard': board,
        'rendered_widgets': rendered_widgets,
        'refresh_seconds': board.refresh_seconds,
        'has_chart': has_chart,
    })


@login_required
@require_perm('reports_view_dashboards')
def wallboard_rotate(request, pk):
    """
    NOC-TV mode: render the wallboard, but on the next page-refresh
    (after `rotate_seconds`) navigate to the NEXT wallboard in the org's
    rotation. Boards with rotate_seconds=0 are skipped from rotation.

    Implementation is a meta-refresh redirect chain — works on any TV
    browser, no JS or cookies required.
    """
    board = get_object_or_404(Wallboard, pk=pk)
    if not _user_can_see_wallboards(request.user, board.organization):
        from django.http import Http404
        raise Http404('Wallboard not found')

    # Compute the rotation target. If rotation isn't enabled on this
    # board, fall through and the page refreshes itself (no redirect).
    if board.rotate_seconds > 0:
        next_board = board.next_in_rotation()
        rotate_target_pk = next_board.pk
        rotate_seconds = board.rotate_seconds
    else:
        rotate_target_pk = None
        rotate_seconds = 0

    from .widget_sources import get_widget_data
    rendered_widgets = []
    has_chart = False
    for w in board.widgets.order_by('order', 'created_at'):
        data = get_widget_data(w.data_source, w.query_params or {})
        if w.widget_type in ('chart_line', 'chart_bar', 'chart_pie'):
            has_chart = True
        rendered_widgets.append({
            'widget': w,
            'data': data,
            'data_json': json.dumps(data, default=str),
        })

    return render(request, 'reports/wallboard_view.html', {
        'wallboard': board,
        'rendered_widgets': rendered_widgets,
        'refresh_seconds': board.refresh_seconds,
        'rotate_target_pk': rotate_target_pk,
        'rotate_seconds': rotate_seconds,
        'has_chart': has_chart,
    })


@login_required
@require_perm('reports_manage_dashboards')
def wallboard_form(request, pk=None):
    """Create or edit a wallboard. Widget editing is admin-only via the
    Django admin for now; this form covers the wallboard fields only."""
    instance = None
    if pk is not None:
        instance = get_object_or_404(Wallboard, pk=pk)
        if not _user_can_see_wallboards(request.user, instance.organization):
            from django.http import Http404
            raise Http404('Wallboard not found')

    orgs = get_user_organizations(request.user)
    is_staff_like = request.user.is_superuser or getattr(request.user, 'is_staff', False)

    if request.method == 'POST':
        org_id = request.POST.get('organization')
        name = (request.POST.get('name') or '').strip()
        description = (request.POST.get('description') or '').strip()
        try:
            refresh_seconds = max(0, int(request.POST.get('refresh_seconds') or 60))
            rotate_seconds = max(0, int(request.POST.get('rotate_seconds') or 0))
            order = max(0, int(request.POST.get('order') or 100))
        except ValueError:
            messages.error(request, 'Refresh / rotate / order must be integers.')
            return redirect(request.path)
        is_active = bool(request.POST.get('is_active'))

        if not name:
            messages.error(request, 'Name is required.')
            return redirect(request.path)

        if instance is None:
            # v3.17.216: org_id == "" or "global" + staff-like user → null org
            from core.models import Organization
            if (org_id in ('', 'global', None)) and is_staff_like:
                org = None
            else:
                try:
                    org = orgs.get(pk=org_id)
                except (Organization.DoesNotExist, ValueError, TypeError):
                    messages.error(request, 'Pick an organization you belong to.')
                    return redirect(request.path)
            instance = Wallboard.objects.create(
                organization=org, name=name, description=description,
                refresh_seconds=refresh_seconds, rotate_seconds=rotate_seconds,
                order=order, is_active=is_active,
                created_by=request.user,
            )
            label = instance.name + (' (Global)' if org is None else '')
            messages.success(request, f'Wallboard "{label}" created.')
        else:
            instance.name = name
            instance.description = description
            instance.refresh_seconds = refresh_seconds
            instance.rotate_seconds = rotate_seconds
            instance.order = order
            instance.is_active = is_active
            instance.save()
            messages.success(request, f'Wallboard "{instance.name}" updated.')
        return redirect('reports:wallboard_list')

    return render(request, 'reports/wallboard_form.html', {
        'wallboard': instance,
        'organizations': orgs,
        'can_create_global': is_staff_like,
    })


@login_required
@require_perm('reports_manage_dashboards')
def wallboard_widget_reorder(request, pk):
    """
    v3.17.215: persist a new widget order for a wallboard.

    Accepts POST with body `{"order": [<widget_pk>, <widget_pk>, ...]}`
    (JSON) or form-encoded `order=<pk>&order=<pk>&...`. Updates
    `WallboardWidget.order` to match the supplied sequence (10, 20, 30,
    … so manually-edited order values stay legible). Returns JSON
    `{ok: True, count: N}`.

    Tenant-scoped: cross-org wallboard pks 404. Rejects non-POST.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    board = get_object_or_404(Wallboard, pk=pk)
    if not _user_can_see_wallboards(request.user, board.organization):
        from django.http import Http404
        raise Http404('Wallboard not found')

    # Accept either JSON body or form-encoded list.
    ids = []
    ctype = (request.META.get('CONTENT_TYPE') or '').lower()
    if 'application/json' in ctype:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        ids = payload.get('order') or []
    else:
        ids = request.POST.getlist('order')

    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return JsonResponse({'error': 'order must be a list of widget pks'}, status=400)

    valid_ids = set(board.widgets.values_list('pk', flat=True))
    if not set(ids).issubset(valid_ids):
        return JsonResponse(
            {'error': 'one or more widget pks do not belong to this wallboard'},
            status=400,
        )

    rank = 10
    for wid in ids:
        WallboardWidget.objects.filter(pk=wid, wallboard=board).update(order=rank)
        rank += 10
    return JsonResponse({'ok': True, 'count': len(ids)})
