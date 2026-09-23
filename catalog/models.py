from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator
from django.db import models
from django.urls import reverse


def validate_image_size(value):
    if value.size > 10 * 1024 * 1024:
        raise ValidationError('Choose an image smaller than 10 MB.')


def product_image_path(instance, filename):
    folder = 'tackle' if instance.product.item_type == Product.ItemType.TACKLE else 'rods'
    return f'{folder}/{instance.product_id}/{uuid4().hex}{Path(filename).suffix.lower()}'


class StoreSettings(models.Model):
    name = models.CharField(max_length=100, default='CaCaCa')
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('8.25'),
        validators=[MinValueValidator(0), MaxValueValidator(100)], help_text='Percentage, e.g. 8.25. Applied by checkout in the commerce milestone.')
    currency = models.CharField(max_length=3, default='USD', choices=[('USD', 'USD')])
    shipping_country = models.CharField(max_length=2, default='US', choices=[('US', 'United States')])
    contact_email = models.EmailField(blank=True)
    about_text = models.TextField(blank=True, help_text='Owner-approved builder story.')
    return_policy = models.TextField(blank=True)
    return_policy_published = models.BooleanField(default=False, help_text='Publish only after reviewing the exact terms.')
    shipping_rate = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('15.00'), validators=[MinValueValidator(0)])
    shipping_mode = models.CharField(max_length=12, default='per_order', choices=[('per_order', 'Per order'), ('per_rod', 'Per item')])
    shipping_configured = models.BooleanField(default=True, help_text='Owner-confirmed default: $15 per order. Zero means free shipping.')
    tax_shipping = models.BooleanField(default=False, help_text='Include shipping in the taxable amount when enabled.')
    checkout_enabled = models.BooleanField(default=False, help_text='Sandbox checkout only. Requires PostgreSQL, shipping settings, and PayPal sandbox credentials.')

    class Meta:
        verbose_name = 'store settings'
        verbose_name_plural = 'store settings'
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='single_store_settings'),
                       models.CheckConstraint(condition=models.Q(tax_rate__gte=0, tax_rate__lte=100), name='valid_tax_percentage')]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return 'CaCaCa store settings'


class Species(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'species'

    def __str__(self):
        return self.name


class DesignTheme(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):
    class ItemType(models.TextChoices):
        ROD = 'rod', 'Fishing rod'
        TACKLE = 'tackle', 'Tackle'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        AVAILABLE = 'available', 'Available'
        RESERVED = 'reserved', 'Reserved'
        MADE_TO_ORDER = 'made_to_order', 'Made to order'
        BUILDING = 'building', 'Building'
        READY = 'ready', 'Ready'
        SOLD = 'sold', 'Sold'
        SHIPPED = 'shipped', 'Shipped'
        ARCHIVED = 'archived', 'Archived'

    class BuildType(models.TextChoices):
        STOCK = 'stock', 'In stock'
        UNIQUE = 'unique', 'One of one'
        MADE_TO_ORDER = 'made_to_order', 'Made to order'
        CUSTOM = 'custom', 'Custom order'

    name = models.CharField(max_length=180)
    item_type = models.CharField(max_length=12, choices=ItemType.choices, default=ItemType.ROD, db_index=True)
    slug = models.SlugField(unique=True)
    rod_id = models.CharField(max_length=60, unique=True)
    serial_number = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    design_theme = models.ForeignKey(DesignTheme, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='rods', help_text='For example: US flag, birds, or another wrap design.')
    design_story = models.TextField('Wrap design details', blank=True,
        help_text='Describe the hand-wrapped artwork, colors, and details visible in the photos.')
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Required before this rod can be offered for sale. A public design preview may have no price.')
    build_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Optional private total cost to build this rod. Used for estimated business reporting.')
    quantity = models.PositiveIntegerField(default=1)
    reserved_quantity = models.PositiveIntegerField(default=0, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    build_type = models.CharField(max_length=20, choices=BuildType.choices, default=BuildType.UNIQUE)
    is_published = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)
    show_in_gallery = models.BooleanField(default=False)
    estimated_build_time = models.CharField(max_length=120, blank=True)
    shipping_estimate = models.CharField(max_length=160, blank=True)
    meta_description = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gt=0), name='positive_product_price'),
            models.CheckConstraint(condition=models.Q(is_published=False) |
                                   (models.Q(price__isnull=False) & models.Q(price__gt=0)),
                                   name='published_product_has_price'),
            models.CheckConstraint(condition=~models.Q(build_type='unique') | models.Q(quantity__lte=1), name='unique_rod_max_one'),
            models.CheckConstraint(condition=models.Q(reserved_quantity__lte=models.F('quantity')), name='reserved_stock_within_quantity'),
        ]

    def clean(self):
        if self.build_type == self.BuildType.UNIQUE and self.quantity > 1:
            raise ValidationError({'quantity': 'A one-of-one rod may have at most one unit.'})
        if self.is_published and self.price is None:
            raise ValidationError({'price': 'Enter a price before publishing this rod.'})

    @property
    def is_available(self):
        return (self.is_published and self.price is not None and
                self.status in [self.Status.AVAILABLE, self.Status.READY] and
                self.quantity > self.reserved_quantity and
                self.build_type in [self.BuildType.STOCK, self.BuildType.UNIQUE])

    @property
    def is_public_preview(self):
        return self.status == self.Status.DRAFT and self.show_in_gallery and not self.is_published

    @property
    def cover(self):
        return next(iter(self.images.all()), None)

    def get_absolute_url(self):
        route = 'tackle_product' if self.item_type == self.ItemType.TACKLE else 'product'
        return reverse(route, kwargs={'slug': self.slug})

    def __str__(self):
        return self.name


class RodSpecification(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='specification')
    builder_model = models.CharField(max_length=120, blank=True)
    length = models.CharField(max_length=60, blank=True)
    power = models.CharField(max_length=60, blank=True)
    action = models.CharField(max_length=60, blank=True)
    line_rating = models.CharField(max_length=100, blank=True)
    lure_rating = models.CharField(max_length=100, blank=True)
    blank_manufacturer = models.CharField(max_length=100, blank=True)
    blank_model = models.CharField(max_length=100, blank=True)
    blank_material = models.CharField(max_length=100, blank=True)
    guide_manufacturer = models.CharField(max_length=100, blank=True)
    guide_type = models.CharField(max_length=100, blank=True)
    reel_seat = models.CharField(max_length=100, blank=True)
    handle_material = models.CharField(max_length=100, blank=True)
    handle_style = models.CharField(max_length=100, blank=True)
    grip_configuration = models.CharField(max_length=100, blank=True)
    rear_grip_length = models.CharField(max_length=60, blank=True)
    foregrip_length = models.CharField(max_length=60, blank=True)
    rod_weight = models.CharField(max_length=60, blank=True)
    wrap_colors = models.CharField(max_length=160, blank=True)
    decorative_details = models.TextField(blank=True)
    builder_notes = models.TextField(blank=True)
    primary_fishing_style = models.CharField(max_length=100, blank=True)
    secondary_fishing_style = models.CharField(max_length=100, blank=True)
    plunking_focused = models.BooleanField(default=False)
    salmon_focused = models.BooleanField(default=False)
    primary_target_species = models.ForeignKey(Species, null=True, blank=True, on_delete=models.SET_NULL, related_name='primary_rods')
    additional_target_species = models.ManyToManyField(Species, blank=True, related_name='additional_rods')
    bank_shore_use = models.BooleanField(default=False)
    river_use = models.BooleanField(default=False)
    water_type = models.CharField(max_length=20, blank=True, choices=[('freshwater', 'Freshwater'), ('saltwater', 'Saltwater'), ('both', 'Freshwater / saltwater')])
    water_condition_notes = models.TextField(blank=True)
    recommended_line_type = models.CharField(max_length=100, blank=True)
    recommended_line_strength = models.CharField(max_length=100, blank=True)
    recommended_leader_range = models.CharField(max_length=100, blank=True)
    recommended_sinker_range = models.CharField(max_length=100, blank=True)
    recommended_reel_style = models.CharField(max_length=100, blank=True)
    recommended_presentation = models.CharField(max_length=160, blank=True)
    casting_distance_intent = models.CharField(max_length=160, blank=True)
    fighting_power_notes = models.TextField(blank=True)
    builder_usage_notes = models.TextField(blank=True)

    def display_fields(self):
        excluded = {'id', 'product', 'primary_target_species', 'plunking_focused', 'salmon_focused', 'bank_shore_use', 'river_use'}
        return [(f.verbose_name.capitalize(), getattr(self, f'get_{f.name}_display')() if f.choices else getattr(self, f.name))
                for f in self._meta.fields if f.name not in excluded and getattr(self, f.name)]

    def __str__(self):
        return f'{self.product.name} specifications'


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=product_image_path, validators=[validate_image_size,
        FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])])
    alt_text = models.CharField(max_length=180, help_text='Describe the item or detail shown.')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.alt_text


class CustomBuildRequest(models.Model):
    class Kind(models.TextChoices):
        ROD = 'rod', 'Rod question'
        QUESTION = 'question', 'General question'
        CUSTOM = 'custom', 'Special request'

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.CUSTOM)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name='customer_questions')
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    fishing_style = models.CharField(max_length=120, blank=True)
    target_species = models.CharField(max_length=160, blank=True)
    fishing_location = models.CharField(max_length=160, blank=True)
    preferred_length = models.CharField(max_length=60, blank=True)
    power_action = models.CharField(max_length=120, blank=True)
    line_weight_preferences = models.CharField(max_length=180, blank=True)
    reel_type = models.CharField(max_length=120, blank=True)
    handle_preferences = models.CharField(max_length=160, blank=True)
    color_preferences = models.CharField(max_length=160, blank=True)
    budget = models.CharField(max_length=100, blank=True)
    timeline = models.CharField(max_length=100, blank=True)
    notes = models.TextField(max_length=4000, blank=True)
    status = models.CharField(max_length=30, default='received', choices=[
        ('received', 'Request received'), ('review', 'Builder review'), ('contacted', 'Customer contacted'),
        ('confirmed', 'Design confirmed'), ('accepted', 'Build accepted'), ('closed', 'Closed'),
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} — {self.created_at:%b %d, %Y}' if self.created_at else self.name
