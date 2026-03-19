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

# Must match the port the FastAPI server runs on (set PORT in .env or default 8010)
BACKEND_PORT = os.getenv("PORT", "8010")
BACKEND_URL = f"http://localhost:{BACKEND_PORT}"


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
    resp = requests.post(f"{BACKEND_URL}/api/v1/chat", json=payload, timeout=60)
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
    resp = requests.post(f"{BACKEND_URL}/api/v1/cart/add", json=payload, timeout=60)
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
    st.set_page_config(page_title="EcoSoul AI Shopping Assistant", page_icon="🥳", layout="centered")
    st.title("EcoSoul AI Shopping Assistant")
    st.write("Chat with the assistant to plan parties or browse EcoSoul products.")

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        # Each message:
        # - user: {"role": "user", "content": str}
        # - assistant: {"role": "assistant", "content": str, "payload": dict}
        st.session_state.messages = []
    if "cart" not in st.session_state:
        st.session_state.cart = []  # simple mock cart for demo

    # Start CTA buttons (always visible at the beginning)
    if not st.session_state.messages:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Plan a party", key="cta_plan_party"):
                _submit_user_message("Plan a party")
                st.rerun()
        with c2:
            if st.button("Browse products", key="cta_browse_products"):
                _submit_user_message("Browse products")
                st.rerun()

    # Sidebar for debug info + mock cart
    with st.sidebar:
        st.markdown("**Session ID**")
        st.code(st.session_state.session_id)

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

                    qty_key = f"qty_{msg_idx}_{idx}"
                    if qty_key not in st.session_state:
                        st.session_state[qty_key] = packs

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
                            # Update backend cart payload (numeric variant id if possible)
                            vid = str(p.get("variant_id", "") or "")
                            # variant_id can be numeric already, or a Shopify GID like gid://shopify/ProductVariant/123
                            vid_num = vid.split("/")[-1] if "gid://shopify" in vid else vid
                            item_id: Any = int(vid_num) if str(vid_num).isdigit() else vid
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

        # Quick replies as buttons (nice for demos)
        quick_replies = payload.get("quick_replies") or []
        if quick_replies:
            cols = st.columns(min(4, len(quick_replies)))
            for i, qr in enumerate(quick_replies[:4]):
                with cols[i]:
                    if st.button(qr, key=f"qr_{msg_idx}_{i}"):
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

