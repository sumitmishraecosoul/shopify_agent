from typing import List

import requests

from .config import settings
from .models import BasketRecommendation


class ShopifyClient:
    """
    Minimal Shopify cart/checkout client.
    This assumes Storefront or AJAX cart APIs; you will need
    to adapt URLs and auth according to your actual setup.
    """

    def __init__(self) -> None:
        self.domain = settings.SHOPIFY_STORE_DOMAIN
        self.token = settings.SHOPIFY_STOREFRONT_API_TOKEN

    def _base_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
        }
        if self.token:
            headers["X-Shopify-Storefront-Access-Token"] = self.token
        return headers

    def apply_basket_to_cart(self, basket: BasketRecommendation) -> tuple[str | None, str | None]:
        """
        Example placeholder implementation that should be wired to your real cart API.
        Returns (cart_id, checkout_url).
        """
        if not self.domain:
            # In local/dev mode we just simulate
            return "dev-cart-id", None

        # Example payload for Storefront Cart API (GraphQL or REST)
        # Here we just show structure; adapt to your actual integration.
        line_items: List[dict] = []
        for item in basket.items:
            line_items.append(
                {
                    "merchandiseId": item.product_id,
                    "quantity": item.packs,
                }
            )

        # You will need to call the correct Shopify endpoint here.
        # This is intentionally left as a stub to avoid hard-coding API specifics.
        # Example:
        # url = f"https://{self.domain}/api/2024-01/graphql.json"
        # resp = requests.post(url, headers=self._base_headers(), json={...})
        # resp.raise_for_status()

        # For now we just return a placeholder
        return "cart-id-placeholder", f"https://{self.domain}/cart"


shopify_client = ShopifyClient()

