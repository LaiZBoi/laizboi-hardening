"""
ModelForms for the resourcing app. Bootstrap-ready widgets so the same
partials work for add + edit.
"""
from django import forms

from .models import UserSkill, UserCertification, WorkingHours


_BS_CTRL = {'class': 'form-control'}
_BS_SELECT = {'class': 'form-select'}
_BS_CHECK = {'class': 'form-check-input'}


class UserSkillForm(forms.ModelForm):
    class Meta:
        model = UserSkill
        fields = ['name', 'proficiency', 'years_experience', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={**_BS_CTRL, 'placeholder': 'e.g. Active Directory, Azure, Networking'}),
            'proficiency': forms.Select(attrs=_BS_SELECT),
            'years_experience': forms.NumberInput(attrs={**_BS_CTRL, 'min': 0, 'max': 60}),
            'notes': forms.Textarea(attrs={**_BS_CTRL, 'rows': 2}),
        }


class UserCertificationForm(forms.ModelForm):
    class Meta:
        model = UserCertification
        fields = [
            'name', 'issuer', 'credential_id',
            'issued_at', 'expires_at',
            'verification_url', 'attachment',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_BS_CTRL),
            'issuer': forms.TextInput(attrs=_BS_CTRL),
            'credential_id': forms.TextInput(attrs=_BS_CTRL),
            'issued_at': forms.DateInput(attrs={**_BS_CTRL, 'type': 'date'}),
            'expires_at': forms.DateInput(attrs={**_BS_CTRL, 'type': 'date'}),
            'verification_url': forms.URLInput(attrs=_BS_CTRL),
            'attachment': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


class WorkingHoursForm(forms.ModelForm):
    class Meta:
        model = WorkingHours
        fields = ['weekday', 'start_time', 'end_time', 'is_active', 'notes']
        widgets = {
            'weekday': forms.Select(attrs=_BS_SELECT),
            'start_time': forms.TimeInput(attrs={**_BS_CTRL, 'type': 'time'}),
            'end_time': forms.TimeInput(attrs={**_BS_CTRL, 'type': 'time'}),
            'is_active': forms.CheckboxInput(attrs=_BS_CHECK),
            'notes': forms.TextInput(attrs={**_BS_CTRL, 'placeholder': 'Optional — e.g. "afternoon shift"'}),
        }
