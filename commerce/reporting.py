from collections import Counter
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate

from catalog.models import CustomBuildRequest, Product
from .models import BusinessExpense, Order, OrderItem, Refund


ZERO = Decimal('0.00')
PAID_STATUSES = [Order.Status.PAID, Order.Status.PARTIAL_REFUND, Order.Status.REFUNDED]


def orders_for_period(start, end):
    return Order.objects.filter(status__in=PAID_STATUSES, paid_at__date__range=(start, end))


def report_data(start, end):
    orders = orders_for_period(start, end)
    money = orders.aggregate(
        gross_sales=Sum('subtotal'), shipping_collected=Sum('shipping'), tax_collected=Sum('tax'),
        total_collected=Sum('total'),
        carrier_shipping=Sum('actual_shipping_cost'),
    )
    money = {key: value or ZERO for key, value in money.items()}
    refunds = Refund.objects.filter(status='COMPLETED', completed_at__date__range=(start, end))
    money['refunds'] = refunds.aggregate(value=Sum('amount'))['value'] or ZERO
    items = OrderItem.objects.filter(order__in=orders)
    product_cost = items.filter(unit_cost__isnull=False).aggregate(
        value=Sum(F('unit_cost') * F('quantity')))['value'] or ZERO
    missing_cost_lines = items.filter(unit_cost__isnull=True).count()
    expenses = BusinessExpense.objects.filter(incurred_on__range=(start, end))
    expense_rows = expenses.values('category').annotate(value=Sum('amount'))
    expense_totals = {row['category']: row['value'] for row in expense_rows}
    payment_fees = expense_totals.get(BusinessExpense.Category.PAYMENT_FEE, ZERO)
    manual_shipping = expense_totals.get(BusinessExpense.Category.SHIPPING, ZERO)
    other_expenses = sum((value for category, value in expense_totals.items()
                          if category not in [BusinessExpense.Category.PAYMENT_FEE,
                                              BusinessExpense.Category.SHIPPING]), ZERO)
    shipping_expense = money['carrier_shipping'] + manual_shipping
    net_collected = money['total_collected'] - money['refunds']
    estimated_profit = (money['gross_sales'] + money['shipping_collected'] - money['refunds'] -
                        product_cost - shipping_expense - payment_fees - other_expenses)
    sales_daily = list(orders.annotate(day=TruncDate('paid_at')).values('day').annotate(
        orders=Count('id'), gross=Sum('subtotal'), tax=Sum('tax'), total=Sum('total'),
        ).order_by('-day'))
    refund_daily = {row['day']: row['refunds'] for row in
                    refunds.annotate(day=TruncDate('completed_at')).values('day').annotate(
                        refunds=Sum('amount'))}
    daily_by_date = {row['day']: {**row, 'refunds': refund_daily.pop(row['day'], ZERO)}
                     for row in sales_daily}
    for day, amount in refund_daily.items():
        daily_by_date[day] = {'day': day, 'orders': 0, 'gross': ZERO, 'tax': ZERO,
                              'total': ZERO, 'refunds': amount}
    daily = [daily_by_date[day] for day in sorted(daily_by_date, reverse=True)]
    fulfillment = {row['fulfillment_status']: row['count'] for row in
                   orders.values('fulfillment_status').annotate(count=Count('id'))}
    requests = CustomBuildRequest.objects.filter(created_at__date__range=(start, end))
    request_counts = {row['status']: row['count'] for row in
                      requests.values('status').annotate(count=Count('id'))}
    request_labels = dict(CustomBuildRequest._meta.get_field('status').choices)
    request_rows = [{'key': key, 'label': request_labels[key], 'count': request_counts.get(key, 0)}
                    for key in request_labels]
    return {
        'orders': orders.prefetch_related('items'), 'order_count': orders.count(),
        **money, 'refunds_negative': -money['refunds'], 'net_collected': net_collected,
        'product_cost': product_cost, 'missing_cost_lines': missing_cost_lines,
        'shipping_expense': shipping_expense, 'payment_fees': payment_fees,
        'other_expenses': other_expenses, 'estimated_profit': estimated_profit,
        'expenses': expenses, 'daily': daily, 'fulfillment': fulfillment,
        'needs_packing': fulfillment.get(Order.FulfillmentStatus.NEEDS_PACKING, 0),
        'packed': fulfillment.get(Order.FulfillmentStatus.PACKED, 0),
        'shipped': fulfillment.get(Order.FulfillmentStatus.SHIPPED, 0),
        'delivered': fulfillment.get(Order.FulfillmentStatus.DELIVERED, 0),
        'completed': fulfillment.get(Order.FulfillmentStatus.COMPLETED, 0),
        'request_rows': request_rows, 'request_count': requests.count(),
    }


def inventory_data():
    products = list(Product.objects.select_related('design_theme').all().order_by('status', 'name'))
    status_counts = Counter(product.status for product in products)
    available_units = 0
    retail_value = ZERO
    cost_value = ZERO
    missing_cost_units = 0
    for product in products:
        product.available_units = max(product.quantity - product.reserved_quantity, 0)
        if product.status in [Product.Status.AVAILABLE, Product.Status.READY]:
            available_units += product.available_units
            if product.price is not None:
                retail_value += product.price * product.available_units
            if product.build_cost is None:
                missing_cost_units += product.available_units
            else:
                cost_value += product.build_cost * product.available_units
    return {
        'products': products, 'product_count': len(products), 'available_units': available_units,
        'retail_value': retail_value, 'cost_value': cost_value,
        'missing_cost_units': missing_cost_units, 'status_counts': status_counts,
    }
