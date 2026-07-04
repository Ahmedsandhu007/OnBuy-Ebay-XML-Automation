# OnBuy eBay Sync

Employees only need to paste an eBay sourcing link into the `Supplier URL`
column of the `OnBuy_Feed_Master` Google Sheet - everything else (SKU, title,
description, images, category, cost, stock) is fetched and filled in
automatically. Every run also updates price/stock in the Sheet, syncs to
OnBuy (via `feed.xml` and/or directly via OnBuy's API - see
`ONBUY_API_PUSH_ENABLED` below), and mirrors the processed data into the
Supabase `OnBuy_Feed_Master` table.

Runs on a schedule via `.github/workflows/run.yml` (every 3 hours) or manually
via the "Run workflow" button on GitHub Actions.

## Required GitHub secrets

Settings -> Secrets and variables -> Actions -> **Secrets** tab:

| Secret | Purpose |
|---|---|
| `GOOGLE_CREDENTIALS` | Google service account JSON for the Sheet |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | eBay Browse API |
| `ONBUY_CONSUMER_KEY` / `ONBUY_SECRET_KEY` | OnBuy API auth (production) |
| `ONBUY_TEST_CONSUMER_KEY` / `ONBUY_TEST_SECRET_KEY` | OnBuy sandbox auth - same `api.onbuy.com`, just different keys |
| `ONBUY_SELLER_ID` / `ONBUY_SITE_ID` | Needed for OnBuy product create/update calls (used for both production and sandbox, since OnBuy hasn't issued separate sandbox IDs) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Feed hosting (see below) |
| `SMTP_USER` / `SMTP_APP_PASSWORD` / `ALERT_EMAIL_TO` | Failure alert emails (Gmail app password, not your normal password) |
| `ALI_APP_KEY` / `ALI_APP_SECRET` | AliExpress (unused by this script today, kept for future use) |

Settings -> Secrets and variables -> Actions -> **Variables** tab (not secret, just config):

| Variable | Purpose |
|---|---|
| `ONBUY_API_PUSH_ENABLED` | `true`/`false` - see rollout note below. Defaults to off. |
| `ONBUY_USE_SANDBOX` | `true`/`false` - routes OnBuy API calls to the sandbox credentials instead of production. Defaults to off. |
| `ONBUY_API_TEST_SKUS` | Comma-separated SKU allowlist while testing the API push against production |
| `SUPABASE_FEED_BUCKET` | Defaults to `onbuy-feeds` if unset |

## Feed hosting (Supabase Storage)

1. In your Supabase project: **Storage -> New bucket**, name it `onbuy-feeds`, make it **Public**.
2. **Settings -> API**: copy the Project URL and the `service_role` key (not `anon`).
3. Add those as the `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` secrets above.

Until those two secrets exist, the workflow automatically falls back to the
old behavior of committing `feed.xml` to git each run, so nothing breaks in
the meantime. Once they're set, that fallback step stops running on its own.

## Rolling out direct OnBuy API sync

`ONBUY_API_PUSH_ENABLED` is off by default - the pipeline behaves exactly as
before (Sheet + XML feed only) until you turn it on. Recommended order, using
the sandbox before ever touching the live account:

1. **Sandbox test**: set `ONBUY_API_PUSH_ENABLED=true` and `ONBUY_USE_SANDBOX=true`.
   Run the workflow manually - the full batch goes through OnBuy's sandbox,
   zero risk to real listings. Check the run log for errors.
2. **Limited production test**: set `ONBUY_USE_SANDBOX=false` and
   `ONBUY_API_TEST_SKUS=<a couple of real SKUs>`. Run manually and confirm
   those specific SKUs update correctly on the real OnBuy account.
3. **Full rollout**: clear `ONBUY_API_TEST_SKUS` to push every processed SKU
   on the normal 3-hour schedule.

The three manual test scripts (`test_onbuy_auth.py`, `test_create_product.py`,
`test_update_listing.py`, triggered from the Actions tab) always run against
the sandbox and never touch the live account.

## Adding new products

Add a row with just the `Supplier URL` filled in (an eBay item link) - the
next run fills in everything else:

- **SKU**: a real barcode (EAN/GTIN/UPC/ISBN) if eBay's listing has one,
  otherwise the eBay item ID. Written back to the Sheet so it's permanent.
- **Category**: auto-matched against `onbuy_categories_only.csv` using the
  fetched title/description.
- Title, Description (sanitized), Brand, Condition, Cost Price, Stock,
  Selling Price, Image URL, Additional Images - all fetched from eBay.

## Supabase database export

Every processed row is also upserted into the Supabase `OnBuy_Feed_Master`
table (same secrets as feed hosting above - `SUPABASE_URL` /
`SUPABASE_SERVICE_KEY`). A few columns in that table don't have an obvious
1:1 mapping from the Sheet; here's what's actually written and why - flag any
of these if they don't match what you intended:

| Column | What's written |
|---|---|
| `Supplier` | Always `"eBay"` for now - ready for when Amazon is added |
| `Category ID` | The numeric OnBuy category ID resolved for the matched category |
| `Profit %` / `Fee %` | The actual `pricing.py` constants used for that row's price |
| `EAN` | The real extracted barcode when eBay has one - may differ from `SKU` if your SKU convention isn't itself a barcode |
| `Sync Status` / `OnBuy Product Created` / `OnBuy Listing Active` / `OnBuy Product ID` / `Last OnBuy Sync` | Only written when an OnBuy push was actually attempted that run (`ONBUY_API_PUSH_ENABLED`) - left untouched otherwise, so they don't get blanked out for rows that weren't pushed |
| `OPC` | Placeholder `"PENDING"` - OnBuy only assigns the real OPC after its async approval queue clears (see `test_check_queue_status.py`), which this pipeline doesn't poll for. A separate backfill script (like `fetch_listing_ids.py` already does for `Listing ID`) would be needed to fill in real values |
| `Price Check Flag` | Not currently written - no defined logic for this yet |
