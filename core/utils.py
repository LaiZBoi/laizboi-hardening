"""
Core utilities
"""
from django.db import models


def descendant_org_ids(organization):
    """
    Phase 18 (v3.17.240): return the set of organization IDs visible
    when scoping to `organization` — i.e. the org itself plus all
    descendants reachable via `Organization.parent`.

    Walks up to 5 levels deep (defense against accidental cycles) and
    breaks early once a level produces no new IDs. Returns a set so
    callers can use it in `__in=` filters.

    Lazy import on Organization to avoid a circular import at module
    load time (core.utils is imported very early in the boot sequence).
    """
    from .models import Organization
    if organization is None:
        return set()
    seen = {organization.pk}
    frontier = [organization.pk]
    for _ in range(5):
        children = list(
            Organization.objects.filter(parent_id__in=frontier)
                                 .values_list('pk', flat=True)
        )
        new = [pk for pk in children if pk not in seen]
        if not new:
            break
        seen.update(new)
        frontier = new
    return seen


class OrganizationQuerySet(models.QuerySet):
    """
    QuerySet that filters by organization automatically.
    """
    def for_organization(self, organization, *, include_descendants=True):
        """
        Filter rows to those owned by `organization`. When
        `include_descendants=True` (default, Phase 18), also include
        rows owned by any descendant via `Organization.parent`.

        Pass `include_descendants=False` for legacy strict scoping when
        a caller needs to address the org alone.
        """
        if include_descendants:
            ids = descendant_org_ids(organization)
            return self.filter(organization_id__in=ids)
        return self.filter(organization=organization)


class OrganizationManager(models.Manager):
    """
    Manager that provides organization filtering.
    """
    def get_queryset(self):
        return OrganizationQuerySet(self.model, using=self._db)

    def for_organization(self, organization, *, include_descendants=True):
        return self.get_queryset().for_organization(
            organization, include_descendants=include_descendants,
        )
