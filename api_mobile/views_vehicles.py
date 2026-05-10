"""
Mobile API vehicle endpoints (v3.17.456).

Service-fleet vehicles assigned to technicians. Each tech sees the
vehicles currently assigned to them (via `VehicleAssignment`), and can:

  * View vehicle detail + on-vehicle inventory (`VehicleInventoryItem`)
  * Log a fuel fill-up (`VehicleFuelLog`)
  * Report damage (`VehicleDamageReport`)

Vehicles are not per-organization in the data model — they're the
company fleet that runs Client St0r — so no `accessible_org_ids` scope
applies. Authorization is "this vehicle is currently assigned to me",
checked on every write endpoint.
"""
from __future__ import annotations

from datetime import date as date_cls
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _serialize_vehicle(v, *, detail: bool = False) -> dict:
    out = {
        'id': v.id,
        'name': v.name,
        'vehicle_type': v.vehicle_type,
        'make': v.make,
        'model': v.model,
        'year': v.year,
        'license_plate': v.license_plate,
        'status': v.status,
        'condition': v.condition,
        'current_mileage': v.current_mileage,
    }
    if detail:
        out.update({
            'color': v.color or '',
            'vin': v.vin or '',
            'qr_code': v.qr_code or '',
            'insurance_provider': v.insurance_provider or '',
            'insurance_policy_number': v.insurance_policy_number or '',
            'insurance_expires_at': v.insurance_expires_at.isoformat()
                if v.insurance_expires_at else None,
        })
    return out


def _serialize_inventory_item(it) -> dict:
    return {
        'id': it.id,
        'vehicle_id': it.vehicle_id,
        'name': it.name,
        'category': it.category or '',
        'quantity': it.quantity,
        'unit': it.unit or '',
        'min_quantity': it.min_quantity,
        'reorder_quantity': it.reorder_quantity,
        'unit_cost': str(it.unit_cost) if it.unit_cost is not None else None,
        'description': it.description or '',
        'location_in_vehicle': it.location_in_vehicle or '',
        'qr_code': it.qr_code or '',
    }


def _serialize_fuel(f) -> dict:
    return {
        'id': f.id,
        'vehicle_id': f.vehicle_id,
        'date': f.date.isoformat() if f.date else None,
        'mileage': f.mileage,
        'gallons': str(f.gallons),
        'cost_per_gallon': str(f.cost_per_gallon),
        'total_cost': str(f.total_cost),
        'station': f.station or '',
        'miles_driven': f.miles_driven,
        'mpg': str(f.mpg) if f.mpg is not None else None,
        'notes': f.notes or '',
    }


def _serialize_damage(d) -> dict:
    return {
        'id': d.id,
        'vehicle_id': d.vehicle_id,
        'incident_date': d.incident_date.isoformat() if d.incident_date else None,
        'reported_by_id': d.reported_by_id,
        'description': d.description or '',
        'severity': d.severity,
        'repair_status': d.repair_status,
        'damage_location': d.damage_location or '',
        'estimated_cost': str(d.estimated_cost) if d.estimated_cost is not None else None,
        'insurance_claim_number': d.insurance_claim_number or '',
    }


def _my_vehicle_or_404(user, pk):
    """Return the ServiceVehicle if the caller has an active assignment, else None."""
    from vehicles.models import ServiceVehicle, VehicleAssignment
    try:
        v = ServiceVehicle.objects.get(pk=pk)
    except ServiceVehicle.DoesNotExist:
        return None
    has_active = VehicleAssignment.objects.filter(
        vehicle=v, user=user, end_date__isnull=True,
    ).exists()
    if not has_active:
        return None
    return v


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def my_vehicles_view(request):
    """
    GET /api/mobile/v1/vehicles/

    Vehicles currently assigned to the caller (active assignment).
    """
    from vehicles.models import VehicleAssignment
    qs = (VehicleAssignment.objects
          .filter(user=request.user, end_date__isnull=True)
          .select_related('vehicle')
          .order_by('-start_date'))
    return Response({
        'count': qs.count(),
        'results': [_serialize_vehicle(a.vehicle, detail=True) for a in qs],
    })


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def vehicle_detail_view(request, pk: int):
    """GET /api/mobile/v1/vehicles/<id>/  — only if assigned to caller."""
    v = _my_vehicle_or_404(request.user, pk)
    if v is None:
        return Response({'detail': 'Not found'}, status=404)
    return Response(_serialize_vehicle(v, detail=True))


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def vehicle_inventory_view(request, pk: int):
    """GET /api/mobile/v1/vehicles/<id>/inventory/ — items stocked in the vehicle."""
    v = _my_vehicle_or_404(request.user, pk)
    if v is None:
        return Response({'detail': 'Not found'}, status=404)
    items = v.inventory_items.all().order_by('category', 'name')
    return Response({
        'count': items.count(),
        'results': [_serialize_inventory_item(i) for i in items],
    })


@api_view(['GET', 'POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def vehicle_fuel_view(request, pk: int):
    """
    GET  /api/mobile/v1/vehicles/<id>/fuel/        — fuel log history (last 50)
    POST /api/mobile/v1/vehicles/<id>/fuel/        — log a fuel fill-up

    POST body: `{date?, mileage, gallons, cost_per_gallon, total_cost?,
                 station?, notes?}`. `total_cost` defaults to gallons *
    cost_per_gallon when omitted. `date` defaults to today.
    """
    from vehicles.models import VehicleFuelLog
    v = _my_vehicle_or_404(request.user, pk)
    if v is None:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'GET':
        logs = v.fuel_logs.all().order_by('-date', '-mileage')[:50]
        return Response({
            'count': logs.count(),
            'results': [_serialize_fuel(f) for f in logs],
        })

    data = request.data or {}

    # Required: mileage, gallons, cost_per_gallon
    try:
        mileage = int(data.get('mileage'))
    except (TypeError, ValueError):
        return Response({'detail': 'mileage required (int)'}, status=400)
    if mileage < 0:
        return Response({'detail': 'mileage must be >= 0'}, status=400)

    try:
        gallons = Decimal(str(data.get('gallons')))
        cost_per_gallon = Decimal(str(data.get('cost_per_gallon')))
    except (InvalidOperation, TypeError, ValueError):
        return Response({'detail': 'gallons and cost_per_gallon required'}, status=400)
    if gallons <= 0 or cost_per_gallon <= 0:
        return Response({'detail': 'gallons and cost_per_gallon must be > 0'}, status=400)

    total_cost_raw = data.get('total_cost')
    if total_cost_raw is not None:
        try:
            total_cost = Decimal(str(total_cost_raw))
        except (InvalidOperation, TypeError, ValueError):
            return Response({'detail': 'invalid total_cost'}, status=400)
    else:
        total_cost = (gallons * cost_per_gallon).quantize(Decimal('0.01'))

    when_raw = data.get('date')
    if when_raw:
        try:
            when = date_cls.fromisoformat(str(when_raw))
        except (ValueError, TypeError):
            return Response({'detail': 'invalid date (use YYYY-MM-DD)'}, status=400)
    else:
        when = timezone.localdate()

    fuel_log = VehicleFuelLog.objects.create(
        vehicle=v,
        date=when,
        mileage=mileage,
        gallons=gallons,
        cost_per_gallon=cost_per_gallon,
        total_cost=total_cost,
        station=(data.get('station') or '')[:200],
        notes=(data.get('notes') or '')[:2000],
    )
    # Keep odometer in sync if this fillup is more recent than the
    # vehicle's recorded mileage.
    if mileage > v.current_mileage:
        v.current_mileage = mileage
        v.save(update_fields=['current_mileage', 'updated_at']
               if any(f.name == 'updated_at' for f in v._meta.fields)
               else ['current_mileage'])
    return Response(_serialize_fuel(fuel_log),
                    status=drf_status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def vehicle_damage_view(request, pk: int):
    """
    GET  /api/mobile/v1/vehicles/<id>/damage/  — damage report history
    POST /api/mobile/v1/vehicles/<id>/damage/  — file a damage report

    POST body: `{description, severity?, damage_location?, incident_date?,
                 estimated_cost?}`. Severity defaults to 'minor';
    incident_date defaults to today.
    """
    from vehicles.models import VehicleDamageReport
    v = _my_vehicle_or_404(request.user, pk)
    if v is None:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'GET':
        reports = v.damage_reports.all().order_by('-incident_date')[:50]
        return Response({
            'count': reports.count(),
            'results': [_serialize_damage(r) for r in reports],
        })

    data = request.data or {}
    description = (data.get('description') or '').strip()
    if not description:
        return Response({'detail': 'description required'}, status=400)

    severity = data.get('severity') or 'minor'
    if severity not in ('minor', 'moderate', 'major', 'total_loss'):
        return Response({'detail': 'invalid severity'}, status=400)

    when_raw = data.get('incident_date')
    if when_raw:
        try:
            when = date_cls.fromisoformat(str(when_raw))
        except (ValueError, TypeError):
            return Response({'detail': 'invalid incident_date'}, status=400)
    else:
        when = timezone.localdate()

    cost_raw = data.get('estimated_cost')
    estimated_cost = None
    if cost_raw is not None:
        try:
            estimated_cost = Decimal(str(cost_raw))
        except (InvalidOperation, TypeError, ValueError):
            return Response({'detail': 'invalid estimated_cost'}, status=400)

    report = VehicleDamageReport.objects.create(
        vehicle=v,
        incident_date=when,
        reported_by=request.user,
        description=description[:5000],
        severity=severity,
        damage_location=(data.get('damage_location') or '')[:200],
        estimated_cost=estimated_cost,
        condition_before=v.condition,
    )
    return Response(_serialize_damage(report),
                    status=drf_status.HTTP_201_CREATED)
