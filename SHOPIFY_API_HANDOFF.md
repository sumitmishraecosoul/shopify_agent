# Shopify Developer API Handoff

This document lists the APIs currently available in Swagger and what to share with Shopify developer.

## Base URLs

- Local: `http://127.0.0.1:8000`
- Office network: `http://192.168.50.56:8000`
- Swagger: `/docs`
- OpenAPI JSON: `/openapi.json`

## API style

- Protocol: HTTP/HTTPS REST
- Payload format: JSON
- WebSocket: Not used currently (normal APIs only)
- Authentication in current Swagger APIs: Implemented with bearer token for all `/api/v1/*` business APIs.

---

## A) Shopify-facing APIs (use these for Shopify integration)

All below routes are under prefix: `/api/v1`

### 0) Login API (mandatory first call)

- Endpoint URL: `POST /api/v1/auth/login`
- Purpose: Generate bearer token.
- Required headers:
  - `Content-Type: application/json`

#### Request body

```json
{
  "username": "shopify_client_app",
  "password": "change-me"
}
```

#### Response body

```json
{
  "access_token": "random-secure-token",
  "token_type": "bearer",
  "expires_in": 3600
}
```

Use `access_token` in all protected APIs:

`Authorization: Bearer <access_token>`

### 1) Chat API

- Endpoint URL: `POST /api/v1/chat`
- Purpose: Main chatbot API for party planning + product browsing.
- Required headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <token>`

#### Request body

```json
{
  "session_id": "string",
  "message": "Plan a party for 50 people",
  "conversation_history": []
}
```

#### Response body (shape)

```json
{
  "success": true,
  "session_id": "string",
  "response": {
    "message": "string",
    "type": "party_recommendation | product_list | question | category_menu | subcategory_menu | clarification | error",
    "suggested_products": [],
    "additional_recommendations": [],
    "cart_items": [],
    "cart_permalink": "string or null",
    "routine_name": "string or null",
    "routine_steps": [],
    "routine_total": 0,
    "routine_savings": 0,
    "add_all_to_cart_button": true,
    "quick_replies": [],
    "suggested_questions": []
  },
  "conversation_context": {
    "detected_intent": "string",
    "detected_disposables": [],
    "party_size": 0,
    "estimated_coverage": {},
    "products_discussed": [],
    "pending_actions": [],
    "mode": "PARTY_PLANNING | PRODUCT_BROWSING | PRODUCT_SEARCH | EVENT_CAMPAIGN",
    "current_category": "string or null",
    "current_subcategory": "string or null"
  }
}
```

#### Product data called in this API

From suggested product cards, Shopify dev can use:

- `product_id` (Shopify product id/GID)
- `variant_id` (Shopify variant id/GID or numeric id)
- `title`, `description`, `price`, `currency`
- `product_url`
- `pack_size`, `packs_recommended`, `total_units`
- optional `cart_item: { id, quantity }`

---

### 2) Add to cart API

- Endpoint URL: `POST /api/v1/cart/add`
- Purpose: Add/update/remove items in session cart and optionally push to real Shopify cart (if cart token is passed).
- Required headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <token>`

#### Request body

```json
{
  "session_id": "string",
  "items": [
    { "id": 45006975795353, "quantity": 2 }
  ],
  "cart_action": "add",
  "checkout_after_add": false,
  "customer_id": "optional-string",
  "cart_token": "optional-shopify-cart-token"
}
```

`cart_action` supported values:
- `add`
- `set` / `update`
- `remove` / `delete`

#### Response body

```json
{
  "success": true,
  "cart": {
    "cart_id": "placeholder-cart-id",
    "cart_url": "https://yourstore.com/cart",
    "checkout_url": "https://yourstore.com/checkout",
    "items_count": 2,
    "total_price": 0,
    "items": [
      { "id": 45006975795353, "quantity": 2 }
    ],
    "cart_permalink": "https://yourstore.com/cart/45006975795353:2"
  },
  "message": "Updated cart (send cart_token to add to real Shopify cart).",
  "next_suggestions": []
}
```

---

### 3) Add multiple items API

- Endpoint URL: `POST /api/v1/cart/add-multiple`
- Purpose: Add full bundle/routine in one call.
- Required headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <token>`

#### Request body

```json
{
  "session_id": "string",
  "items": [
    { "id": 45006975795353, "quantity": 2 },
    { "id": 45006975795354, "quantity": 1 }
  ],
  "routine_id": "optional-string",
  "apply_routine_discount": false,
  "cart_token": "optional-shopify-cart-token"
}
```

#### Response body

Same structure as `/api/v1/cart/add` response.

---

### 4) Get session summary API

- Endpoint URL: `GET /api/v1/session/{session_id}`
- Purpose: Get latest conversation summary.
- Required headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <token>`

#### Request path param

- `session_id` (string)

#### Response body

```json
{
  "session_id": "string",
  "customer_info": null,
  "conversation_summary": {
    "party_size": 50,
    "menu_type": null,
    "disposables_needed": ["plates", "cups", "spoons"],
    "products_recommended": [],
    "products_added_to_cart": [],
    "pending_questions": []
  }
}
```

---

### 5) Clear session API

- Endpoint URL: `DELETE /api/v1/session/{session_id}`
- Purpose: Clear session state.
- Required headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <token>`

#### Response body

```json
{
  "success": true,
  "message": "Session cleared"
}
```

---

### 6) Inventory refresh trigger API (for your data pipeline)

- Endpoint URL: `POST /api/v1/inventory/refresh`
- Purpose: Trigger the backend to download the **latest daily** inventory JSON from Azure and reload in-memory catalog (no server restart needed).
- Required headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <token>`

Auth options:
- **Option A (service token, recommended for scripts)**: set `INVENTORY_REFRESH_SERVICE_TOKEN` on backend and send it as bearer token.
- **Option B (login token)**: call `/api/v1/auth/login` then use returned bearer token.

#### Request body (optional)

```json
{
  "date": "2026-04-30",
  "force": false
}
```

- `date` (optional): `YYYY-MM-DD` to avoid “after midnight” edge cases (pipeline can pass the exact upload date).
- `force` (optional): if `true`, re-download/reload even if Azure ETag is unchanged.

#### Response body

```json
{
  "success": true,
  "message": "refreshed | reloaded | unchanged",
  "products_loaded": 343,
  "etag": "\"0x8D...\"",
  "blob_path": "data_dump/inventory/shopify/2026/04/30/us_shopify_inventory.json",
  "duration_ms": 812,
  "downloaded": true,
  "bytes": 1404757,
  "previous_etag": "\"0x8D...\"",
  "previous_blob_path": "data_dump/inventory/shopify/2026/04/30/us_shopify_inventory.json"
}
```

When to call this:
- After your ETL/script finishes uploading the JSON to Azure for that day (path uses `{YYYY}/{MM}/{DD}`).

---

## B) Internal APIs (generally not for Shopify frontend handoff)

These are in root app Swagger too but mainly internal/demo use:

- `GET /health`
- `POST /chat`
- `POST /cart/apply`

---

## C) Authentication status and requirement

### Current status

- Login API is implemented: `POST /api/v1/auth/login`.
- Bearer-token validation is enforced on business endpoints in `/api/v1/*`.
- Token type is `bearer`; token must be passed in request header.

### What to tell Shopify developer now

1. Call login API first to get access token.
2. Pass token in `Authorization` header for all `/api/v1/chat`, `/api/v1/cart/*`, and `/api/v1/session/*` calls.
3. If token expires, call login again and retry.

### Implemented security contract

#### Login API

- Endpoint: `POST /api/v1/auth/login`
- Request: username/password JSON
- Response: `access_token`, `token_type`, `expires_in`

#### How to pass bearer token

- Pass in header for all protected APIs:

`Authorization: Bearer <access_token>`

#### Example secured request headers

```http
Content-Type: application/json
Authorization: Bearer eyJhbGciOi...
```

---

## D) One-page answer to developer questions

- API details: documented above per endpoint.
- Endpoint URL: base URL + route.
- Method: `GET` / `POST` / `DELETE` as listed.
- Required headers: `Content-Type` + `Authorization: Bearer <token>` for protected APIs.
- Request format: JSON body (except GET/DELETE with path params).
- Response format: JSON.
- Product data fields used: `product_id`, `variant_id`, `title`, `price`, `product_url`, `pack_size`, `packs_recommended`, `total_units`, optional `cart_item`.
- Authentication: bearer token implemented and enforced.
- Token pass location: header (recommended), not body.
- WebSocket or normal API: normal REST API (no WebSocket currently).
