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
    "description": <p><strong>4 GLUE STICK Washable Adhesives Home School Office Craft</strong></p>

<p><strong>Craft Stick Glue 4 Pack</strong></p>

<p>Brand New</p>

<p>These Stick glue is the ideal clean, quick and accurate way of sticking paper, cardboard and photos.</p>

<p>These are solvent free and PVC free therefore wash out of clothes at Thirty degrees.</p>

<p>Ideal for children to use as a PVC-free product certified to comply with stringent EU child safety legislation. These Stick glue is also solvent free, acid free and non-toxic.</p>

<h3>Features</h3>

<ul>
<li>4 Pack Stick Glue</li>
<li>Adhesive sticks</li>
<li>Acid free</li>
<li>Solvent free</li>
<li>Easy to use</li>
<li>Ideal for sticking paper and card</li>
<li>100% safe and suitable for children</li>
</ul>

<p><strong>Please Note:</strong> We will send these 4 sticks as flat packed to reduce postage cost and provide a better price.</p>

<h3>Why Buy From Us?</h3>

<ul>
<li>Top Quality Licensed Products</li>
<li>Premium eBay UK Seller</li>
<li>Same Day or Next Day Fast Dispatch</li>
</ul>

<p>If you have any problems or questions, please contact us via eBay message.</p>

<p>We answer emails within approximately 24 hours to ensure a quick response.</p>

<p>If you are satisfied with your purchase, please leave feedback and star ratings.</p>

<p><strong>Thank You!</strong></p>,
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
