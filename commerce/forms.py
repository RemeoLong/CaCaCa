from django import forms
from django.utils import timezone
from catalog.models import CustomBuildRequest
from .models import BusinessExpense, Order

US_STATES = [(code, name) for code, name in [
    ('AL','Alabama'),('AK','Alaska'),('AZ','Arizona'),('AR','Arkansas'),('CA','California'),
    ('CO','Colorado'),('CT','Connecticut'),('DE','Delaware'),('DC','District of Columbia'),
    ('FL','Florida'),('GA','Georgia'),('HI','Hawaii'),('ID','Idaho'),('IL','Illinois'),('IN','Indiana'),
    ('IA','Iowa'),('KS','Kansas'),('KY','Kentucky'),('LA','Louisiana'),('ME','Maine'),('MD','Maryland'),
    ('MA','Massachusetts'),('MI','Michigan'),('MN','Minnesota'),('MS','Mississippi'),('MO','Missouri'),
    ('MT','Montana'),('NE','Nebraska'),('NV','Nevada'),('NH','New Hampshire'),('NJ','New Jersey'),
    ('NM','New Mexico'),('NY','New York'),('NC','North Carolina'),('ND','North Dakota'),('OH','Ohio'),
    ('OK','Oklahoma'),('OR','Oregon'),('PA','Pennsylvania'),('RI','Rhode Island'),('SC','South Carolina'),
    ('SD','South Dakota'),('TN','Tennessee'),('TX','Texas'),('UT','Utah'),('VT','Vermont'),
    ('VA','Virginia'),('WA','Washington'),('WV','West Virginia'),('WI','Wisconsin'),('WY','Wyoming')]]


class CheckoutForm(forms.Form):
    name = forms.CharField(max_length=120, label='Full name', widget=forms.TextInput(attrs={'autocomplete':'shipping name'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'autocomplete':'email'}))
    phone = forms.CharField(max_length=40, required=False, widget=forms.TextInput(attrs={'autocomplete':'tel', 'type':'tel'}))
    address_line_1 = forms.CharField(max_length=200, label='Street address', widget=forms.TextInput(attrs={'autocomplete':'shipping address-line1'}))
    address_line_2 = forms.CharField(max_length=100, required=False, label='Apartment, suite, etc.', widget=forms.TextInput(attrs={'autocomplete':'shipping address-line2'}))
    admin_area_2 = forms.CharField(max_length=100, label='City', widget=forms.TextInput(attrs={'autocomplete':'shipping address-level2'}))
    admin_area_1 = forms.ChoiceField(choices=[('', 'Choose a state')] + US_STATES, label='State')
    postal_code = forms.RegexField(r'^\d{5}(-\d{4})?$', max_length=10, label='ZIP code', widget=forms.TextInput(attrs={'autocomplete':'shipping postal-code'}))
    country_code = forms.ChoiceField(choices=[('US','United States')], initial='US', label='Country')
    notes = forms.CharField(max_length=2000, required=False, label='Order notes', widget=forms.Textarea(attrs={'rows':3}))
    quote_token = forms.CharField(widget=forms.HiddenInput)
    checkout_key = forms.UUIDField(widget=forms.HiddenInput)

    def order_data(self):
        data = self.cleaned_data
        return {key: data[key] for key in ['name', 'email', 'phone', 'notes']} | {
            'address': {key: data[key] for key in ['address_line_1','address_line_2','admin_area_2','admin_area_1','postal_code','country_code']}}


class ShippingForm(forms.Form):
    carrier = forms.ChoiceField(choices=[('', 'Choose a carrier')] + list(Order.Carrier.choices))
    carrier_other = forms.CharField(max_length=80, required=False, label='Other carrier')
    tracking_number = forms.RegexField(r'^[A-Za-z0-9][A-Za-z0-9 .-]{2,98}[A-Za-z0-9]$', max_length=100)
    actual_shipping_cost = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False, label='Actual shipping cost (USD)')

    def clean(self):
        data = super().clean()
        if data.get('carrier') == 'other' and not data.get('carrier_other'):
            self.add_error('carrier_other', 'Enter the carrier name.')
        return data


class FulfillmentActionForm(forms.Form):
    action = forms.ChoiceField(choices=[
        ('pack', 'Mark packed'), ('ship', 'Mark shipped'),
        ('deliver', 'Mark delivered'), ('complete', 'Mark completed'),
    ])


class ReportFilterForm(forms.Form):
    start = forms.DateField(label='From', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(label='Through', widget=forms.DateInput(attrs={'type': 'date'}))

    def clean(self):
        data = super().clean()
        start, end = data.get('start'), data.get('end')
        if start and end and start > end:
            raise forms.ValidationError('The start date must be on or before the end date.')
        if start and end and (end - start).days > 1826:
            raise forms.ValidationError('Choose a reporting period of five years or less.')
        return data


class OwnerMessageStatusForm(forms.ModelForm):
    class Meta:
        model = CustomBuildRequest
        fields = ['status']
        labels = {'status': 'What is the status?'}


class BusinessExpenseForm(forms.ModelForm):
    class Meta:
        model = BusinessExpense
        fields = ['incurred_on', 'category', 'description', 'amount', 'reference']
        widgets = {'incurred_on': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['incurred_on'].initial = timezone.localdate()
        self.fields['amount'].label = 'Amount (USD)'
        self.fields['reference'].help_text = 'Optional receipt, invoice, or transaction reference.'
