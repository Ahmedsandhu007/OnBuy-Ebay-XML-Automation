import os
import requests

# --------------------------
# AUTH
# --------------------------

consumer_key = os.getenv("ONBUY_CONSUMER_KEY")
secret_key = os.getenv("ONBUY_SECRET_KEY")

auth_response = requests.post(
    "https://api.onbuy.com/v2/auth/request-token",
    data={
        "consumer_key": consumer_key,
        "secret_key": secret_key
    },
    headers={
        "Content-Type": "application/x-www-form-urlencoded"
    }
)

auth_response.raise_for_status()

token = auth_response.json()["access_token"]

print("✅ Token generated")

# --------------------------
# PRODUCT CREATE
# --------------------------

payload = {
    "site_id": 2000,
    "seller_id": 48948,
    "uid": "194343045790-test",
    "published": "1",
    "category_id": 9474,
    "product_codes": [
        "5056345008268"
    ],
    "rrp": "7.50",
    "product_name": "4 GLUE STICK Washable Adhesives Home School Office Decorative Craft",
    "brand_name": "Krd Ltd",
    "description": html_description,
    "default_image": "https://i.ebayimg.com/images/g/8L0AAOSwvIhjaOh7/s-l1600.jpg",
    "additional_images": [
        "https://i.ebayimg.com/images/g/UJIAAOSwKCZjaOiL/s-l140.jpg"
    ],
    "force_update": True
}

response = requests.post(
    "https://api.onbuy.com/v2/products",
    json=payload,
    headers={
        "Authorization": token,
        "Content-Type": "application/json"
    },
    timeout=60
)

print("Status:", response.status_code)
print("Headers:", response.headers)
print("Response:")
print(response.text)
print("Calling:")
print("https://api.onbuy.com/v2/products")
