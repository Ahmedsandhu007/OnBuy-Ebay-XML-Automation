# OnBuy eBay Sync

Employees paste an eBay sourcing link into the `Supplier URL` column of the
`OnBuy_Feed_Master` Google Sheet **and pick a unique SKU** - everything else
(title, description, images, category, cost, stock, selling price) is
fetched and filled in automatically. Every run also updates price/stock in
the Sheet (highlighting out-of-stock rows red), syncs to OnBuy (via
`feed.xml` and/or directly via OnBuy's API - see `ONBUY_API_PUSH_ENABLED`
below), and mirrors the processed data into the Supabase `OnBuy_Feed_Master`
table.

Runs on a schedule via `.github/workflows/run.yml` (every 3 hours by
default) or manually via the "Run workflow" button on GitHub Actions. Batch
size scales automatically with catalog size - see "Scaling" below.

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
| `ONBUY_MAX_PUSHES_PER_RUN` | Defaults to `200` - safety cap under OnBuy's confirmed 240/hour limit, see "Scaling" below |
| `EBAY_DAILY_CALL_BUDGET` | Defaults to `4000` - see "Scaling" below |
| `RUNS_PER_DAY` | Defaults to `8` - must match the cron schedule below |
| `MAX_PRODUCTS_PER_RUN` | Optional hard override for the batch size instead of the budget-derived one |

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

Add a row with the `Supplier URL` (an eBay item link) **and a unique SKU**
filled in - both are required; OnBuy rejects duplicate SKUs, so this isn't
auto-generated. If two rows ever end up with the same SKU by mistake (e.g. a
copy-pasted row), the database export drops the older duplicate and logs a
warning naming the SKU to fix - it won't silently corrupt the run, but the
Sheet still needs a human to correct the duplicate. Everything else fills in
on the next run:

- **Category**: auto-matched against `onbuy_categories_only.csv` using the
  fetched title/description.
- Title, Description (sanitized), Brand, Condition, Cost Price, Stock,
  Image URL, Additional Images - all fetched from eBay.
- **Selling Price**: `max(existing price, default-margin price)`. The
  default margin is 20% platform fee + 20% profit = 40% total over cost. If
  a price is already entered that implies *more* than 40%, it's left alone;
  if it implies less (or is blank), it's raised to the 40% default. Prices
  are never silently lowered.
- **Price Check Flag**: `Normal` (margin at/near the 40% default, ≤45%),
  `Medium` (45-70%), `High` (>70%) - adjust the two threshold constants in
  `generate_xml.py` if these percentages don't match what you meant by "a
  little more" / "much more."
- **Out of stock**: Status set to `INACTIVE`, the whole Sheet row
  highlighted red, and (if `ONBUY_API_PUSH_ENABLED`) pushed to OnBuy with
  stock=0 in the same run. Turns back to white automatically once back in stock.

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
| `EAN` | The real extracted barcode when eBay has one - may differ from `SKU` if your SKU convention isn't itself a barcode. Also written to the Sheet's `EAN` column if it exists. |
| `Sync Status` / `OnBuy Product Created` / `OnBuy Listing Active` / `OnBuy Product ID` / `Last OnBuy Sync` | Updated to fresh values when an OnBuy push was actually attempted that row this run (`ONBUY_API_PUSH_ENABLED`); otherwise carried forward unchanged from whatever Supabase already had, so these never get blanked out on a run that doesn't touch OnBuy. Also written to the Sheet where those columns exist. (These live on the same upsert as everything else in this table - Postgres validates NOT NULL columns before it even looks at ON CONFLICT, so a separate partial-column upsert for just these fields can't work here.) |
| `OPC` | Placeholder `"PENDING"` at first - OnBuy only assigns the real OPC after its async approval queue clears. `backfill_onbuy_status.py` runs hourly (see workflow) to find the real value and confirmed status, writing it to both the Sheet and here. |
| `Price Check Flag` | See "Adding new products" above - also written to the Sheet. |

## Scaling

Batch size (`MAX_PRODUCTS_PER_RUN`) is computed automatically each run from
the current row count and `EBAY_DAILY_CALL_BUDGET` / `RUNS_PER_DAY`, instead
of a fixed number - check the run log for `Batch size: N products/run ...
a full refresh cycle over M rows takes ~X day(s)`. To go faster, either
increase `EBAY_DAILY_CALL_BUDGET` (if your eBay Developer Portal tier allows
more than the ~4,000/day default assumed here) or increase `RUNS_PER_DAY`
and update the cron schedule in `run.yml` to match. OnBuy's own limits
(confirmed from your account: 4,800 PUT/day, 4,800 POST/day, **240/hour for
both**) have much more daily headroom than eBay's at this scale, but the
hourly cap matters once a single run can process hundreds of rows -
`ONBUY_MAX_PUSHES_PER_RUN` (default 200) caps OnBuy pushes per run so one
large run can't burst through the hourly limit on its own; rows beyond that
cap still get their Sheet/Supabase update, just not pushed to OnBuy until
their next turn. eBay is the real bottleneck for how large this catalog can
grow overall.

Google Sheets writes are batched into a single API call per run (not one per
row), regardless of batch size, so growing the batch size doesn't risk
Google's own rate limits either.
