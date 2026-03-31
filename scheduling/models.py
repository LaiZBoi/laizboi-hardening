"""
Scheduling models - Scheduled tasks with sign-off support
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from core.models import Organization, Tag, BaseModel
from core.utils import OrganizationManager


class ScheduledTask(BaseModel):
    """
    A scheduled recurring or one-time task that requires sign-off from assigned users.
    """
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    RECURRENCE_CHOICES = [
        ('none', 'None (one-time)'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('custom', 'Custom'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
        ('skipped', 'Skipped'),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='scheduled_tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')
    due_date = models.DateTimeField(null=True, blank=True)
    recurrence = models.CharField(max_length=20, choices=RECURRENCE_CHOICES, default='none')
    recurrence_interval_days = models.IntegerField(
        null=True, blank=True,
        help_text='Number of days between occurrences (used when recurrence=custom)'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    require_all_signoffs = models.BooleanField(
        default=False,
        help_text='Require all assigned users to sign off before marking complete'
    )
    is_template = models.BooleanField(default=False)
    parent_task = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='child_instances'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_tasks'
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='completed_tasks'
    )
    assigned_to = models.ManyToManyField(
        User, through='TaskAssignment', related_name='assigned_tasks', blank=True
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='scheduled_tasks')

    # PSA ticket linkage (optional)
    psa_ticket = models.ForeignKey(
        'integrations.PSATicket',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='scheduled_tasks',
        help_text='Linked PSA/helpdesk ticket (optional)',
    )

    # Alert preferences
    alert_email = models.BooleanField(default=True, help_text='Send email alerts for this task')
    alert_sms = models.BooleanField(default=False, help_text='Send SMS alerts for this task')
    alert_before_hours = models.PositiveIntegerField(
        default=24,
        help_text='Send an alert this many hours before the due date',
    )
    last_alert_sent_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationManager()

    class Meta:
        db_table = 'scheduling_tasks'
        ordering = ['due_date', 'priority', 'title']
        verbose_name = 'Scheduled Task'
        verbose_name_plural = 'Scheduled Tasks'

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        """True if past due date and not yet completed/cancelled."""
        if self.due_date and self.status in ('pending', 'in_progress'):
            return timezone.now() > self.due_date
        return False

    def get_next_due_date(self):
        """Calculate the next due date based on recurrence setting."""
        if self.recurrence == 'none' or not self.due_date:
            return None
        intervals = {
            'daily': 1,
            'weekly': 7,
            'biweekly': 14,
            'monthly': 30,
            'quarterly': 91,
        }
        if self.recurrence == 'custom':
            days = self.recurrence_interval_days
        else:
            days = intervals.get(self.recurrence)
        if days:
            return self.due_date + timedelta(days=days)
        return None

    def check_completion(self):
        """Check if the task's completion conditions are met and complete it if so."""
        assignments = self.task_assignments.all()
        if not assignments.exists():
            return

        if self.require_all_signoffs:
            completed = all(a.acknowledged for a in assignments)
        else:
            completed = any(a.acknowledged for a in assignments)

        if completed and self.status not in ('completed', 'cancelled'):
            self.status = 'completed'
            self.completed_at = timezone.now()
            self.save()
            if self.recurrence != 'none':
                self.spawn_next_occurrence()

    def spawn_next_occurrence(self):
        """Create the next task instance for recurring tasks."""
        next_due = self.get_next_due_date()
        if not next_due:
            return None

        new_task = ScheduledTask.objects.create(
            organization=self.organization,
            title=self.title,
            description=self.description,
            priority=self.priority,
            due_date=next_due,
            recurrence=self.recurrence,
            recurrence_interval_days=self.recurrence_interval_days,
            status='pending',
            require_all_signoffs=self.require_all_signoffs,
            is_template=False,
            parent_task=self.parent_task if self.parent_task else self,
            created_by=self.created_by,
            psa_ticket=self.psa_ticket,
            alert_email=self.alert_email,
            alert_sms=self.alert_sms,
            alert_before_hours=self.alert_before_hours,
        )

        # Copy tag M2M
        new_task.tags.set(self.tags.all())

        # Copy assignments
        for assignment in self.task_assignments.all():
            TaskAssignment.objects.create(
                task=new_task,
                user=assignment.user,
            )

        return new_task


class TaskAssignment(models.Model):
    """
    Links a user to a scheduled task and tracks their sign-off.
    """
    task = models.ForeignKey(ScheduledTask, on_delete=models.CASCADE, related_name='task_assignments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='task_assignments')
    acknowledged = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'scheduling_assignments'
        unique_together = ('task', 'user')
        verbose_name = 'Task Assignment'
        verbose_name_plural = 'Task Assignments'

    def __str__(self):
        return f"{self.user.username} -> {self.task.title}"

    def sign_off(self, notes=''):
        """Record acknowledgement and trigger completion check."""
        self.acknowledged = True
        self.acknowledged_at = timezone.now()
        if notes:
            self.notes = notes
        self.save()
        self.task.check_completion()


class TaskComment(models.Model):
    """
    Comment on a scheduled task.
    """
    task = models.ForeignKey(ScheduledTask, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='task_comments'
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'scheduling_comments'
        ordering = ['created_at']
        verbose_name = 'Task Comment'
        verbose_name_plural = 'Task Comments'

    def __str__(self):
        return f"Comment on {self.task.title} by {self.author}"
