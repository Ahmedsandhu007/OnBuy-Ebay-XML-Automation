import os
import requests

CONSUMER_KEY = os.getenv("ONBUY_CONSUMER_KEY")
SECRET_KEY = os.getenv("ONBUY_SECRET_KEY")

print("Consumer Key:", CONSUMER_KEY[:10] + "...")
print("Secret Key Loaded:", bool(SECRET_KEY))

# We will determine the correct OAuth flow from documentation
