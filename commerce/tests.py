import copy
import json
import uuid
from hashlib import sha256
from io import BytesIO
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from catalog.models import Product, StoreSettings
from . import fulfillment, notifications, paypal, reporting, services
from .cart import CheckoutError, quote
from .forms import CheckoutForm
from .models import BusinessExpense, NotificationLog, Order, PaymentEvent, Refund


@override_settings(REVIEW_MODE=True, OWNER_SETUP_CODE_HASH=sha256(b'one-time-test-code').hexdigest())
class OwnerReviewSetupTests(TestCase):
    def test_one_time_claim_creates_owner_and_opens_dashboard(self):
        url = reverse('owner_setup')
        self.assertEqual(self.client.get(url).status_code, 200)
        details = {'username': 'reviewowner', 'email': 'owner@example.com',
                   'setup_code': 'one-time-test-code', 'password1': 'StrongReviewPass#9026',
                   'password2': 'StrongReviewPass#9026'}
        wrong = {**details, 'setup_code': 'wrong'}
        self.assertContains(self.client.post(url, wrong), 'not correct')
        self.assertFalse(get_user_model().objects.exists())
        response = self.client.post(url, details)
        self.assertRedirects(response, reverse('owner_dashboard'))
        owner = get_user_model().objects.get(username='reviewowner')
        self.assertTrue(owner.is_staff and owner.is_superuser)
        self.assertContains(self.client.get(reverse('owner_dashboard')), 'Owner review copy')
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_review_prevents_owner_edits(self):
        owner = get_user_model().objects.create_superuser(
            'reviewowner', 'owner@example.com', 'StrongReviewPass#9026')
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse('owner_rod_add')).status_code, 200)
        response = self.client.post(reverse('owner_rod_add'), {'name': 'Should not save'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Product.objects.exists())

    @override_settings(OWNER_SETUP_CODE_HASH='')
    def test_setup_is_closed_without_code(self):
        self.assertEqual(self.client.get(reverse('owner_setup')).status_code, 404)

PAYPAL_SETTINGS = {'PAYPAL_CLIENT_ID':'test-client', 'PAYPAL_CLIENT_SECRET':'test-secret',
    'PAYPAL_WEBHOOK_ID':'test-webhook', 'PAYPAL_MERCHANT_ID':'MERCHANT123'}
ADDRESS = {'address_line_1':'123 Test Street','address_line_2':'','admin_area_2':'Austin',
           'admin_area_1':'TX','postal_code':'78701','country_code':'US'}
CUSTOMER = {'name':'Test Buyer','email':'buyer@example.com','phone':'','notes':'','address':ADDRESS}


def completed(order):
    return {'id':order.paypal_order_id,'status':'COMPLETED','purchase_units':[{
        'custom_id':str(order.pk), 'payee':{'merchant_id':'MERCHANT123'},
        'shipping':{'address':copy.deepcopy(ADDRESS)}, 'amount':{'currency_code':'USD','value':str(order.total)},
        'payments':{'captures':[{'id':'CAPTURE123','status':'COMPLETED','final_capture':True,
            'amount':{'currency_code':'USD','value':str(order.total)}}]}}]}


class FixtureMixin:
    def setUp(self):
        self.store, _ = StoreSettings.objects.get_or_create(pk=1)
        self.store.checkout_enabled = True
        self.store.shipping_configured = True
        self.store.shipping_rate = Decimal('15.00')
        self.store.tax_rate = Decimal('8.25')
        self.store.save()
        self.product = Product.objects.create(name='Test flag rod', slug='test-flag-rod', rod_id='FLAG-TEST',
            price=Decimal('400.00'), quantity=1, is_published=True, status='available')
        self.cart = {str(self.product.pk):1}
        self.key = uuid.uuid4()
        self.owner = uuid.uuid4().hex

    def reserve(self):
        return services.reserve_order(self.cart, self.owner, self.key, quote(self.cart)['token'], CUSTOMER)

    def approved(self):
        order = self.reserve()
        order.status = Order.Status.APPROVAL
        order.paypal_order_id = 'PAYPAL123'
        order.save()
        return order


@override_settings(**PAYPAL_SETTINGS)
class CommerceTests(FixtureMixin, TestCase):
    def test_totals_use_decimal_and_owner_shipping_default(self):
        summary = quote(self.cart)
        self.assertEqual(summary['shipping'], Decimal('15.00'))
        self.assertEqual(summary['tax'], Decimal('33.00'))
        self.assertEqual(summary['total'], Decimal('448.00'))
        self.store.tax_shipping = True
        self.store.save()
        self.assertEqual(quote(self.cart)['tax'], Decimal('34.24'))

    def test_snapshot_does_not_change_after_store_edits(self):
        order = self.reserve()
        self.store.tax_rate = Decimal('10.00')
        self.store.shipping_rate = Decimal('30.00')
        self.store.save()
        self.product.price = Decimal('600.00')
        self.product.save(update_fields=['price'])
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal('448.00'))
        self.assertEqual(order.items.get().price, Decimal('400.00'))

    def test_checkout_rejects_changed_or_forged_quote(self):
        token = quote(self.cart)['token']
        self.store.shipping_rate = Decimal('16.00')
        self.store.save()
        with self.assertRaises(CheckoutError):
            services.reserve_order(self.cart,self.owner,self.key,token,CUSTOMER)
        with self.assertRaises(CheckoutError):
            services.reserve_order(self.cart,self.owner,self.key,'fake-token',CUSTOMER)
        self.assertFalse(Order.objects.exists())


@override_settings(**PAYPAL_SETTINGS)
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FulfillmentTests(FixtureMixin, TestCase):
    def paid_order(self, send_emails=False):
        order = self.approved()
        if send_emails:
            with self.captureOnCommitCallbacks(execute=True):
                services.settle_order(order.pk, completed(order))
        else:
            services.settle_order(order.pk, completed(order))
        return Order.objects.get(pk=order.pk)

    def test_paid_order_sends_customer_and_owner_email_once(self):
        self.store.contact_email = 'owner@example.com'
        self.store.save()
        order = self.paid_order(send_emails=True)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(order.notifications.filter(status=NotificationLog.Status.SENT).count(), 2)
        with self.captureOnCommitCallbacks(execute=True):
            services.settle_order(order.pk, completed(order))
        self.assertEqual(len(mail.outbox), 2)

    def test_fulfillment_sequence_saves_tracking_and_emails_customer(self):
        order = self.paid_order()
        owner = get_user_model().objects.create_superuser('shipper', 'shipper@example.com', 'test-password')
        fulfillment.update_fulfillment(order.pk, 'pack', actor=owner)
        with self.captureOnCommitCallbacks(execute=True):
            fulfillment.update_fulfillment(order.pk, 'ship', {
                'carrier': Order.Carrier.USPS, 'carrier_other': '',
                'tracking_number': '9400111899560000000000',
                'actual_shipping_cost': Decimal('12.40'),
            }, actor=owner)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.SHIPPED)
        self.assertEqual(self.product.status, Product.Status.SHIPPED)
        self.assertIn('tools.usps.com', order.tracking_url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(order.audit.last().actor, owner)
        fulfillment.update_fulfillment(order.pk, 'deliver', actor=owner)
        fulfillment.update_fulfillment(order.pk, 'complete', actor=owner)
        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.COMPLETED)

    def test_owner_listing_to_shipped_sale_stays_visible(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        media_override = override_settings(MEDIA_ROOT=media.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        owner = get_user_model().objects.create_superuser(
            'listing-owner', 'listing-owner@example.com', 'test-password')
        self.client.force_login(owner)
        picture = BytesIO()
        Image.new('RGB', (24, 24), 'blue').save(picture, format='PNG')
        upload = SimpleUploadedFile('blue-wrap.png', picture.getvalue(), content_type='image/png')
        response = self.client.post(reverse('owner_rod_add'), {
            'name': 'Blue plunking rod', 'design_name': 'Blue diamond',
            'price': '475.00', 'quantity': '1', 'listing_state': 'available',
            'description': 'Hand wrapped blue and silver diamond pattern.',
            'length': '10 ft', 'power': 'Medium Heavy', 'action': 'Moderate Fast',
            'primary_fishing_style': 'Plunking', 'photos': [upload],
        })
        rod = Product.objects.get(name='Blue plunking rod')
        self.assertRedirects(response, reverse('owner_rod_edit', args=[rod.pk]))
        self.assertContains(self.client.get('/shop/'), rod.name)

        buyer = Client()
        buyer.post(reverse('cart_change', args=[rod.pk]), {'quantity': 1})
        checkout = buyer.get(reverse('checkout'))
        self.assertEqual(checkout.status_code, 200)
        response = buyer.post(reverse('checkout'), {
            'name': 'Test Buyer', 'email': 'buyer@example.com', **ADDRESS,
            'quote_token': checkout.context['summary']['token'],
            'checkout_key': buyer.session['checkout_key'],
        })
        order = Order.objects.get(items__product=rod)
        self.assertRedirects(response, order.get_absolute_url())
        order.status = Order.Status.APPROVAL
        order.paypal_order_id = 'PAYPAL-LISTING-FLOW'
        order.save(update_fields=['status', 'paypal_order_id'])
        services.settle_order(order.pk, completed(order))
        rod.refresh_from_db()
        self.assertEqual(rod.status, Product.Status.SOLD)
        self.assertNotContains(self.client.get('/shop/'), rod.name)

        fulfillment.update_fulfillment(order.pk, 'pack', actor=owner)
        fulfillment.update_fulfillment(order.pk, 'ship', {
            'carrier': Order.Carrier.USPS,
            'carrier_other': '',
            'tracking_number': '9400111899560000000000',
            'actual_shipping_cost': Decimal('12.40'),
        }, actor=owner)
        rod.refresh_from_db()
        self.assertEqual(rod.status, Product.Status.SHIPPED)
        self.assertContains(self.client.get(reverse('owner_dashboard')), rod.name)
        inventory = self.client.get(reverse('owner_inventory'))
        self.assertContains(inventory, rod.name)
        self.assertContains(inventory, 'Shipped')

    def test_fulfillment_rejects_skipped_steps_and_refunded_order(self):
        order = self.paid_order()
        with self.assertRaises(CheckoutError):
            fulfillment.update_fulfillment(order.pk, 'ship', {'carrier':'usps','tracking_number':'TRACK123'})
        Order.objects.filter(pk=order.pk).update(status=Order.Status.REFUNDED)
        with self.assertRaises(CheckoutError):
            fulfillment.update_fulfillment(order.pk, 'pack')

    def test_owner_dashboard_and_actions_require_staff(self):
        order = self.paid_order()
        self.assertEqual(self.client.get(reverse('owner_dashboard')).status_code, 302)
        owner = get_user_model().objects.create_superuser('owner2', 'owner2@example.com', 'test-password')
        self.client.force_login(owner)
        response = self.client.get(reverse('owner_dashboard'))
        self.assertContains(response, order.number)
        self.assertContains(response, 'Needs packing')
        response = self.client.post(reverse('owner_fulfillment', args=[order.pk]), {'action':'pack'})
        self.assertRedirects(response, reverse('owner_order', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.PACKED)

    def test_receipt_is_private_to_browser_or_staff(self):
        order = self.paid_order()
        self.assertEqual(self.client.get(reverse('receipt', args=[order.pk])).status_code, 404)
        session = self.client.session
        session['checkout_owner'] = self.owner
        session.save()
        response = self.client.get(reverse('receipt', args=[order.pk]))
        self.assertContains(response, order.capture_id)
        other = Client()
        staff = get_user_model().objects.create_superuser('receipt-owner', 'receipt@example.com', 'test-password')
        other.force_login(staff)
        self.assertEqual(other.get(reverse('owner_receipt', args=[order.pk])).status_code, 200)

    def test_email_failure_is_logged_without_exposing_message_body(self):
        order = self.paid_order()
        with patch('commerce.notifications.EmailMultiAlternatives.send', side_effect=RuntimeError('private provider details')):
            self.assertFalse(notifications.send_customer_confirmation(order.pk))
        log = order.notifications.get(kind=NotificationLog.Kind.CUSTOMER_CONFIRMATION)
        self.assertEqual(log.status, NotificationLog.Status.FAILED)
        self.assertEqual(log.error_code, 'RuntimeError')
        self.assertFalse(hasattr(log, 'body'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_quantity, 0)

    def test_duplicate_checkout_is_idempotent(self):
        order = self.reserve()
        duplicate = services.reserve_order(self.cart,self.owner,self.key,'old-token',CUSTOMER)
        self.assertEqual(order.pk, duplicate.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_quantity,1)

    def test_us_address_and_zip_validation(self):
        payload = {'name':'Buyer','email':'buyer@example.com',**ADDRESS,'quote_token':'test','checkout_key':str(uuid.uuid4())}
        self.assertTrue(CheckoutForm(payload).is_valid())
        for changes in [{'country_code':'CA'}, {'admin_area_1':'PR'}, {'postal_code':'not-a-zip'}]:
            self.assertFalse(CheckoutForm(payload | changes).is_valid())

    def test_forged_prices_invalid_quantities_and_sold_cart(self):
        url = reverse('cart_change',args=[self.product.pk])
        self.assertEqual(self.client.post(url,{'quantity':1,'price':'0.01'}).status_code,400)
        for quantity in [-1,2,21,'oops']:
            self.client.post(url,{'quantity':quantity})
            self.assertFalse(self.client.session.get('cart'))
        self.product.status='sold'
        self.product.save()
        self.client.post(url,{'quantity':1})
        self.assertFalse(self.client.session.get('cart'))

    def test_guest_order_authorization_and_redirect_not_payment(self):
        order = self.approved()
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code,404)
        self.assertEqual(self.client.post(reverse('confirm_payment',args=[order.pk])).status_code,404)
        session=self.client.session
        session['checkout_owner']=self.owner
        session.save()
        response=self.client.get(order.get_absolute_url()+'?token=PAYPAL123&PayerID=ATTACKER')
        self.assertEqual(response.status_code,200)
        order.refresh_from_db()
        self.assertEqual(order.status,Order.Status.APPROVAL)
        self.assertIsNone(order.capture_id)
        self.assertEqual(self.client.get(reverse('confirm_payment',args=[order.pk])).status_code,405)

    def test_csrf_protects_cart_and_capture(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('cart_change',args=[self.product.pk]),{'quantity':1}).status_code,403)
        order=self.approved()
        self.assertEqual(client.post(reverse('confirm_payment',args=[order.pk])).status_code,403)

    def test_capture_verification_sells_once_and_preserves_gallery(self):
        order=self.approved()
        data=completed(order)
        services.settle_order(order.pk,data)
        services.settle_order(order.pk,data)
        self.product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((self.product.quantity,self.product.reserved_quantity),(0,0))
        self.assertTrue(self.product.show_in_gallery)
        self.assertEqual(order.status,Order.Status.PAID)
        self.assertEqual(order.audit.filter(action__contains='Payment verified').count(),1)

    def test_wrong_currency_amount_merchant_and_address_do_not_sell(self):
        order=self.approved()
        for key in ['amount','currency','merchant','address','custom_id','capture_amount']:
            Order.objects.filter(pk=order.pk).update(status=Order.Status.APPROVAL)
            data=completed(order)
            unit=data['purchase_units'][0]
            if key=='amount': unit['amount']['value']='0.01'
            if key=='currency': unit['amount']['currency_code']='EUR'
            if key=='merchant': unit['payee']['merchant_id']='WRONG'
            if key=='address': unit['shipping']['address']['country_code']='CA'
            if key=='custom_id': unit['custom_id']=str(uuid.uuid4())
            if key=='capture_amount': unit['payments']['captures'][0]['amount']['value']='0.01'
            result=services.settle_order(order.pk,data)
            self.assertEqual(result.status,Order.Status.REVIEW)
            self.product.refresh_from_db()
            self.assertEqual((self.product.quantity,self.product.reserved_quantity),(1,1))

    def test_bad_remote_order_never_reaches_capture(self):
        order=self.approved()
        remote=completed(order)
        remote['status']='APPROVED'
        remote['purchase_units'][0]['amount']['value']='900.00'
        with patch('commerce.paypal.get_order',return_value=remote), patch('commerce.paypal.capture') as capture:
            with self.assertRaises(CheckoutError):
                services.capture_payment(order.pk,self.owner)
            capture.assert_not_called()

    def test_lost_capture_response_keeps_hold_and_retry_reconciles(self):
        order=self.approved()
        remote=completed(order)
        approved=copy.deepcopy(remote)
        approved['status']='APPROVED'
        with patch('commerce.paypal.get_order',return_value=approved), patch('commerce.paypal.capture',side_effect=paypal.PayPalError('timeout')):
            with self.assertRaises(paypal.PayPalError):
                services.capture_payment(order.pk,self.owner)
        Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now()-timedelta(hours=1))
        self.assertEqual(services.expire_reservations(),0)
        with self.assertRaises(CheckoutError):
            services.cancel_order(order.pk,self.owner)
        with patch('commerce.paypal.get_order',return_value=remote), patch('commerce.paypal.capture') as capture:
            result=services.capture_payment(order.pk,self.owner)
            self.assertEqual(result.status,Order.Status.PAID)
            capture.assert_not_called()

    def test_cancel_and_expiry_release_exactly_once(self):
        order=self.reserve()
        services.cancel_order(order.pk,self.owner)
        with self.assertRaises(CheckoutError): services.cancel_order(order.pk,self.owner)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_quantity,0)
        self.key=uuid.uuid4()
        order=self.reserve()
        Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now()-timedelta(minutes=1))
        self.assertEqual(services.expire_reservations(),1)
        self.assertEqual(services.expire_reservations(),0)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity,self.product.reserved_quantity),(1,0))

    def test_late_payment_after_release_requires_review_not_sale(self):
        order=self.approved()
        services.cancel_order(order.pk,self.owner)
        result=services.settle_order(order.pk,completed(order))
        self.assertEqual(result.status,Order.Status.REVIEW)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity,self.product.reserved_quantity),(1,0))

    def test_webhooks_verify_before_processing_and_dedupe(self):
        order=self.approved()
        event={'id':'EVENT-001','event_type':'PAYMENT.CAPTURE.COMPLETED',
               'resource':{'id':'CAPTURE123','supplementary_data':{'related_ids':{'order_id':'PAYPAL123'}}}}
        url=reverse('paypal_webhook')
        with patch('commerce.paypal.verify_webhook',return_value=False):
            self.assertEqual(self.client.post(url,json.dumps(event),content_type='application/json').status_code,400)
        self.assertEqual(PaymentEvent.objects.count(),0)
        with patch('commerce.paypal.verify_webhook',return_value=True), patch('commerce.paypal.get_order',return_value=completed(order)) as read:
            for _ in range(2): self.assertEqual(self.client.post(url,json.dumps(event),content_type='application/json').status_code,200)
            self.assertEqual(read.call_count,1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity,0)
        self.assertEqual(PaymentEvent.objects.count(),1)

    def test_pending_webhook_preserves_inventory_hold(self):
        order=self.approved()
        event={'id':'EVENT-PENDING','event_type':'PAYMENT.CAPTURE.PENDING',
               'resource':{'id':'CAPTURE123','supplementary_data':{'related_ids':{'order_id':'PAYPAL123'}}}}
        remote=completed(order)
        remote['purchase_units'][0]['payments']['captures'][0]['status']='PENDING'
        with patch('commerce.paypal.verify_webhook',return_value=True), patch('commerce.paypal.get_order',return_value=remote):
            response=self.client.post(reverse('paypal_webhook'),json.dumps(event),content_type='application/json')
        self.assertEqual(response.status_code,200)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status,Order.Status.CAPTURING)
        self.assertTrue(order.reservation_active)
        self.assertEqual((self.product.quantity,self.product.reserved_quantity),(1,1))

    def test_refund_state_is_idempotent_and_does_not_restock(self):
        order=self.approved()
        services.settle_order(order.pk,completed(order))
        refund={'id':'REFUND123','status':'COMPLETED','amount':{'currency_code':'USD','value':'100.00'},
                'links':[{'rel':'up','href':'https://api-m.sandbox.paypal.com/v2/payments/captures/CAPTURE123'}]}
        services.record_refund(order.pk,refund)
        result=services.record_refund(order.pk,refund)
        self.assertEqual(result.refunded_amount,Decimal('100.00'))
        self.assertEqual(result.status,Order.Status.PARTIAL_REFUND)
        self.assertEqual(Refund.objects.count(),1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity,0)

    def test_owner_order_record_readonly(self):
        order=self.reserve()
        owner=get_user_model().objects.create_superuser('test-owner','owner@example.com','test-password')
        self.client.force_login(owner)
        response=self.client.get(reverse('admin:commerce_order_change',args=[order.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,order.number)
        self.assertNotContains(response,'name="status"')

    def test_payment_create_uses_stable_key_and_snapshot(self):
        order=self.reserve()
        response={'id':'PAYPAL123','links':[{'rel':'payer-action','href':'https://www.sandbox.paypal.com/checkoutnow?token=PAYPAL123'}]}
        with patch('commerce.paypal.api',return_value=response) as api:
            paypal.create(order)
            method,path,payload,key=api.call_args.args
            self.assertEqual(key,f'{order.pk}-c')
            self.assertEqual(payload['purchase_units'][0]['amount']['value'],'448.00')
            self.assertEqual(payload['purchase_units'][0]['shipping']['address']['country_code'],'US')
            self.assertEqual(payload['payment_source']['paypal']['experience_context']['shipping_preference'],'SET_PROVIDED_ADDRESS')
        with self.assertRaises(paypal.PayPalError):
            paypal.approval_link({'links':[{'rel':'approve','href':'https://attacker.example/'}]})

    def test_checkout_screen_and_disabled_payment(self):
        self.client.post(reverse('cart_change',args=[self.product.pk]),{'quantity':1})
        response=self.client.get(reverse('checkout'))
        self.assertContains(response,'$448.00')
        self.store.checkout_enabled=False
        self.store.save()
        response=self.client.get(reverse('checkout'))
        self.assertContains(response,'Payment checkout is not open yet')
        self.assertFalse(Order.objects.exists())


@override_settings(**PAYPAL_SETTINGS)
class ReportingTests(FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser('report-owner', 'reports@example.com', 'test-password')
        self.client.force_login(self.user)

    def paid_order(self):
        self.product.build_cost = Decimal('100.00')
        self.product.save(update_fields=['build_cost'])
        order = self.approved()
        services.settle_order(order.pk, completed(order))
        return Order.objects.get(pk=order.pk)

    def test_build_cost_snapshot_and_report_math(self):
        order = self.paid_order()
        self.assertEqual(order.items.get().unit_cost, Decimal('100.00'))
        self.product.build_cost = Decimal('250.00')
        self.product.save(update_fields=['build_cost'])
        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.PARTIAL_REFUND, refunded_amount=Decimal('50.00'),
            actual_shipping_cost=Decimal('12.40'))
        today = timezone.localdate()
        Refund.objects.create(order=order, paypal_id='REPORT-REFUND', amount=Decimal('50.00'),
            status='COMPLETED', completed_at=timezone.now())
        BusinessExpense.objects.create(incurred_on=today, category='payment_fee',
            description='PayPal fee', amount=Decimal('10.00'), created_by=self.user)
        BusinessExpense.objects.create(incurred_on=today, category='supplies',
            description='Packing supplies', amount=Decimal('20.00'), created_by=self.user)
        report = reporting.report_data(today, today)
        self.assertEqual(report['gross_sales'], Decimal('400.00'))
        self.assertEqual(report['tax_collected'], Decimal('33.00'))
        self.assertEqual(report['product_cost'], Decimal('100.00'))
        self.assertEqual(report['shipping_expense'], Decimal('12.40'))
        self.assertEqual(report['estimated_profit'], Decimal('222.60'))
        self.assertEqual(report['missing_cost_lines'], 0)

    def test_reports_require_staff_and_render_selected_period(self):
        order = self.paid_order()
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse('owner_reports')).status_code, 302)
        today = timezone.localdate().isoformat()
        response = self.client.get(reverse('owner_reports'), {'start': today, 'end': today})
        self.assertContains(response, 'Gross product sales')
        self.assertContains(response, '$400.00')
        self.assertContains(response, order.number, count=0)
        self.assertContains(response, 'sold rod line without a saved build cost', count=0)

    def test_refunds_are_reported_on_completion_date(self):
        order = self.paid_order()
        old_date = timezone.now() - timedelta(days=60)
        Order.objects.filter(pk=order.pk).update(paid_at=old_date, status=Order.Status.PARTIAL_REFUND,
                                                  refunded_amount=Decimal('50.00'))
        Refund.objects.create(order=order, paypal_id='LATER-REFUND', amount=Decimal('50.00'),
            status='COMPLETED', completed_at=timezone.now())
        today = timezone.localdate()
        report = reporting.report_data(today, today)
        self.assertEqual(report['gross_sales'], Decimal('0.00'))
        self.assertEqual(report['refunds'], Decimal('50.00'))
        self.assertEqual(report['daily'][0]['orders'], 0)
        self.assertEqual(report['daily'][0]['refunds'], Decimal('50.00'))

    def test_expense_entry_records_owner_and_returns_to_period(self):
        today = timezone.localdate().isoformat()
        response = self.client.post(reverse('owner_expense_add'), {
            'incurred_on': today, 'category': 'software', 'description': 'Store service',
            'amount': '19.99', 'reference': 'INV-1', 'period_start': today, 'period_end': today,
        })
        self.assertEqual(response.status_code, 302)
        expense = BusinessExpense.objects.get()
        self.assertEqual(expense.created_by, self.user)
        self.assertEqual(expense.amount, Decimal('19.99'))
        self.assertIn('start=', response.url)

    def test_csv_exports_are_private_date_filtered_and_formula_safe(self):
        order = self.paid_order()
        Order.objects.filter(pk=order.pk).update(name='=HYPERLINK("bad")')
        today = timezone.localdate().isoformat()
        url = reverse('owner_report_export', args=['orders'])
        anonymous = Client()
        self.assertEqual(anonymous.get(url, {'start': today, 'end': today}).status_code, 302)
        response = self.client.get(url, {'start': today, 'end': today})
        content = response.content.decode('utf-8-sig')
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=HYPERLINK", content)
        self.assertIn(order.number, content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get(url, {'start':'2026-09-22','end':'2025-01-01'}).status_code, 400)

    def test_inventory_and_accounting_exports_have_operational_fields(self):
        self.paid_order()
        today = timezone.localdate().isoformat()
        inventory = self.client.get(reverse('owner_report_export', args=['inventory']),
            {'start': today, 'end': today}).content.decode('utf-8-sig')
        accounting = self.client.get(reverse('owner_report_export', args=['accounting']),
            {'start': today, 'end': today}).content.decode('utf-8-sig')
        self.assertIn('Build cost', inventory)
        self.assertIn('Estimated gross profit before tax', accounting)
        self.assertIn('Tax originally collected', accounting)


@override_settings(**PAYPAL_SETTINGS)
class ConcurrencyTests(FixtureMixin, TransactionTestCase):
    def test_two_buyers_cannot_reserve_the_same_rod(self):
        self.assertEqual(connection.vendor,'postgresql','Run commerce tests on PostgreSQL.')
        token=quote(self.cart)['token']
        barrier=Barrier(2)
        def buyer():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                services.reserve_order(self.cart,uuid.uuid4().hex,uuid.uuid4(),token,CUSTOMER)
                return 'reserved'
            except CheckoutError:
                return 'unavailable'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: buyer(), range(2)))
        self.assertCountEqual(results,['reserved','unavailable'])
        self.assertEqual(Order.objects.count(),1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_quantity,1)

    def test_duplicate_payment_notifications_only_sell_once(self):
        order=self.approved()
        data=completed(order)
        barrier=Barrier(2)
        def notify():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return services.settle_order(order.pk,data).status
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: notify(),range(2)))
        self.assertEqual(results,['paid','paid'])
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity,self.product.reserved_quantity),(0,0))
