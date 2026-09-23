from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Product, ProductImage


PHOTO_WIDE = 'b46f5c89ebda2f99374f744ab88aed034a8f28cc-8.jpg'
PHOTO_TALL = 'b46f5c89ebda2f99374f744ab88aed034a8f28cc-9.jpg'

ITEMS = [
    ('CAC-TACKLE-001', 'rainbow-spinner-pink-squid-rig', 'Rainbow Spinner Pink Squid Rig',
     'One pink squid lure attached to the red, green, and yellow spinner shown at the top of the group photo. '
     'This listing is for that one assembled lure only; the other lures and loose hooks pictured are not included.',
     [(PHOTO_TALL, 'Top assembled lure: red, green, and yellow spinner with pink squid skirt.'),
      (PHOTO_WIDE, 'Group view of the tackle; this listing is the leftmost spinner and pink squid lure.')]),
    ('CAC-TACKLE-002', 'chartreuse-spinner-pink-squid-rig', 'Chartreuse Spinner Pink Squid Rig',
     'One pink squid lure attached to the bright chartreuse spinner with a red blade, shown second in the group photo. '
     'This listing is for that one assembled lure only; the other lures and loose hooks pictured are not included.',
     [(PHOTO_WIDE, 'Group view of the tackle; this listing is the second lure from the left, with a chartreuse spinner.'),
      (PHOTO_TALL, 'Second assembled lure: chartreuse spinner with a red blade and pink squid skirt.')]),
    ('CAC-TACKLE-003', 'pink-squid-rig', 'Pink Squid Rig',
     'One pink squid lure without a spinner, shown third in the group photo. '
     'This listing is for that one assembled lure only; the other lures and loose hooks pictured are not included.',
     [(PHOTO_WIDE, 'Group view of the tackle; this listing is the third pink squid lure from the left.'),
      (PHOTO_TALL, 'Third assembled lure in the group photo: standalone pink squid skirt with hooks.')]),
]


class Command(BaseCommand):
    help = 'Import the three owner-supplied pink squid lure photos as individual $10 tackle listings.'

    def handle(self, *args, **options):
        pictures = Path(settings.BASE_DIR) / 'Pictures'
        for filename in [PHOTO_WIDE, PHOTO_TALL]:
            if not (pictures / filename).is_file():
                raise CommandError(f'Missing source photo: {pictures / filename}')
        for sku, slug, name, description, photos in ITEMS:
            with transaction.atomic():
                item, created = Product.objects.get_or_create(rod_id=sku, defaults={
                    'name': name, 'slug': slug, 'item_type': Product.ItemType.TACKLE,
                    'description': description, 'meta_description': description[:160],
                    'price': Decimal('10.00'), 'quantity': 1, 'build_type': Product.BuildType.STOCK,
                    'status': Product.Status.AVAILABLE, 'is_published': True,
                })
                if item.item_type != Product.ItemType.TACKLE:
                    raise CommandError(f'{sku} already belongs to a rod.')
                if not item.images.exists():
                    for position, (filename, alt_text) in enumerate(photos):
                        with (pictures / filename).open('rb') as source:
                            image = ProductImage(product=item, alt_text=alt_text, position=position)
                            image.image.save(filename, File(source), save=True)
                self.stdout.write(f'{"Added" if created else "Kept"}: {item.name}')
