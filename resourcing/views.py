"""
Resourcing views — Phase 2.1.

* /resourcing/me/                         — three-card profile (skills, certs, hours)
* /resourcing/{skill,cert,hours}/...      — 9 simple CRUD views
* /resourcing/roster/                     — staff/superuser-only tech roster

Permission rule for the CRUD views: a user may manage only their own rows,
EXCEPT superusers + staff who can manage anyone's by passing ?user=<id>
(or POSTing user=<id>). The check is centralized in `_resolve_target_user`.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import UserCertificationForm, UserSkillForm, WorkingHoursForm
from .models import UserCertification, UserSkill, WorkingHours

User = get_user_model()


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _resolve_target_user(request, instance=None):
    """
    Return (user, can_edit). For new rows, the target is determined from
    `?user=<id>` (only honoured for superuser/staff) or falls back to
    request.user. For existing rows, the target is always the row's owner.

    `can_edit` is True if the requester is the owner OR is staff/superuser.
    """
    if instance is not None:
        target = instance.user
    else:
        target_id = request.GET.get('user') or request.POST.get('user')
        if target_id and (request.user.is_superuser or request.user.is_staff):
            target = get_object_or_404(User, pk=int(target_id))
        else:
            target = request.user
    can_edit = (
        request.user.id == target.id
        or request.user.is_superuser
        or request.user.is_staff
    )
    return target, can_edit


def _is_staff_or_super(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def _redirect_to_profile(target):
    """Where to go after CRUD success — own profile, or admin user-scoped view."""
    return redirect(reverse('resourcing:my_resourcing') + (f'?user={target.id}' if target else ''))


# ---------------------------------------------------------------------------
# My Resources (three-card profile page)
# ---------------------------------------------------------------------------

@login_required
def my_resourcing(request):
    """Profile page with Skills / Certifications / Working Hours cards."""
    target_id = request.GET.get('user')
    if target_id and (request.user.is_superuser or request.user.is_staff):
        target = get_object_or_404(User, pk=int(target_id))
        viewing_as_admin = (target.id != request.user.id)
    else:
        target = request.user
        viewing_as_admin = False

    skills = UserSkill.objects.filter(user=target)
    certifications = UserCertification.objects.filter(user=target)
    working_hours = WorkingHours.objects.filter(user=target)

    return render(request, 'resourcing/my_resourcing.html', {
        'target': target,
        'viewing_as_admin': viewing_as_admin,
        'skills': skills,
        'certifications': certifications,
        'working_hours': working_hours,
        'today': timezone.now().date(),
    })


# ---------------------------------------------------------------------------
# Generic add/edit/delete factory helpers
# ---------------------------------------------------------------------------

def _add(request, model_cls, form_cls, template, success_label):
    target, can_edit = _resolve_target_user(request)
    if not can_edit:
        raise PermissionDenied
    if request.method == 'POST':
        form = form_cls(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = target
            try:
                obj.full_clean(exclude=None)
            except Exception as e:
                form.add_error(None, str(e))
            else:
                obj.save()
                messages.success(request, f'{success_label} added.')
                return _redirect_to_profile(target if target.id != request.user.id else None)
    else:
        form = form_cls()
    return render(request, template, {
        'form': form,
        'target': target,
        'mode': 'add',
        'title': f'Add {success_label}',
    })


def _edit(request, pk, model_cls, form_cls, template, success_label):
    instance = get_object_or_404(model_cls, pk=pk)
    target, can_edit = _resolve_target_user(request, instance=instance)
    if not can_edit:
        raise PermissionDenied
    if request.method == 'POST':
        form = form_cls(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            try:
                obj.full_clean(exclude=None)
            except Exception as e:
                form.add_error(None, str(e))
            else:
                obj.save()
                messages.success(request, f'{success_label} updated.')
                return _redirect_to_profile(target if target.id != request.user.id else None)
    else:
        form = form_cls(instance=instance)
    return render(request, template, {
        'form': form,
        'target': target,
        'instance': instance,
        'mode': 'edit',
        'title': f'Edit {success_label}',
    })


def _delete(request, pk, model_cls, success_label):
    instance = get_object_or_404(model_cls, pk=pk)
    target, can_edit = _resolve_target_user(request, instance=instance)
    if not can_edit:
        raise PermissionDenied
    if request.method == 'POST':
        instance.delete()
        messages.success(request, f'{success_label} deleted.')
        return _redirect_to_profile(target if target.id != request.user.id else None)
    return render(request, 'resourcing/confirm_delete.html', {
        'instance': instance,
        'kind': success_label,
        'target': target,
    })


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

@login_required
def skill_add(request):
    return _add(request, UserSkill, UserSkillForm, 'resourcing/skill_form.html', 'Skill')


@login_required
def skill_edit(request, pk):
    return _edit(request, pk, UserSkill, UserSkillForm, 'resourcing/skill_form.html', 'Skill')


@login_required
def skill_delete(request, pk):
    return _delete(request, pk, UserSkill, 'Skill')


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------

@login_required
def cert_add(request):
    return _add(request, UserCertification, UserCertificationForm,
                'resourcing/cert_form.html', 'Certification')


@login_required
def cert_edit(request, pk):
    return _edit(request, pk, UserCertification, UserCertificationForm,
                 'resourcing/cert_form.html', 'Certification')


@login_required
def cert_delete(request, pk):
    return _delete(request, pk, UserCertification, 'Certification')


# ---------------------------------------------------------------------------
# Working Hours
# ---------------------------------------------------------------------------

@login_required
def hours_add(request):
    return _add(request, WorkingHours, WorkingHoursForm,
                'resourcing/hours_form.html', 'Working hours')


@login_required
def hours_edit(request, pk):
    return _edit(request, pk, WorkingHours, WorkingHoursForm,
                 'resourcing/hours_form.html', 'Working hours')


@login_required
def hours_delete(request, pk):
    return _delete(request, pk, WorkingHours, 'Working hours')


# ---------------------------------------------------------------------------
# Tech roster (staff-only)
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def tech_roster(request):
    """
    List every active staff/internal user with skill counts, cert counts
    (with expiry warnings), and a "working now" indicator.
    """
    today = timezone.now().date()
    soon = today + timedelta(days=60)

    # "Internal users" — staff users (django is_staff=True) + anyone whose
    # accounts.UserProfile.user_type == 'staff'. We OR them together so a
    # site that hasn't turned on Django staff still shows MSP techs.
    qs = User.objects.filter(is_active=True).filter(
        Q(is_staff=True)
        | Q(is_superuser=True)
        | Q(profile__user_type='staff')
    ).distinct().select_related('profile')

    qs = qs.annotate(
        skill_count=Count('resourcing_skills', distinct=True),
        cert_count=Count('resourcing_certifications', distinct=True),
    ).order_by('username')

    # Build the "expiring / expired cert" badges per user (single query).
    expiring_by_user = {}
    expired_by_user = {}
    for cert in UserCertification.objects.filter(
        user__in=qs, expires_at__isnull=False
    ).only('user_id', 'expires_at'):
        if cert.expires_at < today:
            expired_by_user[cert.user_id] = expired_by_user.get(cert.user_id, 0) + 1
        elif cert.expires_at <= soon:
            expiring_by_user[cert.user_id] = expiring_by_user.get(cert.user_id, 0) + 1

    rows = []
    for u in qs:
        profile = getattr(u, 'profile', None)
        try:
            working_now = profile.is_working_now() if profile else True
        except Exception:
            working_now = None  # "unknown" — render as grey
        rows.append({
            'user': u,
            'profile': profile,
            'skill_count': u.skill_count,
            'cert_count': u.cert_count,
            'expiring_count': expiring_by_user.get(u.id, 0),
            'expired_count': expired_by_user.get(u.id, 0),
            'working_now': working_now,
        })

    return render(request, 'resourcing/tech_roster.html', {
        'rows': rows,
        'today': today,
    })
