from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.management.commands.import_photo_inventory import RODS
from catalog.management.commands.import_tackle_photos import ITEMS
from catalog.models import DesignTheme, Product, ProductImage, RodSpecification


class Command(BaseCommand):
    help = 'Seed the temporary, view-only Render review with the supplied rods and lures.'

    def handle(self, *args, **options):
        if not settings.REVIEW_MODE:
            raise CommandError('Set CACACA_REVIEW_MODE=true for the temporary review site.')

        source = Path(settings.BASE_DIR) / 'Pictures'
        filenames = {filename for rod in RODS for filename, _ in rod['photos']}
        filenames.update(filename for _, _, _, _, photos in ITEMS for filename, _ in photos)
        missing = [filename for filename in sorted(filenames) if not (source / filename).is_file()]
        if missing:
            raise CommandError(f'Missing supplied review photos: {", ".join(missing)}')

        with transaction.atomic():
            for data in RODS:
                theme, _ = DesignTheme.objects.get_or_create(name=data['theme'])
                ready_for_sale = data['rod_id'] == 'CAC-PHOTO-004'
                rod, _ = Product.objects.get_or_create(rod_id=data['rod_id'], defaults={
                    'name': data['name'], 'slug': data['slug'], 'item_type': Product.ItemType.ROD,
                    'design_theme': theme, 'description': data['description'],
                    'design_story': data['story'], 'meta_description': data['description'][:160],
                    'price': Decimal('900.00') if ready_for_sale else None,
                    'quantity': 1, 'status': Product.Status.AVAILABLE if ready_for_sale else Product.Status.DRAFT,
                    'build_type': Product.BuildType.UNIQUE, 'is_published': ready_for_sale,
                    'show_in_gallery': True,
                })
                if rod.item_type != Product.ItemType.ROD:
                    raise CommandError(f'{data["rod_id"]} is not a rod.')
                spec, _ = RodSpecification.objects.get_or_create(product=rod)
                changes = []
                for field, value in [('wrap_colors', data['wrap_colors']),
                                     ('decorative_details', data['details'])]:
                    if not getattr(spec, field):
                        setattr(spec, field, value)
                        changes.append(field)
                if ready_for_sale:
                    for field, value in [('length', '12 ft'), ('power', 'Heavy'), ('action', 'Fast')]:
                        if not getattr(spec, field):
                            setattr(spec, field, value)
                            changes.append(field)
                if changes:
                    spec.save(update_fields=changes)
                self._add_photos(rod, data['photos'])

            for sku, slug, name, description, photos in ITEMS:
                lure, _ = Product.objects.get_or_create(rod_id=sku, defaults={
                    'name': name, 'slug': slug, 'item_type': Product.ItemType.TACKLE,
                    'description': description, 'meta_description': description[:160],
                    'price': Decimal('10.00'), 'quantity': 1,
                    'status': Product.Status.AVAILABLE, 'build_type': Product.BuildType.STOCK,
                    'is_published': True,
                })
                if lure.item_type != Product.ItemType.TACKLE:
                    raise CommandError(f'{sku} is not tackle.')
                self._add_photos(lure, photos)

        self.stdout.write(self.style.SUCCESS('Review inventory ready: four rods and three lures.'))

    @staticmethod
    def _add_photos(product, photos):
        if product.images.exists():
            return
        for position, (filename, alt_text) in enumerate(photos):
            ProductImage.objects.create(product=product, image=filename,
                                        alt_text=alt_text, position=position)
