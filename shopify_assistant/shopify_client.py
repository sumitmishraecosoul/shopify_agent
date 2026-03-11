import re
from typing import Any, List

import requests

from .config import settings
from .models import BasketRecommendation


def _variant_id_to_numeric(variant_id: str) -> str:
    """
    Shopify Ajax API (cart/add.js) expects numeric variant ID.
    We store GID (e.g. gid://shopify/ProductVariant/42112857309337); extract the number.
    """
    if not variant_id:
        return ""
    s = str(variant_id).strip()
    # GID format: gid://shopify/ProductVariant/42112857309337
    match = re.search(r"/(\d+)\s*$", s)
    if match:
        return match.group(1)
    # Already numeric
    if s.isdigit():
        return s
    return s


class ShopifyClient:
    """
    Shopify cart client: Ajax API (cart/add.js) using the store's cart cookie.
    The cart cookie = session for cart; no traditional sessionid.
    """

    def __init__(self) -> None:
        self.domain = (settings.SHOPIFY_STORE_DOMAIN or "").rstrip("/")
        self.token = settings.SHOPIFY_STOREFRONT_API_TOKEN
        self.cart_token = settings.SHOPIFY_CART_TOKEN

    def _base_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
        }
        if self.token:
            headers["X-Shopify-Storefront-Access-Token"] = self.token
        return headers

    def add_items_via_ajax(
        self,
        items: List[dict],
        cart_token: str | None = None,
    ) -> tuple[bool, dict[str, Any], str]:
        """
        Add line items to Shopify cart using Ajax API (POST /cart/add.js).
        Uses the cart cookie so Shopify associates items with the right cart.
        items: list of dicts with "variant_id" (GID or numeric) and "quantity" (int).
        cart_token: value of the "cart" cookie (or from GET /cart.js token). If None, uses settings.SHOPIFY_CART_TOKEN.
        Returns (success, cart_info, message).
        """
        token = cart_token or self.cart_token
        if not self.domain or not token:
            return False, {}, "Missing SHOPIFY_STORE_DOMAIN or cart_token (set in .env or send in request)."

        # Cookie: cart can be "token" or "token?key=..."
        cookie_value = token.split("?")[0].strip() if token else ""
        if not cookie_value:
            return False, {}, "Invalid cart_token."

        headers = {
            "Content-Type": "application/json",
            "Cookie": f"cart={token}",
        }
        base_url = self.domain
        added: List[dict] = []
        last_error = ""

        for raw_item in items:
            variant_id_raw = raw_item.get("variant_id") or raw_item.get("id") or ""
            vid = _variant_id_to_numeric(str(variant_id_raw))
            if not vid:
                last_error = f"Missing or invalid variant_id: {variant_id_raw}"
                continue
            qty = int(raw_item.get("quantity", 1))
            payload = {"id": vid, "quantity": max(1, qty)}
            try:
                r = requests.post(
                    f"{base_url}/cart/add.js",
                    json=payload,
                    headers=headers,
                    timeout=15,
                )
            except Exception as e:
                last_error = str(e)
                continue
            if r.status_code != 200:
                last_error = r.text or f"HTTP {r.status_code}"
                continue
            try:
                data = r.json()
                added.append({"variant_id": variant_id_raw, "quantity": qty, "title": data.get("title"), "price": data.get("price")})
            except Exception:
                added.append({"variant_id": variant_id_raw, "quantity": qty})

        if not added:
            return False, {}, last_error or "No items could be added."

        # Fetch current cart for summary (optional)
        cart_info: dict[str, Any] = {
            "cart_token": cookie_value,
            "cart_url": f"{base_url}/cart",
            "checkout_url": f"{base_url}/checkout",
            "items_count": len(added),
            "items": added,
            "total_price": 0.0,
        }
        try:
            r2 = requests.get(f"{base_url}/cart.js", headers=headers, timeout=10)
            if r2.status_code == 200:
                c = r2.json()
                cart_info["items_count"] = len(c.get("items", []))
                cart_info["total_price"] = float(c.get("total_price", 0) or 0) / 100.0
                cart_info["cart_id"] = c.get("token")
        except Exception:
            pass

        return True, cart_info, f"Added {len(added)} item(s) to your cart."

    def apply_basket_to_cart(self, basket: BasketRecommendation) -> tuple[str | None, str | None]:
        """
        Placeholder for internal /cart/apply flow. For real add-to-cart use add_items_via_ajax
        with cart_token from the frontend or SHOPIFY_CART_TOKEN in .env.
        Returns (cart_id, checkout_url).
        """
        if not self.domain:
            return "dev-cart-id", None
        if not self.cart_token:
            return "cart-id-placeholder", f"{self.domain}/cart"
        items = [
            {"variant_id": item.product_id, "quantity": item.packs}
            for item in basket.items
        ]
        ok, cart_info, _ = self.add_items_via_ajax(items, self.cart_token)
        if ok:
            return cart_info.get("cart_id") or "cart-id", cart_info.get("checkout_url")
        return "cart-id-placeholder", f"{self.domain}/cart"


shopify_client = ShopifyClient()

