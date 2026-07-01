"""OnBuy REST API client.

Wraps the auth / create-product / update-listing calls that previously only
existed as disconnected manual test scripts (test_onbuy_auth.py,
test_create_product.py, test_update_listing.py) so the scheduled pipeline can
actually push to OnBuy's API instead of relying solely on the XML feed
importer. The XML feed generation stays in generate_xml.py as a fallback.
"""
import logging
import os

import requests

from retry_utils import AuthError, PermanentError, raise_for_status, with_retry

logger = logging.getLogger("onbuy_sync")

BASE_URL = "https://api.onbuy.com/v2"


class OnBuyClient:
    def __init__(self, consumer_key=None, secret_key=None, seller_id=None, site_id=None):
        self.consumer_key = consumer_key or os.getenv("ONBUY_CONSUMER_KEY")
        self.secret_key = secret_key or os.getenv("ONBUY_SECRET_KEY")
        self.seller_id = int(seller_id or os.getenv("ONBUY_SELLER_ID") or 0)
        self.site_id = int(site_id or os.getenv("ONBUY_SITE_ID") or 0)
        self._token = None

    def authenticate(self):
        """Returns True on success. Never raises - callers must check the
        return value before doing per-item work. The old pipeline used a
        possibly-None eBay token as if it were valid, letting a bad token
        cascade through an entire batch; this client refuses to do that."""
        if not self.consumer_key or not self.secret_key:
            logger.error("OnBuy credentials missing (ONBUY_CONSUMER_KEY/ONBUY_SECRET_KEY)")
            return False

        def _do_auth():
            resp = requests.post(
                f"{BASE_URL}/auth/request-token",
                data={"consumer_key": self.consumer_key, "secret_key": self.secret_key},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            raise_for_status(resp, what="onbuy auth")
            token = resp.json().get("access_token")
            if not token:
                raise AuthError("onbuy auth response missing access_token")
            return token

        try:
            self._token = with_retry(_do_auth, what="onbuy auth", max_attempts=3)
        except (AuthError, PermanentError) as exc:
            logger.error("OnBuy authentication failed: %s", exc)
            self._token = None
        except Exception as exc:
            logger.error("OnBuy authentication failed after retries: %s", exc)
            self._token = None
        return bool(self._token)

    def health_check(self):
        return bool(self._token) or self.authenticate()

    def _headers(self):
        # OnBuy expects the raw token as the Authorization value, not "Bearer <token>"
        # - matches the already-verified test_create_product.py/test_update_listing.py behavior.
        return {"Authorization": self._token, "Content-Type": "application/json"}

    def create_product(self, *, sku, ean, title, description, brand, category_id, price, main_image, additional_images):
        payload = {
            "site_id": self.site_id,
            "seller_id": self.seller_id,
            "uid": sku,
            "published": "1",
            "category_id": category_id,
            "product_codes": [ean] if ean else [],
            "rrp": str(price),
            "product_name": title[:150],
            "brand_name": brand or "Unbranded",
            "description": description,
            "default_image": main_image,
            "additional_images": additional_images[:10],
            "force_update": True,
        }

        def _do_create():
            resp = requests.post(f"{BASE_URL}/products", json=payload, headers=self._headers(), timeout=60)
            raise_for_status(resp, what=f"onbuy create_product({sku})")
            return resp.json()

        return with_retry(_do_create, what=f"onbuy create_product({sku})", max_attempts=3)

    def update_listing(self, *, sku, price, stock):
        payload = {
            "site_id": self.site_id,
            "seller_id": self.seller_id,
            "listings": [{"sku": sku, "price": price, "stock": stock, "boost_marketing_commission": 0}],
        }

        def _do_update():
            resp = requests.put(f"{BASE_URL}/listings/by-sku", json=payload, headers=self._headers(), timeout=60)
            raise_for_status(resp, what=f"onbuy update_listing({sku})")
            return resp.json()

        return with_retry(_do_update, what=f"onbuy update_listing({sku})", max_attempts=3)

    def sync_product(self, **kwargs):
        """Update price/stock for an existing SKU; if OnBuy reports the SKU
        doesn't exist yet (a permanent 4xx on the update call), create it
        instead. OnBuy's exact "SKU not found" response hasn't been observed
        against a real unknown SKU yet - verify this fallback the first time
        a genuinely new SKU goes through ONBUY_API_PUSH_ENABLED before trusting
        it at full-catalog scale.
        """
        sku, price, stock = kwargs["sku"], kwargs["price"], kwargs["stock"]
        try:
            return "updated", self.update_listing(sku=sku, price=price, stock=stock)
        except PermanentError as exc:
            logger.info("update_listing(%s) rejected (%s) - attempting create_product instead", sku, exc)
            result = self.create_product(
                sku=sku,
                ean=kwargs.get("ean"),
                title=kwargs["title"],
                description=kwargs["description"],
                brand=kwargs.get("brand"),
                category_id=kwargs["category_id"],
                price=price,
                main_image=kwargs.get("main_image", ""),
                additional_images=kwargs.get("additional_images", []),
            )
            return "created", result
