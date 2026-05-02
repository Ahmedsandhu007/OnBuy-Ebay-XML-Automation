import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import json
import os
import re
import xml.etree.ElementTree as ET
import base64

# ================= CONFIG =================
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

TOTAL_BATCHES = 5
DAILY_API_LIMIT = 4800

FULL_REFRESH = True   # 🔥 TRUE for full run, then set FALSE

PK_TZ = ZoneInfo("Asia/Karachi")

# ================= GOOGLE SHEET =================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

sheet = client.open("OnBuy_Feed_Master").sheet1
data = sheet.get_all_records()

print(f"📊 TOTAL ROWS IN SHEET: {len(data)}")

# ================= HELPERS =================
def to_jpg(url):
    if not url:
        return ""
    url = re.sub(r"\.webp.*$", ".jpg", url)
    url = re.sub(r"\.(png|jpeg).*?$", ".jpg", url)
    return url

def clean_additional_images(images):
    if not images:
        return ""
    imgs = [to_jpg(i.strip()) for i in str(images).split(",") if i.strip()]
    return ",".join(imgs[:5])

def clean_category(cat):
    if not cat:
        return ""
    cat = str(cat).replace("\n", " ").strip()
    if "|" in cat:
        cat = cat.split("|")[-1]
    cat = re.sub(r"\s+", " ", cat).strip()
    return cat

# ================= EBAY TOKEN =================
def get_ebay_token():
    encoded = base64.b64encode(
        f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()
    ).decode()

    res = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"
        }
    )

    return res.json().get("access_token")

# ================= EBAY FETCH =================
def get_ebay_data(url, token):
    try:
        match = re.search(r"/itm/(\d+)", url)
        if not match:
            return None, None

        item_id = match.group(1)

        res = requests.get(
            f"https://api.ebay.com/buy/browse/v1/item/v1|{item_id}|0",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"
            }
        )

        data = res.json()

        price = float(data.get("price", {}).get("value", 0))
        stock = 0

        avail = data.get("estimatedAvailabilities", [])
        if avail:
            stock = avail[0].get("estimatedAvailableQuantity", 5)

        return stock, price

    except:
        return None, None

# ================= UPDATE LOOP =================
token = get_ebay_token()
api_calls = 0

current_hour = datetime.now(PK_TZ).hour
batch_index = current_hour % TOTAL_BATCHES

for idx, row in enumerate(data):

    if not FULL_REFRESH:
        if idx % TOTAL_BATCHES != batch_index:
            continue

    if api_calls >= DAILY_API_LIMIT:
        break

    url = str(row.get("Supplier URL", "")).lower()
    if "ebay." not in url:
        continue

    stock, cost_price = get_ebay_data(url, token)
    api_calls += 1

    if not cost_price:
        continue

    final_price = round(cost_price * 1.4, 2)
    i = idx + 2

    sheet.update(
        range_name=f"H{i}:O{i}",
        values=[[
            float(cost_price),
            "", "", "",
            int(stock or 0),
            float(final_price),
            "ACTIVE" if stock else "INACTIVE",
            datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")
        ]]
    )

    print(f"Updated row {i}")
    time.sleep(0.3)

# ================= XML GENERATION =================
root = ET.Element("products")

count = 0
skipped = 0
skipped_rows = []

for idx, row in enumerate(data):
    try:
        i = idx + 2

        sku = str(row.get("SKU") or "").strip()
        title = str(row.get("Title") or "").strip()
        desc = str(row.get("Description") or "").strip()
        main_image = to_jpg(str(row.get("Image URL") or "").strip())
        brand = str(row.get("Brand") or "").strip()
        category = clean_category(row.get("Category"))

        price_raw = str(row.get("Selling Price (£)") or "0")
        price = float(re.sub(r"[^\d.]", "", price_raw) or 0)

        stock = int(row.get("Stock") or 0)

        reasons = []

        if not sku:
            reasons.append("Missing SKU")
        if not title:
            reasons.append("Missing Title")
        if not desc:
            reasons.append("Missing Description")
        if not main_image:
            reasons.append("Missing Image")
        if not brand:
            reasons.append("Missing Brand")
        if not category:
            reasons.append("Missing Category")
        if price <= 0:
            reasons.append("Invalid Price")
        if stock <= 0:
            reasons.append("Invalid Stock")

        if reasons:
            skipped += 1
            skipped_rows.append((i, sku, ", ".join(reasons)))
            continue

        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = sku
        ET.SubElement(product, "product_name").text = title[:150]
        ET.SubElement(product, "description").text = desc
        ET.SubElement(product, "image_url").text = main_image

        additional_images = clean_additional_images(row.get("Additional Images"))
        if additional_images:
            ET.SubElement(product, "additional_image_urls").text = additional_images

        ET.SubElement(product, "brand").text = brand
        ET.SubElement(product, "category").text = category
        ET.SubElement(product, "ean").text = sku
        ET.SubElement(product, "condition").text = "New"
        ET.SubElement(product, "price").text = str(price)
        ET.SubElement(product, "quantity").text = str(stock)

        count += 1

    except Exception as e:
        skipped += 1
        skipped_rows.append((i, "ERROR", str(e)))

# ================= SAVE =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print("\n✅ FEED GENERATED")
print(f"📦 PRODUCTS IN FEED: {count}")
print(f"⚠ SKIPPED (INCOMPLETE): {skipped}")

print("\n🔍 SKIPPED ROW DETAILS:")
for row_info in skipped_rows[:20]:
    print(f"Row {row_info[0]} | SKU: {row_info[1]} | Issue: {row_info[2]}")
