from django.contrib import admin, messages
from .models import BusinessExpense, NotificationLog, Order, OrderItem, OrderAudit, PaymentEvent, Refund
from .services import expire_reservations


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


class ImmutableInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


class ItemInline(ImmutableInline):
    model = OrderItem
    fields = ['name', 'rod_id', 'serial_number', 'design', 'price', 'unit_cost', 'quantity', 'line_total']


class AuditInline(ImmutableInline):
    model = OrderAudit
    fields = ['created_at', 'action', 'actor']


class RefundInline(ImmutableInline):
    model = Refund
    fields = ['paypal_id', 'amount', 'status', 'completed_at', 'updated_at']


class NotificationInline(ImmutableInline):
    model = NotificationLog
    fields = ['attempted_at', 'kind', 'status', 'recipient', 'error_code']


@admin.register(Order)
class OrderAdmin(ReadOnlyAdmin):
    list_display = ['number', 'name', 'status', 'fulfillment_status', 'carrier', 'total', 'created_at', 'paid_at']
    list_filter = ['status', 'fulfillment_status', 'carrier', 'created_at']
    search_fields = ['id', 'name', 'email', 'paypal_order_id', 'capture_id', 'tracking_number', 'items__rod_id']
    date_hierarchy = 'created_at'
    inlines = [ItemInline, RefundInline, NotificationInline, AuditInline]
    exclude = ['owner_key', 'checkout_key', 'approval_url']

    def get_readonly_fields(self, request, obj=None):
        return [field for field in super().get_readonly_fields(request, obj) if field not in self.exclude]


@admin.register(PaymentEvent)
class PaymentEventAdmin(ReadOnlyAdmin):
    list_display = ['event_id', 'event_type', 'order', 'outcome', 'processed_at']
    list_filter = ['event_type']


@admin.register(BusinessExpense)
class BusinessExpenseAdmin(admin.ModelAdmin):
    list_display = ['incurred_on', 'category', 'description', 'amount', 'reference', 'created_by']
    list_filter = ['category', 'incurred_on']
    search_fields = ['description', 'reference']
    date_hierarchy = 'incurred_on'
    readonly_fields = ['created_by', 'created_at']

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
