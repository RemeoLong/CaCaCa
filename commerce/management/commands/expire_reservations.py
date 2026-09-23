from django.core.management.base import BaseCommand
from commerce.services import expire_reservations

class Command(BaseCommand):
    help = 'Release expired, uncharged inventory holds. Captures with uncertain outcomes are never released.'

    def handle(self, **options):
        self.stdout.write(f'Released {expire_reservations()} expired reservations.')
