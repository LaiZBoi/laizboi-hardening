"""
Scheduling forms
"""
from django import forms
from django.contrib.auth.models import User
from .models import ScheduledTask, TaskComment


class ScheduledTaskForm(forms.ModelForm):
    class Meta:
        model = ScheduledTask
        fields = [
            'title', 'description', 'priority', 'due_date',
            'recurrence', 'recurrence_interval_days', 'require_all_signoffs',
            'psa_ticket', 'alert_email', 'alert_sms', 'alert_before_hours',
        ]
        widgets = {
            'due_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        org = kwargs.pop('org', None)
        super().__init__(*args, **kwargs)
        if org:
            from integrations.models import PSATicket
            self.fields['psa_ticket'].queryset = PSATicket.objects.filter(
                organization=org
            ).order_by('-external_updated_at')
        else:
            from integrations.models import PSATicket
            self.fields['psa_ticket'].queryset = PSATicket.objects.none()
        self.fields['psa_ticket'].required = False
        self.fields['psa_ticket'].empty_label = '— No linked ticket —'
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'


class TaskSignOffForm(forms.Form):
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Notes (optional)',
        help_text='Optional notes about this sign-off.'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'


class TaskCommentForm(forms.ModelForm):
    class Meta:
        model = TaskComment
        fields = ['body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'


class TaskAssignUsersForm(forms.Form):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Assign To',
    )

    def __init__(self, *args, **kwargs):
        org = kwargs.pop('org', None)
        super().__init__(*args, **kwargs)
        if org:
            from accounts.models import Membership
            user_ids = Membership.objects.filter(
                organization=org,
                is_active=True
            ).values_list('user_id', flat=True)
            self.fields['users'].queryset = User.objects.filter(
                id__in=user_ids, is_active=True
            ).order_by('username')
        else:
            self.fields['users'].queryset = User.objects.filter(
                is_active=True
            ).order_by('username')
