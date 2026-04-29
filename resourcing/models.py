"""
Resource-management models — Phase 2.1 + Phase 2.2.

Cross-cuts accounts (User), psa, and core, so this lives in its own app
to avoid circular imports and to keep migration ownership clean.

Phase 2.1 models:
  * UserSkill          — what techs are good at (proficiency tiers)
  * UserCertification  — credentials with optional expiry tracking
  * WorkingHours       — per-weekday availability windows

Phase 2.2 models:
  * Holiday            — org-scoped or global non-working days
  * LeaveRequest       — PTO with approval workflow
  * BillableTarget     — per-tech weekly billable-hours goal

Plus a `working_days_in_period` helper used by Phase 3 capacity reporting
and Phase 8.5 GPS off-shift suppression.
"""
from django.conf import settings as django_settings
from django.db import models


class UserSkill(models.Model):
    PROFICIENCY = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
        ('expert', 'Expert'),
    ]
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='resourcing_skills',
    )
    name = models.CharField(max_length=120)
    proficiency = models.CharField(max_length=20, choices=PROFICIENCY, default='intermediate')
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resourcing_user_skills'
        unique_together = [['user', 'name']]
        ordering = ['name']

    def __str__(self):
        return f'{self.user.username}: {self.name} ({self.get_proficiency_display()})'


class UserCertification(models.Model):
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='resourcing_certifications',
    )
    name = models.CharField(
        max_length=200,
        help_text='e.g. "Microsoft 365 Certified: Modern Desktop Administrator"',
    )
    issuer = models.CharField(
        max_length=120,
        blank=True,
        help_text='e.g. Microsoft, Cisco, CompTIA, AWS',
    )
    credential_id = models.CharField(max_length=120, blank=True)
    issued_at = models.DateField(null=True, blank=True)
    expires_at = models.DateField(
        null=True, blank=True,
        help_text='Leave blank if no expiry.',
    )
    verification_url = models.URLField(blank=True)
    attachment = models.FileField(
        upload_to='certifications/%Y/%m/', null=True, blank=True,
        help_text='PDF or image of the certificate.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resourcing_user_certifications'
        ordering = ['-issued_at', 'name']

    def __str__(self):
        return f'{self.user.username}: {self.name}'

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expires_at is not None and self.expires_at < timezone.now().date()

    @property
    def expires_soon(self):
        """True if expires within the next 60 days (and not already expired)."""
        from datetime import timedelta
        from django.utils import timezone
        if not self.expires_at:
            return False
        today = timezone.now().date()
        return today <= self.expires_at <= today + timedelta(days=60)


class WorkingHours(models.Model):
    """
    A user's working window for a specific weekday. Multiple rows per
    weekday allowed (split shifts: 9-12, 13-17). Times are in the user's
    profile timezone (UserProfile.timezone) — capacity reporting + GPS
    off-shift suppression normalize to UTC at query time.
    """
    WEEKDAYS = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='resourcing_working_hours',
    )
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resourcing_working_hours'
        ordering = ['user', 'weekday', 'start_time']

    def __str__(self):
        return f'{self.user.username} {self.get_weekday_display()} {self.start_time}–{self.end_time}'

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.end_time <= self.start_time:
            raise ValidationError({'end_time': 'End time must be after start time.'})


# ---------------------------------------------------------------------------
# Phase 2.2 — Holiday + LeaveRequest + BillableTarget
# ---------------------------------------------------------------------------


class Holiday(models.Model):
    """Org-wide non-working day. Used by capacity reporting (subtracts
    from available hours) and the off-shift suppression query for
    GPS auto-time (Phase 8.5)."""
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='resourcing_holidays',
        null=True, blank=True,
        help_text='NULL = applies to every org (e.g. national holidays).',
    )
    name = models.CharField(max_length=120)
    date = models.DateField()
    is_recurring_yearly = models.BooleanField(
        default=False,
        help_text='If true, the date applies every year (only month + day matter).',
    )
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resourcing_holidays'
        ordering = ['date', 'name']
        indexes = [models.Index(fields=['organization', 'date'])]

    def __str__(self):
        return f'{self.name} ({self.date})'

    @classmethod
    def is_holiday(cls, target_date, organization=None):
        """True if target_date is a holiday for `organization` (or any
        global holiday). Honors yearly recurrence."""
        from django.db.models import Q
        qs = cls.objects.filter(
            Q(organization__isnull=True) | Q(organization=organization)
        )
        # Exact date match
        if qs.filter(date=target_date, is_recurring_yearly=False).exists():
            return True
        # Recurring yearly — match month+day only
        if qs.filter(
            is_recurring_yearly=True,
            date__month=target_date.month,
            date__day=target_date.day,
        ).exists():
            return True
        return False


class LeaveRequest(models.Model):
    LEAVE_TYPES = [
        ('vacation', 'Vacation'),
        ('sick', 'Sick Leave'),
        ('personal', 'Personal Day'),
        ('bereavement', 'Bereavement'),
        ('jury_duty', 'Jury Duty'),
        ('parental', 'Parental Leave'),
        ('unpaid', 'Unpaid Leave'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='resourcing_leave_requests',
    )
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPES, default='vacation')
    start_date = models.DateField()
    end_date = models.DateField()
    is_half_day = models.BooleanField(
        default=False,
        help_text='Half-day flag — only meaningful when start_date == end_date.',
    )
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    approver = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resourcing_leave_requests'
        ordering = ['-start_date', '-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['start_date', 'end_date']),
        ]

    def __str__(self):
        return f'{self.user.username} — {self.get_leave_type_display()} {self.start_date}'

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date must be on or after start date.'})

    @property
    def total_days(self):
        if self.is_half_day and self.start_date == self.end_date:
            return 0.5
        return (self.end_date - self.start_date).days + 1

    @classmethod
    def is_user_on_leave(cls, user, target_date):
        """True if `user` has an approved leave covering `target_date`."""
        return cls.objects.filter(
            user=user, status='approved',
            start_date__lte=target_date, end_date__gte=target_date,
        ).exists()


class BillableTarget(models.Model):
    """Per-tech weekly billable-hours target. Used by Phase 3 utilization
    reporting: actual_billable_hours / target_hours_per_week → % utilization."""
    user = models.OneToOneField(
        django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='resourcing_billable_target',
    )
    target_hours_per_week = models.DecimalField(
        max_digits=5, decimal_places=2, default=32,
        help_text='Hours per week the tech should bill against client work.',
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resourcing_billable_targets'

    def __str__(self):
        return f'{self.user.username}: {self.target_hours_per_week}h/wk'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def working_days_in_period(user, start_date, end_date, organization=None):
    """Count working days for `user` in [start_date, end_date], excluding:
    - Days the user has no WorkingHours for that weekday
    - Org or global holidays
    - Approved leave requests
    Used by Phase 3 capacity reporting + Phase 8.5 off-shift suppression."""
    from datetime import timedelta
    days = 0
    cur = start_date
    any_whs = user.resourcing_working_hours.exists()
    while cur <= end_date:
        weekday = cur.weekday()
        # Skip if no working hours configured for this weekday (and the user
        # has any working hours at all — backwards-compat with v3.17.132)
        whs = user.resourcing_working_hours.filter(weekday=weekday, is_active=True)
        if any_whs and not whs.exists():
            cur += timedelta(days=1); continue
        # Skip holidays
        if Holiday.is_holiday(cur, organization=organization):
            cur += timedelta(days=1); continue
        # Skip approved leave
        if LeaveRequest.is_user_on_leave(user, cur):
            cur += timedelta(days=1); continue
        days += 1
        cur += timedelta(days=1)
    return days
