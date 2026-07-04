import base64
import csv
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import json
import requests
from oauth2client.service_account import ServiceAccountCredentials

import notify
import pricing
import storage
import supabase_db
from onbuy_client import OnBuyClient
from retry_utils import AuthError, PermanentError, TransientError, raise_for_status, with_retry
from sanitize import sanitize_description, validate_images

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("onbuy_sync")

# ================= CONFIG =================
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

# ================= SETTINGS =================

# TRUE = FETCH ALL PRODUCTS
# FALSE = SMART BATCHING
FULL_REFRESH = False

# AFTER FIRST FULL FETCH
# CHANGE TO 12 OR 20
MAX_PRODUCTS_PER_RUN = 12

# CATEGORY REMAP
RUN_CATEGORY_MAPPING = True

# ================= ONBUY API PUSH (safety-gated) =================
# Off by default: this pipeline previously only ever produced feed.xml for
# OnBuy's own feed importer to consume. Turning this on makes it call OnBuy's
# write APIs directly for real SKUs. Since this hasn't been exercised against
# the live account yet, roll it out gradually:
#   1) leave ONBUY_API_PUSH_ENABLED unset/false -> behaves exactly as before
#   2) set ONBUY_API_PUSH_ENABLED=true and ONBUY_API_TEST_SKUS=sku1,sku2
#      -> only those SKUs go through the API, everything else still only
#         goes through the Sheet + feed.xml as before
#   3) once verified, clear ONBUY_API_TEST_SKUS to push every processed SKU
ONBUY_API_PUSH_ENABLED = os.getenv("ONBUY_API_PUSH_ENABLED", "false").strip().lower() == "true"
ONBUY_API_TEST_SKUS = {s.strip() for s in os.getenv("ONBUY_API_TEST_SKUS", "").split(",") if s.strip()}

# How many eBay fetch failures (after retries) in one run before we email an alert.
FETCH_FAILURE_ALERT_THRESHOLD = 3

PK_TZ = ZoneInfo("Asia/Karachi")


def should_push_to_onbuy(sku):
    if not ONBUY_API_PUSH_ENABLED:
        return False
    if ONBUY_API_TEST_SKUS:
        return sku in ONBUY_API_TEST_SKUS
    return True


# ================= HELPERS =================
def col_letter(n):
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def parse_time(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime(2000, 1, 1)


def tokenize(text):
    return set(re.findall(r"\w+", str(text).lower()))


def clean_category(cat):
    if not cat:
        return ""
    cat = str(cat).replace("\n", " ").strip()
    cat = re.sub(r"\s+", " ", cat).strip()
    return cat


def to_jpg(url):
    if not url:
        return ""
    url = re.sub(r"\.webp.*$", ".jpg", url)
    url = re.sub(r"\.(png|jpeg).*?$", ".jpg", url)
    return url


def empty_ebay_response(item_id=""):
    return {
        "stock": 0,
        "price": 0,
        "description": "",
        "main_image": "",
        "additional_images": [],
        "title": "",
        "brand": "",
        "product_code": item_id,
        "condition": "",
    }


_BARCODE_ASPECT_NAMES = ("EAN", "GTIN", "UPC", "ISBN")


def extract_product_code(data, fallback):
    """Look for a real barcode (EAN/GTIN/UPC/ISBN) in eBay's item aspects -
    same array already parsed for Brand, no extra API call. Falls back to the
    eBay item ID (always available) so every row gets a stable identifier
    even when the listing has no barcode specified. Used to auto-assign a
    SKU for rows where an employee only pasted the sourcing link.
    """
    for aspect in data.get("localizedAspects", []):
        name = aspect.get("name", "").strip().upper()
        if name in _BARCODE_ASPECT_NAMES:
            values = aspect.get("value", "")
            raw = values[0] if isinstance(values, list) else values
            digits = re.sub(r"\D", "", str(raw))
            if digits:
                return digits
    return fallback


# ================= EBAY TOKEN =================
def get_ebay_token():
    def _do_token():
        encoded = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {encoded}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=30,
        )
        raise_for_status(resp, what="ebay token")
        token = resp.json().get("access_token")
        if not token:
            raise AuthError("ebay token response missing access_token")
        return token

    try:
        return with_retry(_do_token, what="ebay token", max_attempts=3)
    except (AuthError, PermanentError) as exc:
        logger.error("eBay authentication failed: %s", exc)
        return None
    except Exception as exc:
        logger.error("eBay authentication failed after retries: %s", exc)
        return None


ITEM_GROUP_ERROR_ID = 11006  # "The legacy Id is invalid... use get_items_by_item_group"


def _is_item_group_error(resp):
    try:
        errors = resp.json().get("errors", [])
    except ValueError:
        return False
    return any(e.get("errorId") == ITEM_GROUP_ERROR_ID for e in errors)


def _fetch_item_group_as_item(item_group_id, token):
    """Some eBay listings are multi-variation ("item group") listings - e.g.
    a listing with size/color options - which get_item_by_legacy_id rejects
    with errorId 11006, pointing at this endpoint instead. Picks the first
    variation as a representative item and reshapes the response so the rest
    of get_ebay_data's parsing works unchanged. If your SKU is meant to track
    a *specific* variation rather than "whichever eBay lists first", check the
    chosen item_id logged below against what you expect.
    """
    resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item/get_items_by_item_group",
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"},
        params={"item_group_id": item_group_id},
        timeout=20,
    )
    raise_for_status(resp, what=f"ebay item group {item_group_id}")
    group_data = resp.json()

    items = group_data.get("items", [])
    if not items:
        return None

    chosen = items[0]
    logger.info(
        "Item %s is a multi-variation listing - using variation %s",
        item_group_id,
        chosen.get("legacyItemId") or chosen.get("itemId"),
    )

    description = chosen.get("description")
    if not description:
        for common in group_data.get("commonDescriptions", []):
            if chosen.get("itemId") in common.get("itemIds", []):
                description = common.get("description", "")
                break
    chosen["description"] = description or ""

    return chosen


# ================= EBAY FETCH =================
def get_ebay_data(url, token):
    """Returns (available, data). available=False with empty_ebay_response()
    means eBay gave us a definitive "not available" answer (404 / no price /
    out of stock) - a real signal, not a failure.

    Raises TransientError/PermanentError if the fetch itself failed after
    retries. Callers MUST NOT treat that the same as "removed" - the previous
    version's bare `except Exception` did exactly that and zeroed live
    listings on ordinary network blips.
    """
    match = re.search(r"/itm/(\d+)", url)
    if not match:
        return False, empty_ebay_response()
    item_id = match.group(1)

    def _do_fetch():
        resp = requests.get(
            "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id",
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"},
            params={"legacy_item_id": item_id},
            timeout=20,
        )
        if resp.status_code == 404:
            return None  # confirmed removed - a real signal, not an error
        if resp.status_code == 400 and _is_item_group_error(resp):
            return _fetch_item_group_as_item(item_id, token)
        raise_for_status(resp, what=f"ebay item {item_id}")
        return resp.json()

    data = with_retry(_do_fetch, what=f"ebay item {item_id}", max_attempts=3)

    if data is None:
        logger.info("REMOVED LISTING: %s", item_id)
        return False, empty_ebay_response(item_id)

    price_data = data.get("price", {}) or {}
    price = float(price_data.get("value", 0) or 0)
    if price <= 0:
        logger.info("NO PRICE: %s", item_id)
        return False, empty_ebay_response(item_id)

    estimated = data.get("estimatedAvailabilities", [])
    stock = 5
    if estimated:
        status = estimated[0].get("estimatedAvailabilityStatus", "")
        if status in ("OUT_OF_STOCK", "UNAVAILABLE"):
            logger.info("OUT OF STOCK: %s", item_id)
            return False, empty_ebay_response(item_id)
        stock = estimated[0].get("estimatedAvailableQuantity", 5)
    if not stock or stock <= 0:
        stock = 5

    html_description = sanitize_description(data.get("description", ""))

    main_image = ""
    if data.get("image"):
        main_image = to_jpg(data["image"].get("imageUrl", ""))

    additional_images = []
    for img in data.get("additionalImages", []):
        img_url = to_jpg(img.get("imageUrl", ""))
        if img_url:
            additional_images.append(img_url)

    all_images = validate_images([main_image] + additional_images, max_images=11)
    main_image = all_images[0] if all_images else ""
    additional_images = all_images[1:11]

    title = data.get("title", "")

    brand = ""
    for aspect in data.get("localizedAspects", []):
        if aspect.get("name", "").lower() == "brand":
            values = aspect.get("value", "")
            brand = values[0] if isinstance(values, list) else values

    product_code = extract_product_code(data, fallback=item_id)
    condition = data.get("condition") or "New"

    return True, {
        "stock": stock,
        "price": price,
        "description": html_description,
        "main_image": main_image,
        "additional_images": additional_images,
        "title": title,
        "brand": brand,
        "product_code": product_code,
        "condition": condition,
    }


def main():
    run_had_errors = False
    fetch_failures = 0

    # ================= GOOGLE SHEET =================
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("OnBuy_Feed_Master").sheet1

    data = sheet.get_all_records()
    headers = sheet.row_values(1)
    col_map = {col: idx + 1 for idx, col in enumerate(headers)}

    logger.info("TOTAL ROWS IN SHEET: %d", len(data))

    # ================= CATEGORY FILE =================
    onbuy_categories = []
    category_id_by_path = {}

    with open("onbuy_categories_only.csv", newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            category = row.get("OnBuy Category Path")
            if category:
                onbuy_categories.append(category)
                try:
                    category_id_by_path[category.strip().lower()] = int(row.get("Category ID"))
                except (TypeError, ValueError):
                    category_id_by_path[category.strip().lower()] = None

    logger.info("Loaded %d OnBuy categories", len(onbuy_categories))

    valid_onbuy_categories = set(cat.strip().lower() for cat in onbuy_categories)

    def is_valid_onbuy_category(category):
        return str(category).strip().lower() in valid_onbuy_categories

    def map_onbuy_category(title, current_category, description=""):
        product_text = f"{title}\n{current_category}\n{description}".lower()
        product_words = tokenize(product_text)

        best_match = None
        best_score = 0
        for category_path in onbuy_categories:
            category_words = tokenize(category_path)
            score = len(product_words.intersection(category_words))
            for word in product_words:
                if word in category_path.lower():
                    score += 2
            if score > best_score:
                best_score = score
                best_match = category_path

        if best_match and best_score >= 2:
            return best_match
        return current_category

    # ================= ONBUY CLIENT =================
    onbuy = OnBuyClient()
    onbuy_ready = False
    if ONBUY_API_PUSH_ENABLED:
        onbuy_ready = onbuy.authenticate()
        if not onbuy_ready:
            run_had_errors = True
            logger.error("ONBUY_API_PUSH_ENABLED is true but OnBuy authentication failed - skipping all OnBuy API pushes this run")

    # ================= CATEGORY MAPPING =================
    # Cheap full-catalog pass (no eBay calls) for rows that already have a
    # Title/Description from a previous run. A brand-new row (employee only
    # pasted the URL) still has blank Title/Description at this point, so it
    # can't be mapped yet here - the main loop below re-checks category using
    # the freshly-fetched eBay data for exactly that case.
    if RUN_CATEGORY_MAPPING:
        logger.info("Updating categories...")
        category_updates = []
        for idx, row in enumerate(data):
            i = idx + 2
            current_category = str(row.get("Category") or "").strip()
            if is_valid_onbuy_category(current_category):
                continue
            mapped = map_onbuy_category(row.get("Title"), current_category, row.get("Description"))
            if mapped != current_category:
                category_updates.append({"range": f"{col_letter(col_map['Category'])}{i}", "values": [[mapped]]})
                logger.info("Mapped row %d", i)
        if category_updates:
            sheet.batch_update(category_updates)

    # ================= PRODUCT ORDER =================
    if FULL_REFRESH:
        sorted_data = list(enumerate(data))
    else:
        sorted_data = sorted(enumerate(data), key=lambda x: parse_time(x[1].get("Last Checked Time", "")))

    # While testing the OnBuy API push against a specific SKU allowlist, move
    # those SKUs to the front of the queue - otherwise a manual test run can
    # easily land on a batch that doesn't include any of them (oldest-checked
    # rows win by default), making it look like the push silently did nothing
    # when really it just never got a chance to run.
    if ONBUY_API_PUSH_ENABLED and ONBUY_API_TEST_SKUS:
        sorted_data = sorted(
            sorted_data,
            key=lambda x: str(x[1].get("SKU") or "").strip() not in ONBUY_API_TEST_SKUS,
        )

    logger.info("Processing %d products", min(len(sorted_data), MAX_PRODUCTS_PER_RUN))

    # ================= MAIN UPDATE LOOP =================
    token = get_ebay_token()
    if not token:
        # Abort instead of proceeding to call every row with a bad/missing
        # token - the old code sent "Authorization: Bearer None" per row,
        # which zeroed price/stock for the entire batch on a single auth failure.
        logger.error("Could not obtain an eBay token - aborting run without touching any rows")
        notify.send_alert_email(
            "eBay authentication failed - run aborted",
            "generate_xml.py could not obtain an eBay OAuth token this run. "
            "No sheet rows were touched. Check EBAY_CLIENT_ID/EBAY_CLIENT_SECRET.",
        )
        sys.exit(1)

    updated_count = 0
    onbuy_pushed = 0
    onbuy_failed = 0
    supabase_rows = []

    for idx, row in sorted_data[:MAX_PRODUCTS_PER_RUN]:
        i = idx + 2
        url = str(row.get("Supplier URL", "")).strip()

        if "ebay." not in url.lower():
            continue

        try:
            _, ebay_data = get_ebay_data(url, token)
        except (TransientError, PermanentError) as exc:
            fetch_failures += 1
            run_had_errors = True
            logger.error("Row %d (%s): fetch failed after retries, leaving existing values untouched - %s", i, url, exc)
            continue

        stock = ebay_data["stock"]
        cost_price = ebay_data["price"]

        # ================= SKU (auto-assigned if the employee only pasted the URL) =================
        sku = str(row.get("SKU") or "").strip()
        sku_needs_write = False
        if not sku:
            sku = str(ebay_data.get("product_code") or "").strip()
            if sku:
                sku_needs_write = True
                logger.info("Row %d: no SKU provided, auto-assigned %s", i, sku)
            else:
                logger.warning("Row %d: no SKU provided and could not derive one from %s - skipping", i, url)
                continue

        # ================= CATEGORY (re-checked here with fresh title/description so a
        # brand-new row gets categorized on this same pass, not just the upfront
        # full-catalog remap above, which ran before this row's eBay data existed) ====
        current_category = str(row.get("Category") or "").strip()
        if is_valid_onbuy_category(current_category):
            category = current_category
            category_needs_write = False
        else:
            category = map_onbuy_category(ebay_data["title"], current_category, ebay_data["description"])
            category_needs_write = category != current_category
        category_id = category_id_by_path.get(category.strip().lower())

        # ================= PRICING =================
        shipping_cost = float(row.get("Shipping Cost (£)") or 0)
        selling_price = 0 if stock == 0 else pricing.calculate_selling_price(cost_price, shipping_cost)

        additional_images_str = ",".join(ebay_data["additional_images"])
        now_str = datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")

        updates = [
            {"range": f"{col_letter(col_map['Cost Price (£)'])}{i}", "values": [[cost_price]]},
            {"range": f"{col_letter(col_map['Stock'])}{i}", "values": [[stock]]},
            {"range": f"{col_letter(col_map['Selling Price (£)'])}{i}", "values": [[selling_price]]},
            {"range": f"{col_letter(col_map['Status'])}{i}", "values": [["ACTIVE" if stock > 0 else "INACTIVE"]]},
            {"range": f"{col_letter(col_map['Description'])}{i}", "values": [[ebay_data["description"]]]},
            {"range": f"{col_letter(col_map['Image URL'])}{i}", "values": [[ebay_data["main_image"]]]},
            {"range": f"{col_letter(col_map['Additional Images'])}{i}", "values": [[additional_images_str]]},
            {"range": f"{col_letter(col_map['Brand'])}{i}", "values": [[ebay_data["brand"]]]},
            {"range": f"{col_letter(col_map['Title'])}{i}", "values": [[ebay_data["title"]]]},
            {"range": f"{col_letter(col_map['Last Updated'])}{i}", "values": [[now_str]]},
            {"range": f"{col_letter(col_map['Last Checked Time'])}{i}", "values": [[now_str]]},
        ]
        if sku_needs_write:
            updates.append({"range": f"{col_letter(col_map['SKU'])}{i}", "values": [[sku]]})
        if category_needs_write:
            updates.append({"range": f"{col_letter(col_map['Category'])}{i}", "values": [[category]]})

        try:
            with_retry(sheet.batch_update, updates, what=f"sheet update row {i}", max_attempts=3)
        except Exception as exc:
            run_had_errors = True
            logger.error("Row %d: sheet update failed after retries, skipping - %s", i, exc)
            continue

        updated_count += 1
        logger.info("Updated row %d", i)
        time.sleep(0.5)  # keep the Sheets API write rate gentle, as the original pipeline did

        # ================= ONBUY API PUSH (gated, see ONBUY_API_PUSH_ENABLED) =================
        ean = ebay_data.get("product_code") or sku
        sync_status = None
        onbuy_product_created = None
        onbuy_listing_active = None
        onbuy_product_id = None
        last_onbuy_sync = None

        if sku and onbuy_ready and should_push_to_onbuy(sku):
            try:
                action, result = onbuy.sync_product(
                    sku=sku,
                    ean=ean,
                    title=ebay_data["title"] or str(row.get("Title") or ""),
                    description=ebay_data["description"],
                    brand=ebay_data["brand"],
                    category_id=category_id,
                    price=selling_price,
                    stock=stock,
                    main_image=ebay_data["main_image"],
                    additional_images=ebay_data["additional_images"],
                )
                onbuy_pushed += 1
                logger.info("OnBuy %s: %s", action, sku)
                last_onbuy_sync = now_str
                if action == "created":
                    # Accepted into OnBuy's async approval queue - not confirmed live yet.
                    # The real OPC/approval status only appears later via
                    # OnBuyClient.check_queue(); this pipeline doesn't poll for it, so
                    # these reflect "submitted", not "confirmed active".
                    sync_status = "Pending Approval"
                    onbuy_product_created = "TRUE"
                    onbuy_listing_active = "FALSE"
                    onbuy_product_id = str(result.get("queue_id", "")) if isinstance(result, dict) else ""
                else:
                    sync_status = "Synced"
                    onbuy_product_created = "TRUE"
                    onbuy_listing_active = "TRUE"
            except Exception as exc:
                onbuy_failed += 1
                run_had_errors = True
                sync_status = "Failed"
                logger.error("OnBuy push failed for SKU %s: %s", sku, exc)

        # ================= SUPABASE EXPORT ROW (upserted once after the loop) =================
        supabase_row = {
            "SKU": sku,
            "Title": ebay_data["title"] or str(row.get("Title") or ""),
            "Description": ebay_data["description"],
            "Brand": ebay_data["brand"],
            "Category": category,
            "Category ID": str(category_id) if category_id is not None else None,
            "Supplier URL": url,
            "Supplier": "eBay",
            "Cost Price (£)": cost_price,
            "Shipping Cost (£)": str(shipping_cost) if shipping_cost else None,
            "Profit %": str(pricing.MIN_PROFIT_PERCENT),
            "Fee %": str(pricing.PLATFORM_FEE_PERCENT),
            "Stock": stock,
            "Selling Price (£)": selling_price,
            "Status": "ACTIVE" if stock > 0 else "INACTIVE",
            "Last Updated": datetime.now(PK_TZ).isoformat(),
            "Image URL": ebay_data["main_image"],
            "Additional Images": additional_images_str,
            "Condition": ebay_data.get("condition") or "New",
            "Last Checked Time": datetime.now(PK_TZ).isoformat(),
            "EAN": ean,
            # OPC (OnBuy's permanent product code) is only known once the async
            # queue clears - see OnBuyClient.check_queue(). This column is NOT
            # NULL, so new rows get a placeholder; a separate backfill script
            # (like fetch_listing_ids.py already does for Listing ID) would be
            # needed to write real OPC values without this pipeline clobbering
            # them back to "PENDING" on the next run.
            "OPC": "PENDING",
        }
        listing_id = str(row.get("Listing ID") or "").strip()
        if listing_id:
            supabase_row["Listing ID"] = listing_id
        if sync_status:
            supabase_row["Sync Status"] = sync_status
        if onbuy_product_created:
            supabase_row["OnBuy Product Created"] = onbuy_product_created
        if onbuy_listing_active:
            supabase_row["OnBuy Listing Active"] = onbuy_listing_active
        if onbuy_product_id:
            supabase_row["OnBuy Product ID"] = onbuy_product_id
        if last_onbuy_sync:
            supabase_row["Last OnBuy Sync"] = last_onbuy_sync

        supabase_rows.append(supabase_row)

    supabase_ok = supabase_db.upsert_products(supabase_rows)

    # ================= GENERATE XML (kept as fallback) =================
    root = ET.Element("products")
    feed_count = 0
    skipped_feed = 0

    for row in sheet.get_all_records():
        try:
            sku = str(row.get("SKU") or "").strip()
            title = str(row.get("Title") or "").strip()
            desc = str(row.get("Description") or "").strip()
            brand = str(row.get("Brand") or "").strip()
            category = clean_category(row.get("Category"))
            image = to_jpg(row.get("Image URL"))
            additional_images = [img.strip() for img in str(row.get("Additional Images") or "").split(",") if img.strip()][:10]
            price = float(row.get("Selling Price (£)") or 0)
            stock = int(row.get("Stock") or 0)

            if not all([sku, title, category]):
                skipped_feed += 1
                continue

            product = ET.SubElement(root, "product")
            ET.SubElement(product, "sku").text = sku
            ET.SubElement(product, "product_name").text = title[:150]
            ET.SubElement(product, "description").text = desc
            ET.SubElement(product, "image_url").text = image

            for img_idx, img in enumerate(additional_images):
                ET.SubElement(product, f"additional_image_url_{img_idx + 1}").text = img

            ET.SubElement(product, "brand").text = brand
            ET.SubElement(product, "category").text = category
            ET.SubElement(product, "condition").text = "New"
            ET.SubElement(product, "ean").text = sku
            ET.SubElement(product, "price").text = str(price)
            ET.SubElement(product, "quantity").text = str(stock)

            feed_count += 1
        except Exception:
            skipped_feed += 1

    ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)
    feed_url = storage.upload_feed()

    # ================= FINAL LOGS + ALERTS =================
    logger.info("DONE")
    logger.info("Updated rows: %d", updated_count)
    logger.info("OnBuy API pushed: %d, failed: %d", onbuy_pushed, onbuy_failed)
    logger.info("Feed products: %d, skipped: %d", feed_count, skipped_feed)
    logger.info("Feed URL: %s", feed_url or "(not uploaded - see SUPABASE_URL/SUPABASE_SERVICE_KEY)")
    logger.info("Supabase database export: %s (%d rows)", "OK" if supabase_ok else "skipped/failed", len(supabase_rows))

    if fetch_failures >= FETCH_FAILURE_ALERT_THRESHOLD or onbuy_failed > 0:
        notify.send_alert_email(
            "Sync run finished with errors",
            f"eBay fetch failures: {fetch_failures}\n"
            f"OnBuy push failures: {onbuy_failed}\n"
            f"Updated rows: {updated_count}\n"
            f"Feed products: {feed_count}, skipped: {skipped_feed}\n"
            "Check the GitHub Actions run log for details.",
        )

    if run_had_errors:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Run crashed")
        notify.send_alert_email("Run crashed", "generate_xml.py raised an unhandled exception - see the GitHub Actions log.")
        raise
