import json
from typing import Dict, Any, List

import requests

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
        resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        return _parse_llm_response(resp) or "(No response)"

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
            "You are an assistant for a tableware store that sells ONLY eco-friendly products (plates, bowls, spoons, cups). "
            "Do NOT ask the user for eco-friendly preference or budget. We only sell eco-friendly items.\n"
            "Extract: party size (number of guests), and per-person quantities when the user says them "
            "(e.g. '1 plate, 2 spoons, 3 bowls per person' -> plates_per_person: 1, spoons_per_person: 2, bowls_per_person: 3). "
            "If the user says they need 'plates, cups, cutlery' or 'glasses, cups, cutlery, plates', set disposables_needed to [\"plates\", \"cups\", \"spoons\"] or similar.\n"
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
            "    \"disposables_needed\": [\"plates\"|\"bowls\"|\"spoons\"|\"cups\", ...],\n"
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
        resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        raw = _parse_llm_response(resp)

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

