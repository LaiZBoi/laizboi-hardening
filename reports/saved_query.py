"""
Phase 26 v1 (v3.17.246) — Saved Query helpers.

The model is in `reports.models.SavedQuery`. This module owns the
allow-list of which model fields are queryable + how to build a Django
ORM filter from the saved JSON. Allow-listing is the security boundary
— a user can NOT save a query that filters on, say, an arbitrary
related model's field. They get exactly what `MODEL_CONFIG` exposes.
"""
from django.db.models import Q


# Per-target metadata.
#   model: import path used by `apps.get_model`.
#   filterable_fields: dict of {field_name: type}. Type is one of:
#       'str'  → equals / contains
#       'int'  → equals / gt / gte / lt / lte
#       'date' → gt / gte / lt / lte
#       'bool' → equals
#       'fk'   → equals on the FK pk
#   columns: list of field names safe to render in the result table.
MODEL_CONFIG = {
    'psa.Ticket': {
        'label': 'PSA Tickets',
        'filterable_fields': {
            'subject': 'str',
            'description': 'str',
            'assigned_to__username': 'str',
            'organization__name': 'str',
            'priority__code': 'str',
            'status__slug': 'str',
            'queue__name': 'str',
            'is_internal': 'bool',
            'sla_breached_response': 'bool',
            'sla_breached_resolution': 'bool',
            'created_at': 'date',
            'closed_at': 'date',
        },
        'columns': [
            'ticket_number', 'subject', 'organization__name',
            'priority__code', 'status__name', 'queue__name',
            'assigned_to__username', 'created_at', 'closed_at',
        ],
        'default_sort': '-created_at',
    },
    'assets.Asset': {
        'label': 'Assets',
        'filterable_fields': {
            'name': 'str',
            'asset_type': 'str',
            'organization__name': 'str',
            'serial_number': 'str',
            'vendor': 'str',
            'warranty_expiry': 'date',
            'purchase_date': 'date',
        },
        'columns': [
            'name', 'asset_type', 'organization__name',
            'serial_number', 'vendor', 'warranty_expiry', 'purchase_date',
        ],
        'default_sort': 'name',
    },
    'vault.Password': {
        'label': 'Vault Passwords',
        'filterable_fields': {
            'title': 'str',
            'username': 'str',
            'password_type': 'str',
            'organization__name': 'str',
            'is_personal': 'bool',
            'requires_reveal_approval': 'bool',
            'expires_at': 'date',
        },
        'columns': [
            'title', 'username', 'password_type', 'organization__name',
            'expires_at', 'requires_reveal_approval',
        ],
        'default_sort': 'title',
    },
}

OPERATORS_BY_TYPE = {
    'str': ['equals', 'contains', 'startswith'],
    'int': ['equals', 'gt', 'gte', 'lt', 'lte'],
    'date': ['gt', 'gte', 'lt', 'lte'],
    'bool': ['equals'],
    'fk': ['equals'],
}

OP_TO_LOOKUP = {
    'equals': '',
    'contains': '__icontains',
    'startswith': '__istartswith',
    'gt': '__gt',
    'gte': '__gte',
    'lt': '__lt',
    'lte': '__lte',
}


def get_model(target_model):
    """Resolve the target_model string to a Django model class, or None."""
    from django.apps import apps
    if target_model not in MODEL_CONFIG:
        return None
    try:
        app_label, model_name = target_model.split('.')
        return apps.get_model(app_label, model_name)
    except (ValueError, LookupError):
        return None


def build_filter(target_model, filters):
    """
    Build a `Q` from a list of `{field, op, value}` dicts. Bad / unknown
    fields are silently dropped — we do NOT want a saved query to error
    out because the model's schema changed; just skip stale filters.

    Returns Q() (matches everything) if the filter list is empty/invalid.
    """
    config = MODEL_CONFIG.get(target_model)
    if not config:
        return Q()
    allowed = config['filterable_fields']
    q = Q()
    for f in filters or []:
        if not isinstance(f, dict):
            continue
        field = f.get('field')
        op = f.get('op') or 'equals'
        value = f.get('value')
        if field not in allowed:
            continue
        if op not in OPERATORS_BY_TYPE.get(allowed[field], []):
            continue
        ftype = allowed[field]
        # Cast value to the expected python type. Strings stay strings.
        if ftype == 'bool':
            value = str(value).lower() in ('1', 'true', 'yes', 'on')
        elif ftype == 'int':
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif value is None or value == '':
            continue
        lookup = field + OP_TO_LOOKUP[op]
        q &= Q(**{lookup: value})
    return q


def execute(saved_query, *, organization=None):
    """
    Run a `SavedQuery` and return (model, queryset). Tenant scope:
    if `organization` is provided AND the model has an `organization`
    FK, scope to that org. Otherwise the queryset is global.
    """
    model = get_model(saved_query.target_model)
    if model is None:
        return None, model.objects.none() if model else []
    qs = model.objects.all()
    if organization is not None:
        try:
            qs = qs.filter(organization=organization)
        except Exception:
            pass
    qs = qs.filter(build_filter(saved_query.target_model, saved_query.filters or []))
    sort = saved_query.sort_by or MODEL_CONFIG[saved_query.target_model]['default_sort']
    try:
        qs = qs.order_by(sort)
    except Exception:
        pass
    return model, qs


def render_columns(saved_query):
    cfg = MODEL_CONFIG.get(saved_query.target_model)
    if not cfg:
        return []
    if saved_query.columns:
        return [c for c in saved_query.columns if c in cfg['columns']]
    return list(cfg['columns'])
