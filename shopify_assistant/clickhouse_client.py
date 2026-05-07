import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path
from typing import List, Dict, Any

import requests
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None
try:
    from azure.storage.blob import BlobClient
except Exception:  # pragma: no cover
    BlobClient = None


class ClickHouseClient:
    """
    Temporary stand-in data provider that reads from a local JSON
    inventory export (us_shopify_inventory1.json) instead of ClickHouse.

    The public method signature is kept the same so we can later
    replace this implementation with a real ClickHouse-backed one
    without changing the rest of the code.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._base_dir = Path(__file__).resolve().parent
        self._json_path_primary = self._base_dir / "us_shopify_inventory.json"
        self._json_path_v3 = self._base_dir / "us_shopify_inventory3.json"
        self._json_path_v1 = self._base_dir / "us_shopify_inventory1.json"
        self._last_inventory_etag: str | None = None
        self._last_inventory_blob_path: str | None = None
        self._last_inventory_bytes: int | None = None

        # Initial load on startup (download if configured).
        ok, msg, _ = self.refresh_inventory(download_from_azure=True, log=True)
        if not ok:
            sys.stderr.write(f"Inventory load failed: {msg}\n")
            sys.stderr.flush()

    def refresh_inventory(
        self,
        *,
        download_from_azure: bool = True,
        log: bool = False,
        requested_date: str | None = None,
        force: bool = False,
        container_name: str | None = None,
        blob_path: str | None = None,
        triggered_by: str | None = None,
    ) -> tuple[bool, str, dict]:
        """
        Refresh inventory at runtime.
        - If download_from_azure: attempts to download the daily JSON to `us_shopify_inventory.json`
        - Reloads the in-memory product catalog atomically
        Returns (ok, message, meta).
        """
        with self._lock:
            started = time.time()
            if load_dotenv is not None:
                try:
                    load_dotenv(self._base_dir / ".env", override=False)
                except Exception:
                    pass

            downloaded = False
            download_msg = "skipped"
            meta: dict = {
                "etag": self._last_inventory_etag,
                "blob_path": self._last_inventory_blob_path,
                "previous_etag": self._last_inventory_etag,
                "previous_blob_path": self._last_inventory_blob_path,
                "bytes": self._last_inventory_bytes,
                "downloaded": False,
            }
            if download_from_azure:
                downloaded, download_msg, dl_meta = self._download_inventory_to_primary(
                    requested_date=requested_date,
                    force=force,
                    container_name=container_name,
                    blob_path=blob_path,
                )
                meta.update(dl_meta or {})
                meta["downloaded"] = bool(downloaded)
                if log:
                    who = f" triggered_by={triggered_by}" if triggered_by else ""
                    sys.stderr.write(f"{download_msg}{who}\n")
                    sys.stderr.flush()

                # If force was requested, we expect a re-download attempt. If it didn't download,
                # return a failure so callers can detect that the remote refresh did not happen.
                if force and not downloaded:
                    meta["duration_ms"] = int((time.time() - started) * 1000)
                    return False, f"force requested but download did not occur: {download_msg}", meta

                # Idempotency: if Azure file is unchanged and force is false, skip reloading/parsing.
                if (
                    (not force)
                    and (not downloaded)
                    and isinstance(download_msg, str)
                    and download_msg.startswith("Inventory unchanged")
                    and self._products is not None
                    and len(self._products) > 0
                ):
                    meta["duration_ms"] = int((time.time() - started) * 1000)
                    meta["products_loaded"] = len(self._products)
                    return True, "unchanged", meta

            json_path = self._select_local_inventory_path()
            if not json_path.exists():
                self._products = []
                meta["duration_ms"] = int((time.time() - started) * 1000)
                return False, f"no local inventory file found (expected {self._json_path_primary.name} or fallbacks)", meta

            try:
                products = self._parse_inventory_json(json_path)
            except Exception as ex:
                meta["duration_ms"] = int((time.time() - started) * 1000)
                return False, f"failed to parse inventory JSON ({json_path.name}): {ex}", meta

            self._products = products
            if log:
                sys.stderr.write(f"Inventory loaded from {json_path.name}; products={len(products)}\n")
                sys.stderr.flush()
            meta["duration_ms"] = int((time.time() - started) * 1000)
            meta["products_loaded"] = len(products)
            return True, "refreshed" if downloaded else "reloaded", meta

    def _select_local_inventory_path(self) -> Path:
        # Load order:
        # 1) us_shopify_inventory.json (downloaded-from-Azure if configured)
        # 2) us_shopify_inventory3.json
        # 3) us_shopify_inventory1.json
        if self._json_path_primary.exists():
            return self._json_path_primary
        return self._json_path_v3 if self._json_path_v3.exists() else self._json_path_v1

    def _download_inventory_to_primary(
        self,
        *,
        requested_date: str | None,
        force: bool,
        container_name: str | None,
        blob_path: str | None,
    ) -> tuple[bool, str, dict]:
        """
        Download inventory JSON to `us_shopify_inventory.json` using either:
        - INVENTORY_JSON_URL (direct/SAS URL)
        - INVENTORY_JSON_BLOB_* (container url + sas + path template)
        - AZURE_* (connection string + container + path template)
        Returns (downloaded, message, meta).
        """
        meta: dict = {"etag": None, "blob_path": None, "bytes": None}

        def _resolve_date() -> datetime:
            if not requested_date:
                return datetime.now()
            try:
                return datetime.strptime(requested_date.strip(), "%Y-%m-%d")
            except Exception:
                return datetime.now()

        dt = _resolve_date()
        blob_path_override = (blob_path or "").strip().lstrip("/") or None
        container_override = (container_name or "").strip() or None

        # 1) Direct/SAS URL
        inventory_url = (os.getenv("INVENTORY_JSON_URL") or "").strip()
        if inventory_url:
            try:
                r = requests.get(inventory_url, timeout=60)
                r.raise_for_status()
                etag = (r.headers.get("ETag") or r.headers.get("Etag") or "").strip() or None
                meta.update({"etag": etag, "blob_path": urlparse(inventory_url).path})
                if (not force) and etag and etag == self._last_inventory_etag:
                    return False, "Inventory unchanged (etag match) - skipping download", meta
                self._json_path_primary.write_bytes(r.content)
                self._last_inventory_etag = etag
                self._last_inventory_blob_path = meta["blob_path"]
                self._last_inventory_bytes = len(r.content)
                meta["bytes"] = self._last_inventory_bytes
                return True, "Loaded inventory from Azure (URL) -> saved to us_shopify_inventory.json", meta
            except Exception as ex:
                return False, f"Azure URL download failed: {ex}", meta

        # 2) Container URL + SAS token + template
        container_url = (os.getenv("INVENTORY_JSON_BLOB_CONTAINER_URL") or "").strip().rstrip("/")
        sas_token = (os.getenv("INVENTORY_JSON_BLOB_SAS_TOKEN") or "").strip().lstrip("?")
        blob_path_template = (os.getenv("INVENTORY_JSON_BLOB_PATH_TEMPLATE") or "").strip()
        if container_url and sas_token and blob_path_template:
            resolved_blob_path = blob_path_override or blob_path_template.format(
                YYYY=dt.strftime("%Y"),
                MM=dt.strftime("%m"),
                DD=dt.strftime("%d"),
            ).lstrip("/")
            url = f"{container_url}/{resolved_blob_path}?{sas_token}"
            meta["blob_path"] = resolved_blob_path
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                etag = (r.headers.get("ETag") or r.headers.get("Etag") or "").strip() or None
                meta["etag"] = etag
                if (not force) and etag and etag == self._last_inventory_etag and resolved_blob_path == self._last_inventory_blob_path:
                    return False, "Inventory unchanged (etag match) - skipping download", meta
                self._json_path_primary.write_bytes(r.content)
                self._last_inventory_etag = etag
                self._last_inventory_blob_path = resolved_blob_path
                self._last_inventory_bytes = len(r.content)
                meta["bytes"] = self._last_inventory_bytes
                return True, "Loaded inventory from Azure (SAS) -> saved to us_shopify_inventory.json", meta
            except Exception as ex:
                return False, f"Azure SAS download failed: {ex}", meta

        # 3) Connection string + container + template
        azure_conn = (os.getenv("AZURE_CONNECTION_STRING") or "").strip()
        azure_container = container_override or (os.getenv("AZURE_CONTAINER_NAME") or "").strip()
        azure_blob_template = (os.getenv("AZURE_BLOB_PATH_TEMPLATE") or "").strip()
        if azure_conn and azure_container and azure_blob_template and BlobClient is not None:
            resolved_blob_path = blob_path_override or azure_blob_template.format(
                YYYY=dt.strftime("%Y"),
                MM=dt.strftime("%m"),
                DD=dt.strftime("%d"),
            ).lstrip("/")
            meta["blob_path"] = resolved_blob_path
            try:
                bc = BlobClient.from_connection_string(
                    conn_str=azure_conn,
                    container_name=azure_container,
                    blob_name=resolved_blob_path,
                )
                try:
                    props = bc.get_blob_properties()
                    etag = str(getattr(props, "etag", "") or "")
                    etag = etag.strip() or None
                except Exception:
                    etag = None
                meta["etag"] = etag
                if (not force) and etag and etag == self._last_inventory_etag and resolved_blob_path == self._last_inventory_blob_path:
                    return False, "Inventory unchanged (etag match) - skipping download", meta
                data = bc.download_blob().readall()
                if not data:
                    return False, "Azure download returned empty payload", meta
                self._json_path_primary.write_bytes(data)
                self._last_inventory_etag = etag
                self._last_inventory_blob_path = resolved_blob_path
                self._last_inventory_bytes = len(data)
                meta["bytes"] = self._last_inventory_bytes
                return True, "Loaded inventory from Azure (conn str) -> saved to us_shopify_inventory.json", meta
            except Exception as ex:
                return False, f"Azure conn-string download failed: {ex}", meta

        return False, "No Azure inventory source configured (set INVENTORY_JSON_URL or AZURE_* or INVENTORY_JSON_BLOB_*)", meta

    def _parse_inventory_json(self, json_path: Path) -> List[Dict[str, Any]]:
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
                featured_image = product.get("featuredImage") or {}
                image_url = featured_image.get("url") or None
                image_alt = featured_image.get("altText") or None

                text_for_cat = f"{product_type} {title}".lower()
                if "plate" in text_for_cat:
                    category = "plates"
                elif "bowl" in text_for_cat:
                    category = "bowls"
                elif "spoon" in text_for_cat or "cutlery" in text_for_cat:
                    category = "spoons"
                elif "fork" in text_for_cat:
                    category = "forks"
                elif "cup" in text_for_cat or "glass" in text_for_cat:
                    category = "cups"
                else:
                    category = "other"

                pack_size = 30
                m = re.search(r"(\d+)", variant_title) or re.search(r"(\d+)", title)
                if m:
                    try:
                        pack_size = int(m.group(1))
                    except Exception:
                        pack_size = 30

                eco_text = f"{title} {product_type}".lower()
                if any(k in eco_text for k in ["compostable", "areca", "palm", "bamboo", "sugarcane"]):
                    eco_score = 5
                else:
                    eco_score = 3

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
                        "product_id": str(variant.get("id") or item.get("id") or ""),
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
                        "image_url": image_url,
                        "image_alt": image_alt,
                    }
                )

        return products
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
        # Token-based matching makes free-text search robust:
        # "show me wipes for home" should still match wipe products.
        raw_tokens = re.findall(r"[a-z0-9]+", q)
        stop_words = {
            "show",
            "me",
            "for",
            "the",
            "a",
            "an",
            "and",
            "or",
            "to",
            "of",
            "in",
            "on",
            "with",
            "please",
            "need",
            "want",
            "looking",
            "products",
            "product",
            "browse",
            "search",
            "find",
        }
        tokens = [t for t in raw_tokens if t not in stop_words and len(t) >= 3]

        def _match_token(tok: str, haystack: str) -> bool:
            if tok in haystack:
                return True
            # basic singular/plural normalization
            if tok.endswith("s") and tok[:-1] and tok[:-1] in haystack:
                return True
            if f"{tok}s" in haystack:
                return True
            return False

        for p in self._products:
            if not p.get("available"):
                continue
            if category:
                if p.get("category") != category:
                    continue
            elif q:
                haystack = f"{p.get('title','')} {p.get('product_type','')} {' '.join(p.get('tags', []) or [])}".lower()
                # Prefer token matching for natural language queries. Fall back to
                # strict substring for short/simple queries.
                if tokens:
                    if not any(_match_token(tok, haystack) for tok in tokens):
                        continue
                elif q not in haystack:
                    continue
            results.append(p)
            if len(results) >= limit:
                break
        return results


clickhouse_client = ClickHouseClient()

