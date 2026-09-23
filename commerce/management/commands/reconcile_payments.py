from django.core.management.base import BaseCommand
from commerce import paypal, services
from commerce.models import Order

class Command(BaseCommand):
    help = 'Read PayPal status for unresolved orders; never initiates a new capture or releases uncertain stock.'

    def handle(self, **options):
        for order in Order.objects.filter(status__in=['capturing','review','approval'], paypal_order_id__isnull=False):
            try:
                remote = paypal.get_order(order.paypal_order_id)
                if remote.get('status') == 'COMPLETED':
                    result = services.settle_order(order.pk, remote)
                    self.stdout.write(f'{order.number}: {result.status}')
                else:
                    self.stdout.write(f'{order.number}: still requires customer action or owner review')
            except paypal.PayPalError:
                self.stderr.write(f'{order.number}: PayPal unavailable; inventory hold preserved')
