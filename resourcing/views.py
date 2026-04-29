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

from .forms import (
    BillableTargetForm, HolidayForm, LeaveRequestForm,
    UserCertificationForm, UserSkillForm, WorkingHoursForm,
)
from .models import (
    BillableTarget, Holiday, LeaveRequest,
    UserCertification, UserSkill, WorkingHours,
)

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
    """Profile page with Skills / Certifications / Working Hours / Leave / Billable cards."""
    target_id = request.GET.get('user')
    if target_id and (request.user.is_superuser or request.user.is_staff):
        target = get_object_or_404(User, pk=int(target_id))
        viewing_as_admin = (target.id != request.user.id)
    else:
        target = request.user
        viewing_as_admin = False

    today = timezone.now().date()
    skills = UserSkill.objects.filter(user=target)
    certifications = UserCertification.objects.filter(user=target)
    working_hours = WorkingHours.objects.filter(user=target)

    # Phase 2.2 — leave summary (this calendar year)
    year_start = today.replace(month=1, day=1)
    leave_qs_this_year = LeaveRequest.objects.filter(
        user=target, start_date__gte=year_start,
    )
    approved_this_year = leave_qs_this_year.filter(status='approved')
    pending_this_year = leave_qs_this_year.filter(status='pending')
    days_used_this_year = sum(lr.total_days for lr in approved_this_year)

    # Billable target
    billable_target = BillableTarget.objects.filter(user=target).first()

    return render(request, 'resourcing/my_resourcing.html', {
        'target': target,
        'viewing_as_admin': viewing_as_admin,
        'skills': skills,
        'certifications': certifications,
        'working_hours': working_hours,
        'today': today,
        'approved_count': approved_this_year.count(),
        'pending_count': pending_this_year.count(),
        'days_used_this_year': days_used_this_year,
        'billable_target': billable_target,
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


# ---------------------------------------------------------------------------
# Phase 2.2 — Holidays (staff/superuser only)
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def holiday_list(request):
    holidays = Holiday.objects.select_related('organization').all()
    return render(request, 'resourcing/holiday_list.html', {
        'holidays': holidays,
        'today': timezone.now().date(),
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def holiday_add(request):
    if request.method == 'POST':
        form = HolidayForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Holiday added.')
            return redirect('resourcing:holiday_list')
    else:
        form = HolidayForm()
    return render(request, 'resourcing/holiday_form.html', {
        'form': form, 'mode': 'add', 'title': 'Add Holiday',
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def holiday_edit(request, pk):
    instance = get_object_or_404(Holiday, pk=pk)
    if request.method == 'POST':
        form = HolidayForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, 'Holiday updated.')
            return redirect('resourcing:holiday_list')
    else:
        form = HolidayForm(instance=instance)
    return render(request, 'resourcing/holiday_form.html', {
        'form': form, 'instance': instance, 'mode': 'edit', 'title': 'Edit Holiday',
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def holiday_delete(request, pk):
    instance = get_object_or_404(Holiday, pk=pk)
    if request.method == 'POST':
        instance.delete()
        messages.success(request, 'Holiday deleted.')
        return redirect('resourcing:holiday_list')
    return render(request, 'resourcing/confirm_delete.html', {
        'instance': instance, 'kind': 'Holiday', 'target': None,
    })


# ---------------------------------------------------------------------------
# Phase 2.2 — Leave requests
# ---------------------------------------------------------------------------

@login_required
def my_leave(request):
    """Current user's leave requests, optionally filterable by status / year."""
    status_filter = request.GET.get('status', '')
    year_filter = request.GET.get('year', '')

    qs = LeaveRequest.objects.filter(user=request.user)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if year_filter:
        try:
            yr = int(year_filter)
            qs = qs.filter(start_date__year=yr)
        except (TypeError, ValueError):
            pass

    today = timezone.now().date()
    pending = qs.filter(status__in=('pending',)).order_by('start_date')
    upcoming = qs.filter(status='approved', end_date__gte=today).order_by('start_date')
    past = qs.exclude(pk__in=[lr.pk for lr in pending] + [lr.pk for lr in upcoming]).order_by('-start_date')

    return render(request, 'resourcing/my_leave.html', {
        'pending': pending,
        'upcoming': upcoming,
        'past': past,
        'status_filter': status_filter,
        'year_filter': year_filter,
        'leave_status_choices': LeaveRequest.STATUS_CHOICES,
    })


@login_required
def leave_request_add(request):
    if request.method == 'POST':
        form = LeaveRequestForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.status = 'pending'
            try:
                obj.full_clean(exclude=None)
            except Exception as e:
                form.add_error(None, str(e))
            else:
                obj.save()
                messages.success(request, 'Leave request submitted.')
                return redirect('resourcing:my_leave')
    else:
        form = LeaveRequestForm()
    return render(request, 'resourcing/leave_form.html', {
        'form': form, 'mode': 'add', 'title': 'Request Leave',
    })


@login_required
def leave_request_cancel(request, pk):
    """Owner can cancel their own pending leave request."""
    instance = get_object_or_404(LeaveRequest, pk=pk)
    if instance.user_id != request.user.id and not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied
    if instance.status != 'pending':
        messages.error(request, 'Only pending leave requests can be cancelled.')
        return redirect('resourcing:my_leave')
    if request.method == 'POST':
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Leave request cancelled.')
        return redirect('resourcing:my_leave')
    return render(request, 'resourcing/confirm_delete.html', {
        'instance': instance,
        'kind': 'Leave request (cancel)',
        'target': None,
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def leave_approvals(request):
    """Staff queue of pending leave requests across all users.

    Supports bulk approve/deny via POST with leave_ids[] + action=approve|deny.
    """
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('leave_ids')
        note = request.POST.get('note', '')
        if action in ('approve', 'deny') and ids:
            from audit.models import AuditLog
            new_status = 'approved' if action == 'approve' else 'denied'
            now = timezone.now()
            count = 0
            for lr in LeaveRequest.objects.filter(pk__in=ids, status='pending'):
                lr.status = new_status
                lr.approver = request.user
                lr.decided_at = now
                lr.decision_note = note
                lr.save(update_fields=['status', 'approver', 'decided_at',
                                       'decision_note', 'updated_at'])
                AuditLog.log(
                    user=request.user,
                    action='update',
                    object_type='LeaveRequest',
                    object_id=lr.pk,
                    object_repr=str(lr),
                    description=f'Leave {new_status} for {lr.user.username} '
                                f'({lr.start_date} → {lr.end_date})',
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    path=request.path,
                    extra_data={'action': new_status, 'note': note},
                )
                count += 1
            messages.success(request, f'{count} request(s) {new_status}.')
        return redirect('resourcing:leave_approvals')

    pending = LeaveRequest.objects.filter(status='pending').select_related('user').order_by('start_date')
    return render(request, 'resourcing/leave_approvals.html', {
        'pending': pending,
    })


@login_required
@user_passes_test(_is_staff_or_super, login_url='/accounts/profile/')
def leave_decide(request, pk):
    """POST endpoint: ?action=approve|deny + optional note. Audit-logged."""
    if request.method != 'POST':
        return HttpResponseForbidden('POST required.')
    instance = get_object_or_404(LeaveRequest, pk=pk)
    action = request.GET.get('action') or request.POST.get('action')
    note = request.POST.get('note', '')
    if action not in ('approve', 'deny'):
        messages.error(request, 'Invalid action.')
        return redirect('resourcing:leave_approvals')
    new_status = 'approved' if action == 'approve' else 'denied'
    instance.status = new_status
    instance.approver = request.user
    instance.decided_at = timezone.now()
    instance.decision_note = note
    instance.save(update_fields=['status', 'approver', 'decided_at',
                                 'decision_note', 'updated_at'])
    from audit.models import AuditLog
    AuditLog.log(
        user=request.user,
        action='update',
        object_type='LeaveRequest',
        object_id=instance.pk,
        object_repr=str(instance),
        description=f'Leave {new_status} for {instance.user.username} '
                    f'({instance.start_date} → {instance.end_date})',
        ip_address=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        path=request.path,
        extra_data={'action': new_status, 'note': note},
    )
    messages.success(request, f'Leave request {new_status}.')
    return redirect('resourcing:leave_approvals')


# ---------------------------------------------------------------------------
# Phase 2.2 — Billable target
# ---------------------------------------------------------------------------

@login_required
def my_billable_target(request):
    """View own billable target. Staff can view anyone via ?user=<id>."""
    target_id = request.GET.get('user')
    if target_id and (request.user.is_superuser or request.user.is_staff):
        target = get_object_or_404(User, pk=int(target_id))
    else:
        target = request.user
    bt = BillableTarget.objects.filter(user=target).first()
    can_edit = (request.user.id == target.id
                or request.user.is_staff or request.user.is_superuser)
    return render(request, 'resourcing/billable_target_form.html', {
        'target': target,
        'billable_target': bt,
        'can_edit': can_edit,
        'view_only': True,
    })


@login_required
def billable_target_edit(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    if request.user.id != target.id and not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied
    bt, _ = BillableTarget.objects.get_or_create(user=target)
    if request.method == 'POST':
        form = BillableTargetForm(request.POST, instance=bt)
        if form.is_valid():
            form.save()
            messages.success(request, 'Billable target updated.')
            if request.user.id == target.id:
                return redirect('resourcing:my_billable_target')
            return redirect(reverse('resourcing:my_billable_target') + f'?user={target.id}')
    else:
        form = BillableTargetForm(instance=bt)
    return render(request, 'resourcing/billable_target_form.html', {
        'form': form,
        'target': target,
        'billable_target': bt,
        'mode': 'edit',
        'title': 'Edit Billable Target',
        'can_edit': True,
        'view_only': False,
    })
