import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import time
import json
import os
import re

# ================= CONFIG =================
API_KEY = os.getenv("RAINFOREST_API_KEY")

# ================= AUTH =================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])

creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

sheet = client.open("OnBuy_Feed_Master").sheet1
data = sheet.get_all_records()

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-GB,en;q=0.9"
}

# ================= AMAZON =================
def extract_asin(url):
    match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{10})", url)
    return match.group(1).upper() if match else None


def get_amazon_data(url):
    try:
        asin = extract_asin(url)
        if not asin:
            return None, None

        params = {
            "api_key": API_KEY,
            "type": "product",
            "amazon_domain": "amazon.co.uk",
            "asin": asin
        }

        res = requests.get("https://api.rainforestapi.com/request", params=params, timeout=20)
        data = res.json().get("product", {})

        price = None
        if data.get("buybox_winner"):
            price = data["buybox_winner"]["price"]["value"]
        elif data.get("price"):
            price = data["price"]["value"]

        availability = data.get("availability", "").lower()

        if "in stock" in availability:
            stock = 10
        elif "out of stock" in availability:
            stock = 0
        else:
            stock = 5

        print(f"Amazon API | {asin} | Stock: {stock} | Price: {price}")

        return stock, price

    except Exception as e:
        print("Amazon error:", e)
        return None, None


# ================= EBAY =================
def get_ebay_data(url):
    try:
        from bs4 import BeautifulSoup

        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")

        text = soup.text.lower()

        # ---------- PRICE ----------
        price = None

        # JSON extraction
        for script in soup.find_all("script"):
            if script.string and "price" in script.string:
                matches = re.findall(r'"price":"?([0-9]+\.[0-9]+)"?', script.string)
                if matches:
                    price = float(matches[0])
                    break

        # Selectors
        if price is None:
            selectors = [".x-price-primary span", "#prcIsum"]
            for sel in selectors:
                tag = soup.select_one(sel)
                if tag:
                    txt = tag.text.replace("£", "").replace(",", "").strip()
                    if txt.replace(".", "").isdigit():
                        price = float(txt)
                        break

        # Regex fallback
        if price is None:
            matches = re.findall(r"£\s?([0-9]+(?:\.[0-9]{1,2})?)", soup.text)
            if matches:
                price = float(matches[0])

        # ---------- STOCK (FIXED) ----------
        stock = None

        # 1. Quantity detection (MOST IMPORTANT)
        qty_match = re.search(r"(\d+)\s+available", text)
        if qty_match:
            try:
                stock = int(qty_match.group(1))
            except:
                stock = 1

        # 2. Limited stock phrases
        if stock is None:
            if "last one" in text:
                stock = 1
            elif "limited quantity" in text:
                stock = 2

        # 3. Out-of-stock / ended listing
        if any(x in text for x in [
            "out of stock",
            "this listing was ended",
            "sold out",
            "no longer available"
        ]):
            stock = 0

        # 4. Final fallback
        if stock is None:
            stock = 1

        print(f"eBay parsed → Stock: {stock}, Price: {price}")

        return stock, price

    except Exception as e:
        print("eBay error:", e)
        return None, None


# ================= XML =================
root = ET.Element("products")

# ================= MAIN LOOP =================
for i, row in enumerate(data, start=2):

    url = str(row.get("Supplier URL", "")).strip().lower()

    stock, price = None, None

    if "amazon." in url:
        stock, price = get_amazon_data(url)

    elif "ebay." in url:
        stock, price = get_ebay_data(url)

    print(f"URL: {url}")
    print(f"Result → Stock: {stock}, Price: {price}")

    # Fallback
    if price is None:
        price = row.get("Cost Price (£)", 0)

    if stock is None:
        stock = row.get("Stock", 0)

    cost_price = round(price, 2)
    selling_price = round(price * 1.35, 2)

    status = "ACTIVE" if stock > 0 else "INACTIVE"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Sheet update
    try:
        sheet.update(range_name=f"H{i}:O{i}", values=[[
            cost_price,
            "", "", "",
            stock,
            selling_price,
            status,
            timestamp
        ]])
    except Exception as e:
        print("Sheet error:", e)

    # XML
    if status == "ACTIVE":
        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "title").text = row.get("Title", "")
        ET.SubElement(product, "description").text = row.get("Description", "")
        ET.SubElement(product, "price").text = str(selling_price)
        ET.SubElement(product, "quantity").text = str(stock)
        ET.SubElement(product, "brand").text = row.get("Brand", "")
        ET.SubElement(product, "image_url").text = row.get("Image URL", "")
        ET.SubElement(product, "additional_images").text = row.get("Additional Images", "")
        ET.SubElement(product, "category").text = row.get("Category", "")
        ET.SubElement(product, "condition").text = row.get("Condition", "")

    print(f"Processed row {i}")
    time.sleep(2)

# SAVE XML
tree = ET.ElementTree(root)
tree.write("feed.xml", encoding="utf-8", xml_declaration=True)

print("XML Updated Successfully!")
