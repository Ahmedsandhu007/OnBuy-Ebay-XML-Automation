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
    "site_id": int(os.getenv("ONBUY_SITE_ID")),
    "seller_id": int(os.getenv("ONBUY_SELLER_ID")),
    "uid": "TEST-001",
    "published": "1",
    "category_id": 2305,
    "product_codes": [
        "1234567890123"
    ],
    "product_name": "Test Coffee Machine",
    "brand_name": "Test Brand",
    "description": "<p>Test Product From API</p>",
    "default_image": "https://upload.wikimedia.org/wikipedia/commons/3/3f/JPEG_example_flower.jpg",
    "force_update": True,
    "ai_content_marked": False
}

response = requests.post(
    "https://api.onbuy.com/gb/v2/products",
    json=payload,
    headers={
        "Authorization": token,
        "Content-Type": "application/json"
    },
    timeout=60
)

print("Status:", response.status_code)
print(response.text)
