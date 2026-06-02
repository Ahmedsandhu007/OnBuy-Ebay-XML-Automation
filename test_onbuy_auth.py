import os
import requests

CONSUMER_KEY = os.getenv("ONBUY_CONSUMER_KEY")
SECRET_KEY = os.getenv("ONBUY_SECRET_KEY")

url = "https://api.onbuy.com/v2/auth/request-token"

payload = {
    "consumer_key": CONSUMER_KEY,
    "secret_key": SECRET_KEY
}

headers = {
    "Content-Type": "application/x-www-form-urlencoded"
}

response = requests.post(
    url,
    data=payload,
    headers=headers,
    timeout=30
)

print("Status Code:", response.status_code)
print("Response:")
print(response.text)

if response.status_code == 200:

    token_data = response.json()

    access_token = token_data.get(
        "access_token"
    )

    print("\nSUCCESS")
    print("Token received")

    if access_token:
        print(
            access_token[:40] + "..."
        )

else:

    print("\nFAILED")
