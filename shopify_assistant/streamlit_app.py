import os
import uuid
from pathlib import Path
from typing import List, Dict, Any

import requests
import streamlit as st

# Load .env from shopify_assistant folder so PORT is available
_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    with _env.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

# Backend endpoint resolution:
# 1) BACKEND_URL env var if provided
# 2) PORT from .env (legacy behavior)
# 3) common fallback port 8000
BACKEND_URL_ENV = (os.getenv("BACKEND_URL", "") or "").strip().rstrip("/")
BACKEND_PORT = os.getenv("PORT", "8010")
API_AUTH_USERNAME = os.getenv("API_AUTH_USERNAME", "shopify_client_app")
API_AUTH_PASSWORD = os.getenv("API_AUTH_PASSWORD", "change-me")


def _candidate_backend_urls() -> list[str]:
    urls: list[str] = []
    if BACKEND_URL_ENV:
        urls.append(BACKEND_URL_ENV)
    # Prefer configured app port from .env (default 8010).
    urls.append(f"http://localhost:{BACKEND_PORT}")
    # Optional fallback if user runs temporary alternative API port.
    urls.append("http://localhost:8004")
    # de-duplicate while preserving order
    seen = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def _resolve_backend_url() -> str:
    selected = st.session_state.get("backend_url")
    if selected:
        return str(selected)

    for base in _candidate_backend_urls():
        try:
            # Confirm this backend actually exposes auth route (not just any FastAPI app).
            r = requests.post(
                f"{base}/api/v1/auth/login",
                json={"username": "", "password": ""},
                timeout=3,
            )
            # 422/401/200 means route exists; 404 means wrong backend instance.
            if r.status_code in (200, 401, 422):
                st.session_state.backend_url = base
                return base
        except requests.RequestException:
            continue
    # Default to first candidate for consistent error messaging
    fallback = _candidate_backend_urls()[0]
    st.session_state.backend_url = fallback
    return fallback


def _get_api_token() -> str:
    """
    Login once and cache bearer token in Streamlit session state.
    """
    token = st.session_state.get("api_access_token")
    if token:
        return str(token)

    username = str(st.session_state.get("api_auth_username", API_AUTH_USERNAME))
    password = str(st.session_state.get("api_auth_password", API_AUTH_PASSWORD))
    payload = {"username": username, "password": password}
    # Try selected backend first, then fall back across candidates if login route is missing.
    candidates = [str(st.session_state.get("backend_url") or "")] + _candidate_backend_urls()
    seen = set()
    for backend_url in candidates:
        if not backend_url or backend_url in seen:
            continue
        seen.add(backend_url)
        try:
            resp = requests.post(f"{backend_url}/api/v1/auth/login", json=payload, timeout=30)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json() or {}
            token = str(data.get("access_token") or "")
            if not token:
                raise RuntimeError("Auth login succeeded but access token is missing.")
            st.session_state.backend_url = backend_url
            st.session_state.api_access_token = token
            return token
        except requests.RequestException:
            continue
    raise RuntimeError(f"Could not login to backend. Please ensure API is running on configured port {BACKEND_PORT}.")


def send_message(session_id: str, message: str, history: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """
    Call the external Shopify-style chat endpoint so the demo uses
    the same schema Shopify developers will consume.
    """
    payload = {
        "session_id": session_id,
        "message": message,
        "conversation_history": history or [],
    }
    backend_url = _resolve_backend_url()
    try:
        token = _get_api_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post(f"{backend_url}/api/v1/chat", json=payload, headers=headers, timeout=60)
        if resp.status_code == 401:
            st.session_state.pop("api_access_token", None)
            token = _get_api_token()
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.post(f"{backend_url}/api/v1/chat", json=payload, headers=headers, timeout=60)
    except requests.RequestException as ex:
        return {
            "success": False,
            "session_id": session_id,
            "response": {
                "type": "error",
                "message": (
                    f"Backend is not reachable at {backend_url}. "
                    "Please start FastAPI server and try again."
                ),
                "suggested_products": [],
                "cart_items": [],
                "cart_permalink": None,
                "quick_replies": ["New session"],
                "suggested_questions": [],
            },
            "conversation_context": {},
            "raw_error": str(ex),
        }
    if resp.status_code >= 400:
        # Provide a clean error payload so the UI doesn't crash on 500s.
        return {
            "success": False,
            "session_id": session_id,
            "response": {
                "type": "error",
                "message": f"Backend error ({resp.status_code}). Please check the backend terminal logs.",
                "suggested_products": [],
                "cart_items": [],
                "cart_permalink": None,
                "quick_replies": ["New session"],
                "suggested_questions": [],
            },
            "conversation_context": {},
            "raw_error": resp.text,
        }
    return resp.json()


def cart_add(session_id: str, items: List[Dict[str, Any]], cart_action: str = "add") -> Dict[str, Any]:
    """
    Call backend cart endpoint so the server-side cart payload stays updated.
    """
    payload = {
        "session_id": session_id,
        "items": items,
        "cart_action": cart_action,
        "checkout_after_add": False,
        "cart_token": None,
    }
    backend_url = _resolve_backend_url()
    token = _get_api_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(f"{backend_url}/api/v1/cart/add", json=payload, headers=headers, timeout=60)
    if resp.status_code == 401:
        st.session_state.pop("api_access_token", None)
        token = _get_api_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post(f"{backend_url}/api/v1/cart/add", json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()

def _add_to_mock_cart(title: str, variant_id: str, quantity: int) -> None:
    variant_id = str(variant_id or "")
    quantity = int(quantity or 1)
    if quantity <= 0:
        return
    for item in st.session_state.cart:
        if str(item.get("variant_id")) == variant_id and variant_id:
            item["quantity"] = int(item.get("quantity", 0) or 0) + quantity
            return
    st.session_state.cart.append({"title": title, "variant_id": variant_id, "quantity": quantity})


def _submit_user_message(msg: str) -> None:
    msg = (msg or "").strip()
    if not msg:
        return
    st.session_state.messages.append({"role": "user", "content": msg})
    backend_resp = send_message(st.session_state.session_id, msg)
    resp_payload = backend_resp.get("response", {}) or {}
    bot_text = resp_payload.get("message", "") or ""
    resp_type = resp_payload.get("type", "")
    st.session_state.messages.append(
        {"role": "assistant", "content": f"[{resp_type}] {bot_text}", "payload": resp_payload, "raw": backend_resp}
    )


def main() -> None:
    st.set_page_config(page_title="Verdant · EcoSoul", page_icon="🌿", layout="centered")
    st.title("Terra")
    st.caption("EcoSoul Intelligence — sustainable disposables, party bundles & campaign-ready picks.")
    st.write("Plan a gathering, browse the catalog, or curate products for a marketing event.")

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        # Each message:
        # - user: {"role": "user", "content": str}
        # - assistant: {"role": "assistant", "content": str, "payload": dict}
        st.session_state.messages = []
    if "cart" not in st.session_state:
        st.session_state.cart = []  # simple mock cart for demo
    if "api_auth_username" not in st.session_state:
        st.session_state.api_auth_username = API_AUTH_USERNAME
    if "api_auth_password" not in st.session_state:
        st.session_state.api_auth_password = API_AUTH_PASSWORD

    # Start CTA buttons (always visible at the beginning)
    if not st.session_state.messages:
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Plan a party", key="cta_plan_party"):
                _submit_user_message("Plan a party")
                st.rerun()
        with c2:
            if st.button("Browse products", key="cta_browse_products"):
                _submit_user_message("Browse products")
                st.rerun()
        with c3:
            if st.button("Event plan", key="cta_event_plan"):
                _submit_user_message("Event plan")
                st.rerun()

    # Sidebar for debug info + mock cart
    with st.sidebar:
        st.markdown("**Session ID**")
        st.code(st.session_state.session_id)

        st.markdown("---")
        st.markdown("### API Login")
        st.text_input("Username", key="api_auth_username")
        st.text_input("Password", key="api_auth_password", type="password")
        if st.button("Login API", key="api_login_btn"):
            st.session_state.pop("api_access_token", None)
            try:
                _get_api_token()
                st.success("API login successful")
            except Exception as ex:
                st.error(f"API login failed: {ex}")
        if st.session_state.get("api_access_token"):
            st.caption("Auth status: Logged in")
        else:
            st.caption("Auth status: Not logged in")

        st.markdown("---")
        st.markdown("### Mock Cart")
        if not st.session_state.cart:
            st.caption("Cart is empty.")
        else:
            total_items = sum(int(i["quantity"]) for i in st.session_state.cart)
            st.caption(f"Items: {total_items}")
            for item in st.session_state.cart:
                st.write(f"- **{item['title']}**  × {item['quantity']}")

    # Display chat history (including product cards + buttons)
    for msg_idx, msg in enumerate(st.session_state.messages):
        if msg["role"] == "user":
            st.markdown(f"**You:** {msg['content']}")
            continue

        st.markdown(f"**Assistant:** {msg['content']}")

        payload = msg.get("payload") or {}
        products = payload.get("suggested_products") or []
        if products:
            st.markdown("**Recommended products**")
            for idx, p in enumerate(products):
                title = p.get("title", "")
                price = p.get("price")
                packs = int(p.get("packs_recommended", p.get("quantity", 1)) or 1)
                pack_size = int(p.get("pack_size", 1) or 1)
                total_units = p.get("total_units", packs * pack_size)

                st.markdown("---")
                row = st.container()
                with row:
                    c_info, c_qty, c_actions = st.columns([3, 2, 2])
                    qty_key = f"qty_{msg_idx}_{idx}"
                    if qty_key not in st.session_state:
                        st.session_state[qty_key] = packs

                    with c_info:
                        img = p.get("image_url") or ""
                        if isinstance(img, str) and img.strip():
                            try:
                                st.image(img, use_container_width=True)
                            except Exception:
                                # If Streamlit can't render the image (bad URL / SSL), still show the link.
                                st.caption(img)
                        st.markdown(f"**{title}**")
                        if price is not None:
                            st.caption(f"Price: ${price:.2f}")
                        st.caption(f"Pack size: {pack_size}, total units: {total_units}")
                        # Show individual per-product cart payload from API.
                        base_item = p.get("cart_item") or {}
                        item_id_preview = base_item.get("id")
                        if item_id_preview is None:
                            vid_preview = str(p.get("variant_id", "") or "")
                            vid_num_preview = vid_preview.split("/")[-1] if "gid://shopify" in vid_preview else vid_preview
                            item_id_preview = int(vid_num_preview) if str(vid_num_preview).isdigit() else vid_num_preview
                        st.caption("Individual cart payload")
                        st.json(
                            {
                                "id": item_id_preview,
                                "quantity": int(st.session_state.get(qty_key, packs)),
                            }
                        )

                    with c_qty:
                        st.caption("Packs")
                        minus_col, num_col, plus_col = st.columns([1, 2, 1])
                        with minus_col:
                            if st.button("−", key=f"minus_{msg_idx}_{idx}"):
                                current = max(1, int(st.session_state[qty_key]) - 1)
                                st.session_state[qty_key] = current
                        with num_col:
                            new_val = st.number_input(
                                "Packs",
                                min_value=1,
                                max_value=999,
                                value=int(st.session_state[qty_key]),
                                key=f"num_{msg_idx}_{idx}",
                                label_visibility="collapsed",
                            )
                            st.session_state[qty_key] = new_val
                        with plus_col:
                            if st.button("+", key=f"plus_{msg_idx}_{idx}"):
                                current = int(st.session_state[qty_key]) + 1
                                st.session_state[qty_key] = current

                    with c_actions:
                        if st.button("Add to cart", key=f"add_mock_{msg_idx}_{idx}"):
                            # Prefer explicit per-item payload from API.
                            # Fallback to variant_id extraction for compatibility.
                            item_payload = p.get("cart_item") or {}
                            item_id: Any = item_payload.get("id")
                            if item_id is None:
                                vid = str(p.get("variant_id", "") or "")
                                vid_num = vid.split("/")[-1] if "gid://shopify" in vid else vid
                                item_id = int(vid_num) if str(vid_num).isdigit() else vid
                            cart_add(
                                st.session_state.session_id,
                                items=[{"id": item_id, "quantity": int(st.session_state[qty_key])}],
                                cart_action="add",
                            )
                            # Keep mock cart in sidebar in sync for demo
                            _add_to_mock_cart(title=title, variant_id=str(item_id), quantity=int(st.session_state[qty_key]))
                            st.rerun()
                        if st.button("Remove item", key=f"remove_{msg_idx}_{idx}"):
                            _submit_user_message(f"remove {title}")
                            st.rerun()

        additional_products = payload.get("additional_recommendations") or []
        if additional_products:
            st.markdown("**You may also like**")
            for idx, p in enumerate(additional_products):
                title = p.get("title", "")
                price = p.get("price")
                pack_size = int(p.get("pack_size", 1) or 1)
                qty_key = f"addon_qty_{msg_idx}_{idx}"
                if qty_key not in st.session_state:
                    st.session_state[qty_key] = 1

                st.markdown("---")
                row = st.container()
                with row:
                    c_info, c_qty, c_actions = st.columns([3, 2, 2])

                    with c_info:
                        img = p.get("image_url") or ""
                        if isinstance(img, str) and img.strip():
                            try:
                                st.image(img, use_container_width=True)
                            except Exception:
                                st.caption(img)
                        st.markdown(f"**{title}**")
                        if price is not None:
                            st.caption(f"Price: ${price:.2f}")
                        st.caption(f"Pack size: {pack_size}")
                        addon_item = p.get("cart_item") or {}
                        st.caption("Individual cart payload")
                        st.json(
                            {
                                "id": addon_item.get("id"),
                                "quantity": int(st.session_state.get(qty_key, 1)),
                            }
                        )

                    with c_qty:
                        st.caption("Packs")
                        st.session_state[qty_key] = st.number_input(
                            "Addon Packs",
                            min_value=1,
                            max_value=999,
                            value=int(st.session_state[qty_key]),
                            key=f"addon_num_{msg_idx}_{idx}",
                            label_visibility="collapsed",
                        )

                    with c_actions:
                        if st.button("Add to cart", key=f"addon_add_{msg_idx}_{idx}"):
                            item_payload = p.get("cart_item") or {}
                            item_id: Any = item_payload.get("id")
                            if item_id is None:
                                vid = str(p.get("variant_id", "") or "")
                                vid_num = vid.split("/")[-1] if "gid://shopify" in vid else vid
                                item_id = int(vid_num) if str(vid_num).isdigit() else vid
                            cart_add(
                                st.session_state.session_id,
                                items=[{"id": item_id, "quantity": int(st.session_state[qty_key])}],
                                cart_action="add",
                            )
                            _add_to_mock_cart(title=title, variant_id=str(item_id), quantity=int(st.session_state[qty_key]))
                            st.rerun()

        cart_items = payload.get("cart_items") or []
        cart_permalink = payload.get("cart_permalink")
        if cart_items or cart_permalink:
            with st.expander("Cart payload (Shopify-ready)"):
                if cart_items:
                    st.json(cart_items)
                if cart_permalink:
                    st.code(cart_permalink)
                    try:
                        st.link_button("Open cart permalink", cart_permalink)
                    except Exception:
                        st.markdown(f"[Open cart permalink]({cart_permalink})")

            # Add-all to mock cart button (uses cart_items)
            if cart_items and st.button("Add ALL to mock cart", key=f"add_all_{msg_idx}"):
                # Best effort titles from suggested_products list
                vid_to_title = {str(p.get("variant_id")): p.get("title", "") for p in (products or [])}
                for it in cart_items:
                    vid = str(it.get("id") or "")
                    qty = int(it.get("quantity", 1) or 1)
                    _add_to_mock_cart(title=vid_to_title.get(vid) or "Item", variant_id=vid, quantity=qty)
                st.rerun()

        # Quick replies as buttons (render all; wrap in rows of 4)
        quick_replies = payload.get("quick_replies") or []
        if quick_replies:
            for row_start in range(0, len(quick_replies), 4):
                chunk = quick_replies[row_start : row_start + 4]
                cols = st.columns(len(chunk))
                for i, qr in enumerate(chunk):
                    with cols[i]:
                        if st.button(qr, key=f"qr_{msg_idx}_{row_start}_{i}"):
                            _submit_user_message(qr)
                            st.rerun()

    user_input = st.text_input("Your message", key="chat_input")

    col1, col2 = st.columns([1, 1])
    with col1:
        send_btn = st.button("Send")
    with col2:
        reset_btn = st.button("New session")

    if reset_btn:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.cart = []
        st.rerun()

    if send_btn and user_input.strip():
        try:
            _submit_user_message(user_input.strip())
        except Exception as e:
            st.error(f"Error calling backend: {e}")

        st.rerun()


if __name__ == "__main__":
    main()

