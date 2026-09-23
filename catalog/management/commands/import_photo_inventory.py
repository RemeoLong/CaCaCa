from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from catalog.models import DesignTheme, Product, ProductImage, RodSpecification


PHOTO_PREFIX = 'b46f5c89ebda2f99374f744ab88aed034a8f28cc'

RODS = [
    {
        'rod_id': 'CAC-PHOTO-001',
        'slug': 'electric-blue-silver-diamond-wrap',
        'name': 'Electric Blue & Silver Diamond Wrap',
        'theme': 'Blue & Silver Geometric',
        'description': ('A hand-wrapped geometric design built around vivid blue, silver, white, and black. '
                        'Repeating diamond shapes and starburst details run along the decorative section for '
                        'a crisp, high-contrast finish.'),
        'story': ('Electric-blue panels alternate with silver-and-black diamonds, narrow white lines, and '
                  'small star-like accents. The photos show the actual completed wrap alongside the other '
                  'finished rods.'),
        'wrap_colors': 'Electric blue, silver, white, and black',
        'details': 'Repeating diamonds, fine crossing lines, and starburst accents.',
        'photos': [
            (f'{PHOTO_PREFIX}-1.jpg', 'Three finished rods; the electric blue and silver diamond wrap is shown at left.'),
            (f'{PHOTO_PREFIX}-2.jpg', 'Side view of three finished rods; the electric blue and silver wrap is shown at top.'),
            (f'{PHOTO_PREFIX}-3.jpg', 'Full decorative sections of three finished rods; blue and silver wrap shown at left.'),
        ],
    },
    {
        'rod_id': 'CAC-PHOTO-002',
        'slug': 'blue-gold-black-diamond-wrap',
        'name': 'Blue, Gold & Black Diamond Wrap',
        'theme': 'Blue, Gold & Black Geometric',
        'description': ('A bold hand-wrapped design combining bright blue and yellow-gold panels over a deep '
                        'black base. Fine silver lines and small red accent squares add detail between the '
                        'repeating diamonds.'),
        'story': ('The wrap moves between blue geometric panels, gold sections, black crosshatching, and silver '
                  'pinstriping. Gold-and-silver starburst details continue near the guide wrap.'),
        'wrap_colors': 'Blue, gold, black, silver, and small red accents',
        'details': 'Layered diamonds, crosshatching, silver pinstriping, and starburst guide accents.',
        'photos': [
            ('attachment.jpg', 'Close-up of the completed blue, gold, and black geometric hand wrap.'),
            (f'{PHOTO_PREFIX}-7.jpg', 'Blue, gold, and black diamond wrap photographed in daylight.'),
            (f'{PHOTO_PREFIX}-5.jpg', 'Close-up of black, gold, and silver starburst details beside a rod guide.'),
            (f'{PHOTO_PREFIX}-3.jpg', 'Three finished rods; the blue, gold, and black wrap is shown in the center.'),
        ],
    },
    {
        'rod_id': 'CAC-PHOTO-003',
        'slug': 'black-white-blue-diamond-wrap',
        'name': 'Black, White & Blue Diamond Wrap',
        'theme': 'Black, White & Blue Geometric',
        'description': ('A high-contrast hand-wrapped pattern with black and white diamonds, blue borders, and '
                        'star-like accents. The repeating pattern gives the finished rod a clean monochrome '
                        'look with flashes of blue.'),
        'story': ('Alternating black and white diamond sections create the main rhythm of this wrap. Narrow blue '
                  'bands and bright starburst details add color along the decorative section.'),
        'wrap_colors': 'Black, white, silver, and blue',
        'details': 'Alternating diamonds with blue borders and starburst accents.',
        'photos': [
            (f'{PHOTO_PREFIX}-3.jpg', 'Three finished rods; the black, white, and blue diamond wrap is shown at right.'),
            (f'{PHOTO_PREFIX}-2.jpg', 'Side view of three finished rods; the black, white, and blue wrap is shown at bottom.'),
            (f'{PHOTO_PREFIX}-1.jpg', 'Full decorative sections of three finished rods; black and white wrap shown at right.'),
        ],
    },
    {
        'rod_id': 'CAC-PHOTO-004',
        'slug': 'red-white-blue-diamond-wrap',
        'name': 'Red, White & Blue Diamond Wrap',
        'theme': 'Red, White & Blue Geometric',
        'description': ('A hand-wrapped red, white, and blue geometric design with alternating diamond sections '
                        'and reflective accents. The color pattern runs along the decorative section of the '
                        'finished rod.'),
        'story': ('Red and blue sections repeat between pale silver-white diamonds over a dark base. The outdoor '
                  'view shows the completed decorative section, while the close-up shows the weave and color changes.'),
        'wrap_colors': 'Red, white, blue, silver, and black',
        'details': 'Alternating patriotic-color diamonds with crossing thread details.',
        'photos': [
            (f'{PHOTO_PREFIX}-6.jpg', 'Full view of the completed red, white, and blue diamond wrap in daylight.'),
            (f'{PHOTO_PREFIX}-4.jpg', 'Close-up of the red, white, blue, and silver geometric hand wrap.'),
        ],
    },
]


class Command(BaseCommand):
    help = 'Create private draft rod inventory from the owner-supplied Pictures folder.'

    def add_arguments(self, parser):
        parser.add_argument('--source', type=Path, default=settings.BASE_DIR / 'Pictures')

    def handle(self, *args, **options):
        source = options['source'].resolve()
        if not source.is_dir():
            raise CommandError(f'Photo folder does not exist: {source}')

        created_rods = created_photos = 0
        for item in RODS:
            theme, _ = DesignTheme.objects.get_or_create(name=item['theme'])
            rod, created = Product.objects.get_or_create(
                rod_id=item['rod_id'],
                defaults={
                    'name': item['name'], 'slug': item['slug'], 'design_theme': theme,
                    'description': item['description'], 'design_story': item['story'],
                    'price': None, 'quantity': 1, 'status': Product.Status.DRAFT,
                    'build_type': Product.BuildType.UNIQUE, 'is_published': False,
                    'featured': False, 'show_in_gallery': False,
                    'meta_description': item['description'][:160],
                },
            )
            if created:
                created_rods += 1
            spec, _ = RodSpecification.objects.get_or_create(product=rod)
            if not spec.wrap_colors:
                spec.wrap_colors = item['wrap_colors']
            if not spec.decorative_details:
                spec.decorative_details = item['details']
            spec.save()

            for position, (filename, alt_text) in enumerate(item['photos']):
                photo_path = source / filename
                if not photo_path.is_file():
                    raise CommandError(f'Missing source photo: {photo_path}')
                if ProductImage.objects.filter(product=rod, alt_text=alt_text).exists():
                    continue
                photo = ProductImage(product=rod, alt_text=alt_text, position=position)
                with photo_path.open('rb') as handle:
                    photo.image.save(photo_path.name, File(handle), save=True)
                created_photos += 1

        self.stdout.write(self.style.SUCCESS(
            f'Inventory ready: {created_rods} new draft rods and {created_photos} new rod photos.'
        ))
