from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .models import (
    ChatRequest,
    ChatResponse,
    CartApplyRequest,
    CartApplyResponse,
    SessionState,
    PartyPlan,
    Intent,
    ConversationStep,
    Action,
)
from .llm_client import llm_client
from .engine import build_recommendation
from .shopify_client import shopify_client
from .external_api import router as external_router

# In-memory session store for v1 (can be moved to ClickHouse/Redis later)
SESSIONS: dict[str, SessionState] = {}


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# External, Shopify-facing API (L'Occitane-style responses)
app.include_router(external_router)


def _get_or_create_session(session_id: str) -> SessionState:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = SessionState(session_id=session_id, party_plan=PartyPlan())
    return SESSIONS[session_id]


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok", "service": settings.APP_NAME, "version": settings.APP_VERSION}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    session = _get_or_create_session(request.session_id)

    # 1) Use LLM to classify intent & extract slots
    extraction = llm_client.extract_intent_and_slots(request.message, session.party_plan)
    intent: Intent = extraction["intent"]
    slots = extraction["slots"] or {}

    # 2) Merge new slots into current party plan (normalize types and "glasses" -> "cups")
    updated_plan_data = session.party_plan.model_dump()
    for k, v in (slots or {}).items():
        if v is None or k in ("eco_preference", "budget_per_person"):
            continue
        if k == "disposables_needed" and isinstance(v, list):
            def _norm_disposable(x: str) -> str:
                s = str(x).lower()
                if s in ("glasses", "cups"):
                    return "cups"
                if s in ("cutlery", "spoon"):
                    return "spoons"
                return s
            updated_plan_data[k] = [_norm_disposable(x) for x in v]
        elif k in ("plates_per_person", "spoons_per_person", "bowls_per_person", "cups_per_person", "party_size"):
            try:
                updated_plan_data[k] = int(v)
            except (TypeError, ValueError):
                pass
        elif k in ("event_type", "location", "menu_type") and isinstance(v, str):
            updated_plan_data[k] = v
        elif k == "courses" and isinstance(v, list):
            updated_plan_data[k] = [str(x) for x in v]
        else:
            updated_plan_data[k] = v
    session.party_plan = PartyPlan(**updated_plan_data)

    # Never ask for eco or budget
    missing_slots = [
        s for s in (extraction.get("missing_slots") or [])
        if str(s).lower() not in ("eco_preference", "budget_per_person")
    ]

    # 3) Decide next step
    actions: list[Action] = []

    if intent == Intent.SMALL_TALK or intent == Intent.UNKNOWN:
        # Simple small talk: just echo via LLM chat
        text = llm_client.chat(
            [
                {
                    "role": "system",
                    "content": "You are a friendly assistant for an eco-friendly tableware store.",
                },
                {"role": "user", "content": request.message},
            ]
        )
        return ChatResponse(response=text, data={"party_plan": session.party_plan.model_dump()}, actions=actions)

    if intent == Intent.PLAN_EVENT or session.step in (
        ConversationStep.PLANNING,
        ConversationStep.AWAITING_DETAILS,
    ):
        if missing_slots:
            session.step = ConversationStep.AWAITING_DETAILS
            missing_str = ", ".join(missing_slots)
            followup = llm_client.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You help users choose party supplies (plates, bowls, spoons, cups) from our store. "
                            "We only sell eco-friendly products; do NOT ask about eco preference or budget. "
                            "Ask ONE short question only for the missing detail. Current plan: party_size, per-person quantities, etc. "
                            "Be concise and friendly."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Still needed: {missing_str}. Current plan: {session.party_plan.model_dump_json()}",
                    },
                ]
            )
            return ChatResponse(
                response=followup,
                data={"party_plan": session.party_plan.model_dump(), "missing_slots": missing_slots},
                actions=actions,
            )

        # We have enough info to recommend
        recommendation = build_recommendation(session.party_plan)
        if not recommendation:
            raise HTTPException(status_code=400, detail="Not enough information to build a recommendation.")

        session.last_basket = recommendation
        session.step = ConversationStep.AWAITING_CONFIRMATION

        # Let LLM explain the recommendation (our actual products and quantities; small backup included)
        explanation = llm_client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an assistant for a tableware store. The following are our REAL products and exact quantities "
                        "we recommend for the customer (we have included a small backup). List each product with its name, "
                        "pack size, and total units in 3-4 short bullet points. Then ask: Would you like to add these to your cart, "
                        "or adjust quantities? Do not ask about eco-friendly or budget—we only sell eco-friendly products."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Party plan: {session.party_plan.model_dump_json()}\nRecommendation: {recommendation.model_dump_json()}",
                },
            ]
        )

        actions.append(Action(type="OFFER_ADD_TO_CART", payload={}))
        return ChatResponse(
            response=explanation,
            data={"party_plan": session.party_plan.model_dump(), "basket": recommendation.model_dump()},
            actions=actions,
        )

    if intent == Intent.CONFIRM_ADD_TO_CART and session.last_basket:
        # User has confirmed adding to cart
        session.step = ConversationStep.CART_APPLIED
        cart_id, checkout_url = shopify_client.apply_basket_to_cart(session.last_basket)
        payload = {"cart_id": cart_id, "checkout_url": checkout_url}
        actions.append(Action(type="NAVIGATE_TO_CHECKOUT", payload=payload))
        text = "Great, I’ve added everything to your cart. You can proceed to checkout now."
        return ChatResponse(
            response=text,
            data={"party_plan": session.party_plan.model_dump(), "basket": session.last_basket.model_dump(), "cart": payload},
            actions=actions,
        )

    # Fallback: treat as adjustment or generic plan refinement
    explanation = llm_client.chat(
        [
            {
                "role": "system",
                "content": (
                    "You are helping refine a party supplies recommendation for plates, bowls, spoons, etc. "
                    "Respond politely and, if needed, ask what should be changed (e.g. more plates, fewer spoons)."
                ),
            },
            {"role": "user", "content": request.message},
        ]
    )
    return ChatResponse(response=explanation, data={"party_plan": session.party_plan.model_dump()}, actions=actions)


@app.post("/cart/apply", response_model=CartApplyResponse)
def cart_apply(req: CartApplyRequest) -> CartApplyResponse:
    if not req.basket.items:
        raise HTTPException(status_code=400, detail="Basket is empty.")
    cart_id, checkout_url = shopify_client.apply_basket_to_cart(req.basket)
    message = "Cart updated successfully."
    return CartApplyResponse(cart_id=cart_id, checkout_url=checkout_url, message=message)


if __name__ == "__main__":
    import uvicorn

    # Use a direct app instance here (no reload/workers) so running
    # `python -m shopify_assistant.main` works reliably in your venv.
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)

