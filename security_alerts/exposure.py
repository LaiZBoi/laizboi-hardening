"""
Phase 23 v3.17.339 — Exposure scoring.

Per-Organization rolling score (0–1000) computed from open
SecurityAlerts (severity-weighted) + open Vulnerabilities + asset
criticality count. Higher score = more exposure = higher attention.

The score is cached on `core.Organization.exposure_score` and
recomputed in batch by the `recompute_exposure_scores` management
command (lightweight enough to run hourly via cron). The display
surfaces (org detail, threat dashboard) read the cached value.
"""
from __future__ import annotations


SEVERITY_WEIGHTS = {
    'info': 1,
    'low': 2,
    'medium': 5,
    'high': 12,
    'critical': 25,
}

VULN_SEVERITY_WEIGHTS = {
    'low': 2,
    'medium': 6,
    'high': 15,
    'critical': 30,
}

# Asset count bonus: +1 per asset / 5 (capped at +50). Big estates
# inherently have more attack surface.
ASSET_COUNT_DIVISOR = 5
ASSET_COUNT_CAP = 50

MAX_SCORE = 1000


def compute_exposure_score(org) -> int:
    """Compute the exposure score for one Organization. Pure read-only."""
    from security_alerts.models import SecurityAlert, SecurityIncident

    score = 0

    # Open / unresolved SecurityAlerts — count both as the org tenant + as the client_org.
    alert_qs = SecurityAlert.objects.filter(
        status__in=['new', 'acknowledged'],
    ).filter(_org_match_q(org))
    sev_counts = {}
    for sev, _label in SecurityAlert.SEVERITY_CHOICES:
        sev_counts[sev] = alert_qs.filter(severity=sev).count()
    for sev, n in sev_counts.items():
        score += SEVERITY_WEIGHTS.get(sev, 0) * n

    # Open SecurityIncidents — bigger weight, fewer expected.
    inc_qs = SecurityIncident.objects.filter(
        status__in=['open', 'investigating', 'contained'],
    ).filter(_org_match_q(org))
    for sev, _label in SecurityAlert.SEVERITY_CHOICES:
        n = inc_qs.filter(severity=sev).count()
        score += SEVERITY_WEIGHTS.get(sev, 0) * 2 * n

    # Open Vulnerabilities — global vulns count too if their affected_assets
    # touch this org. For perf use raw counts: per-org rows + global rows.
    try:
        from assets.models import Vulnerability
        vqs = Vulnerability.objects.filter(is_active=True).filter(
            models_q_or(organization=org, organization__isnull=True),
        )
        for sev, _label in Vulnerability.SEVERITY_CHOICES:
            n = vqs.filter(severity=sev).count()
            score += VULN_SEVERITY_WEIGHTS.get(sev, 0) * n
    except Exception:
        pass

    # Asset count bonus (small, capped).
    try:
        from assets.models import Asset
        asset_count = Asset.objects.filter(organization=org).count()
        score += min(asset_count // ASSET_COUNT_DIVISOR, ASSET_COUNT_CAP)
    except Exception:
        pass

    return min(score, MAX_SCORE)


def _org_match_q(org):
    """Build a Q matching either organization=org or client_org=org so that
    MSP-tenant alerts pinned to this client also count."""
    from django.db.models import Q
    return Q(organization=org) | Q(client_org=org)


def models_q_or(**kwargs):
    """Tiny helper: build a Q OR-ing a dict's positive matches.

    e.g. ``models_q_or(organization=org, organization__isnull=True)``
    →  Q(organization=org) | Q(organization__isnull=True).
    """
    from django.db.models import Q
    q = None
    for key, val in kwargs.items():
        clause = Q(**{key: val})
        q = clause if q is None else q | clause
    return q


def recompute_for_org(org) -> int:
    """Compute + persist + return the new score for one org."""
    from django.utils import timezone
    score = compute_exposure_score(org)
    org.exposure_score = score
    org.exposure_score_updated_at = timezone.now()
    org.save(update_fields=['exposure_score', 'exposure_score_updated_at'])
    return score
