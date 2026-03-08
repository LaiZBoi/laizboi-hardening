"""
Assets views
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from core.middleware import get_request_organization
from core.decorators import require_write, require_organization_context
from core.webhook_sender import send_webhook
from core.models import Webhook
from .models import Asset, Contact, Relationship
from .forms import AssetForm, ContactForm


@login_required
def asset_list(request):
    """
    List all assets in current organization with filtering.
    In global view mode, shows all assets across all organizations.
    """
    org = get_request_organization(request)

    # Check if user is in global view mode (no org but is superuser/staff)
    is_staff = request.is_staff_user if hasattr(request, 'is_staff_user') else False
    in_global_view = not org and (request.user.is_superuser or is_staff)

    if in_global_view:
        # Global view: show all assets across all organizations
        assets = Asset.objects.all().select_related('organization').prefetch_related('tags').select_related('primary_contact', 'equipment_model__vendor')
        all_assets = Asset.objects.all()
    else:
        # Organization view: show only assets for current org
        assets = Asset.objects.for_organization(org).prefetch_related('tags').select_related('primary_contact', 'equipment_model__vendor')
        all_assets = Asset.objects.for_organization(org)

    # Apply filters
    filter_type = request.GET.get('type', '')
    filter_manufacturer = request.GET.get('manufacturer', '')
    filter_status = request.GET.get('status', '')
    filter_location = request.GET.get('location', '')
    filter_needs_reorder = request.GET.get('needs_reorder', '')

    if filter_type:
        assets = assets.filter(asset_type=filter_type)

    if filter_manufacturer:
        assets = assets.filter(manufacturer__icontains=filter_manufacturer)

    if filter_status:
        # Status is typically stored in custom_fields
        assets = assets.filter(custom_fields__status=filter_status)

    if filter_location:
        # Location is typically stored in custom_fields
        assets = assets.filter(custom_fields__location__icontains=filter_location)

    if filter_needs_reorder:
        assets = assets.filter(needs_reorder=True)

    # Get unique values for filter dropdowns
    manufacturers = all_assets.exclude(manufacturer='').values_list('manufacturer', flat=True).distinct().order_by('manufacturer')
    asset_types = Asset.ASSET_TYPES

    # Get unique statuses and locations from custom_fields
    # Use values_list to avoid loading full model objects
    statuses = set()
    locations = set()
    custom_fields_list = all_assets.values_list('custom_fields', flat=True)
    for cf in custom_fields_list:
        if cf and cf.get('status'):
            statuses.add(cf['status'])
        if cf and cf.get('location'):
            locations.add(cf['location'])

    return render(request, 'assets/asset_list.html', {
        'assets': assets,
        'manufacturers': manufacturers,
        'asset_types': asset_types,
        'statuses': sorted(statuses),
        'locations': sorted(locations),
        'filter_type': filter_type,
        'filter_manufacturer': filter_manufacturer,
        'filter_status': filter_status,
        'filter_location': filter_location,
        'filter_needs_reorder': filter_needs_reorder,
        'in_global_view': in_global_view,
    })


@login_required
def asset_detail(request, pk):
    """
    View asset details with relationships.
    Supports global view mode for superusers/staff users.
    """
    org = get_request_organization(request)

    # Check if user is in global view mode (no org but is superuser/staff)
    is_staff = request.is_staff_user if hasattr(request, 'is_staff_user') else False
    in_global_view = not org and (request.user.is_superuser or is_staff)

    if in_global_view:
        # Global view: access asset from any organization
        asset = get_object_or_404(Asset, pk=pk)
        asset_org = asset.organization
    else:
        # Organization view: filter by current org
        asset = get_object_or_404(Asset, pk=pk, organization=org)
        asset_org = org

    # Get relationships for this asset's organization
    relationships = Relationship.objects.filter(
        organization=asset_org,
        source_type='asset',
        source_id=asset.id
    )

    # Get asset images
    from files.models import Attachment
    asset_images = Attachment.objects.filter(
        organization=asset_org,
        entity_type='asset',
        entity_id=asset.id,
        content_type__startswith='image/'
    ).order_by('-created_at')[:50]

    return render(request, 'assets/asset_detail.html', {
        'asset': asset,
        'relationships': relationships,
        'asset_images': asset_images,
        'in_global_view': in_global_view,
    })


@login_required
@require_write
@require_organization_context
def asset_create(request):
    """
    Create new asset.
    """
    org = get_request_organization(request)

    # Check for redirect parameter (e.g., from rack page)
    redirect_to = request.GET.get('redirect') or request.POST.get('redirect')

    if request.method == 'POST':
        form = AssetForm(request.POST, organization=org)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.organization = org
            asset.created_by = request.user

            # Initialize ports if port_count is specified
            port_count = form.cleaned_data.get('port_count')
            if port_count and asset.has_ports():
                # Determine port type based on asset type
                port_type = 'patch_panel' if asset.asset_type in ['patch_panel', 'fiber_panel'] else 'switch'
                asset.initialize_ports(port_count, port_type)

            asset.save()
            form.save_m2m()

            # Trigger webhook
            send_webhook(
                Webhook.EVENT_ASSET_CREATED,
                {
                    'asset_id': asset.id,
                    'asset_name': asset.name,
                    'asset_type': asset.asset_type,
                    'serial_number': asset.serial_number,
                    'created_by': request.user.username,
                },
                organization=org
            )

            if port_count and asset.has_ports():
                messages.success(request, f"Asset '{asset.name}' created successfully with {port_count} ports.")
            else:
                messages.success(request, f"Asset '{asset.name}' created successfully.")

            # Handle redirect
            if redirect_to and redirect_to.startswith('rack_'):
                # Extract rack ID from "rack_123" format
                try:
                    rack_id = redirect_to.split('_')[1]
                    return redirect('monitoring:rack_device_create', rack_id=rack_id)
                except (IndexError, ValueError):
                    pass

            return redirect('assets:asset_detail', pk=asset.pk)
    else:
        form = AssetForm(organization=org)

    return render(request, 'assets/asset_form.html', {
        'form': form,
        'action': 'Create',
        'redirect_to': redirect_to,  # Pass to template for hidden field
    })


@login_required
@require_write
def asset_edit(request, pk):
    """
    Edit asset.
    """
    org = get_request_organization(request)

    # In global view mode, get asset without org filter and use asset's org
    if org is None:
        asset = get_object_or_404(Asset, pk=pk)
        org = asset.organization
    else:
        asset = get_object_or_404(Asset, pk=pk, organization=org)

    if request.method == 'POST':
        form = AssetForm(request.POST, instance=asset, organization=org)
        if form.is_valid():
            # Track if port count changed
            old_port_count = asset.get_port_count()
            new_port_count = form.cleaned_data.get('port_count')

            asset = form.save(commit=False)

            # Re-initialize ports if count changed
            if new_port_count and asset.has_ports() and new_port_count != old_port_count:
                port_type = 'patch_panel' if asset.asset_type in ['patch_panel', 'fiber_panel'] else 'switch'
                asset.initialize_ports(new_port_count, port_type)
                messages.info(request, f"Port configuration updated to {new_port_count} ports.")

            asset.save()
            form.save_m2m()

            # Trigger webhook
            send_webhook(
                Webhook.EVENT_ASSET_UPDATED,
                {
                    'asset_id': asset.id,
                    'asset_name': asset.name,
                    'asset_type': asset.asset_type,
                    'serial_number': asset.serial_number,
                    'updated_by': request.user.username,
                },
                organization=org
            )

            messages.success(request, f"Asset '{asset.name}' updated successfully.")
            return redirect('assets:asset_detail', pk=asset.pk)
    else:
        form = AssetForm(instance=asset, organization=org)

    return render(request, 'assets/asset_form.html', {
        'form': form,
        'asset': asset,
        'action': 'Edit',
    })


@login_required
def contact_list(request):
    """
    List all contacts in current organization or all contacts in global view.
    """
    org = get_request_organization(request)

    # Check if user is in global view mode (no org but is superuser/staff)
    is_staff = request.is_staff_user if hasattr(request, 'is_staff_user') else False
    in_global_view = not org and (request.user.is_superuser or is_staff)

    if in_global_view:
        # Global view: show all contacts across all organizations
        contacts = Contact.objects.all().select_related('organization')
    else:
        # Organization view: show only contacts for current org
        contacts = Contact.objects.for_organization(org)

    return render(request, 'assets/contact_list.html', {
        'contacts': contacts,
        'in_global_view': in_global_view,
    })


@login_required
def contact_detail(request, pk):
    """
    View contact details.
    """
    org = get_request_organization(request)
    contact = get_object_or_404(Contact, pk=pk, organization=org)

    # Get assets associated with this contact
    # FIX: Add select_related for query optimization
    assets = Asset.objects.filter(
        organization=org,
        primary_contact=contact
    ).select_related('organization', 'equipment_model')

    return render(request, 'assets/contact_detail.html', {
        'contact': contact,
        'assets': assets,
    })


@login_required
@require_write
@require_organization_context
def contact_create(request):
    """
    Create new contact.
    """
    org = get_request_organization(request)

    if request.method == 'POST':
        form = ContactForm(request.POST, organization=org)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.organization = org
            contact.save()
            messages.success(request, f"Contact '{contact.name}' created successfully.")
            return redirect('assets:contact_detail', pk=contact.pk)
    else:
        form = ContactForm(organization=org)

    return render(request, 'assets/contact_form.html', {
        'form': form,
        'action': 'Create',
    })


@login_required
@require_write
def contact_edit(request, pk):
    """
    Edit contact.
    """
    org = get_request_organization(request)
    contact = get_object_or_404(Contact, pk=pk, organization=org)

    if request.method == 'POST':
        form = ContactForm(request.POST, instance=contact, organization=org)
        if form.is_valid():
            contact = form.save()
            messages.success(request, f"Contact '{contact.name}' updated successfully.")
            return redirect('assets:contact_detail', pk=contact.pk)
    else:
        form = ContactForm(instance=contact, organization=org)

    return render(request, 'assets/contact_form.html', {
        'form': form,
        'contact': contact,
        'action': 'Edit',
    })


@login_required
@require_write
def asset_generate_profile(request, pk):
    """
    Generate (or regenerate) a profile document for an asset and link it.
    Builds a rich HTML document from the asset's fields + any linked RMM device data.
    Redirects to the newly created/updated document.
    """
    from django.utils.text import slugify
    from django.utils import timezone
    from docs.models import Document

    org = get_request_organization(request)
    if org:
        asset = get_object_or_404(Asset, pk=pk, organization=org)
    else:
        asset = get_object_or_404(Asset, pk=pk)

    # Gather RMM device data if linked
    rmm = None
    try:
        rmm = asset.rmm_devices.select_related('connection').first()
    except Exception:
        pass

    # Build HTML content
    now = timezone.now().strftime('%Y-%m-%d %H:%M')
    asset_type_label = asset.get_asset_type_display()

    def row(label, value):
        if not value:
            return ''
        return f'<tr><th style="width:35%">{label}</th><td>{value}</td></tr>'

    # Identity section
    identity_rows = (
        row('Asset Type', asset_type_label) +
        row('Serial Number', asset.serial_number) +
        row('Asset Tag', asset.asset_tag) +
        row('Manufacturer', asset.manufacturer) +
        row('Model', asset.model)
    )

    # Network section
    network_rows = (
        row('Hostname', asset.hostname) +
        row('IP Address', asset.ip_address) +
        row('MAC Address', asset.mac_address)
    )

    # OS / Hardware section
    hw_rows = (
        row('Operating System', asset.os_name) +
        row('OS Version', asset.os_version) +
        row('CPU', asset.cpu) +
        row('RAM', f'{asset.ram_gb} GB' if asset.ram_gb else '') +
        row('Storage', asset.storage)
    )

    # RMM section
    rmm_section = ''
    if rmm:
        rmm_rows = (
            row('RMM Provider', rmm.connection.get_provider_type_display() if rmm.connection else '') +
            row('External ID', rmm.external_id) +
            row('Device Type', rmm.device_type) +
            row('Online', '<span class="badge bg-success">Yes</span>' if rmm.is_online else '<span class="badge bg-secondary">No</span>') +
            row('Last Seen', rmm.last_seen.strftime('%Y-%m-%d %H:%M') if rmm.last_seen else '') +
            row('Site/Client', rmm.site_name) +
            row('OS (RMM)', f'{rmm.os_type} {rmm.os_version}'.strip())
        )
        if rmm_rows:
            rmm_section = f'''
<div class="card mb-3">
  <div class="card-header bg-info text-white"><i class="fas fa-plug me-2"></i>RMM Agent</div>
  <div class="card-body p-0">
    <table class="table table-sm table-striped mb-0">{rmm_rows}</table>
  </div>
</div>'''

    # Alerts section
    alerts_section = ''
    if rmm:
        active_alerts = list(rmm.alerts.filter(status='active').order_by('-triggered_at')[:10])
        if active_alerts:
            alert_rows = ''.join(
                f'<tr>'
                f'<td><span class="badge bg-{"danger" if a.severity == "critical" else "warning" if a.severity == "warning" else "info"}">{a.severity}</span></td>'
                f'<td>{a.alert_type}</td>'
                f'<td>{a.message[:120]}</td>'
                f'<td>{a.triggered_at.strftime("%Y-%m-%d %H:%M") if a.triggered_at else ""}</td>'
                f'</tr>'
                for a in active_alerts
            )
            alerts_section = f'''
<div class="card mb-3">
  <div class="card-header bg-warning"><i class="fas fa-exclamation-triangle me-2"></i>Active Alerts</div>
  <div class="card-body p-0">
    <table class="table table-sm mb-0">
      <thead><tr><th>Severity</th><th>Type</th><th>Message</th><th>Triggered</th></tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </div>
</div>'''

    notes_section = ''
    if asset.notes:
        import html as html_lib
        notes_section = f'''
<div class="card mb-3">
  <div class="card-header"><i class="fas fa-sticky-note me-2"></i>Notes</div>
  <div class="card-body"><p class="mb-0" style="white-space:pre-wrap">{html_lib.escape(asset.notes)}</p></div>
</div>'''

    online_badge = ''
    if rmm:
        online_badge = (' <span class="badge bg-success ms-2">Online</span>' if rmm.is_online
                        else ' <span class="badge bg-secondary ms-2">Offline</span>')

    content = f'''<div class="container-fluid p-0">

<div class="alert alert-secondary d-flex justify-content-between align-items-center mb-3">
  <span><i class="fas fa-info-circle me-2"></i>Auto-generated profile — last updated {now}</span>
  <span class="badge bg-primary">{asset_type_label}</span>
</div>

<h4 class="mb-3"><i class="fas fa-server me-2"></i>{asset.name}{online_badge}</h4>

<div class="row">
  <div class="col-md-6">
    <div class="card mb-3">
      <div class="card-header"><i class="fas fa-id-card me-2"></i>Identity</div>
      <div class="card-body p-0">
        <table class="table table-sm table-striped mb-0">{identity_rows or "<tr><td class='text-muted'>No identity data recorded.</td></tr>"}</table>
      </div>
    </div>
  </div>
  <div class="col-md-6">
    <div class="card mb-3">
      <div class="card-header"><i class="fas fa-network-wired me-2"></i>Network</div>
      <div class="card-body p-0">
        <table class="table table-sm table-striped mb-0">{network_rows or "<tr><td class='text-muted'>No network data recorded.</td></tr>"}</table>
      </div>
    </div>
  </div>
</div>

<div class="card mb-3">
  <div class="card-header"><i class="fas fa-microchip me-2"></i>Hardware &amp; OS</div>
  <div class="card-body p-0">
    <table class="table table-sm table-striped mb-0">{hw_rows or "<tr><td class='text-muted'>No hardware data recorded.</td></tr>"}</table>
  </div>
</div>

{rmm_section}
{alerts_section}
{notes_section}

</div>'''

    # Create or update document
    doc = asset.profile_document
    if doc:
        doc.title = f'{asset.name} — Device Profile'
        doc.content = content
        doc.content_type = 'html'
        doc.save()
        messages.success(request, f'Profile document updated for {asset.name}.')
    else:
        base_slug = slugify(f'{asset.name}-profile')
        slug = base_slug
        counter = 1
        while Document.objects.filter(organization=asset.organization, slug=slug).exists():
            slug = f'{base_slug}-{counter}'
            counter += 1
        doc = Document.objects.create(
            organization=asset.organization,
            title=f'{asset.name} — Device Profile',
            content=content,
            content_type='html',
            slug=slug,
        )
        asset.profile_document = doc
        asset.save(update_fields=['profile_document'])
        messages.success(request, f'Profile document created for {asset.name}.')

    return redirect('docs:document_detail', pk=doc.pk)


@login_required
@require_write
def asset_delete(request, pk):
    """
    Delete asset.
    """
    org = get_request_organization(request)
    asset = get_object_or_404(Asset, pk=pk, organization=org)

    if request.method == 'POST':
        name = asset.name
        asset.delete()
        messages.success(request, f"Asset '{name}' deleted successfully.")
        return redirect('assets:asset_list')

    return render(request, 'assets/asset_confirm_delete.html', {
        'asset': asset,
    })


@login_required
@require_write
def contact_delete(request, pk):
    """
    Delete contact.
    """
    org = get_request_organization(request)
    contact = get_object_or_404(Contact, pk=pk, organization=org)

    if request.method == 'POST':
        name = contact.name
        contact.delete()
        messages.success(request, f"Contact '{name}' deleted successfully.")
        return redirect('assets:contact_list')

    return render(request, 'assets/contact_confirm_delete.html', {
        'contact': contact,
    })


@csrf_exempt
@login_required
def asset_api_detail(request, pk):  # noqa: write permission checked inline for PATCH
    """
    GET  /assets/api/assets/<pk>/  — return asset info as JSON
    PATCH /assets/api/assets/<pk>/ — update editable fields
    """
    import json

    org = get_request_organization(request)
    if org:
        asset = get_object_or_404(Asset, pk=pk, organization=org)
    else:
        asset = get_object_or_404(Asset, pk=pk)

    if request.method == 'GET':
        return JsonResponse({
            'id': asset.id,
            'name': asset.name,
            'asset_type': asset.asset_type,
            'asset_type_display': asset.get_asset_type_display(),
            'hostname': asset.hostname,
            'ip_address': str(asset.ip_address) if asset.ip_address else '',
            'mac_address': asset.mac_address,
            'os_name': asset.os_name,
            'os_version': asset.os_version,
            'cpu': asset.cpu,
            'ram_gb': asset.ram_gb,
            'storage': asset.storage,
            'manufacturer': asset.manufacturer,
            'model': asset.model,
            'serial_number': asset.serial_number,
            'port_count': asset.port_count,
        })

    if request.method == 'PATCH':
        # Require write permission
        if not (request.user.is_superuser or request.user.is_staff):
            from core.decorators import get_user_membership
            membership = get_user_membership(request)
            if not membership or not membership.can_write():
                return JsonResponse({'success': False, 'error': 'Write permission required'}, status=403)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

        allowed = ['hostname', 'ip_address', 'mac_address', 'os_name', 'os_version',
                   'cpu', 'ram_gb', 'storage']
        for field in allowed:
            if field in data:
                val = data[field]
                if field == 'ip_address' and val == '':
                    val = None
                if field == 'ram_gb':
                    try:
                        val = int(val) if val else None
                    except (ValueError, TypeError):
                        return JsonResponse({'success': False, 'error': 'ram_gb must be a number'}, status=400)
                setattr(asset, field, val)
        try:
            asset.save()
        except Exception:
            import logging
            logging.getLogger('assets').exception('asset_api_detail save failed pk=%s', pk)
            return JsonResponse({'success': False, 'error': 'Save failed'}, status=500)

        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def equipment_model_api(request, pk):
    """
    API endpoint to return equipment model data as JSON.
    Used for auto-populating asset forms.
    """
    from django.http import JsonResponse
    from .models import EquipmentModel

    try:
        equipment = EquipmentModel.objects.select_related('vendor').get(pk=pk)

        data = {
            'vendor_name': equipment.vendor.name,
            'model_name': equipment.model_name,
            'is_rackmount': equipment.is_rackmount,
            'rack_units': equipment.rack_units,
            'equipment_type': equipment.equipment_type,
        }

        return JsonResponse(data)
    except EquipmentModel.DoesNotExist:
        return JsonResponse({'error': 'Equipment model not found'}, status=404)


@login_required
def equipment_models_by_vendor_api(request, vendor_id):
    """
    API endpoint to return equipment models for a specific vendor.
    Used for cascading dropdown functionality.
    """
    from django.http import JsonResponse
    from .models import EquipmentModel

    models = EquipmentModel.objects.filter(
        vendor_id=vendor_id,
        is_active=True
    ).values('id', 'model_name', 'equipment_type').order_by('model_name')

    return JsonResponse(list(models), safe=False)


# ========================================
# Equipment Catalog Management Views
# ========================================

@login_required
def vendor_list(request):
    """List all hardware vendors."""
    from .models import Vendor
    vendors = Vendor.objects.filter(is_active=True).order_by('name')

    return render(request, 'assets/vendor_list.html', {
        'vendors': vendors,
    })


@login_required
def vendor_detail(request, pk):
    """View vendor details with equipment models."""
    from .models import Vendor, EquipmentModel
    vendor = get_object_or_404(Vendor, pk=pk)

    equipment_models = EquipmentModel.objects.filter(
        vendor=vendor,
        is_active=True
    ).order_by('equipment_type', 'model_name')

    # Group by equipment type
    models_by_type = {}
    for model in equipment_models:
        if model.equipment_type not in models_by_type:
            models_by_type[model.equipment_type] = []
        models_by_type[model.equipment_type].append(model)

    return render(request, 'assets/vendor_detail.html', {
        'vendor': vendor,
        'equipment_models': equipment_models,
        'models_by_type': models_by_type,
    })


@login_required
@require_write
def vendor_create(request):
    """Create new hardware vendor."""
    from .forms import VendorForm

    if request.method == 'POST':
        form = VendorForm(request.POST)
        if form.is_valid():
            vendor = form.save()
            messages.success(request, f"Vendor '{vendor.name}' created successfully.")
            return redirect('assets:vendor_detail', pk=vendor.pk)
    else:
        form = VendorForm()

    return render(request, 'assets/vendor_form.html', {
        'form': form,
        'action': 'Create',
    })


@login_required
@require_write
def vendor_edit(request, pk):
    """Edit existing vendor."""
    from .forms import VendorForm
    vendor = get_object_or_404(Vendor, pk=pk)

    if request.method == 'POST':
        form = VendorForm(request.POST, instance=vendor)
        if form.is_valid():
            vendor = form.save()
            messages.success(request, f"Vendor '{vendor.name}' updated successfully.")
            return redirect('assets:vendor_detail', pk=vendor.pk)
    else:
        form = VendorForm(instance=vendor)

    return render(request, 'assets/vendor_form.html', {
        'form': form,
        'vendor': vendor,
        'action': 'Edit',
    })


@login_required
def equipment_model_list(request):
    """List all equipment models with filtering."""
    from .models import EquipmentModel, Vendor

    # Get filter parameters
    vendor_id = request.GET.get('vendor')
    equipment_type = request.GET.get('type')
    search = request.GET.get('search')

    models = EquipmentModel.objects.filter(is_active=True).select_related('vendor')

    if vendor_id:
        models = models.filter(vendor_id=vendor_id)
    if equipment_type:
        models = models.filter(equipment_type=equipment_type)
    if search:
        models = models.filter(model_name__icontains=search)

    models = models.order_by('vendor__name', 'equipment_type', 'model_name')

    # Get filter options
    vendors = Vendor.objects.filter(is_active=True).order_by('name')
    equipment_types = EquipmentModel.EQUIPMENT_TYPES

    return render(request, 'assets/equipment_model_list.html', {
        'models': models,
        'vendors': vendors,
        'equipment_types': equipment_types,
        'selected_vendor': vendor_id,
        'selected_type': equipment_type,
        'search_query': search,
    })


@login_required
def equipment_model_detail(request, pk):
    """View equipment model details."""
    from .models import EquipmentModel, Asset
    model = get_object_or_404(EquipmentModel.objects.select_related('vendor'), pk=pk)

    # Get assets using this model
    org = get_request_organization(request)
    assets = Asset.objects.filter(
        organization=org,
        equipment_model=model
    ).select_related('primary_contact')

    return render(request, 'assets/equipment_model_detail.html', {
        'model': model,
        'assets': assets,
    })


@login_required
@require_write
def equipment_model_create(request):
    """Create new equipment model."""
    from .forms import EquipmentModelForm

    if request.method == 'POST':
        form = EquipmentModelForm(request.POST)
        if form.is_valid():
            model = form.save()
            messages.success(request, f"Equipment model '{model.model_name}' created successfully.")
            return redirect('assets:equipment_model_detail', pk=model.pk)
    else:
        form = EquipmentModelForm()

    return render(request, 'assets/equipment_model_form.html', {
        'form': form,
        'action': 'Create',
    })


@login_required
@require_write
def equipment_model_edit(request, pk):
    """Edit existing equipment model."""
    from .forms import EquipmentModelForm
    model = get_object_or_404(EquipmentModel, pk=pk)

    if request.method == 'POST':
        form = EquipmentModelForm(request.POST, instance=model)
        if form.is_valid():
            model = form.save()
            messages.success(request, f"Equipment model '{model.model_name}' updated successfully.")
            return redirect('assets:equipment_model_detail', pk=model.pk)
    else:
        form = EquipmentModelForm(instance=model)

    return render(request, 'assets/equipment_model_form.html', {
        'form': form,
        'model': model,
        'action': 'Edit',
    })

# ============================================================================
# Network Scan Import
# ============================================================================

@login_required
@require_organization_context
def network_scan_import(request):
    """
    Upload and preview network scan results.
    """
    org = get_request_organization(request)

    return render(request, 'assets/network_scan_import.html', {
        'org': org,
    })


@login_required
@require_organization_context
def network_scan_upload(request):
    """
    Handle network scan file upload and show preview.
    """
    import json
    from django.http import JsonResponse

    org = get_request_organization(request)

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    if 'scan_file' not in request.FILES:
        return JsonResponse({'error': 'No file uploaded'}, status=400)

    scan_file = request.FILES['scan_file']

    try:
        # Parse JSON
        scan_data = json.load(scan_file)

        if 'devices' not in scan_data:
            return JsonResponse({'error': 'Invalid scan file format'}, status=400)

        devices = scan_data['devices']

        # Batch-fetch all org assets once before the matching loop
        org_assets_cache = list(Asset.objects.for_organization(org).select_related('asset_type'))

        # Match devices against existing assets
        matched_devices = []
        for device in devices:
            match_info = match_device_to_asset(device, org, assets_cache=org_assets_cache)
            matched_devices.append({
                'device': device,
                'match': match_info,
            })

        # Store in session for confirmation
        request.session['pending_scan_import'] = {
            'scan_date': scan_data.get('scan_date'),
            'device_count': len(devices),
            'devices': matched_devices,
        }

        return JsonResponse({
            'success': True,
            'redirect': '/assets/network-scan/preview/'
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON file'}, status=400)
    except Exception:
        return JsonResponse({'error': 'An error occurred processing the file'}, status=500)


@login_required
@require_organization_context
def network_scan_preview(request):
    """
    Preview network scan import before applying.
    """
    org = get_request_organization(request)

    scan_import = request.session.get('pending_scan_import')
    if not scan_import:
        messages.error(request, "No pending scan import found.")
        return redirect('assets:network_scan_import')

    # Categorize devices
    new_devices = []
    update_devices = []
    duplicate_devices = []

    for item in scan_import['devices']:
        match = item['match']
        if match['status'] == 'new':
            new_devices.append(item)
        elif match['status'] == 'update':
            update_devices.append(item)
        elif match['status'] == 'duplicate':
            duplicate_devices.append(item)

    return render(request, 'assets/network_scan_preview.html', {
        'org': org,
        'scan_import': scan_import,
        'new_devices': new_devices,
        'update_devices': update_devices,
        'duplicate_devices': duplicate_devices,
    })


@login_required
@require_organization_context
@require_write
def network_scan_apply(request):
    """
    Apply network scan import after user confirmation.
    """
    from django.db import transaction

    org = get_request_organization(request)

    if request.method != 'POST':
        return redirect('assets:network_scan_preview')

    scan_import = request.session.get('pending_scan_import')
    if not scan_import:
        messages.error(request, "No pending scan import found.")
        return redirect('assets:network_scan_import')

    # Get user selections
    selected_new = request.POST.getlist('create_new')
    selected_updates = request.POST.getlist('update_existing')

    created_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        with transaction.atomic():
            for item in scan_import['devices']:
                device = item['device']
                match = item['match']

                # Create new asset
                if match['status'] == 'new' and device['mac_address'] in selected_new:
                    create_asset_from_scan(device, org)
                    created_count += 1

                # Update existing asset
                elif match['status'] == 'update' and device['mac_address'] in selected_updates:
                    update_asset_from_scan(device, match['asset'], org)
                    updated_count += 1

                else:
                    skipped_count += 1

        # Clear session
        del request.session['pending_scan_import']

        messages.success(
            request,
            f"Network scan import complete! Created: {created_count}, Updated: {updated_count}, Skipped: {skipped_count}"
        )

        return redirect('assets:asset_list')

    except Exception as e:
        messages.error(request, f"Import failed: {str(e)}")
        return redirect('assets:network_scan_preview')


def match_device_to_asset(device, org, assets_cache=None):
    """
    Match scanned device to existing asset.

    Returns:
        dict with keys: status, asset, match_reason
        status: 'new', 'update', 'duplicate'

    When assets_cache is provided (a pre-fetched list of Asset objects),
    matching is done in Python to avoid per-device DB queries.
    """
    mac = device.get('mac_address', '').upper()
    ip = device.get('ip')

    if assets_cache is not None:
        # Filter in Python using pre-fetched cache
        # Try matching by MAC address (most reliable)
        existing = None
        if mac:
            for a in assets_cache:
                if a.mac_address and a.mac_address.upper() == mac:
                    existing = a
                    break

        if existing:
            return {
                'status': 'update',
                'asset': {
                    'id': existing.id,
                    'name': existing.name,
                    'asset_type': existing.asset_type,
                    'mac_address': existing.mac_address,
                    'ip_address': str(existing.ip_address) if existing.ip_address else None,
                },
                'match_reason': 'MAC address match',
            }

        # Try matching by IP address (less reliable)
        if ip:
            for a in assets_cache:
                if str(a.ip_address) == str(ip):
                    existing = a
                    break

        if existing:
            if mac and existing.mac_address and existing.mac_address.upper() != mac:
                return {
                    'status': 'duplicate',
                    'asset': {
                        'id': existing.id,
                        'name': existing.name,
                        'asset_type': existing.asset_type,
                        'mac_address': existing.mac_address,
                        'ip_address': str(existing.ip_address) if existing.ip_address else None,
                    },
                    'match_reason': 'IP address match but different MAC (possible reassignment)',
                }

            return {
                'status': 'update',
                'asset': {
                    'id': existing.id,
                    'name': existing.name,
                    'asset_type': existing.asset_type,
                    'mac_address': existing.mac_address,
                    'ip_address': str(existing.ip_address) if existing.ip_address else None,
                },
                'match_reason': 'IP address match',
            }

    else:
        # Fallback: use DB queries when no cache provided
        # Try matching by MAC address (most reliable)
        if mac:
            existing = Asset.objects.filter(
                organization=org,
                mac_address__iexact=mac
            ).first()

            if existing:
                return {
                    'status': 'update',
                    'asset': {
                        'id': existing.id,
                        'name': existing.name,
                        'asset_type': existing.asset_type,
                        'mac_address': existing.mac_address,
                        'ip_address': str(existing.ip_address) if existing.ip_address else None,
                    },
                    'match_reason': 'MAC address match',
                }

        # Try matching by IP address (less reliable)
        if ip:
            existing = Asset.objects.filter(
                organization=org,
                ip_address=ip
            ).first()

            if existing:
                if mac and existing.mac_address and existing.mac_address.upper() != mac:
                    return {
                        'status': 'duplicate',
                        'asset': {
                            'id': existing.id,
                            'name': existing.name,
                            'asset_type': existing.asset_type,
                            'mac_address': existing.mac_address,
                            'ip_address': str(existing.ip_address) if existing.ip_address else None,
                        },
                        'match_reason': 'IP address match but different MAC (possible reassignment)',
                    }

                return {
                    'status': 'update',
                    'asset': {
                        'id': existing.id,
                        'name': existing.name,
                        'asset_type': existing.asset_type,
                        'mac_address': existing.mac_address,
                        'ip_address': str(existing.ip_address) if existing.ip_address else None,
                    },
                    'match_reason': 'IP address match',
                }

    # No match - new device
    return {
        'status': 'new',
        'asset': None,
        'match_reason': 'No existing asset found',
    }


def create_asset_from_scan(device, org):
    """Create new asset from scanned device data."""
    # Generate name
    hostname = device.get('hostname') or device['ip'].replace('.', '-')
    name = hostname if hostname else f"Device-{device['ip']}"

    # Create asset
    asset = Asset.objects.create(
        organization=org,
        name=name,
        asset_type=device.get('device_type', 'other'),
        manufacturer=device.get('vendor', ''),
        hostname=device.get('hostname', ''),
        ip_address=device.get('ip'),
        mac_address=device.get('mac_address', '').upper(),
        port_count=len(device.get('ports', [])),
        custom_fields={
            'discovered_os': device.get('os', ''),
            'discovery_date': device.get('last_seen'),
            'open_ports': [f"{p['port']}/{p['protocol']}" for p in device.get('ports', [])],
            'discovery_method': 'network_scan',
        },
        notes=f"Auto-discovered via network scan on {device.get('last_seen', 'unknown date')}\n\n"
              f"OS: {device.get('os', 'Unknown')}\n"
              f"Open ports: {', '.join([str(p['port']) for p in device.get('ports', [])])}"
    )

    return asset


def update_asset_from_scan(device, existing_asset_data, org):
    """Update existing asset with scanned device data."""
    asset = Asset.objects.get(id=existing_asset_data['id'], organization=org)

    # Update fields
    if device.get('ip'):
        asset.ip_address = device['ip']

    if device.get('mac_address'):
        asset.mac_address = device['mac_address'].upper()

    if device.get('hostname') and not asset.hostname:
        asset.hostname = device['hostname']

    if device.get('vendor') and not asset.manufacturer:
        asset.manufacturer = device['vendor']

    # Update port count
    if device.get('ports'):
        asset.port_count = len(device['ports'])

    # Update custom fields
    if not asset.custom_fields:
        asset.custom_fields = {}

    asset.custom_fields['last_scanned'] = device.get('last_seen')
    asset.custom_fields['discovered_os'] = device.get('os', '')
    asset.custom_fields['open_ports'] = [f"{p['port']}/{p['protocol']}" for p in device.get('ports', [])]

    # Append to notes
    asset.notes += f"\n\nUpdated from network scan on {device.get('last_seen', 'unknown date')}\n"
    asset.notes += f"OS: {device.get('os', 'Unknown')}\n"
    asset.notes += f"Open ports: {', '.join([str(p['port']) for p in device.get('ports', [])])}\n"

    asset.save()

    return asset
