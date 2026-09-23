from decimal import Decimal
from io import BytesIO
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

from PIL import Image
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from .forms import CustomBuildForm
from .models import Product, RodSpecification, Species, StoreSettings, CustomBuildRequest, ProductImage, DesignTheme


class ReviewInventorySeedTests(TestCase):
    @override_settings(REVIEW_MODE=True, MEDIA_URL='/static/review-media/')
    def test_seed_is_repeatable_and_review_photos_have_static_urls(self):
        for _ in range(2):
            call_command('seed_review_inventory', stdout=StringIO())
        self.assertEqual(Product.objects.filter(item_type=Product.ItemType.ROD).count(), 4)
        self.assertEqual(Product.objects.filter(item_type=Product.ItemType.TACKLE).count(), 3)
        self.assertEqual(ProductImage.objects.count(), 18)
        self.assertEqual(Product.objects.filter(status=Product.Status.DRAFT, show_in_gallery=True).count(), 3)
        self.assertEqual(Product.objects.filter(is_published=True).count(), 4)
        self.assertEqual(Product.objects.get(rod_id='CAC-PHOTO-004').price, Decimal('900.00'))
        self.assertTrue(ProductImage.objects.first().image.url.startswith('/static/review-media/'))
        self.assertContains(self.client.get('/shop/'), 'Red, White &amp; Blue Diamond Wrap')
        self.assertContains(self.client.get('/tackle/'), 'Pink Squid Rig')


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rod = Product.objects.create(name='Test river rod', slug='test-river-rod', rod_id='TEST-001',
            price=Decimal('425.00'), status='available', is_published=True)
        cls.king = Species.objects.create(name='Chinook / King salmon')
        cls.coho = Species.objects.create(name='Coho / Silver salmon')
        cls.spec = RodSpecification.objects.create(product=cls.rod, length='10 ft', salmon_focused=True,
            plunking_focused=True, primary_target_species=cls.king)
        cls.spec.additional_target_species.add(cls.coho)

    def test_public_pages_render(self):
        for url in ['/', '/shop/', '/salmon-rods/', '/gallery/', '/about/', '/faq/', '/contact/',
                    '/returns/', '/custom-rods/', '/health/', '/robots.txt', '/sitemap.xml']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_accented_logo_branding(self):
        response = self.client.get('/')
        self.assertContains(response, 'CáCáCá')
        self.assertContains(response, 'brand/cacaca-logo-accented.png')

    def test_published_product_without_specification_renders_safely(self):
        product = Product.objects.create(name='Photo-only rod', slug='photo-only-rod', rod_id='PHOTO-1',
            price=Decimal('300.00'), status='available', is_published=True)
        self.assertEqual(self.client.get(product.get_absolute_url()).status_code, 200)
        self.assertContains(self.client.get('/shop/'), product.name)

    def test_product_specs_and_multiple_species_display(self):
        response = self.client.get(self.rod.get_absolute_url())
        for text in ['10 ft', 'Chinook / King salmon', 'Coho / Silver salmon', '$425.00']:
            self.assertContains(response, text)

    def test_draft_and_unpublished_rods_are_private(self):
        for status, published in [('draft', True), ('available', False)]:
            self.rod.status, self.rod.is_published = status, published
            self.rod.save()
            self.assertEqual(self.client.get(self.rod.get_absolute_url()).status_code, 404)
            self.assertNotContains(self.client.get('/shop/'), self.rod.name)

    def test_sold_rod_kept_in_gallery_not_shop(self):
        self.rod.status = 'sold'
        self.rod.show_in_gallery = True
        self.rod.save()
        self.assertFalse(self.rod.is_available)
        self.assertNotContains(self.client.get('/shop/'), self.rod.name)
        self.assertContains(self.client.get('/gallery/'), self.rod.name)

    def test_species_and_search_filters(self):
        self.assertContains(self.client.get('/shop/', {'species': self.coho.pk}), self.rod.name)
        self.assertNotContains(self.client.get('/shop/', {'q': 'not-a-rod'}), self.rod.name)

    def test_one_of_one_quantity_constraint(self):
        self.rod.quantity = 2
        with self.assertRaises(ValidationError):
            self.rod.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=self.rod.pk).update(quantity=2)

    def test_private_draft_can_wait_for_price_but_published_rod_cannot(self):
        draft = Product.objects.create(name='Draft photo rod', slug='draft-photo-rod',
            rod_id='DRAFT-001', price=None, status='draft', is_published=False)
        draft.full_clean()
        self.assertFalse(draft.is_available)
        draft.is_published = True
        with self.assertRaises(ValidationError):
            draft.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=draft.pk).update(is_published=True)

    def test_private_return_draft_not_rendered(self):
        settings = StoreSettings.objects.get(pk=1)
        settings.return_policy = 'Private draft terms'
        settings.return_policy_published = False
        settings.save()
        self.assertNotContains(self.client.get('/returns/'), 'Private draft terms')
        settings.return_policy_published = True
        settings.save()
        self.assertContains(self.client.get('/returns/'), 'Private draft terms')

    def test_tax_setting_validated(self):
        settings = StoreSettings.objects.get(pk=1)
        self.assertEqual(settings.tax_rate, Decimal('8.25'))
        settings.tax_rate = Decimal('101')
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_custom_request_saved_once_and_not_public(self):
        response = self.client.post('/custom-rods/', {'name': 'Test customer', 'email': 'test@example.com'})
        self.assertRedirects(response, '/custom-rods/received/')
        self.assertEqual(CustomBuildRequest.objects.count(), 1)
        self.assertEqual(CustomBuildRequest.objects.get().kind, CustomBuildRequest.Kind.CUSTOM)
        self.client.post('/custom-rods/', {'name': 'Test customer', 'email': 'test@example.com'})
        self.assertEqual(CustomBuildRequest.objects.count(), 1)
        self.assertNotContains(self.client.get('/custom-rods/received/'), 'test@example.com')

    def test_csrf_and_form_validation(self):
        from django.test import Client
        self.assertEqual(Client(enforce_csrf_checks=True).post('/custom-rods/', {}).status_code, 403)
        self.assertFalse(CustomBuildForm({'name': 'Test', 'email': 'invalid'}).is_valid())
        self.assertFalse(CustomBuildForm({'name': 'Test', 'email': 'test@example.com', 'website': 'spam'}).is_valid())

    def test_admin_requires_staff(self):
        self.assertEqual(self.client.get('/admin/catalog/product/').status_code, 302)
        user = get_user_model().objects.create_user('customer', password='test-only-strong-password')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/admin/catalog/product/').status_code, 302)

    def test_image_validation(self):
        from django.forms import modelform_factory
        form_class = modelform_factory(ProductImage, fields=['product', 'image', 'alt_text'])
        payload = BytesIO()
        Image.new('RGB', (20, 20), 'navy').save(payload, format='PNG')
        form = form_class({'product': self.rod.pk, 'alt_text': 'Test image'},
                          {'image': SimpleUploadedFile('rod.png', payload.getvalue(), content_type='image/png')})
        self.assertTrue(form.is_valid(), form.errors)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            form.save()
        bad = form_class({'product': self.rod.pk, 'alt_text': 'Invalid'},
                         {'image': SimpleUploadedFile('rod.png', b'not an image', content_type='image/png')})
        self.assertFalse(bad.is_valid())

    def test_inquiry_keeps_product_reference(self):
        response = self.client.get('/custom-rods/', {'rod': self.rod.slug})
        self.assertContains(response, 'Test river rod (TEST-001)')

    def test_finished_collection_excludes_unavailable_and_commission_rods(self):
        for changes in [{'status': 'reserved'}, {'status': 'sold'}, {'quantity': 0},
                        {'build_type': 'made_to_order'}, {'build_type': 'custom'}]:
            Product.objects.filter(pk=self.rod.pk).update(status='available', quantity=1, build_type='unique')
            Product.objects.filter(pk=self.rod.pk).update(**changes)
            for url in ['/', '/shop/']:
                self.assertNotContains(self.client.get(url), self.rod.name)

    def test_wrap_theme_filter_search_and_details(self):
        theme = DesignTheme.objects.create(name='US flag')
        other = DesignTheme.objects.create(name='Birds')
        self.rod.design_theme = theme
        self.rod.design_story = 'Red, white, and blue hand-wrapped artwork.'
        self.rod.save()
        self.assertContains(self.client.get('/shop/', {'design': theme.pk}), self.rod.name)
        self.assertNotContains(self.client.get('/shop/', {'design': other.pk}), self.rod.name)
        self.assertContains(self.client.get('/shop/', {'q': 'US flag'}), self.rod.name)
        self.assertContains(self.client.get(self.rod.get_absolute_url()), self.rod.design_story)

    def test_finished_rod_contact_does_not_require_custom_specifications(self):
        url = f'/contact/?rod={self.rod.slug}'
        self.assertNotContains(self.client.get(url), 'Preferred length')
        response = self.client.post(url, {'name': 'Shopper', 'email': 'shopper@example.com', 'notes': 'Can I see the wrap closer?'})
        self.assertRedirects(response, '/contact/received/')
        inquiry = CustomBuildRequest.objects.get()
        self.assertIn('TEST-001', inquiry.notes)
        self.assertEqual(inquiry.kind, CustomBuildRequest.Kind.ROD)
        self.assertEqual(inquiry.product, self.rod)
        self.assertEqual(inquiry.status, 'received')

    def test_owner_listing_page_has_photo_and_design_controls(self):
        owner = get_user_model().objects.create_superuser('owner', 'owner@example.com', 'test-only-password')
        self.client.force_login(owner)
        response = self.client.get('/admin/catalog/product/add/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The finished rod')
        self.assertContains(response, 'Wrap design details')
        self.assertContains(response, 'Rod photos')

    def test_homepage_features_actual_rod_photo(self):
        ProductImage.objects.create(product=self.rod, image='rods/test-cover.png', alt_text='Test wrap close-up')
        response = self.client.get('/')
        self.assertEqual(response.context['hero_rod'], self.rod)
        self.assertContains(response, 'Test wrap close-up')

    def test_production_config_fails_without_required_secrets(self):
        import os
        import subprocess
        import sys
        env = os.environ.copy()
        env['DJANGO_DEBUG'] = 'false'
        env['CACACA_LOCAL_ENV'] = 'false'
        env.pop('DJANGO_SECRET_KEY', None)
        result = subprocess.run([sys.executable, '-c', 'import config.settings'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY is required', result.stderr)
        env['DJANGO_SECRET_KEY'] = 'test-value-not-a-real-secret'
        env.pop('DATABASE_URL', None)
        result = subprocess.run([sys.executable, '-c', 'import config.settings'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PostgreSQL DATABASE_URL is required', result.stderr)

    @override_settings(PUBLIC_BASE_URL='https://cacaca.example', SITE_INDEXABLE=True)
    def test_sitemap_lists_only_public_rods_and_search_pages_are_safe(self):
        private = Product.objects.create(name='Private rod', slug='private-rod', rod_id='PRIVATE-1',
            price=None, status=Product.Status.DRAFT, is_published=False)
        preview = Product.objects.create(name='Preview rod', slug='preview-rod', rod_id='PREVIEW-1',
            price=None, status=Product.Status.DRAFT, is_published=False, show_in_gallery=True)
        sold = Product.objects.create(name='Sold rod', slug='sold-rod', rod_id='SOLD-1',
            price=Decimal('425.00'), status=Product.Status.SOLD, is_published=True,
            show_in_gallery=True)
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response['Content-Type'], 'application/xml')
        root = ElementTree.fromstring(response.content)
        urls = {item.text for item in root.findall('.//{*}loc')}
        self.assertIn('https://cacaca.example/', urls)
        self.assertIn('https://cacaca.example' + self.rod.get_absolute_url(), urls)
        self.assertIn('https://cacaca.example' + preview.get_absolute_url(), urls)
        self.assertIn('https://cacaca.example' + sold.get_absolute_url(), urls)
        self.assertNotIn('https://cacaca.example' + private.get_absolute_url(), urls)
        self.assertFalse(any('/owner/' in url or '/orders/' in url for url in urls))

        product = self.client.get(self.rod.get_absolute_url())
        self.assertContains(product, 'rel="canonical" href="https://cacaca.example/rods/test-river-rod/"')
        self.assertContains(product, 'property="og:title" content="Test river rod | CaCaCa"')
        self.assertNotContains(product, 'content="noindex, nofollow"')
        ProductImage.objects.create(product=self.rod, image='rods/test-cover.png',
                                    alt_text='Blue and silver wrap')
        self.assertContains(self.client.get(self.rod.get_absolute_url()),
                            'property="og:image" content="https://cacaca.example/media/rods/test-cover.png"')
        filtered = self.client.get('/shop/?q=river')
        self.assertContains(filtered, 'content="noindex, nofollow"')
        self.assertNotContains(filtered, 'rel="canonical"')
        self.assertContains(self.client.get('/cart/'), 'content="noindex, nofollow"')
        robots = self.client.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /owner/', robots)
        self.assertIn('Sitemap: https://cacaca.example/sitemap.xml', robots)

    @override_settings(SITE_INDEXABLE=False)
    def test_staging_is_not_marked_for_search_indexing(self):
        self.assertEqual(self.client.get('/robots.txt').content.decode(), 'User-agent: *\nDisallow: /\n')
        self.assertContains(self.client.get(self.rod.get_absolute_url()), 'content="noindex, nofollow"')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OwnerMessageWorkflowTests(TestCase):
    def setUp(self):
        self.rod = Product.objects.create(name='Question rod', slug='question-rod', rod_id='QUESTION-1',
            price=Decimal('425.00'), status=Product.Status.AVAILABLE, is_published=True)
        self.owner = get_user_model().objects.create_superuser(
            'message-owner', 'message-owner@example.com', 'test-only-password')

    def test_rod_question_reaches_owner_inbox_and_status_does_not_send_reply(self):
        response = self.client.post('/contact/?rod=question-rod', {
            'name': 'Interested buyer', 'email': 'buyer@example.com',
            'notes': 'Can I see the full rod?',
        })
        self.assertRedirects(response, '/contact/received/')
        record = CustomBuildRequest.objects.get()
        self.assertEqual((record.kind, record.product), (CustomBuildRequest.Kind.ROD, self.rod))
        inbox_url = reverse('owner_messages')
        detail_url = reverse('owner_message', args=[record.pk])
        self.assertEqual(self.client.get(inbox_url).status_code, 302)
        self.assertEqual(self.client.get(detail_url).status_code, 302)
        self.client.force_login(self.owner)
        dashboard = self.client.get(reverse('owner_dashboard'))
        self.assertEqual(dashboard.context['open_requests'], 1)
        self.assertContains(dashboard, 'Customer messages')
        self.assertContains(self.client.get(inbox_url), 'Question rod')
        detail = self.client.get(detail_url)
        self.assertContains(detail, 'buyer@example.com')
        self.assertContains(detail, 'Can I see the full rod?')
        self.assertContains(detail, reverse('owner_rod_edit', args=[self.rod.pk]))
        self.assertRedirects(self.client.post(detail_url, {'status': 'contacted'}), detail_url)
        record.refresh_from_db()
        self.assertEqual(record.status, 'contacted')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.post(detail_url, {'status': 'not-a-status'}).status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.status, 'contacted')
        self.assertRedirects(self.client.post(detail_url, {'status': 'closed'}), detail_url)
        self.assertEqual(self.client.get(inbox_url).context['page_obj'].paginator.count, 0)
        self.assertContains(self.client.get(inbox_url + '?show=closed'), 'Question rod')

    def test_general_question_and_nonstaff_access(self):
        self.client.post('/contact/', {'name': 'Visitor', 'email': 'visitor@example.com',
                                       'notes': 'Do you have more photos?'})
        record = CustomBuildRequest.objects.get()
        self.assertEqual(record.kind, CustomBuildRequest.Kind.QUESTION)
        self.assertIsNone(record.product)
        visitor = get_user_model().objects.create_user('visitor', password='test-only-password')
        self.client.force_login(visitor)
        self.assertEqual(self.client.get(reverse('owner_messages')).status_code, 302)
        self.assertEqual(self.client.post(reverse('owner_message', args=[record.pk]),
                                          {'status': 'closed'}).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'received')


class OwnerRodWorkflowTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            'rod-owner', 'rod-owner@example.com', 'test-only-password')
        self.media = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media.cleanup)

    def photo(self, name, color):
        payload = BytesIO()
        Image.new('RGB', (24, 24), color).save(payload, format='PNG')
        return SimpleUploadedFile(name, payload.getvalue(), content_type='image/png')

    def test_owner_app_manifest_and_private_mobile_workspace(self):
        manifest = self.client.get(reverse('owner_manifest'))
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest['Content-Type'], 'application/manifest+json')
        data = manifest.json()
        self.assertEqual((data['start_url'], data['scope'], data['display']),
                         ('/owner/', '/owner/', 'standalone'))
        self.assertEqual({icon['sizes'] for icon in data['icons']}, {'192x192', '512x512'})
        for icon in data['icons']:
            self.assertTrue((Path(__file__).resolve().parents[1] /
                             icon['src'].lstrip('/')).exists())
        worker = self.client.get(reverse('owner_service_worker'))
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker['Service-Worker-Allowed'], '/owner/')
        self.assertIn('no-store', worker.content.decode())
        self.assertNotIn('caches.open', worker.content.decode())
        self.assertEqual(self.client.get(reverse('owner_dashboard')).status_code, 302)
        self.client.force_login(self.owner)
        dashboard = self.client.get(reverse('owner_dashboard'))
        self.assertContains(dashboard, reverse('owner_manifest'))
        self.assertContains(dashboard, 'owner-mobile-nav')
        self.assertContains(dashboard, 'owner-install-button')
        self.assertIn('no-store', dashboard['Cache-Control'])
        self.assertIn('no-store', self.client.get(reverse('owner_shipping'))['Cache-Control'])

    def test_owner_can_take_phone_camera_photo_for_public_preview(self):
        self.client.force_login(self.owner)
        form = self.client.get(reverse('owner_rod_add'))
        self.assertContains(form, 'capture="environment"')
        response = self.client.post(reverse('owner_rod_add'), {
            'name': 'Phone photo rod', 'quantity': '1', 'listing_state': 'preview',
            'description': 'Blue hand-wrapped design photographed on a phone.',
            'camera_photo': self.photo('camera.png', 'blue'),
        })
        rod = Product.objects.get(name='Phone photo rod')
        self.assertRedirects(response, reverse('owner_rod_edit', args=[rod.pk]))
        self.assertTrue(rod.is_public_preview)
        self.assertEqual(rod.images.count(), 1)
        self.assertContains(self.client.get(rod.get_absolute_url()), 'Phone photo rod')

    def test_owner_adds_photos_lists_rod_and_marks_it_sold(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('owner_rod_add'), {
            'name': 'Blue diamond rod', 'design_name': 'Blue diamond', 'price': '475.00',
            'quantity': '1', 'listing_state': 'available',
            'description': 'Blue and silver diamond hand wrap.', 'featured': 'on',
            'length': '10 ft', 'power': 'Medium Heavy', 'action': 'Moderate Fast',
            'line_rating': '15-30 lb', 'primary_fishing_style': 'Plunking',
            'photos': [self.photo('cover.png', 'blue'), self.photo('detail.png', 'silver')],
        })
        rod = Product.objects.get(name='Blue diamond rod')
        self.assertRedirects(response, reverse('owner_rod_edit', args=[rod.pk]))
        self.assertEqual(rod.images.count(), 2)
        self.assertTrue(rod.is_available)
        self.assertEqual(rod.specification.length, '10 ft')
        self.assertContains(self.client.get('/shop/'), rod.name)
        self.assertContains(self.client.get(rod.get_absolute_url()), 'Medium Heavy')
        self.assertContains(self.client.get(reverse('owner_inventory')), rod.name)
        self.assertContains(self.client.get(reverse('owner_rod_edit', args=[rod.pk])), 'Current photos')

        second_photo = rod.images.order_by('position')[1]
        self.client.post(reverse('owner_rod_edit', args=[rod.pk]), {
            'name': rod.name, 'design_name': 'Blue diamond', 'price': '475.00',
            'quantity': '1', 'listing_state': 'available',
            'description': 'Blue and silver diamond hand wrap.', 'featured': 'on',
            'length': '10 ft', 'power': 'Medium Heavy', 'action': 'Moderate Fast',
            'line_rating': '15-30 lb', 'primary_fishing_style': 'Plunking',
            'cover_photo': str(second_photo.pk),
        })
        rod.refresh_from_db()
        self.assertEqual(rod.cover.pk, second_photo.pk)

        response = self.client.post(reverse('owner_inventory_update', args=[rod.pk]), {
            f'rod-{rod.pk}-price': '475.00', f'rod-{rod.pk}-quantity': '1',
            f'rod-{rod.pk}-listing_state': 'sold',
        })
        self.assertRedirects(response, reverse('owner_inventory'))
        rod.refresh_from_db()
        self.assertEqual(rod.status, Product.Status.SOLD)
        self.assertEqual(rod.quantity, 0)
        self.assertNotContains(self.client.get('/shop/'), rod.name)
        self.assertContains(self.client.get('/gallery/'), rod.name)
        self.assertContains(self.client.get(reverse('owner_dashboard')), rod.name)

    def test_public_preview_shows_rod_without_price_or_purchase(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('owner_rod_add'), {
            'name': 'Blue preview rod', 'design_name': 'Blue diamond',
            'quantity': '1', 'listing_state': 'preview',
            'description': 'Blue and silver hand-wrapped diamond pattern.',
            'photos': [self.photo('blue-preview.png', 'blue')],
        })
        rod = Product.objects.get(name='Blue preview rod')
        self.assertRedirects(response, reverse('owner_rod_edit', args=[rod.pk]))
        self.assertTrue(rod.is_public_preview)
        self.assertFalse(rod.is_available)
        for url in ['/', '/shop/', '/gallery/']:
            self.assertContains(self.client.get(url), rod.name)
        detail = self.client.get(rod.get_absolute_url())
        self.assertContains(detail, 'Design preview')
        self.assertContains(detail, 'Not for sale yet')
        self.assertNotContains(detail, 'Add to cart')
        self.assertNotContains(detail, '$None')
        self.assertEqual(self.client.post(reverse('cart_change', args=[rod.pk]), {'quantity': 1}).status_code, 404)
        priced = Product.objects.create(name='Priced rod', slug='priced-rod', rod_id='PRICED-1',
            price=Decimal('425.00'), status=Product.Status.AVAILABLE, is_published=True)
        for sort in ['price_asc', 'price_desc']:
            page = self.client.get('/shop/', {'sort': sort}).context['page_obj']
            self.assertEqual([item.pk for item in page], [priced.pk, rod.pk])

        self.client.post(reverse('owner_inventory_update', args=[rod.pk]), {
            f'rod-{rod.pk}-price': '', f'rod-{rod.pk}-quantity': '1',
            f'rod-{rod.pk}-listing_state': 'draft',
        })
        rod.refresh_from_db()
        self.assertFalse(rod.show_in_gallery)
        self.assertEqual(self.client.get(rod.get_absolute_url()).status_code, 404)
        self.assertEqual([item.pk for item in self.client.get('/shop/').context['page_obj']], [priced.pk])

    def test_public_preview_needs_a_photo_and_description(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('owner_rod_add'), {
            'name': 'Incomplete public rod', 'quantity': '1', 'listing_state': 'preview',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add at least one photo before showing a public preview.')
        self.assertContains(response, 'Add a description before showing a public preview.')
        self.assertFalse(Product.objects.filter(name='Incomplete public rod').exists())

    def test_owner_cannot_publish_without_price_or_photo(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('owner_rod_add'), {
            'name': 'Unfinished listing', 'quantity': '1', 'listing_state': 'available',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter the selling price')
        self.assertContains(response, 'Add at least one photo')
        self.assertContains(response, 'Enter this rod detail')
        self.assertFalse(Product.objects.filter(name='Unfinished listing').exists())

    def test_inventory_cannot_publish_a_draft_missing_rod_details(self):
        rod = Product.objects.create(name='Photo draft', slug='photo-draft', rod_id='DRAFT-2',
            price=None, status=Product.Status.DRAFT, is_published=False,
            description='Blue and black hand wrap.')
        ProductImage.objects.create(product=rod, image='rods/example.jpg', alt_text='Blue wrap')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('owner_inventory_update', args=[rod.pk]), {
            f'rod-{rod.pk}-price': '450.00', f'rod-{rod.pk}-quantity': '1',
            f'rod-{rod.pk}-listing_state': 'available',
        })
        self.assertRedirects(response, reverse('owner_inventory'))
        rod.refresh_from_db()
        self.assertEqual(rod.status, Product.Status.DRAFT)
        self.assertContains(self.client.get(reverse('owner_inventory')), 'Rod length, Power, Action')

    def test_owner_pages_require_staff(self):
        for url in [reverse('owner_rod_add'), reverse('owner_inventory'),
                    reverse('owner_shipping'), reverse('owner_settings')]:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_owner_can_change_shipping_and_tax_from_simple_settings(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('owner_settings'), {
            'shipping_rate': '18.00', 'shipping_mode': 'per_order', 'tax_rate': '8.25',
            'contact_email': 'orders@example.com',
        })
        self.assertRedirects(response, reverse('owner_settings'))
        store = StoreSettings.objects.get(pk=1)
        self.assertEqual(store.shipping_rate, Decimal('18.00'))
        self.assertEqual(store.contact_email, 'orders@example.com')
        self.assertContains(self.client.get(reverse('owner_shipping')), '$18.00')

    def test_owner_test_checkout_switch_requires_sandbox_connection(self):
        self.client.force_login(self.owner)
        values = {'shipping_rate': '15.00', 'shipping_mode': 'per_order',
                  'tax_rate': '8.25', 'checkout_enabled': 'on'}
        response = self.client.post(reverse('owner_settings'), values)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PayPal sandbox credentials must be configured')
        self.assertContains(response, 'Sandbox checkout is off.')
        store = StoreSettings.objects.get(pk=1)
        self.assertFalse(store.checkout_enabled)
        self.assertContains(self.client.get(reverse('owner_settings')), 'A sandbox business account and app are still needed')

        with override_settings(PAYPAL_CLIENT_ID='test-client', PAYPAL_CLIENT_SECRET='test-secret',
                               PAYPAL_WEBHOOK_ID='test-webhook', PAYPAL_MERCHANT_ID='TESTMERCHANT'):
            response = self.client.post(reverse('owner_settings'), values)
            self.assertRedirects(response, reverse('owner_settings'))
            store.refresh_from_db()
            self.assertTrue(store.checkout_enabled)
            self.assertContains(self.client.get(reverse('owner_dashboard')), 'Open for testing')
            self.assertNotContains(self.client.get(reverse('owner_settings')), 'test-secret')

        self.assertContains(self.client.get(reverse('owner_dashboard')), 'Needs setup')

    def test_reserved_rod_cannot_be_marked_sold_from_inventory(self):
        rod = Product.objects.create(name='Reserved rod', slug='reserved-rod', rod_id='RES-1',
            price=Decimal('450.00'), quantity=1, reserved_quantity=1,
            status=Product.Status.AVAILABLE, is_published=True)
        self.client.force_login(self.owner)
        self.client.post(reverse('owner_inventory_update', args=[rod.pk]), {
            f'rod-{rod.pk}-price': '450.00', f'rod-{rod.pk}-quantity': '0',
            f'rod-{rod.pk}-listing_state': 'sold',
        })
        rod.refresh_from_db()
        self.assertEqual(rod.status, Product.Status.AVAILABLE)
        self.assertEqual(rod.quantity, 1)


class TackleWorkflowTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            'tackle-owner', 'tackle-owner@example.com', 'test-only-password')
        self.media = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media.cleanup)

    def test_owner_lists_tackle_separately_and_cart_uses_item_price_and_stock(self):
        self.assertEqual(self.client.get(reverse('owner_tackle')).status_code, 302)
        self.client.force_login(self.owner)
        payload = BytesIO()
        Image.new('RGB', (24, 24), 'pink').save(payload, format='PNG')
        photo = SimpleUploadedFile('pink-rig.png', payload.getvalue(), content_type='image/png')
        response = self.client.post(reverse('owner_tackle_add'), {
            'name': 'Pink test lure', 'price': '10.00', 'quantity': '2',
            'listing_state': 'available', 'description': 'One pink test lure.', 'photos': [photo],
        })
        item = Product.objects.get(name='Pink test lure')
        self.assertRedirects(response, reverse('owner_tackle_edit', args=[item.pk]))
        self.assertEqual(item.item_type, Product.ItemType.TACKLE)
        self.assertEqual(item.images.count(), 1)
        self.assertTrue(item.is_available)
        self.assertContains(self.client.get(reverse('tackle')), item.name)
        self.assertContains(self.client.get(reverse('home')), item.name)
        self.assertNotContains(self.client.get(reverse('shop')), item.name)
        self.assertEqual(self.client.get(item.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse('product', args=[item.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('owner_rod_edit', args=[item.pk])).status_code, 404)
        contact_url = reverse('contact') + f'?tackle={item.slug}'
        self.assertContains(self.client.get(contact_url), 'Ask about this tackle.')
        self.assertRedirects(self.client.post(contact_url, {
            'name': 'Shopper', 'email': 'shopper@example.com', 'notes': 'Is this still available?',
        }), reverse('inquiry_success'))
        inquiry = CustomBuildRequest.objects.get(product=item)
        self.assertIn(item.rod_id, inquiry.notes)
        self.assertRedirects(self.client.post(reverse('cart_change', args=[item.pk]),
            {'quantity': '1'}), reverse('cart'))
        from commerce.cart import quote
        summary = quote({str(item.pk): 2})
        self.assertEqual((summary['subtotal'], summary['shipping'], summary['tax'], summary['total']),
                         (Decimal('20.00'), Decimal('15.00'), Decimal('1.65'), Decimal('36.65')))

        response = self.client.post(reverse('owner_tackle_edit', args=[item.pk]), {
            'name': item.name, 'price': '12.50', 'quantity': '3',
            'listing_state': 'available', 'description': item.description,
        })
        self.assertRedirects(response, reverse('owner_tackle_edit', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual((item.price, item.quantity), (Decimal('12.50'), 3))
