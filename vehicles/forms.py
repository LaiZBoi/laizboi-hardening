"""
Forms for Service Vehicles
"""
from django import forms
from django.contrib.auth import get_user_model
from .models import (
    ServiceVehicle, VehicleInventoryItem, VehicleDamageReport,
    VehicleMaintenanceRecord, VehicleFuelLog, VehicleAssignment,
    VehicleServiceSchedule, VehicleServiceAlert, VehicleServiceProvider,
    ShopInventoryItem, VehicleReceipt
)

User = get_user_model()


class ServiceVehicleForm(forms.ModelForm):
    """Form for creating/editing vehicles"""

    class Meta:
        model = ServiceVehicle
        fields = [
            'name', 'vehicle_type', 'diagram_type', 'make', 'model', 'year', 'color',
            'vin', 'license_plate', 'qr_code', 'status', 'condition', 'current_mileage',
            'insurance_provider', 'insurance_policy_number', 'insurance_expires_at', 'insurance_premium',
            'registration_expires_at', 'latitude', 'longitude',
            'purchase_date', 'purchase_price', 'assigned_to', 'notes'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'vehicle_type': forms.Select(attrs={'class': 'form-select'}),
            'diagram_type': forms.Select(attrs={'class': 'form-select'}),
            'make': forms.TextInput(attrs={'class': 'form-control'}),
            'model': forms.TextInput(attrs={'class': 'form-control'}),
            'year': forms.NumberInput(attrs={'class': 'form-control'}),
            'color': forms.TextInput(attrs={'class': 'form-control'}),
            'vin': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '17-character VIN'}),
            'license_plate': forms.TextInput(attrs={'class': 'form-control'}),
            'qr_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Scan or enter QR code'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'condition': forms.Select(attrs={'class': 'form-select'}),
            'current_mileage': forms.NumberInput(attrs={'class': 'form-control'}),
            'insurance_provider': forms.TextInput(attrs={'class': 'form-control'}),
            'insurance_policy_number': forms.TextInput(attrs={'class': 'form-control'}),
            'insurance_expires_at': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'insurance_premium': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'registration_expires_at': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'placeholder': 'e.g., 40.712776'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'placeholder': 'e.g., -74.005974'}),
            'purchase_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'assigned_to': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # All users can be assigned vehicles
        self.fields['assigned_to'].queryset = User.objects.filter(
            is_active=True
        ).distinct().order_by('first_name', 'last_name')


class VehicleInventoryItemForm(forms.ModelForm):
    """Form for managing vehicle inventory"""

    min_quantity = forms.IntegerField(
        required=False, initial=0, min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text="Minimum quantity alert threshold",
    )
    reorder_quantity = forms.IntegerField(
        required=False, initial=0, min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'How many to order when restocking'}),
        help_text="How many to order when restocking",
    )

    class Meta:
        model = VehicleInventoryItem
        fields = [
            'name', 'category', 'quantity', 'unit', 'min_quantity',
            'reorder_quantity', 'unit_cost', 'description', 'location_in_vehicle',
            'qr_code', 'reorder_link'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Cables, Tools, Hardware'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ea, ft, box, etc.'}),
            'unit_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'location_in_vehicle': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Toolbox, Rear compartment'}),
            'qr_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Auto-generated on save (or enter custom)'}),
            'reorder_link': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://amazon.com/...'}),
        }

    def clean_min_quantity(self):
        return self.cleaned_data.get('min_quantity') or 0

    def clean_reorder_quantity(self):
        return self.cleaned_data.get('reorder_quantity') or 0


class ShopInventoryItemForm(forms.ModelForm):
    """Form for managing shop/warehouse inventory"""

    min_quantity = forms.IntegerField(
        required=False, initial=0, min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text="Minimum quantity alert threshold",
    )
    reorder_quantity = forms.IntegerField(
        required=False, initial=0, min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'How many to order when restocking'}),
        help_text="How many to order when restocking",
    )

    class Meta:
        model = ShopInventoryItem
        fields = [
            'name', 'category', 'quantity', 'unit', 'min_quantity',
            'reorder_quantity', 'unit_cost', 'description', 'location_in_shop',
            'qr_code', 'reorder_link'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Cables, Tools, Hardware'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ea, ft, box, etc.'}),
            'unit_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'location_in_shop': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Shelf A3, Storage room'}),
            'qr_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Auto-generated on save (or enter custom)'}),
            'reorder_link': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://amazon.com/...'}),
        }

    def clean_min_quantity(self):
        return self.cleaned_data.get('min_quantity') or 0

    def clean_reorder_quantity(self):
        return self.cleaned_data.get('reorder_quantity') or 0


class VehicleDamageReportForm(forms.ModelForm):
    """Form for reporting vehicle damage"""

    class Meta:
        model = VehicleDamageReport
        fields = [
            'incident_date', 'reported_by', 'description', 'severity', 'repair_status',
            'damage_location', 'estimated_cost', 'actual_cost',
            'insurance_claim_number', 'insurance_payout',
            'repair_date', 'repair_shop', 'repair_notes',
            'condition_before', 'condition_after'
        ]
        widgets = {
            'incident_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reported_by': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'severity': forms.Select(attrs={'class': 'form-select'}),
            'repair_status': forms.Select(attrs={'class': 'form-select'}),
            'damage_location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Front bumper, Driver door'}),
            'estimated_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'actual_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'insurance_claim_number': forms.TextInput(attrs={'class': 'form-control'}),
            'insurance_payout': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'repair_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'repair_shop': forms.TextInput(attrs={'class': 'form-control'}),
            'repair_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'condition_before': forms.TextInput(attrs={'class': 'form-control'}),
            'condition_after': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # All users can report damage
        self.fields['reported_by'].queryset = User.objects.filter(
            is_active=True
        ).distinct().order_by('first_name', 'last_name')


class VehicleMaintenanceRecordForm(forms.ModelForm):
    """Form for logging maintenance"""

    class Meta:
        model = VehicleMaintenanceRecord
        fields = [
            'maintenance_type', 'description', 'service_date', 'mileage_at_service',
            'performed_by', 'labor_cost', 'parts_cost',
            'is_scheduled', 'next_due_mileage', 'next_due_date', 'notes'
        ]
        widgets = {
            'maintenance_type': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'service_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'mileage_at_service': forms.NumberInput(attrs={'class': 'form-control'}),
            'performed_by': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Mechanic or service center'}),
            'labor_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'parts_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'is_scheduled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'next_due_mileage': forms.NumberInput(attrs={'class': 'form-control'}),
            'next_due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class VehicleFuelLogForm(forms.ModelForm):
    """Form for logging fuel purchases"""

    class Meta:
        model = VehicleFuelLog
        fields = [
            'date', 'mileage', 'gallons', 'cost_per_gallon',
            'station', 'notes'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'mileage': forms.NumberInput(attrs={'class': 'form-control'}),
            'gallons': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cost_per_gallon': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'station': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Gas station name/location'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class VehicleAssignmentForm(forms.ModelForm):
    """Form for assigning vehicles to users"""

    class Meta:
        model = VehicleAssignment
        fields = ['user', 'start_date', 'starting_mileage', 'notes']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'starting_mileage': forms.NumberInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, vehicle=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle

        # All users can be assigned vehicles
        self.fields['user'].queryset = User.objects.filter(
            is_active=True
        ).distinct().order_by('first_name', 'last_name')

    def clean(self):
        cleaned_data = super().clean()

        # Check for existing active assignment
        if self.vehicle:
            active_assignment = self.vehicle.assignments.filter(end_date__isnull=True).first()
            if active_assignment and (not self.instance or self.instance.pk != active_assignment.pk):
                raise forms.ValidationError(
                    f'This vehicle is already assigned to {active_assignment.user.get_full_name()}. '
                    f'Please end the current assignment first.'
                )

        return cleaned_data


class VehicleServiceScheduleForm(forms.ModelForm):
    class Meta:
        model = VehicleServiceSchedule
        fields = [
            'name', 'service_type', 'interval_miles', 'interval_days',
            'warning_miles', 'warning_days',
            'last_service_mileage', 'last_service_date', 'is_active', 'notes'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Oil Change, Tire Rotation'}),
            'service_type': forms.Select(attrs={'class': 'form-select'}),
            'interval_miles': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 5000'}),
            'interval_days': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 90'}),
            'warning_miles': forms.NumberInput(attrs={'class': 'form-control'}),
            'warning_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'last_service_mileage': forms.NumberInput(attrs={'class': 'form-control'}),
            'last_service_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('interval_miles') and not cleaned_data.get('interval_days'):
            raise forms.ValidationError("Set at least one interval: miles or days.")
        return cleaned_data


class VehicleServiceAlertAcknowledgeForm(forms.Form):
    acknowledgement_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional notes…'}),
        label="Notes"
    )
    record_service_done = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_record_service'}),
        label="Record service as completed now"
    )
    service_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="Service Date"
    )
    service_mileage = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label="Mileage at Service"
    )
    performed_by = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Mechanic or shop name'}),
        label="Performed By"
    )
    service_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        label="Service Notes"
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('record_service_done'):
            if not cleaned_data.get('service_date'):
                self.add_error('service_date', 'Required when recording service.')
            if cleaned_data.get('service_mileage') is None:
                self.add_error('service_mileage', 'Required when recording service.')
        return cleaned_data


class VehicleServiceProviderForm(forms.ModelForm):
    class Meta:
        model = VehicleServiceProvider
        fields = [
            'name', 'contact_name', 'phone', 'email', 'address',
            'website', 'service_types', 'is_preferred', 'notes'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Shop or service center name'}),
            'contact_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Primary contact person'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '(555) 555-5555'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'website': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://'}),
            'service_types': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Oil change, Tires, Brakes, State inspection'}),
            'is_preferred': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class VehicleReceiptForm(forms.ModelForm):
    """Form for creating/editing vehicle receipts"""

    # Image upload field (not a model field — handled separately in view)
    receipt_image = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': 'image/*',
            'capture': 'environment',  # mobile: prefer rear camera
        }),
        label='Receipt Image (photo or scan)',
    )

    class Meta:
        model = VehicleReceipt
        fields = [
            'receipt_date', 'vendor', 'category', 'amount', 'tax_amount',
            'odometer', 'description', 'notes',
        ]
        widgets = {
            'receipt_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'vendor': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Store or business name'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
            'tax_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
            'odometer': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Optional mileage reading'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Items purchased'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Any additional notes'}),
        }
