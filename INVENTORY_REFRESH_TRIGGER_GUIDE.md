# Inventory Refresh Trigger (For Data Pipeline Developer)

This guide explains how to call the backend **refresh trigger API** after your script uploads the daily Shopify inventory JSON to Azure Blob Storage.

## What your pipeline should do (high level)

1. Export Shopify inventory JSON.
2. Upload it to Azure Blob at the daily path (example):
   - `data_dump/inventory/shopify/YYYY/MM/DD/us_shopify_inventory.json`
3. After upload is complete, call the backend trigger API:
   - `POST /api/v1/inventory/refresh`

The backend will download the latest blob and reload inventory **without restart**.

---

## Base URL (you will be given one)

You will receive one of these from the backend team:

- Local/staging: `http://127.0.0.1:8010`
- VPS: `http://<VPS_IP>:8010`
- Domain: `https://<domain>`

Use it like:

- `BASE_URL = http://<host>:8010`

---

## Authentication (recommended: Service Token)

Use a stable token so you **do not need login** in scripts.

### Required header

`Authorization: Bearer <INVENTORY_REFRESH_SERVICE_TOKEN>`

The backend team will give you the token value.

---

## Trigger API

- **Method**: `POST`
- **URL**: `${BASE_URL}/api/v1/inventory/refresh`
- **Headers**:
  - `Content-Type: application/json`
  - `Authorization: Bearer <INVENTORY_REFRESH_SERVICE_TOKEN>`

### Request body

Send the date you uploaded (prevents “after midnight” edge cases).

```json
{
  "date": "2026-04-30",
  "force": false
}
```

- **date**: `YYYY-MM-DD` (the date folder you uploaded into)
- **force**:
  - `false` (default): backend uses ETag idempotency; if unchanged, it returns `message: "unchanged"`
  - `true`: backend re-downloads/reloads even if unchanged

---

## Expected response (example)

```json
{
  "success": true,
  "message": "refreshed | reloaded | unchanged",
  "products_loaded": 244,
  "etag": "\"0x8DEA...\"",
  "blob_path": "data_dump/inventory/shopify/2026/04/30/us_shopify_inventory.json",
  "duration_ms": 1086,
  "downloaded": false,
  "bytes": null,
  "previous_etag": "\"0x8DEA...\"",
  "previous_blob_path": "data_dump/inventory/shopify/2026/04/30/us_shopify_inventory.json"
}
```

Meaning of `message`:
- **refreshed**: downloaded a new blob + reloaded in memory
- **reloaded**: did not download (or download failed), but reloaded from local inventory file
- **unchanged**: blob is the same (ETag match), so backend skipped reload for speed

---

## Copy/paste Python example (with retry/backoff)

```python
import time
import requests
from datetime import date

BASE_URL = "http://<VPS_IP>:8010"
SERVICE_TOKEN = "<INVENTORY_REFRESH_SERVICE_TOKEN>"

def trigger_inventory_refresh(upload_date: str, force: bool = False) -> dict:
    url = f"{BASE_URL}/api/v1/inventory/refresh"
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"date": upload_date, "force": force}

    # Retry/backoff because network blips happen
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * attempt)

if __name__ == "__main__":
    # Use the same date you used in the Azure path.
    upload_date = date.today().isoformat()  # "YYYY-MM-DD"
    resp = trigger_inventory_refresh(upload_date, force=False)
    print(resp)
```

---

## Operational notes / best practices

- **Always call refresh only after upload completes** (and the blob is visible).
- **Always pass the upload date** you used in the Azure path.
- If you upload after midnight local time, pass the explicit date folder you used.
- If the trigger returns HTTP `401`, your service token is missing/incorrect.
- If the trigger returns `success: false`, log and alert.
- If the trigger returns `message: unchanged`, that’s OK—your uploaded file matches what backend already has.

---

## Alternate auth (if service token is not available)

If your team insists on login-based tokens, you must:

1) `POST ${BASE_URL}/api/v1/auth/login` with username/password  
2) Use returned bearer token to call `/api/v1/inventory/refresh`

Service token is preferred for scripts because it is simpler and more reliable.

