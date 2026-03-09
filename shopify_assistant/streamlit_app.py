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
    payload = {
        "session_id": session_id,
        "message": message,
        "conversation_history": history or [],
    }
    resp = requests.post(f"{BACKEND_URL}/chat", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    st.set_page_config(page_title="Shopify Party Planner Assistant", page_icon="🥳", layout="centered")
    st.title("Shopify Party Planner Assistant")
    st.write("Talk to the assistant about your party and let it recommend plates, bowls, spoons, etc.")

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []  # each: {"role": "user"/"assistant", "content": str}

    # Sidebar for debug info
    with st.sidebar:
        st.markdown("**Session ID**")
        st.code(st.session_state.session_id)

    # Display chat history
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            st.markdown(f"**You:** {msg['content']}")
        else:
            st.markdown(f"**Assistant:** {msg['content']}")

    user_input = st.text_input("Your message", key="chat_input")

    col1, col2 = st.columns([1, 1])
    with col1:
        send_btn = st.button("Send")
    with col2:
        reset_btn = st.button("New session")

    if reset_btn:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    if send_btn and user_input.strip():
        msg = user_input.strip()
        st.session_state.messages.append({"role": "user", "content": msg})

        try:
            backend_resp = send_message(st.session_state.session_id, msg)
        except Exception as e:
            st.error(f"Error calling backend: {e}")
        else:
            text = backend_resp.get("response", "")
            st.session_state.messages.append({"role": "assistant", "content": text})

            # Show structured data and actions in an expander
            with st.expander("Debug: raw backend response"):
                st.json(backend_resp)

        st.rerun()


if __name__ == "__main__":
    main()

