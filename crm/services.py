"""
crm/services.py — engine helpers for commissions + lead scoring.

Phase 5.2: split commission/scoring logic away from models + views so
both the engine and the lead.save() hook can call into them without
circular imports.
"""
from decimal import Decimal


def compute_commission_for_opportunity(opportunity):
    """
    Find the highest-priority active CommissionRule that matches `opportunity`,
    create a `Commission` row for opportunity.assigned_to, return the
    Commission instance (or None if no rule matched / no assignee).

    Idempotent: if a Commission already exists for (opportunity, assignee),
    update its amount and rule but don't double-create.
    """
    from .models import CommissionRule, Commission
    if not opportunity.assigned_to_id:
        return None
    rules = CommissionRule.objects.filter(
        organization=opportunity.organization, is_active=True,
    ).order_by('priority', 'pk')
    rule = next((r for r in rules if r.matches(opportunity)), None)
    if not rule:
        return None
    amount = rule.compute(opportunity)
    if amount <= 0:
        return None
    obj, _ = Commission.objects.update_or_create(
        opportunity=opportunity, user=opportunity.assigned_to,
        defaults={'rule': rule, 'amount': amount, 'status': 'pending'},
    )
    return obj


def score_lead(lead):
    """
    Heuristic 0-100 lead score. Saved on the Lead model via the field added
    in Phase 5.2. Inputs:
      +20 if estimated_value >= 10k
      +15 if industry is a target sector (kept generic — based on common
          MSP-target industries: legal/healthcare/finance/professional services)
      +15 if employee_count >= 50
      +20 if contact_email + contact_phone both present (good contact data)
      +10 if website present
      +10 if campaign attribution (intentional source)
      +10 baseline if assigned_to is set (someone owns it)
    """
    score = 10  # baseline if status not new (i.e. someone has touched it)
    if lead.status == 'new':
        score = 0
    if (lead.estimated_value or 0) >= 10000:
        score += 20
    target_industries = {'legal', 'healthcare', 'finance', 'professional services',
                         'accounting', 'manufacturing', 'medical'}
    if lead.industry and lead.industry.lower() in target_industries:
        score += 15
    if (lead.employee_count or 0) >= 50:
        score += 15
    if lead.contact_email and lead.contact_phone:
        score += 20
    if lead.website:
        score += 10
    if lead.campaign_id:
        score += 10
    if lead.assigned_to_id:
        score += 10
    return min(100, max(0, score))
