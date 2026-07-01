# OnBuy eBay Sync

Pulls live price/stock/description/images from eBay UK, writes them into the
`OnBuy_Feed_Master` Google Sheet, and syncs to OnBuy - either via the XML feed
(`feed.xml`) or directly via OnBuy's API (see `ONBUY_API_PUSH_ENABLED` below).

Runs on a schedule via `.github/workflows/run.yml` (every 3 hours) or manually
via the "Run workflow" button on GitHub Actions.

## Required GitHub secrets

Settings -> Secrets and variables -> Actions -> **Secrets** tab:

| Secret | Purpose |
|---|---|
| `GOOGLE_CREDENTIALS` | Google service account JSON for the Sheet |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | eBay Browse API |
| `ONBUY_CONSUMER_KEY` / `ONBUY_SECRET_KEY` | OnBuy API auth |
| `ONBUY_SELLER_ID` / `ONBUY_SITE_ID` | Needed for OnBuy product create/update calls |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Feed hosting (see below) |
| `SMTP_USER` / `SMTP_APP_PASSWORD` / `ALERT_EMAIL_TO` | Failure alert emails (Gmail app password, not your normal password) |
| `ALI_APP_KEY` / `ALI_APP_SECRET` | AliExpress (unused by this script today, kept for future use) |

Settings -> Secrets and variables -> Actions -> **Variables** tab (not secret, just config):

| Variable | Purpose |
|---|---|
| `ONBUY_API_PUSH_ENABLED` | `true`/`false` - see rollout note below. Defaults to off. |
| `ONBUY_API_TEST_SKUS` | Comma-separated SKU allowlist while testing the API push |
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
before (Sheet + XML feed only) until you turn it on:

1. Set `ONBUY_API_PUSH_ENABLED=true` and `ONBUY_API_TEST_SKUS=<a couple of real SKUs>`.
2. Run the workflow manually and check those SKUs update correctly on OnBuy.
3. Once confident, clear `ONBUY_API_TEST_SKUS` to push every processed SKU.
