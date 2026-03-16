import json
import re
from typing import Dict, Any, List

import requests
from requests.exceptions import RequestException

from .config import settings
from .models import Intent, PartyPlan


def _parse_llm_response(resp: requests.Response) -> str:
    """
    Parse LLM API response. Handles both single JSON and streaming/NDJSON
    (multiple JSON objects, one per line). Returns the combined message content.
    """
    text = resp.text
    if not text.strip():
        return ""

    # Try single JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "message" in data and isinstance(data["message"], dict) and "content" in data["message"]:
                return (data["message"]["content"] or "").strip()
            if "choices" in data and data["choices"] and isinstance(data["choices"][0], dict):
                msg = data["choices"][0].get("message") or data["choices"][0]
                if isinstance(msg, dict) and "content" in msg:
                    return (msg["content"] or "").strip()
        return str(data).strip()
    except json.JSONDecodeError:
        pass

    # Streaming/NDJSON: one JSON object per line
    parts: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                if "message" in data and isinstance(data["message"], dict) and "content" in data["message"]:
                    content = data["message"].get("content") or ""
                    if content:
                        parts.append(content)
                elif "choices" in data and data["choices"]:
                    msg = data["choices"][0].get("message") or data["choices"][0]
                    if isinstance(msg, dict) and msg.get("content"):
                        parts.append(msg["content"])
        except json.JSONDecodeError:
            continue
    return "".join(parts).strip() if parts else text.strip()


class LLMClient:
    """
    Minimal client for your local LLM server (Ollama-style HTTP).
    """

    def __init__(self) -> None:
        self.base_url = str(settings.LLM_BASE_URL).rstrip("/")
        self.model = settings.LLM_MODEL_NAME

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """
        Generic chat completion for explanation / follow-up text.
        """
        payload = {
            "model": self.model,
            "messages": messages,
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=30)
            resp.raise_for_status()
            return _parse_llm_response(resp) or "(No response)"
        except RequestException:
            # If LLM is offline, return a short safe fallback.
            last_user = ""
            for m in reversed(messages or []):
                if (m or {}).get("role") == "user":
                    last_user = str((m or {}).get("content") or "")
                    break
            return "I can help with party planning or product browsing. Tell me how many guests you have and which city you're in."

    def _heuristic_extract(self, user_message: str, current_plan: PartyPlan) -> Dict[str, Any]:
        """
        Deterministic fallback extractor when the LLM is unreachable.
        """
        text = (user_message or "").strip()
        lower = text.lower()

        slots: Dict[str, Any] = {}

        # party_size: "50 guests" / "for 2000 people" / "200"
        m = re.search(r"\b(\d{1,6})\b", lower)
        if m and any(k in lower for k in ["guest", "guests", "people", "persons", "person", "attendees", "invite", "invited", "for"]):
            try:
                slots["party_size"] = int(m.group(1))
            except Exception:
                pass

        # location: accept city-like strings when not a guest-count message
        if any(ch.isalpha() for ch in text) and not any(k in lower for k in ["guest", "guests", "people", "persons"]):
            if len(text) <= 60 and not any(k in lower for k in ["plan a party", "browse products", "browse", "products"]):
                slots["location"] = text

        # Mode hints
        if any(k in lower for k in ["browse products", "browse", "show products", "tableware", "drinkware", "kitchenware", "personal care"]):
            intent = Intent.UNKNOWN
        elif any(k in lower for k in ["plan a party", "party", "birthday", "wedding", "event"]):
            intent = Intent.PLAN_EVENT
        elif any(k in lower for k in ["add all", "add everything", "go ahead", "proceed", "checkout"]):
            intent = Intent.CONFIRM_ADD_TO_CART
        else:
            intent = Intent.UNKNOWN

        # missing slots: only the essentials for our current external flow
        missing: List[str] = []
        party_size = slots.get("party_size") or current_plan.party_size
        location = slots.get("location") or current_plan.location
        if not party_size:
            missing.append("party_size")
        if party_size and not location:
            missing.append("location")

        return {"intent": intent, "slots": slots, "missing_slots": missing}

    def extract_intent_and_slots(
        self,
        user_message: str,
        current_plan: PartyPlan,
    ) -> Dict[str, Any]:
        """
        Ask the model to classify intent and extract structured slots.
        Returns a dict:
        {
          "intent": "PLAN_EVENT" | ...,
          "slots": {...},
          "missing_slots": [...],
        }
        """
        system_prompt = (
            "You are an assistant for a tableware store that sells ONLY eco-friendly products (plates, bowls, cutlery/spoons, cups). "
            "Do NOT ask the user for eco-friendly preference or budget. We only sell eco-friendly items.\n"
            "Cutlery and spoon/spoons mean the SAME thing on our website (we show 'cutlery'). When the user says 'cutlery', 'spoons', or 'spoon', always use \"spoons\" in disposables_needed and spoons_per_person.\n"
            "Extract: party size (number of guests), and per-person quantities when the user says them "
            "(e.g. '1 plate, 2 spoons, 3 bowls per person' or 'cutlery' -> plates_per_person: 1, spoons_per_person: 2, bowls_per_person: 3). "
            "If the user says they need 'plates, cups, cutlery' or 'glasses, cups, cutlery, plates' or 'spoons', set disposables_needed to [\"plates\", \"cups\", \"spoons\"] (always use \"spoons\" for cutlery/spoon). "
            "When suggesting products, include cutlery (spoons) when the user asked for cutlery or spoons and inventory is available.\n"
            "In missing_slots list ONLY what is still needed to recommend products: e.g. party_size if unknown, or how many plates/spoons/bowls/cups per person if they did not say. "
            "Never put eco_preference or budget_per_person in missing_slots.\n"
            "Respond with valid JSON only, this exact schema:\n"
            "{\n"
            '  \"intent\": \"PLAN_EVENT\" | \"ADJUST_PLAN\" | \"CONFIRM_ADD_TO_CART\" | '
            '\"EDIT_QUANTITY\" | \"CHECK_CART\" | \"CHECKOUT\" | \"SMALL_TALK\" | \"UNKNOWN\",\n'
            "  \"slots\": {\n"
            "    \"party_size\": int | null,\n"
            "    \"event_type\": string | null,\n"
            "    \"location\": string | null,\n"
            "    \"menu_type\": string | null,\n"
            "    \"courses\": [string, ...],\n"
            "    \"disposables_needed\": [\"plates\"|\"bowls\"|\"spoons\"|\"cups\", ...] (use \"spoons\" for cutlery/spoon),\n"
            "    \"plates_per_person\": int | null,\n"
            "    \"spoons_per_person\": int | null,\n"
            "    \"bowls_per_person\": int | null,\n"
            "    \"cups_per_person\": int | null\n"
            "  },\n"
            "  \"missing_slots\": [string, ...]\n"
            "}\n"
            "Do not add any explanation, only JSON."
        )

        user_prompt = (
            f"Current known plan (may be partial): {current_plan.model_dump_json()}\n"
            f"User message: {user_message}"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=30)
            resp.raise_for_status()
            raw = _parse_llm_response(resp)
        except RequestException:
            return self._heuristic_extract(user_message, current_plan)

        if not raw:
            return {
                "intent": Intent.UNKNOWN,
                "slots": {},
                "missing_slots": [],
            }

        raw = raw.strip()
        # Best effort JSON parsing (LLM might wrap in markdown or add extra text)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(raw[start : end + 1])
                except Exception:
                    data = {}
            else:
                data = {}

        intent_str = str(data.get("intent", "UNKNOWN")).strip()
        try:
            intent = Intent(intent_str) if intent_str in [e.value for e in Intent] else Intent.UNKNOWN
        except Exception:
            intent = Intent.UNKNOWN

        slots = data.get("slots", {}) or {}
        missing_slots = data.get("missing_slots", []) or []

        return {
            "intent": intent,
            "slots": slots,
            "missing_slots": missing_slots,
        }


llm_client = LLMClient()

