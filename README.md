# CaCaCa custom fishing rods

Working foundation for the owner-hosted Django storefront. Branding and sales decisions are recorded in `BUILD-DECISIONS.md`.

## Implemented

- Responsive branded homepage, catalog, salmon category, product detail, gallery, about, FAQ, contact, and returns pages.
- A separate tackle collection with three individually listed pink squid lures from the supplied photos, plus owner controls for tackle price, stock, photos, and publication.
- Product and one-to-one rod specifications, multiple species, ordered photo uploads, publication controls, and one-of-one quantity validation.
- Search, build-type/species filters, sorting, and pagination.
- Django administration for rods, photos, species, store settings, and custom inquiries.
- Rod questions and special requests saved in the database with a simple owner inbox. Changing a message status does not send a reply.
- Owner-editable 8.25% tax setting, $15 flat shipping default, USD/US defaults, and draft/published return-policy content.
- Cart, guest checkout, server-calculated totals, US address collection, immutable order-price snapshots, and private guest order links.
- PostgreSQL inventory reservations, 20-minute abandoned-checkout expiry, one-of-one double-sale protection, and sold-rod gallery transitions.
- PayPal sandbox Orders v2 integration with server-created orders, approval, server capture verification, verified/idempotent webhooks, reconciliation support, and refund-state tracking.
- Environment-based configuration, PostgreSQL local development, migrations, and a database health endpoint.
- Phone-friendly owner dashboard with a customer-message inbox, paid-order queue, packing and shipping workflow, official carrier tracking links, fulfillment history, and actual postage recording.
- Installable CaCaCa Owner PWA with a phone action bar for Home, Add rod, Rods, Tackle, Shipping, and Messages. The rod and tackle forms accept a phone-camera photo or multiple gallery photos.
- Private customer receipts, printable owner receipts, order-confirmation/owner-notice/shipping emails, retry controls, and delivery-attempt logs that omit message bodies.
- Date-filtered owner reports for product sales, refunds, originally collected tax, shipping, fulfillment, build costs, manual expenses, inventory, and custom requests, with protected CSV exports.
- Search preview metadata, canonical links, a public-product XML sitemap, and a robots file that keeps staging out of search engines.

## Run locally on Windows

Python 3.12 or newer is required. The current workspace already has a `.venv`.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe scripts/setup_local_database.py
docker compose --env-file .env.local up -d db
./scripts/dev.ps1
```

Open http://127.0.0.1:8000 and use `/owner/` for the day-to-day owner dashboard. The owner pages provide quick links to add rods, manage inventory, check shipping, and edit sales settings. Use `/admin/` only for advanced settings. The generated `.env.local` is ignored by Git and loads only when `CACACA_LOCAL_ENV=true`. Production settings refuse to start without a secret key and PostgreSQL URL. `.env.example` documents all supported variables.

The original SQLite preview was migrated to PostgreSQL and retained as a private local backup under `artifacts/`. PostgreSQL is required for checkout because its row locks protect one-of-one inventory.

## Add your first rod

The storefront now prioritizes finished, hand-wrapped rods. Custom requests remain a secondary footer/FAQ option. The homepage automatically features a listed rod photo when available; until then it uses the supplied logo.

1. Sign in and open `/owner/rods/add/` from the owner dashboard.
2. Enter the rod name, design name, description, selling price, and quantity. Add the rod length, power, and action using details confirmed by the builder. Fishing style and line rating can also be entered.
3. On a phone, use **Take a photo** to open the camera or **Rod photos** to choose several gallery pictures. JPEG, PNG, and WebP photos may be up to 10 MB each. Choose a clear overall view first, then close-ups of the hand wrap. The first new photo becomes the cover; the cover can be changed later.
4. Choose Private draft to hide an unfinished rod, or Public preview to show its photos and description without a purchase button. A public preview needs a photo and description, but may wait for its price and builder-confirmed specifications. The dashboard shows what each preview still needs before sale.
5. Choose In the shop when the price, description, photo, length, power, and action are ready. Available rods and public previews both appear in the collection with clear labels; only available rods can be added to the cart.
6. Use `/owner/rods/` to adjust price or quantity and to see available rods, public previews, private drafts, and sold rods. Paid rods stay in Sold rods after shipment. `/owner/shipping/` shows orders that need packing or shipping.

The full `/admin/` remains available for advanced fields such as private build cost. Each order saves the build cost known at checkout so later edits do not rewrite historical profit estimates.

## Manage tackle

The customer-facing `/tackle/` page and homepage section list the three pink squid lures separately at **$10 each**, with one unit initially available for each. The two supplied group photos appear on the listings; descriptions identify which lure is for sale and clarify that other pictured lures and loose hooks are not included. The owner can replace these with solo product photos later.

Use `/owner/tackle/` to edit each item's price, quantity, description, cover photo, and visibility, or `/owner/tackle/add/` to list another item from a phone. Tackle uses the same cart, tax, flat per-order shipping, order, and fulfillment workflow as rods. Online payment remains unavailable until the PayPal setup below is complete; customers can use the item-specific question link meanwhile.

Store settings contain contact email, builder story, tax rate, and policy text. Return text stays private until the publication checkbox is enabled. Settings changes use Django's admin audit history.

## Commerce and PayPal sandbox

Checkout currently uses an owner-editable flat **$15 shipping charge per order** and **8.25% tax**. Shipping is limited to the 50 states and Washington, DC. Whether shipping itself is taxable is also editable. Each order stores the exact product prices, tax rate, shipping rule, and totals that applied when the customer checked out.

To enable sandbox payment testing:

1. Follow PayPal's [REST API getting-started guide](https://developer.paypal.com/api/get-started/) and [sandbox account guide](https://developer.paypal.com/sandbox-testing/accounts). Use a sandbox business account for the store and a sandbox personal account for the test buyer. Select or create a sandbox REST app.
2. Give the app a publicly reachable HTTPS webhook URL ending in `/payments/paypal/webhook/`; PayPal's [webhook guide](https://developer.paypal.com/api/rest/webhooks/rest/) explains the resulting webhook ID. Set `PUBLIC_BASE_URL` to the HTTPS test site's base URL. Localhost cannot receive PayPal webhooks.
3. Add the sandbox app's `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`, and `PAYPAL_MERCHANT_ID` to the private `.env.local` file, then restart the server. Never paste a client secret into chat, a website form, or source code. Do not use live credentials here.
4. Open `/owner/settings/`, check shipping and tax, enter the owner order-alert email, then turn on **PayPal sandbox checkout for testing**. The form refuses to enable it until PostgreSQL, shipping, and all sandbox credentials are present.
5. In a separate staging copy with test inventory, use the sandbox personal account to test checkout, verified payment, the owner shipping board, receipt, order email, and tracking email. Test webhook delivery and a refund before moving toward live payments. Do not use a real one-of-one rod for this rehearsal because a successful test marks it sold.

Checkout remains disabled unless PostgreSQL, the confirmed shipping setting, all sandbox credentials, and the owner switch are present. The owner dashboard shows the effective status, so a switch left on after credentials are removed appears as **Needs setup**. The application is pinned to PayPal's sandbox API; production payments require a separate launch review and configuration change.

Customers can add a published, available rod to the cart and review server-calculated totals without PayPal credentials. A rod is reserved only after valid checkout details are submitted and payment setup begins. Abandoned pre-payment holds expire after 20 minutes. Once capture starts, an uncertain response keeps stock held until PayPal is reconciled; it is never released blindly.

## Fulfillment and email

Verified payments automatically enter the owner dashboard at **Needs packing**. The owner opens an order, marks it packed, then records USPS, UPS, FedEx, or another carrier plus the tracking number and optional actual shipping cost. Marking it shipped changes the rod lifecycle to Shipped and sends the customer a tracking email. Delivered and Completed are explicit later steps. Each action records its time and owner account.

## Install the owner app on a phone

After the site is hosted at a public **HTTPS** address, open `/owner/` on the owner's phone and sign in. On Android Chrome, use **Install app** if shown, or the browser's install option. On iPhone, open the page in Safari, tap **Share**, then **Add to Home Screen**. The installed app opens the owner dashboard and provides a bottom action bar for rods, photo uploads, inventory, shipping, and messages. The local `127.0.0.1` address is only available on this computer and cannot be installed from the owner's phone.

The app needs an internet connection and staff sign-in. It intentionally does not store rod photos, customer addresses, orders, or form submissions for offline use. The owner buys the shipping label outside the site, then opens the paid order, marks it packed, enters carrier and tracking, and marks it shipped; that last action sends the customer shipping notice. No shipping label is bought or printed by the app.

The customer’s private order page shows fulfillment progress and provides a printable receipt after verified payment. The owner can open the same receipt from the order workspace. Customer confirmations, owner new-order notices, and shipping confirmations are recorded as sent, failed, or skipped; email bodies and provider error text are not stored. A failed delivery does not undo payment or inventory changes and can be retried from the order workspace.

Local development prints email to the terminal. For hosted email, set `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `EMAIL_USE_TLS`. Set the Store settings contact email to receive new-order notices. Test the chosen provider before enabling live checkout.

## Customer messages

Open `/owner/messages/` to review questions about specific rods, general questions, and special build requests. New messages appear on the owner dashboard. The owner can open a message, see the customer's contact details and any linked rod, and mark it reviewed, contacted, or closed. A status change is only an internal note; it never sends a customer email or text. Customer contact information is available only to signed-in staff.

## Search visibility

Public rod pages have individual titles, descriptions, canonical links, and share-preview images. `/sitemap.xml` lists public pages and rods, including public previews and sold rods that remain visible; private drafts and customer/owner pages are excluded. Filtered collection URLs are marked not to index so searches do not create duplicate pages.

`SITE_INDEXABLE=false` is the safe default for local development and staging. Set `PUBLIC_BASE_URL` to the final public HTTPS domain and change `SITE_INDEXABLE=true` only when the site is ready for search engines. The robots file then advertises the sitemap and excludes owner, cart, checkout, order, payment, and confirmation paths. This is a search-engine instruction, not access control for private pages.

## Business reports and exports

Open `/owner/reports/` to choose a date period and review gross product sales, shipping collected, originally collected tax, refunds, saved product build costs, recorded shipping expense, manually entered payment fees and other expenses, and estimated gross profit. The same workspace shows fulfillment counts, daily sales, current inventory value, cost completeness, and custom-request status.

Build cost is a private rod field. It is copied onto the order item during checkout, so changing the current rod later does not rewrite historical estimates. Actual label cost is saved during fulfillment. Payment fees, additional shipping costs, supplies, marketing, software, and other expenses can be entered manually from the report page.

Owner-only CSV downloads cover the accounting summary, orders, inventory, requests, and expenses. Exports use the selected date range and neutralize spreadsheet formulas in text fields. Sales use the verified-payment date and refunds use the date they complete. The report is operational: tax is excluded from estimated profit, and refunds are not allocated between product, shipping, and tax. Use provider and professional accounting records for filings.

Run these regularly in a deployed environment:

```powershell
.venv/Scripts/python.exe manage.py expire_reservations
.venv/Scripts/python.exe manage.py reconcile_payments
```

`expire_reservations` can run every minute. `reconcile_payments` should run periodically and after payment-provider incidents. Neither command charges a new payment.

## Validation

```powershell
$env:CACACA_LOCAL_ENV = 'true'
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py test
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
```

## Owner review site (staging)

Keep the review site separate from the local store and any eventual public launch. For a short, view-only owner review, a free web service and temporary PostgreSQL database on Render are enough. Bundle the existing catalog photos with the deployment so they reappear after the free web service sleeps or restarts. Do not rely on this temporary setup for owner uploads or changes that must survive the move to paid hosting. Do not upload `.env.local`, `db.sqlite3`, `artifacts/`, `.venv/`, `media/`, or `staticfiles/` as private local data. The original `Pictures/` directory is source material for the sample rod and tackle listings. Create a new owner login on staging instead of copying local accounts or customer data.

Use `DJANGO_DEBUG=false`, a new `DJANGO_SECRET_KEY`, the staging HTTPS hostname in `DJANGO_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, `PUBLIC_BASE_URL` set to that HTTPS address, and `SITE_INDEXABLE=false`. Keep PayPal checkout disabled for this review. Configure a production web server, collect static files, run migrations, and verify the catalog photos and owner sign-in before inviting the owner. Render's free PostgreSQL database expires after 30 days, and its free web service loses runtime uploads on restart; the paid host will need persistent photo storage before the owner uses the site to manage inventory.

The temporary review site is hosted on Render at `https://cacaca-nywx.onrender.com/`. Add persistent photo storage when moving to paid hosting. The local development server is separate from this hosted review site.

For the temporary review copy, set `CACACA_REVIEW_MODE=true` in Render and run `python manage.py seed_review_inventory` after migrations. This command creates four rod listings (one priced rod and three public design previews) and the three $10 lure listings without touching existing listings on later runs. It links their images to the original photos bundled in `Pictures/`, which `collectstatic` publishes as `review-media/`. Those bundled images survive free-service restarts. This mode is for viewing only: newly uploaded photos are not persistent on Render Free, and staging inventory is separate from the local store.

To let the owner inspect the private workspace on this review copy, generate a long random one-time setup code and put only its SHA-256 hex digest in Render as `CACACA_OWNER_SETUP_CODE_HASH`. Give the code directly to the owner, who opens `/owner/setup/` and chooses their own username, email, and password. The setup page disappears after the first staff account is created. Review mode permits sign-in and password changes but rejects inventory, shipping, and admin edits; the owner pages show a review banner. Do not use this bootstrap setting on the paid/live store. Remove the setup-code environment variable after the account is claimed.

The Render web service uses **Build Command** `pip install -r requirements.txt && python manage.py collectstatic --noinput` and **Start Command** `python manage.py migrate --noinput && python manage.py seed_review_inventory && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`. It needs `DJANGO_SECRET_KEY` and `DATABASE_URL` (the internal URL from its separate Render PostgreSQL service). Render supplies the service hostname automatically; the app adds it to allowed hosts and trusted HTTPS origins and uses it as the public base URL by default. `DJANGO_DEBUG=false` and `SITE_INDEXABLE=false` are already the defaults. Do not add secrets to GitHub.

## Next milestones / launch blockers

This is still a development store, not a production launch.

- Real PayPal sandbox credentials and end-to-end sandbox buyer testing. Checkout is disabled until these are supplied and the owner enables it.
- Optional customer accounts/order history and privacy-conscious traffic analytics.
- Stronger admin authentication and persistent login throttling, public-form abuse protection, privacy policy, deployment-specific security review, and media serving configuration.
- Owner-approved final search descriptions, analytics, PostgreSQL concurrency tests, and production deployment testing.
- Owner-approved copy, product information/photos, shipping rules, and return terms.
- Production deployment, monitoring, backups, tested restore, and clean-environment handoff verification.

The commerce test suite runs against PostgreSQL and includes a simulated sale from the quick owner listing form through guest checkout, verified payment, packing, and shipping. It also covers simultaneous reservations, duplicate notifications, lost capture responses, invalid totals/currency/merchant/address data, CSRF, guest-order and receipt authorization, reservation expiry, refunds, email failure isolation, and owner access. Refunds never automatically relist a rod; the owner must inspect its condition first.

The original supplied brand image remains in `static/brand/cacaca-logo.png`. The website uses `static/brand/cacaca-logo-accented.png`, with the display mark written **CáCáCá** so each “cá” carries the Vietnamese acute accent for “fish.”
