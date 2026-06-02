import os
import requests

# AUTH
auth = requests.post(
    "https://api.onbuy.com/v2/auth/request-token",
    data={
        "consumer_key": os.getenv("ONBUY_CONSUMER_KEY"),
        "secret_key": os.getenv("ONBUY_SECRET_KEY")
    }
)

token = auth.json()["access_token"]

payload = {
    "site_id": int(os.getenv("ONBUY_SITE_ID")),
    "seller_id": int(os.getenv("ONBUY_SELLER_ID")),
    "listings": [
        {
            "sku": "194343045790-test",
            "price": 7.5,
            "stock": 10,
            "boost_marketing_commission": 0
        }
    ]
}

response = requests.put(
    "https://api.onbuy.com/v2/listings/by-sku",
    json=payload,
    headers={
        "Authorization": token,
        "Content-Type": "application/json"
    }
)

print("Status:", response.status_code)
print(response.text)
