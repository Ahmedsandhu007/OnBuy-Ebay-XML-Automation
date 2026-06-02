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
    "description": <strong>4 Glue Stick Washable Adhesives Home School Office Craft</strong>

<p>Brand new 4 pack of glue sticks suitable for home, school, office and craft projects.</p>

<p>These glue sticks provide a clean and easy way to bond paper, cardboard, photographs and other lightweight materials. The formula is solvent free, PVC free, non-toxic and washable from clothing at 30&deg;C.</p>

<p><strong>Features:</strong></p>

<ul>
<li>Pack of 4 glue sticks</li>
<li>Strong adhesive formula</li>
<li>Acid free</li>
<li>Solvent free</li>
<li>PVC free</li>
<li>Non-toxic</li>
<li>Easy to apply</li>
<li>Suitable for paper, card and craft projects</li>
<li>Suitable for home, school and office use</li>
<li>Washable from clothing at 30&deg;C</li>
</ul>

<p><strong>Package Includes:</strong></p>

<ul>
<li>4 x Glue Sticks</li>
</ul>

<p>Please note: Product packaging may vary.</p>,
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
print(response.text)

if response.status_code == 200:
    data = response.json()

    if "queue_id" in data:
        print("QUEUE ID:", data["queue_id"])

    if "uid" in data:
        print("UID:", data["uid"])
