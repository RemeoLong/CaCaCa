from django.contrib import admin
from django.utils.html import format_html
from .models import Product, ProductImage, RodSpecification, Species, StoreSettings, CustomBuildRequest, DesignTheme

admin.site.site_header = 'CaCaCa Workshop'
admin.site.site_title = 'CaCaCa admin'
admin.site.index_title = 'Manage your storefront'

class RodSpecificationInline(admin.StackedInline):
    model = RodSpecification
    extra = 1
    max_num = 1
    filter_horizontal = ['additional_target_species']
    classes = ['collapse']

class ProductImageInline(admin.StackedInline):
    model = ProductImage
    extra = 1
    readonly_fields = ['preview']
    fields = ['preview', 'image', 'alt_text', 'position']
    verbose_name = 'rod photo'
    verbose_name_plural = 'Rod photos — add a full-rod view and close-ups of the wrap design'

    @admin.display(description='Photo preview')
    def preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" alt="{}" style="max-width:240px;max-height:180px;object-fit:contain">', obj.image.url, obj.alt_text)
        return 'Upload a photo, then save to see its preview.'

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'item_type', 'design_theme', 'rod_id', 'status', 'price', 'build_cost', 'quantity', 'is_published', 'show_in_gallery']
    list_filter = ['item_type', 'status', 'design_theme', 'build_type', 'is_published', 'show_in_gallery']
    search_fields = ['name', 'rod_id', 'serial_number', 'design_theme__name', 'design_story']
    prepopulated_fields = {'slug': ['name']}
    readonly_fields = ['created_at', 'updated_at', 'reserved_quantity']
    inlines = [ProductImageInline, RodSpecificationInline]
    save_on_top = True
    fieldsets = [
        ('The finished rod', {'fields': ['name', 'slug', 'rod_id', 'item_type', 'design_theme', 'design_story', 'description'],
            'description': 'List a rod you have already hand-wrapped. Add the facts you know now and complete the remaining details before publishing.'}),
        ('Price, cost & availability', {'fields': ['price', 'build_cost', 'quantity', 'reserved_quantity', 'status', 'build_type'],
            'description': 'A price is required before a rod can be offered for sale. Build cost is private and supports estimated profit reporting. Choose Available or Ready when the listing is complete.'}),
        ('Show on the website', {'fields': ['is_published', 'featured', 'show_in_gallery'],
            'description': 'For a public preview without a price, keep status Draft and Is published unchecked, then check Show in gallery. To sell, enter a price and set status Available with Is published checked. Featured available rods appear first on the homepage.'}),
        ('Additional details', {'fields': ['serial_number', 'shipping_estimate', 'estimated_build_time',
            'meta_description', 'created_at', 'updated_at'], 'classes': ['collapse']}),
    ]

@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not StoreSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(Species)
admin.site.register(DesignTheme)

@admin.register(CustomBuildRequest)
class CustomBuildRequestAdmin(admin.ModelAdmin):
    list_display = ['name', 'kind', 'product', 'email', 'status', 'created_at']
    list_filter = ['kind', 'status']
    search_fields = ['name', 'email', 'target_species']
    readonly_fields = ['created_at']
