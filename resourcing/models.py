"""
Resource-management models — Phase 2.1.

Cross-cuts accounts (User), psa, and core, so this lives in its own app
to avoid circular imports and to keep migration ownership clean.

Three models:
  * UserSkill          — what techs are good at (proficiency tiers)
  * UserCertification  — credentials with optional expiry tracking
  * WorkingHours       — per-weekday availability windows
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
