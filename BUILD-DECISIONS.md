# CaCaCa build decisions

## Updated storefront priority

The primary business is selling finished rods already customized with hand-wrapped artwork, including US flag designs, birds, and other patterns. Photos of the actual completed rod, wrap design, price, and availability lead the storefront. Custom commissions are a secondary option only.

The homepage and collection show available, in-stock rods plus owner-approved public design previews. Previews show real rod photos and descriptions while their price or builder-confirmed specifications are completed; they have no cart button and cannot be reserved. Private drafts remain hidden. Made-to-order and custom-order products do not lead the collection. Sold work can remain in the gallery.

These decisions supplement `Custom-Fishing-Rod-Website-Build-Spec-v2.md`. The user's confirmed choices below take precedence over conflicting wording in that specification.

## Confirmed by the owner

- Business name: **CaCaCa** (preserve this capitalization).
- Fishing terminology: **plunking**. Replace the specification's references to “splunking” in implementation and customer-facing content.
- Currency: **USD**.
- Shipping destinations: **United States only**.
- Initial configured tax rate: **8.25%**, editable by an authorized administrator. This is an owner-supplied setting, not a determination of jurisdiction-specific tax obligations.
- Shipping: **$15 flat rate per order** by default, editable by an authorized administrator.
- Supplied brand image: `ChatGPT Image Sep 21, 2026, 08_50_59 PM.png`.
- Display logo: **CáCáCá**, with an acute accent over each “a” to reflect Vietnamese “cá” (fish). The registered store-name setting remains CaCaCa unless the owner changes it separately.
- Return-policy direction: a standard policy; exact terms remain unspecified.

## Implementation preparation

- Retain the specification's portable Django and PostgreSQL architecture.
- Use the supplied logo and draw the visual palette from its navy, forest green, teal, silver, and white.
- Store the tax rate as a decimal setting and calculate checkout totals on the server. Preserve the applied tax rate and charged amounts on each order so later settings changes do not alter historical orders.
- Enforce US shipping eligibility on the server. The first checkout version accepts the 50 states and Washington, DC; territories and military addresses still need a business decision.
- Make return-policy content editable. Keep any proposed policy clearly marked as a draft until its return window, condition requirements, custom/one-of-one exclusions, return postage, and damaged-item handling are settled.
- Keep unprovided product details, performance claims, prices, and builder biography as placeholders rather than invented business facts.

## Current build status

The foundation, catalog, ready-made rod storefront, PostgreSQL inventory reservations, PayPal sandbox commerce, owner fulfillment, and operational reporting phases are implemented. Paid orders move through Needs packing, Packed, Shipped, Delivered, and Completed. Carrier, tracking, optional actual postage, timestamps, and the acting owner are saved. Customers receive order and shipping messages, can see tracking on their private order page, and can print a receipt. The owner has a dedicated mobile-friendly work queue in addition to the full administration area.

Reporting uses immutable order snapshots for product price and private build cost. It separates product sales, shipping collected, originally collected tax, refunds, actual and manual shipping expense, payment fees, and other manual expenses. Estimated profit excludes collected tax. Because provider refunds do not identify the product/shipping/tax allocation, the report shows the whole refund separately and is explicitly operational rather than tax accounting. Owner-only CSV exports apply the selected period and protect spreadsheet text cells from formula execution.

The owner now has a simple rod form, photo upload, inventory and shipping views, and a checklist showing missing sale details. A simulated sale from that form through shipping passes in the test database. All four imported rods are visible publicly: the priced red, white, and blue rod is offered for sale, while the other three are public previews awaiting real prices and builder-confirmed specifications. Store settings now show a test-sale checklist and allow the owner to enter an order-alert email and control sandbox checkout. The switch is rejected until PostgreSQL, shipping, and sandbox credentials are ready. An owner-only message inbox now groups rod questions, general questions, and special requests and lets the owner update status without sending a reply. Real PayPal sandbox testing still requires an owner-created sandbox account, a public HTTPS test URL, and a separate staging copy so a test payment does not sell real inventory. Security and deployment work remain on the build roadmap in the README.

The site now has canonical and social-preview metadata for public pages, an XML sitemap limited to visible rods, and a launch-controlled search-indexing setting. Development and staging default to noindex until the final public domain is configured and approved for launch.

The owner workspace now includes an installable, online-only PWA for the phone. Its home-screen icon and bottom action bar lead directly to rod creation, inventory, shipping, and customer messages. The owner can take a camera photo in the rod form or choose several existing photos. Shipping means entering carrier and tracking **after** purchasing the label elsewhere; the app does not buy labels. Private owner pages are not stored for offline use.
