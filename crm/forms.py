"""
ModelForms for CRM. Bootstrap-friendly widgets so the same partials
work for create + edit views.
"""
from django import forms

from .models import Campaign, CommissionRule, Lead, Opportunity


_BS_CTRL = {'class': 'form-control'}
_BS_SELECT = {'class': 'form-select'}
_BS_CHECK = {'class': 'form-check-input'}


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = [
            'name', 'description', 'channel',
            'start_date', 'end_date', 'budget', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_BS_CTRL),
            'description': forms.Textarea(attrs={**_BS_CTRL, 'rows': 3}),
            'channel': forms.Select(attrs=_BS_SELECT),
            'start_date': forms.DateInput(attrs={**_BS_CTRL, 'type': 'date'}),
            'end_date': forms.DateInput(attrs={**_BS_CTRL, 'type': 'date'}),
            'budget': forms.NumberInput(attrs={**_BS_CTRL, 'step': '0.01', 'min': 0}),
            'is_active': forms.CheckboxInput(attrs=_BS_CHECK),
        }


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = [
            'company_name', 'contact_first_name', 'contact_last_name',
            'contact_email', 'contact_phone', 'contact_title',
            'website', 'industry', 'employee_count',
            'estimated_value', 'status', 'source', 'campaign',
            'assigned_to', 'notes',
        ]
        widgets = {
            'company_name': forms.TextInput(attrs=_BS_CTRL),
            'contact_first_name': forms.TextInput(attrs=_BS_CTRL),
            'contact_last_name': forms.TextInput(attrs=_BS_CTRL),
            'contact_email': forms.EmailInput(attrs=_BS_CTRL),
            'contact_phone': forms.TextInput(attrs=_BS_CTRL),
            'contact_title': forms.TextInput(attrs=_BS_CTRL),
            'website': forms.URLInput(attrs=_BS_CTRL),
            'industry': forms.TextInput(attrs=_BS_CTRL),
            'employee_count': forms.NumberInput(attrs={**_BS_CTRL, 'min': 0}),
            'estimated_value': forms.NumberInput(attrs={**_BS_CTRL, 'step': '0.01', 'min': 0}),
            'status': forms.Select(attrs=_BS_SELECT),
            'source': forms.TextInput(attrs=_BS_CTRL),
            'campaign': forms.Select(attrs=_BS_SELECT),
            'assigned_to': forms.Select(attrs=_BS_SELECT),
            'notes': forms.Textarea(attrs={**_BS_CTRL, 'rows': 3}),
        }


class CommissionRuleForm(forms.ModelForm):
    class Meta:
        model = CommissionRule
        fields = [
            'name', 'is_active', 'priority',
            'applies_to_user', 'min_value',
            'rate_pct', 'flat_amount', 'notes',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_BS_CTRL),
            'is_active': forms.CheckboxInput(attrs=_BS_CHECK),
            'priority': forms.NumberInput(attrs={**_BS_CTRL, 'min': 0}),
            'applies_to_user': forms.Select(attrs=_BS_SELECT),
            'min_value': forms.NumberInput(attrs={**_BS_CTRL, 'step': '0.01', 'min': 0}),
            'rate_pct': forms.NumberInput(attrs={**_BS_CTRL, 'step': '0.01', 'min': 0, 'max': 100}),
            'flat_amount': forms.NumberInput(attrs={**_BS_CTRL, 'step': '0.01', 'min': 0}),
            'notes': forms.Textarea(attrs={**_BS_CTRL, 'rows': 3}),
        }


class OpportunityForm(forms.ModelForm):
    class Meta:
        model = Opportunity
        fields = [
            'name', 'description',
            'client_org', 'stage',
            'estimated_value', 'probability_pct',
            'expected_close_date', 'actual_close_date',
            'lost_reason', 'campaign', 'assigned_to', 'notes',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_BS_CTRL),
            'description': forms.Textarea(attrs={**_BS_CTRL, 'rows': 3}),
            'client_org': forms.Select(attrs=_BS_SELECT),
            'stage': forms.Select(attrs=_BS_SELECT),
            'estimated_value': forms.NumberInput(attrs={**_BS_CTRL, 'step': '0.01', 'min': 0}),
            'probability_pct': forms.NumberInput(attrs={**_BS_CTRL, 'min': 0, 'max': 100}),
            'expected_close_date': forms.DateInput(attrs={**_BS_CTRL, 'type': 'date'}),
            'actual_close_date': forms.DateInput(attrs={**_BS_CTRL, 'type': 'date'}),
            'lost_reason': forms.TextInput(attrs=_BS_CTRL),
            'campaign': forms.Select(attrs=_BS_SELECT),
            'assigned_to': forms.Select(attrs=_BS_SELECT),
            'notes': forms.Textarea(attrs={**_BS_CTRL, 'rows': 3}),
        }
