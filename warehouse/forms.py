from django import forms
from django.utils import timezone
from .models import WarehouseOperation, Catalog


class WarehouseOperationForm(forms.Form):
    """
    Flat form — all fields as simple CharField/DateField/etc.
    FK resolution (customer/shipper/carrier/bundle_type) is done in the view.
    This avoids ModelChoiceField validation issues with combobox hidden inputs.
    """
    date             = forms.DateField(required=True)
    customer_id      = forms.IntegerField(required=False)   # FK id from combobox
    customer_text    = forms.CharField(required=False)      # manual text fallback
    operation_type   = forms.ChoiceField(choices=[('ENTRY','Entry'),('EXIT','Exit')])
    entry_dispatched = forms.CharField(required=False)
    shipper_id       = forms.IntegerField(required=False)
    shipper_text     = forms.CharField(required=False)
    invoice          = forms.CharField(required=False)
    po_order         = forms.CharField(required=False)
    seal             = forms.CharField(required=False)
    carrier_id       = forms.IntegerField(required=False)
    carrier_text     = forms.CharField(required=False)
    pro              = forms.CharField(required=False)
    trailer          = forms.CharField(required=False)
    bundle_type_id   = forms.IntegerField(required=False)
    bundle_type_text = forms.CharField(required=False)
    bundle_qty       = forms.IntegerField(required=False)
    weight_lbs       = forms.DecimalField(required=False, max_digits=10, decimal_places=2)
    weight_kgs       = forms.DecimalField(required=False, max_digits=10, decimal_places=2)
    description      = forms.CharField(required=False)
    note             = forms.CharField(required=False)
    damage           = forms.BooleanField(required=False)
    damage_description = forms.CharField(required=False)

    def clean(self):
        cleaned = super().clean()
        cid   = cleaned.get('customer_id')
        ctext = cleaned.get('customer_text', '').strip()
        if not cid and not ctext:
            self.add_error(None, 'Customer is required.')
        if not cleaned.get('operation_type'):
            self.add_error('operation_type', 'Type of Operation is required.')
        return cleaned


class CatalogForm(forms.ModelForm):
    contact_email = forms.CharField(required=False)

    class Meta:
        model = Catalog
        fields = ['category', 'name', 'contact_email', 'phone', 'address', 'notes']


class EmailReportForm(forms.Form):
    recipient_email = forms.EmailField()
    subject         = forms.CharField()
    message         = forms.CharField(required=False)
