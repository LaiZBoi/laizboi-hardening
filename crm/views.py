"""
CRM views — Phase 5.1.

Surfaces:
* /crm/                          — overview dashboard
* /crm/leads/                    — searchable, filterable leads table
* /crm/leads/new/, /<pk>/edit/   — lead form
* /crm/leads/<pk>/               — lead detail w/ Convert + Disqualify
* /crm/leads/<pk>/convert/       — POST: create Org + Opp from lead
* /crm/leads/<pk>/disqualify/    — POST: status=disqualified + reason note
* /crm/pipeline/                 — 6-column drag-and-drop kanban
* /crm/opportunities/...         — list / create / edit / detail
* /crm/opportunities/<pk>/stage/ — POST: drag-drop stage change
* /crm/opportunities/<pk>/to-quote/ — POST: convert opportunity to psa.Quote
* /crm/campaigns/...             — list / create / edit / detail
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.permission_utils import require_perm, user_has_perm
from audit.models import AuditLog
from core.models import Organization

from .forms import CampaignForm, LeadForm, OpportunityForm
from .models import Campaign, Lead, Opportunity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_org(request):
    """Return the current Organization on the request, or 404-ish redirect."""
    return getattr(request, 'current_organization', None)


def _scope_qs(request, qs):
    """Scope a queryset to the current org. Superusers see everything."""
    if request.user.is_superuser:
        return qs
    org = _current_org(request)
    if not org:
        return qs.none()
    return qs.filter(organization=org)


def _audit(request, *, action, obj, description, extra=None):
    AuditLog.log(
        user=request.user,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=obj.pk,
        object_repr=str(obj),
        description=description,
        ip_address=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        path=request.path,
        extra_data=extra or {},
    )


def _unique_slug(name):
    base = slugify(name) or 'org'
    candidate = base
    n = 2
    while Organization.objects.filter(slug=candidate).exists():
        candidate = f'{base}-{n}'
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Home / Overview
# ---------------------------------------------------------------------------

@login_required
@require_perm('crm_view')
def crm_home(request):
    """Dashboard: pipeline summary cards + recent activity."""
    opps = _scope_qs(request, Opportunity.objects.all())
    leads = _scope_qs(request, Lead.objects.all())

    open_opps = opps.exclude(stage__in=('closed_won', 'closed_lost'))
    open_count = open_opps.count()

    weighted_total = Decimal('0')
    for o in open_opps.only('estimated_value', 'probability_pct'):
        weighted_total += o.weighted_value

    week_ago = timezone.now() - timedelta(days=7)
    new_leads_week = leads.filter(created_at__gte=week_ago).count()

    thirty = timezone.now() - timedelta(days=30)
    leads_30d = leads.filter(created_at__gte=thirty)
    leads_30d_total = leads_30d.count()
    leads_30d_converted = leads_30d.filter(status='converted').count()
    conversion_rate = (
        (leads_30d_converted / leads_30d_total * 100.0)
        if leads_30d_total else 0.0
    )

    recent_leads = leads.order_by('-created_at')[:8]
    recent_opps = opps.order_by('-created_at')[:8]

    ctx = {
        'open_count': open_count,
        'weighted_total': weighted_total.quantize(Decimal('0.01')),
        'new_leads_week': new_leads_week,
        'conversion_rate': round(conversion_rate, 1),
        'leads_30d_total': leads_30d_total,
        'leads_30d_converted': leads_30d_converted,
        'recent_leads': recent_leads,
        'recent_opps': recent_opps,
        'can_manage_pipeline': user_has_perm(request.user, 'crm_manage_pipeline'),
        'can_manage_campaigns': user_has_perm(request.user, 'crm_manage_campaigns'),
        'can_view_forecast': user_has_perm(request.user, 'crm_view_forecast'),
    }
    return render(request, 'crm/home.html', ctx)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@login_required
@require_perm('crm_view')
def lead_list(request):
    qs = _scope_qs(request, Lead.objects.select_related(
        'campaign', 'assigned_to', 'organization',
    ))
    q = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or '').strip()
    if q:
        qs = qs.filter(
            Q(company_name__icontains=q)
            | Q(contact_first_name__icontains=q)
            | Q(contact_last_name__icontains=q)
            | Q(contact_email__icontains=q)
            | Q(industry__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    qs = qs.order_by('-created_at')[:500]
    ctx = {
        'leads': qs,
        'q': q,
        'status': status,
        'status_choices': Lead.STATUS_CHOICES,
        'can_create_lead': user_has_perm(request.user, 'crm_create_lead'),
    }
    return render(request, 'crm/lead_list.html', ctx)


@login_required
@require_perm('crm_view')
def lead_detail(request, pk):
    lead = get_object_or_404(_scope_qs(request, Lead.objects.all()), pk=pk)
    ctx = {
        'lead': lead,
        'can_create_lead': user_has_perm(request.user, 'crm_create_lead'),
        'can_manage_pipeline': user_has_perm(request.user, 'crm_manage_pipeline'),
    }
    return render(request, 'crm/lead_detail.html', ctx)


@login_required
@require_perm('crm_create_lead')
def lead_form(request, pk=None):
    instance = None
    if pk:
        instance = get_object_or_404(_scope_qs(request, Lead.objects.all()), pk=pk)
    if request.method == 'POST':
        form = LeadForm(request.POST, instance=instance)
        # Limit campaign FK to current org
        org = _current_org(request)
        if org:
            form.fields['campaign'].queryset = Campaign.objects.filter(organization=org)
        if form.is_valid():
            obj = form.save(commit=False)
            if not instance:
                obj.organization = org
                obj.created_by = request.user
            obj.save()
            _audit(
                request,
                action='update' if instance else 'create',
                obj=obj,
                description=f'{"Updated" if instance else "Created"} lead {obj.company_name}',
            )
            messages.success(request, 'Lead saved.')
            return redirect('crm:lead_detail', pk=obj.pk)
    else:
        form = LeadForm(instance=instance)
        org = _current_org(request)
        if org:
            form.fields['campaign'].queryset = Campaign.objects.filter(organization=org)
    return render(request, 'crm/lead_form.html', {
        'form': form,
        'instance': instance,
        'title': 'Edit Lead' if instance else 'New Lead',
    })


@login_required
@require_perm('crm_manage_pipeline')
@require_POST
def lead_convert(request, pk):
    """Convert lead to Organization + Opportunity."""
    lead = get_object_or_404(_scope_qs(request, Lead.objects.all()), pk=pk)
    if lead.status == 'converted' and lead.converted_to_opportunity_id:
        messages.info(request, 'Lead already converted.')
        return redirect('crm:opportunity_detail', pk=lead.converted_to_opportunity_id)

    msp_org = lead.organization

    with transaction.atomic():
        new_org = Organization.objects.create(
            name=lead.company_name[:255],
            slug=_unique_slug(lead.company_name),
            email=lead.contact_email or '',
            phone=lead.contact_phone or '',
            website=lead.website or '',
            primary_contact_name=lead.contact_full_name,
            primary_contact_title=lead.contact_title or '',
            primary_contact_email=lead.contact_email or '',
            primary_contact_phone=lead.contact_phone or '',
        )
        opp = Opportunity.objects.create(
            organization=msp_org,
            client_org=new_org,
            name=f'{lead.company_name} — Initial Opportunity',
            description=lead.notes or '',
            stage='qualified',
            estimated_value=lead.estimated_value or Decimal('0'),
            probability_pct=20,
            source_lead=lead,
            campaign=lead.campaign,
            assigned_to=lead.assigned_to,
            created_by=request.user,
        )
        lead.status = 'converted'
        lead.converted_to_org = new_org
        lead.converted_to_opportunity = opp
        lead.save(update_fields=[
            'status', 'converted_to_org', 'converted_to_opportunity',
            'updated_at',
        ])

    _audit(
        request, action='create', obj=opp,
        description=f'Converted lead "{lead.company_name}" → Organization "{new_org.name}" + Opportunity #{opp.pk}',
        extra={'lead_id': lead.pk, 'new_org_id': new_org.pk, 'opportunity_id': opp.pk},
    )
    messages.success(request, f'Converted lead. Created organization "{new_org.name}" and opportunity.')
    return redirect('crm:opportunity_detail', pk=opp.pk)


@login_required
@require_perm('crm_manage_pipeline')
@require_POST
def lead_disqualify(request, pk):
    lead = get_object_or_404(_scope_qs(request, Lead.objects.all()), pk=pk)
    reason = (request.POST.get('reason') or '').strip()
    lead.status = 'disqualified'
    if reason:
        prefix = lead.notes + '\n\n' if lead.notes else ''
        lead.notes = f'{prefix}[Disqualified {timezone.now():%Y-%m-%d}] {reason}'
    lead.save(update_fields=['status', 'notes', 'updated_at'])
    _audit(
        request, action='update', obj=lead,
        description=f'Disqualified lead "{lead.company_name}"',
        extra={'reason': reason},
    )
    messages.success(request, 'Lead disqualified.')
    return redirect('crm:lead_detail', pk=lead.pk)


# ---------------------------------------------------------------------------
# Pipeline / Opportunities
# ---------------------------------------------------------------------------

@login_required
@require_perm('crm_manage_pipeline')
def pipeline_kanban(request):
    """6-column drag-and-drop board."""
    qs = _scope_qs(request, Opportunity.objects.select_related(
        'client_org', 'assigned_to',
    ))
    columns = []
    for code, label in Opportunity.STAGE_CHOICES:
        col_qs = qs.filter(stage=code).order_by('-created_at')
        opps = list(col_qs)
        weighted = sum((o.weighted_value for o in opps), Decimal('0'))
        columns.append({
            'code': code,
            'label': label,
            'opps': opps,
            'count': len(opps),
            'weighted': weighted.quantize(Decimal('0.01')),
        })
    return render(request, 'crm/pipeline_kanban.html', {
        'columns': columns,
        'stage_choices': Opportunity.STAGE_CHOICES,
    })


@login_required
@require_perm('crm_view')
def opportunity_list(request):
    qs = _scope_qs(request, Opportunity.objects.select_related(
        'client_org', 'assigned_to', 'campaign',
    )).order_by('-created_at')
    stage = (request.GET.get('stage') or '').strip()
    if stage:
        qs = qs.filter(stage=stage)
    return render(request, 'crm/opportunity_list.html', {
        'opportunities': qs[:500],
        'stage': stage,
        'stage_choices': Opportunity.STAGE_CHOICES,
        'can_manage_pipeline': user_has_perm(request.user, 'crm_manage_pipeline'),
    })


@login_required
@require_perm('crm_view')
def opportunity_detail(request, pk):
    opp = get_object_or_404(
        _scope_qs(request, Opportunity.objects.select_related(
            'client_org', 'assigned_to', 'campaign', 'source_lead', 'quote',
        )),
        pk=pk,
    )
    return render(request, 'crm/opportunity_detail.html', {
        'opp': opp,
        'can_manage_pipeline': user_has_perm(request.user, 'crm_manage_pipeline'),
    })


@login_required
@require_perm('crm_manage_pipeline')
def opportunity_form(request, pk=None):
    instance = None
    if pk:
        instance = get_object_or_404(_scope_qs(request, Opportunity.objects.all()), pk=pk)
    if request.method == 'POST':
        form = OpportunityForm(request.POST, instance=instance)
        org = _current_org(request)
        if org:
            form.fields['campaign'].queryset = Campaign.objects.filter(organization=org)
        if form.is_valid():
            obj = form.save(commit=False)
            if not instance:
                obj.organization = org
                obj.created_by = request.user
            obj.save()
            _audit(
                request,
                action='update' if instance else 'create',
                obj=obj,
                description=f'{"Updated" if instance else "Created"} opportunity {obj.name}',
            )
            messages.success(request, 'Opportunity saved.')
            return redirect('crm:opportunity_detail', pk=obj.pk)
    else:
        form = OpportunityForm(instance=instance)
        org = _current_org(request)
        if org:
            form.fields['campaign'].queryset = Campaign.objects.filter(organization=org)
    return render(request, 'crm/opportunity_form.html', {
        'form': form,
        'instance': instance,
        'title': 'Edit Opportunity' if instance else 'New Opportunity',
    })


@login_required
@require_perm('crm_manage_pipeline')
@require_POST
def opportunity_set_stage(request, pk):
    """Drag-drop endpoint. Updates `stage` and sets `actual_close_date`
    when transitioning to closed_won/closed_lost."""
    opp = get_object_or_404(_scope_qs(request, Opportunity.objects.all()), pk=pk)
    new_stage = (request.POST.get('stage') or '').strip()
    valid = {code for code, _ in Opportunity.STAGE_CHOICES}
    if new_stage not in valid:
        return JsonResponse({'ok': False, 'error': 'invalid stage'}, status=400)
    if new_stage == opp.stage:
        return JsonResponse({'ok': True, 'noop': True, 'stage': opp.stage})

    old_stage = opp.stage
    opp.stage = new_stage
    update_fields = ['stage', 'updated_at']
    if new_stage in ('closed_won', 'closed_lost') and not opp.actual_close_date:
        opp.actual_close_date = timezone.now().date()
        update_fields.append('actual_close_date')
    opp.save(update_fields=update_fields)

    _audit(
        request, action='update', obj=opp,
        description=f'Moved opportunity "{opp.name}" {old_stage} → {new_stage}',
        extra={'old_stage': old_stage, 'new_stage': new_stage},
    )
    return JsonResponse({
        'ok': True,
        'stage': opp.stage,
        'stage_label': opp.get_stage_display(),
        'weighted_value': str(opp.weighted_value.quantize(Decimal('0.01'))),
    })


@login_required
@require_perm('crm_manage_pipeline')
@require_POST
def opportunity_to_quote(request, pk):
    """Create a draft psa.Quote from the opportunity."""
    opp = get_object_or_404(_scope_qs(request, Opportunity.objects.all()), pk=pk)
    if opp.quote_id:
        messages.info(request, 'Opportunity already has a quote attached.')
        return redirect('psa:quote_edit', pk=opp.quote_id) if _has_quote_edit_url() else redirect('crm:opportunity_detail', pk=opp.pk)

    from psa.models import Quote
    quote = Quote.objects.create(
        organization=opp.organization,
        client_org=opp.client_org,
        title=opp.name[:300],
        description=opp.description or '',
        status='draft',
        created_by=request.user,
    )
    opp.quote = quote
    opp.save(update_fields=['quote', 'updated_at'])

    _audit(
        request, action='create', obj=quote,
        description=f'Drafted quote {quote.quote_number} from opportunity "{opp.name}"',
        extra={'opportunity_id': opp.pk, 'quote_id': quote.pk},
    )
    messages.success(request, f'Quote {quote.quote_number} drafted from opportunity.')
    # Try the most likely quote-edit URL names; fall back to detail.
    for name in ('psa:quote_edit', 'psa:quote_detail', 'psa:quote_update'):
        try:
            return redirect(name, pk=quote.pk)
        except Exception:
            continue
    return redirect('crm:opportunity_detail', pk=opp.pk)


def _has_quote_edit_url():
    try:
        reverse('psa:quote_edit', kwargs={'pk': 1})
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

@login_required
@require_perm('crm_manage_campaigns')
def campaign_list(request):
    qs = _scope_qs(request, Campaign.objects.all()).annotate(
        lead_count=Count('leads', distinct=True),
        opp_count=Count('opportunities', distinct=True),
    ).order_by('-created_at')
    return render(request, 'crm/campaign_list.html', {
        'campaigns': qs,
    })


@login_required
@require_perm('crm_manage_campaigns')
def campaign_form(request, pk=None):
    instance = None
    if pk:
        instance = get_object_or_404(_scope_qs(request, Campaign.objects.all()), pk=pk)
    if request.method == 'POST':
        form = CampaignForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not instance:
                obj.organization = _current_org(request)
                obj.created_by = request.user
            obj.save()
            _audit(
                request,
                action='update' if instance else 'create',
                obj=obj,
                description=f'{"Updated" if instance else "Created"} campaign {obj.name}',
            )
            messages.success(request, 'Campaign saved.')
            return redirect('crm:campaign_detail', pk=obj.pk)
    else:
        form = CampaignForm(instance=instance)
    return render(request, 'crm/campaign_form.html', {
        'form': form,
        'instance': instance,
        'title': 'Edit Campaign' if instance else 'New Campaign',
    })


@login_required
@require_perm('crm_manage_campaigns')
def campaign_detail(request, pk):
    campaign = get_object_or_404(_scope_qs(request, Campaign.objects.all()), pk=pk)
    leads = campaign.leads.all().order_by('-created_at')[:200]
    opps = campaign.opportunities.select_related('client_org').all().order_by('-created_at')[:200]

    converted = leads.filter(status='converted').count()
    won_value = Decimal('0')
    won_count = 0
    for o in opps:
        if o.stage == 'closed_won':
            won_count += 1
            won_value += o.estimated_value or Decimal('0')

    roi = None
    if campaign.budget and campaign.budget > 0:
        roi = ((won_value - campaign.budget) / campaign.budget * 100).quantize(Decimal('0.1'))

    return render(request, 'crm/campaign_detail.html', {
        'campaign': campaign,
        'leads': leads,
        'opps': opps,
        'lead_count': leads.count(),
        'converted_count': converted,
        'won_count': won_count,
        'won_value': won_value.quantize(Decimal('0.01')),
        'roi': roi,
    })
