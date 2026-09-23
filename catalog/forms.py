from pathlib import Path
from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import connection
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from .models import CustomBuildRequest, DesignTheme, Product, ProductImage, RodSpecification, StoreSettings
from commerce import paypal


LISTING_STATES = [
    ('draft', 'Private draft — finish later'),
    ('preview', 'Public preview — show photos, not for sale'),
    ('available', 'In the shop — ready to sell'),
    ('sold', 'Sold — keep in the gallery'),
    ('archived', 'Archived — hide from the website'),
]


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput(attrs={'accept': 'image/jpeg,image/png,image/webp'})

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        if len(files) > 12:
            raise ValidationError('Choose no more than 12 photos at a time.')
        cleaned = []
        extension_validator = FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])
        for upload in files:
            extension_validator(upload)
            if upload.size > 10 * 1024 * 1024:
                raise ValidationError(f'{upload.name} is larger than 10 MB.')
            cleaned.append(super().clean(upload, initial))
        return cleaned


def listing_state_for(product):
    if product.status in [Product.Status.SOLD, Product.Status.SHIPPED]:
        return 'sold'
    if product.status == Product.Status.ARCHIVED:
        return 'archived'
    if product.is_published and product.status in [Product.Status.AVAILABLE, Product.Status.READY]:
        return 'available'
    if product.status == Product.Status.DRAFT and product.show_in_gallery:
        return 'preview'
    return 'draft'


def apply_listing_state(product, state):
    if state == 'available':
        product.status = Product.Status.AVAILABLE
        product.is_published = True
        product.show_in_gallery = True
    elif state == 'sold':
        if product.status != Product.Status.SHIPPED:
            product.status = Product.Status.SOLD
        product.quantity = 0
        product.is_published = True
        product.show_in_gallery = True
        product.featured = False
    elif state == 'archived':
        product.status = Product.Status.ARCHIVED
        product.is_published = False
        product.show_in_gallery = False
        product.featured = False
    elif state == 'preview':
        product.status = Product.Status.DRAFT
        product.is_published = False
        product.show_in_gallery = True
        product.featured = False
    else:
        product.status = Product.Status.DRAFT
        product.is_published = False
        product.show_in_gallery = False
        product.featured = False


def listing_gaps(product):
    """Owner-facing facts to complete before a finished rod goes in the shop."""
    gaps = []
    if product.price is None:
        gaps.append('Selling price')
    if not product.description.strip():
        gaps.append('Description')
    if not product.images.exists():
        gaps.append('Rod photo')
    spec = getattr(product, 'specification', None)
    for field, label in [('length', 'Rod length'), ('power', 'Power'), ('action', 'Action')]:
        if not spec or not getattr(spec, field).strip():
            gaps.append(label)
    return gaps


class OwnerRodForm(forms.Form):
    name = forms.CharField(max_length=180, label='Rod name',
        widget=forms.TextInput(attrs={'placeholder': 'Example: Red, White & Blue Diamond Wrap'}))
    design_name = forms.CharField(max_length=80, required=False, label='Wrap design',
        widget=forms.TextInput(attrs={'placeholder': 'Example: US flag, birds, blue diamond'}))
    price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01, required=False,
        label='Selling price (USD)')
    quantity = forms.IntegerField(min_value=0, max_value=1, initial=1, label='Number available',
        help_text='Use 1 while this one-of-one rod is available. Sold rods are changed to 0 automatically.')
    listing_state = forms.ChoiceField(choices=LISTING_STATES, initial='draft', label='Where should it appear?')
    description = forms.CharField(required=False, label='Description',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Describe the colors, pattern, and special details customers can see.'}))
    length = forms.CharField(max_length=60, required=False, label='Rod length',
        widget=forms.TextInput(attrs={'placeholder': 'Example: 10 ft 6 in'}))
    power = forms.CharField(max_length=60, required=False, label='Power',
        widget=forms.TextInput(attrs={'placeholder': 'Example: Medium Heavy'}))
    action = forms.CharField(max_length=60, required=False, label='Action',
        widget=forms.TextInput(attrs={'placeholder': 'Example: Moderate Fast'}))
    line_rating = forms.CharField(max_length=100, required=False, label='Line rating',
        widget=forms.TextInput(attrs={'placeholder': 'Only if confirmed by the builder'}))
    primary_fishing_style = forms.CharField(max_length=100, required=False, label='Fishing style',
        widget=forms.TextInput(attrs={'placeholder': 'Example: Plunking'}))
    featured = forms.BooleanField(required=False, label='Feature this rod on the homepage')
    photos = MultipleImageField(required=False, label='Rod photos',
        help_text='Choose several photos together. The first photo becomes the cover.')
    camera_photo = forms.ImageField(required=False, label='Take a photo',
        widget=forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp', 'capture': 'environment'}),
        help_text='On a phone, open the camera to photograph this rod. You can also choose gallery photos below.')

    def clean_camera_photo(self):
        upload = self.cleaned_data.get('camera_photo')
        if upload:
            FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])(upload)
            if upload.size > 10 * 1024 * 1024:
                raise ValidationError('Choose a camera photo smaller than 10 MB.')
        return upload

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance and not args and 'data' not in kwargs:
            spec = getattr(instance, 'specification', None)
            kwargs['initial'] = {
                'name': instance.name,
                'design_name': instance.design_theme.name if instance.design_theme else '',
                'price': instance.price,
                'quantity': instance.quantity,
                'listing_state': listing_state_for(instance),
                'description': instance.description,
                'featured': instance.featured,
                'length': spec.length if spec else '',
                'power': spec.power if spec else '',
                'action': spec.action if spec else '',
                'line_rating': spec.line_rating if spec else '',
                'primary_fishing_style': spec.primary_fishing_style if spec else '',
            }
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if len(data.get('photos') or []) + bool(data.get('camera_photo')) > 12:
            self.add_error('photos', 'Choose no more than 12 photos at a time, including the camera photo.')
        state = data.get('listing_state')
        if state in ['available', 'sold'] and data.get('price') is None:
            self.add_error('price', 'Enter the selling price before putting this rod in the shop or gallery.')
        if state == 'available' and data.get('quantity') != 1:
            self.add_error('quantity', 'An available one-of-one rod must have 1 available.')
        if self.instance and data.get('quantity') is not None and data['quantity'] < self.instance.reserved_quantity:
            self.add_error('quantity', 'This rod is reserved in an order. Finish or cancel the order before lowering its stock.')
        if self.instance and self.instance.reserved_quantity and state in ['sold', 'archived', 'draft', 'preview']:
            self.add_error('listing_state', 'This rod is reserved in an order. Finish or cancel the order first.')
        removed = ({value for value in self.data.getlist('remove_photos') if value.isdigit()}
                   if self.is_bound and hasattr(self.data, 'getlist') else set())
        remaining = (self.instance.images.exclude(pk__in=removed).count() if self.instance else 0)
        if state == 'available' and not (remaining or data.get('photos') or data.get('camera_photo')):
            self.add_error('photos', 'Add at least one photo before putting this rod in the shop.')
        if state == 'preview' and not (remaining or data.get('photos') or data.get('camera_photo')):
            self.add_error('photos', 'Add at least one photo before showing a public preview.')
        if state == 'available' and not (data.get('description') or '').strip():
            self.add_error('description', 'Add a description before putting this rod in the shop.')
        if state == 'preview' and not (data.get('description') or '').strip():
            self.add_error('description', 'Add a description before showing a public preview.')
        if state == 'available':
            for field in ['length', 'power', 'action']:
                if not (data.get(field) or '').strip():
                    self.add_error(field, 'Enter this rod detail before putting it in the shop.')
        return data

    def _unique_slug(self, name):
        base = slugify(name)[:45] or 'finished-rod'
        slug = base
        number = 2
        query = Product.objects.exclude(pk=self.instance.pk) if self.instance else Product.objects.all()
        while query.filter(slug=slug).exists():
            slug = f'{base}-{number}'
            number += 1
        return slug

    def save(self):
        data = self.cleaned_data
        product = self.instance or Product(
            rod_id=f"CAC-{timezone.localdate():%Y%m}-{uuid4().hex[:6].upper()}",
            build_type=Product.BuildType.UNIQUE,
        )
        product.name = data['name']
        if not product.slug:
            product.slug = self._unique_slug(data['name'])
        product.design_theme = (DesignTheme.objects.get_or_create(name=data['design_name'].strip())[0]
                                if data['design_name'].strip() else None)
        product.price = data['price']
        product.quantity = data['quantity']
        product.description = data['description']
        product.featured = data['featured']
        product.meta_description = data['description'][:160]
        apply_listing_state(product, data['listing_state'])
        product.full_clean()
        product.save()
        spec, _ = RodSpecification.objects.get_or_create(product=product)
        for field in ['length', 'power', 'action', 'line_rating', 'primary_fishing_style']:
            setattr(spec, field, data[field].strip())
        spec.save()
        last_position = product.images.aggregate(value=Max('position'))['value']
        start = 0 if last_position is None else last_position + 1
        uploads = ([data['camera_photo']] if data.get('camera_photo') else []) + data['photos']
        for offset, upload in enumerate(uploads):
            photo = ProductImage(product=product, alt_text=f'{product.name} — photo {start + offset + 1}',
                                 position=start + offset)
            photo.image.save(Path(upload.name).name, upload, save=True)
        self.instance = product
        return product


class OwnerTackleForm(forms.Form):
    STATES = [('draft', 'Private draft'), ('available', 'In the shop'),
              ('sold', 'Sold out'), ('archived', 'Archived')]
    name = forms.CharField(max_length=180, label='Item name')
    price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01, required=False,
                               label='Price per item (USD)')
    quantity = forms.IntegerField(min_value=0, max_value=999, initial=1, label='Number available')
    listing_state = forms.ChoiceField(choices=STATES, initial='draft', label='Where should it appear?')
    description = forms.CharField(required=False, label='What is included?',
                                  widget=forms.Textarea(attrs={'rows': 4}))
    photos = MultipleImageField(required=False, label='Item photos',
                                help_text='Choose several photos together. The first is the cover.')
    camera_photo = forms.ImageField(required=False, label='Take a photo',
        widget=forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp', 'capture': 'environment'}))

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance and not args and 'data' not in kwargs:
            kwargs['initial'] = {'name': instance.name, 'price': instance.price,
                'quantity': instance.quantity, 'listing_state': listing_state_for(instance),
                'description': instance.description}
        super().__init__(*args, **kwargs)

    def clean_camera_photo(self):
        upload = self.cleaned_data.get('camera_photo')
        if upload:
            FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])(upload)
            if upload.size > 10 * 1024 * 1024:
                raise ValidationError('Choose a camera photo smaller than 10 MB.')
        return upload

    def clean(self):
        data = super().clean()
        if len(data.get('photos') or []) + bool(data.get('camera_photo')) > 12:
            self.add_error('photos', 'Choose no more than 12 photos at a time.')
        state = data.get('listing_state')
        if state in ['available', 'sold'] and data.get('price') is None:
            self.add_error('price', 'Enter a price before showing this item.')
        if state == 'available' and not data.get('quantity'):
            self.add_error('quantity', 'Enter at least one item to sell.')
        if self.instance and data.get('quantity') is not None and data['quantity'] < self.instance.reserved_quantity:
            self.add_error('quantity', 'This item is reserved in an order. Finish or cancel the order first.')
        if self.instance and self.instance.reserved_quantity and state != 'available':
            self.add_error('listing_state', 'This item is reserved in an order. Finish or cancel the order first.')
        removed = ({value for value in self.data.getlist('remove_photos') if value.isdigit()}
                   if self.is_bound and hasattr(self.data, 'getlist') else set())
        remaining = self.instance.images.exclude(pk__in=removed).count() if self.instance else 0
        if state == 'available' and not (remaining or data.get('photos') or data.get('camera_photo')):
            self.add_error('photos', 'Add a photo before putting this item in the shop.')
        if state == 'available' and not (data.get('description') or '').strip():
            self.add_error('description', 'Describe what the customer will receive.')
        return data

    def save(self):
        data = self.cleaned_data
        product = self.instance or Product(
            item_type=Product.ItemType.TACKLE,
            rod_id=f"CAC-TAC-{timezone.localdate():%Y%m}-{uuid4().hex[:5].upper()}",
            build_type=Product.BuildType.STOCK)
        product.name = data['name']
        if not product.slug:
            base = slugify(data['name'])[:45] or 'fishing-tackle'
            slug, number = base, 2
            while Product.objects.filter(slug=slug).exclude(pk=product.pk).exists():
                slug, number = f'{base}-{number}', number + 1
            product.slug = slug
        product.price = data['price']
        product.quantity = data['quantity']
        product.description = data['description']
        product.meta_description = data['description'][:160]
        apply_listing_state(product, data['listing_state'])
        product.full_clean()
        product.save()
        last_position = product.images.aggregate(value=Max('position'))['value']
        start = 0 if last_position is None else last_position + 1
        uploads = ([data['camera_photo']] if data.get('camera_photo') else []) + data['photos']
        for offset, upload in enumerate(uploads):
            photo = ProductImage(product=product, alt_text=f'{product.name} — photo {start + offset + 1}',
                                 position=start + offset)
            photo.image.save(Path(upload.name).name, upload, save=True)
        self.instance = product
        return product


class OwnerInventoryForm(forms.Form):
    price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01, required=False,
        label='Price')
    quantity = forms.IntegerField(min_value=0, max_value=1, label='Available')
    listing_state = forms.ChoiceField(choices=LISTING_STATES, label='Status')

    def __init__(self, *args, instance, **kwargs):
        self.instance = instance
        if not args and 'data' not in kwargs:
            kwargs['initial'] = {'price': instance.price, 'quantity': instance.quantity,
                                 'listing_state': listing_state_for(instance)}
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        state = data.get('listing_state')
        if state in ['available', 'sold'] and data.get('price') is None:
            self.add_error('price', 'Enter a price first.')
        if state == 'available' and data.get('quantity') != 1:
            self.add_error('quantity', 'Use 1 for a rod that is in the shop.')
        if data.get('quantity') is not None and data['quantity'] < self.instance.reserved_quantity:
            self.add_error('quantity', 'This rod is reserved in an order. Finish or cancel the order first.')
        if self.instance.reserved_quantity and state in ['sold', 'archived', 'draft', 'preview']:
            self.add_error('listing_state', 'This rod is reserved in an order. Finish or cancel the order first.')
        if state == 'preview' and not self.instance.images.exists():
            self.add_error('listing_state', 'Add a photo before showing a public preview.')
        if state == 'preview' and not self.instance.description.strip():
            self.add_error('listing_state', 'Add a description before showing a public preview.')
        if state == 'available' and not self.instance.images.exists():
            self.add_error('listing_state', 'Add a photo to this rod before putting it in the shop.')
        if state == 'available' and not self.instance.description.strip():
            self.add_error('listing_state', 'Add a description to this rod before putting it in the shop.')
        if state == 'available':
            spec = getattr(self.instance, 'specification', None)
            if not spec or not all(getattr(spec, field).strip() for field in ['length', 'power', 'action']):
                self.add_error('listing_state', 'Add rod length, power, and action in Edit photos & details first.')
        return data

    def save(self):
        product = self.instance
        product.price = self.cleaned_data['price']
        product.quantity = self.cleaned_data['quantity']
        apply_listing_state(product, self.cleaned_data['listing_state'])
        product.full_clean()
        product.save()
        return product


class OwnerStoreForm(forms.ModelForm):
    class Meta:
        model = StoreSettings
        fields = ['shipping_rate', 'shipping_mode', 'tax_rate', 'contact_email', 'checkout_enabled']
        labels = {
            'shipping_rate': 'Shipping charge (USD)',
            'shipping_mode': 'How shipping is charged',
            'tax_rate': 'Sales tax percentage',
            'contact_email': 'Email for new-order alerts',
            'checkout_enabled': 'Open PayPal sandbox checkout for testing',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['shipping_rate'].help_text = 'The current charge is shown in the field above.'
        self.fields['tax_rate'].help_text = 'Enter a percentage, such as 8.25.'
        self.fields['contact_email'].help_text = 'Order notices go here once email delivery is configured.'
        self.fields['checkout_enabled'].help_text = 'Test payments only. This cannot be turned on until sandbox credentials and shipping are configured.'

    def clean(self):
        data = super().clean()
        if data.get('checkout_enabled'):
            if connection.vendor != 'postgresql' or not self.instance.shipping_configured:
                self.add_error('checkout_enabled', 'Database and shipping setup must be complete before testing checkout.')
            if not paypal.configured():
                self.add_error('checkout_enabled', 'PayPal sandbox credentials must be configured before testing checkout.')
        return data

class CustomBuildForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = CustomBuildRequest
        exclude = ['kind', 'product', 'status', 'created_at']
        labels = {
            'fishing_location': 'River or fishing location (optional)',
            'power_action': 'Preferred power / action',
            'line_weight_preferences': 'Line, leader, or sinker preferences',
            'budget': 'Budget range (USD)', 'timeline': 'Desired timeline',
            'notes': 'Anything else you want the builder to know?',
        }
        widgets = {'notes': forms.Textarea(attrs={'rows': 4}),
                   'email': forms.EmailInput(attrs={'autocomplete': 'email'}),
                   'name': forms.TextInput(attrs={'autocomplete': 'name'}),
                   'phone': forms.TextInput(attrs={'autocomplete': 'tel', 'type': 'tel'})}

    def clean_website(self):
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('Please leave this field empty.')
        return ''


class RodInquiryForm(CustomBuildForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['notes'].required = True

    class Meta(CustomBuildForm.Meta):
        fields = ['name', 'email', 'notes']
        exclude = None
        labels = {'notes': 'Your question'}
