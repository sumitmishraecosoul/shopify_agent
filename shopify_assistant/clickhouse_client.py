import json
import re
from pathlib import Path
from typing import List, Dict, Any


class ClickHouseClient:
    """
    Temporary stand-in data provider that reads from a local JSON
    inventory export (us_shopify_inventory1.json) instead of ClickHouse.

    The public method signature is kept the same so we can later
    replace this implementation with a real ClickHouse-backed one
    without changing the rest of the code.
    """

    def __init__(self) -> None:
        base_dir = Path(__file__).resolve().parent
        json_path = base_dir / "us_shopify_inventory1.json"
        if not json_path.exists():
            self._products: List[Dict[str, Any]] = []
            return

        with json_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        products: List[Dict[str, Any]] = []
        regions = raw.get("regions", [])
        for region in regions:
            items = region.get("items", [])
            for item in items:
                variant = (item or {}).get("variant") or {}
                product = variant.get("product") or {}

                title = product.get("title") or ""
                variant_title = variant.get("title") or ""
                product_type = product.get("productType") or ""

                # Derive a coarse category from product type / title
                text_for_cat = f"{product_type} {title}".lower()
                if "plate" in text_for_cat:
                    category = "plates"
                elif "bowl" in text_for_cat:
                    category = "bowls"
                elif "spoon" in text_for_cat:
                    category = "spoons"
                elif "fork" in text_for_cat:
                    category = "forks"
                elif "cup" in text_for_cat or "glass" in text_for_cat:
                    category = "cups"
                else:
                    category = "other"

                # Try to infer pack size from variant/product title (first integer found)
                pack_size = 30
                m = re.search(r"(\d+)", variant_title) or re.search(r"(\d+)", title)
                if m:
                    try:
                        pack_size = int(m.group(1))
                    except Exception:
                        pack_size = 30

                # Simple eco_score heuristic
                eco_text = f"{title} {product_type}".lower()
                if any(k in eco_text for k in ["compostable", "areca", "palm", "bamboo", "sugarcane"]):
                    eco_score = 5
                else:
                    eco_score = 3

                # Price in cents; prefer explicit price_list if present, otherwise variant price
                price_cents = 0
                price_list = item.get("price_list") or {}
                price = price_list.get("amount") or variant.get("price") or variant.get("compareAtPrice")
                if price is not None:
                    try:
                        price_cents = int(float(price) * 100)
                    except Exception:
                        price_cents = 0

                inventory_qty = variant.get("inventoryQuantity") or 0
                available = inventory_qty > 0

                products.append(
                    {
                        # Internal identifier used by the recommendation engine (kept stable)
                        "product_id": str(variant.get("id") or item.get("id") or ""),
                        # Extra Shopify identifiers for external API responses
                        "variant_gid": str(variant.get("id") or ""),
                        "product_gid": str(product.get("id") or ""),
                        "handle": str(product.get("handle") or ""),
                        "title": title,
                        "product_type": product_type,
                        "category": category,
                        "pack_size": pack_size,
                        "material": "",
                        "eco_score": eco_score,
                        "price_cents": price_cents,
                        "tags": [product_type],
                        "available": available,
                    }
                )

        self._products = products

    def fetch_products_for_category(
        self,
        category: str,
        eco_preference: str | None = None,
        max_price_cents: int | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter the in-memory product list by coarse category and simple constraints.
        """
        records = [p for p in self._products if p.get("category") == category and p.get("available")]

        if eco_preference and eco_preference.lower() == "high":
            records = [p for p in records if int(p.get("eco_score", 0)) >= 4]

        if max_price_cents is not None:
            records = [p for p in records if int(p.get("price_cents", 0)) <= max_price_cents]

        # Sort roughly by eco_score desc, then price asc
        records.sort(key=lambda r: (-int(r.get("eco_score", 0)), int(r.get("price_cents", 0))))
        return records

    def get_product_by_internal_id(self, product_id: str) -> Dict[str, Any] | None:
        """
        Look up a product record by the internal product_id we expose to the
        recommendation engine. This is used by the external API layer to
        enrich basket items with Shopify GIDs, handles, etc.
        """
        for p in self._products:
            if str(p.get("product_id")) == str(product_id):
                return p
        return None

    def search_products(
        self,
        query: str | None = None,
        category: str | None = None,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        """
        Simple keyword and/or category search across the in-memory product list.
        Intended for product browsing / fallback search flows.

        Rules:
        - If category is provided: we always filter by category and ignore query
          (so 'Cold cups' and 'Hot cups' both show cups, even if titles differ).
        - If no category: we use query to filter by title/product_type.
        """
        q = (query or "").strip().lower()
        results: List[Dict[str, Any]] = []

        for p in self._products:
            if not p.get("available"):
                continue
            if category:
                if p.get("category") != category:
                    continue
            elif q:
                haystack = f"{p.get('title','')} {p.get('product_type','')}".lower()
                if q not in haystack:
                    continue
            results.append(p)
            if len(results) >= limit:
                break
        return results


clickhouse_client = ClickHouseClient()

