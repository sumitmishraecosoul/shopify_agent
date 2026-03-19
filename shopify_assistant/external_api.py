from typing import List

from fastapi import APIRouter

import re

from .clickhouse_client import clickhouse_client
from .engine import build_recommendation
from .llm_client import llm_client
from .config import settings
from .models import (
    BasketItem,
    BasketRecommendation,
    ChatRequest,
    ConversationMode,
    ConversationStep,
    ExternalCartAddMultipleRequest,
    ExternalCartAddRequest,
    ExternalCartAddResponse,
    ExternalChatPayload,
    ExternalChatResponse,
    ExternalConversationContext,
    ExternalProductCard,
    ExternalRoutineStep,
    ExternalSessionResponse,
    Intent,
    PartyPlan,
    SessionState,
)
from .shopify_client import shopify_client


router = APIRouter(prefix="/api/v1", tags=["external"])

# Simple in-memory session store for the external API layer
EXTERNAL_SESSIONS: dict[str, SessionState] = {}


def _get_or_create_session(session_id: str) -> SessionState:
    if session_id not in EXTERNAL_SESSIONS:
        EXTERNAL_SESSIONS[session_id] = SessionState(session_id=session_id, party_plan=PartyPlan())
    return EXTERNAL_SESSIONS[session_id]


def _gid_to_numeric_id(gid: str) -> str:
    """
    Convert Shopify GID (gid://shopify/ProductVariant/123) to numeric id string.
    If gid is already numeric, returns it.
    """
    s = str(gid or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s
    m = re.search(r"/(\d+)\s*$", s)
    return m.group(1) if m else s


def _build_cart_permalink(cart_items: List[dict]) -> str | None:
    domain = (settings.SHOPIFY_STORE_DOMAIN or "").rstrip("/")
    if not domain or not cart_items:
        return None
    parts: List[str] = []
    for it in cart_items:
        vid = str(it.get("id") or "").strip()
        qty = int(it.get("quantity", 1) or 1)
        if not vid:
            continue
        parts.append(f"{vid}:{max(1, qty)}")
    if not parts:
        return None
    return f"{domain}/cart/{','.join(parts)}"


def _normalize_cart_items(items: List[dict]) -> List[dict]:
    """
    Normalize items to: [{id: <numeric-or-string>, quantity: int>=1}]
    """
    out: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        vid = it.get("id")
        if vid is None:
            continue
        vid_s = str(vid).strip()
        if not vid_s:
            continue
        qty = int(it.get("quantity", 1) or 1)
        out.append({"id": int(vid_s) if vid_s.isdigit() else vid_s, "quantity": max(1, qty)})
    return out


def _merge_cart_items(existing: List[dict], incoming: List[dict], action: str = "add") -> List[dict]:
    """
    Merge cart items for demo session cart state.
    - action=add: adds quantities
    - action=set|update: sets quantities
    - action=remove|delete: removes matching ids
    """
    action_l = (action or "add").lower().strip()
    base = _normalize_cart_items(existing)
    inc = _normalize_cart_items(incoming)

    idx: dict[str, int] = {str(it["id"]): i for i, it in enumerate(base)}

    if action_l in ("remove", "delete"):
        remove_ids = {str(it["id"]) for it in inc}
        return [it for it in base if str(it["id"]) not in remove_ids]

    for it in inc:
        key = str(it["id"])
        if key in idx:
            if action_l in ("set", "update"):
                base[idx[key]]["quantity"] = max(1, int(it["quantity"]))
            else:  # add
                base[idx[key]]["quantity"] = max(1, int(base[idx[key]]["quantity"]) + int(it["quantity"]))
        else:
            base.append({"id": it["id"], "quantity": max(1, int(it["quantity"]))})
            idx[key] = len(base) - 1
    return base


def _category_menu_quick_replies() -> List[str]:
    return ["Tableware", "Drinkware", "Kitchenware", "Personal care"]


def _subcategories_for(category: str) -> List[str]:
    c = (category or "").lower()
    if c == "tableware":
        return ["Plates", "Bowls", "Cutlery", "Shop all tableware"]
    if c == "drinkware":
        return ["Hot cups", "Cold cups", "Ripple cups", "Straws", "Shop all drinkware"]
    if c == "kitchenware":
        return [
            "Drawer organizer",
            "Clamshells",
            "Resealable bags",
            "Paper towels",
            "Cutting boards",
            "Bamboo spatulas",
            "Bamboo trays",
            "Compost bags",
            "Shop all kitchenware",
        ]
    if c == "personal care":
        return ["Facial tissues", "Toilet paper", "Baby wipes", "Flushable wipes", "Cleaning wipes", "Shop all personal care"]
    return []


def _infer_main_category(lower: str) -> str | None:
    if any(w in lower for w in ["tableware", "plate", "plates", "bowl", "bowls", "cutlery"]):
        return "tableware"
    if any(w in lower for w in ["drinkware", "hot cup", "cold cup", "ripple", "straw", "straws", "cups"]):
        return "drinkware"
    if any(w in lower for w in ["kitchenware", "kitchen", "tray", "trays", "board", "boards", "bag", "bags", "towel", "towels", "clamshell"]):
        return "kitchenware"
    if any(w in lower for w in ["personal care", "tissue", "tissues", "toilet", "wipes", "wipe"]):
        return "personal care"
    return None


def _infer_leaf_category_from_subcategory(category: str, subcategory: str) -> str | None:
    """
    Map website subcategory selection to our coarse inventory categories.
    """
    c = (category or "").lower()
    s = (subcategory or "").lower()
    if c == "tableware":
        if "plate" in s:
            return "plates"
        if "bowl" in s:
            return "bowls"
        if "cutlery" in s:
            return "spoons"
    if c == "drinkware":
        if "cup" in s or "straw" in s:
            return "cups"
    # Fallback: no strict mapping; search will still work by keyword
    return None


def _infer_party_item_category(lower: str) -> str | None:
    """
    Map a user phrase to an inventory leaf category.
    Used for "show different plates/bowls/cups" style requests.
    """
    if any(w in lower for w in ["plate", "plates"]):
        return "plates"
    if any(w in lower for w in ["bowl", "bowls"]):
        return "bowls"
    if any(w in lower for w in ["cup", "cups", "glass", "glasses", "straw", "straws"]):
        return "cups"
    if any(w in lower for w in ["spoon", "spoons", "cutlery"]):
        return "spoons"
    if any(w in lower for w in ["fork", "forks"]):
        return "forks"
    return None


def _out_of_stock_message(category_label: str) -> str:
    """Message when user asks for a specific product/category but inventory is unavailable."""
    return (
        f"Currently for {category_label.lower()} inventory is out of stock. "
        "Please check after sometime for this product if you want to buy it. "
        "Till then please explore our other products—or I can help you explore other products; just ask me."
    )


def _build_product_list_payload(
    *,
    message: str,
    leaf_category: str,
    subcategory_label: str,
    quick_replies: List[str] | None = None,
) -> tuple[ExternalChatPayload, ExternalConversationContext]:
    alt_products = clickhouse_client.fetch_products_for_category(category=leaf_category, eco_preference="high")
    cards: List[ExternalProductCard] = []
    if not alt_products:
        payload = ExternalChatPayload(
            message=_out_of_stock_message(subcategory_label),
            type="product_list",
            suggested_products=[],
            cart_items=[],
            cart_permalink="",
            quick_replies=quick_replies or ["Explore other products", "Plan a party", "Browse products"],
        )
        ctx = ExternalConversationContext(
            detected_intent=Intent.ADJUST_PLAN.value,
            detected_disposables=[],
            party_size=None,
            estimated_coverage={},
            products_discussed=[],
            pending_actions=[],
            mode=ConversationMode.PRODUCT_BROWSING,
            current_category="tableware" if leaf_category in ("plates", "bowls", "spoons", "forks") else "drinkware",
            current_subcategory=subcategory_label,
        )
        return payload, ctx
    for p in alt_products[:10]:
        price = (int(p.get("price_cents", 0)) or 0) / 100.0
        pack_size = int(p.get("pack_size", 1) or 1)
        cards.append(
            ExternalProductCard(
                product_id=str(p.get("product_gid") or p.get("product_id") or ""),
                variant_id=_gid_to_numeric_id(str(p.get("variant_gid") or p.get("product_id") or "")),
                title=p.get("title", ""),
                description=f"Alternative {subcategory_label.lower()} option from our catalog.",
                price=price,
                currency="USD",
                image_url=p.get("image_url") or None,
                product_url=f"/products/{p.get('handle','')}",
                badges=["eco-friendly"],
                options=[],
                selected_options={},
                quantity=1,
                pack_size=pack_size,
                packs_recommended=1,
                total_units=pack_size,
                key_benefits=[],
                statistics=None,
            )
        )

    payload = ExternalChatPayload(
        message=message,
        type="product_list",
        suggested_products=cards,
        cart_items=[],
        cart_permalink=_build_cart_permalink(
            [{"id": int(c.variant_id) if str(c.variant_id).isdigit() else c.variant_id, "quantity": 1} for c in cards[:6] if c.variant_id]
        ),
        quick_replies=quick_replies or ["Plan a party", "Browse products"],
    )
    ctx = ExternalConversationContext(
        detected_intent=Intent.ADJUST_PLAN.value,
        detected_disposables=[],
        party_size=None,
        estimated_coverage={},
        products_discussed=[c.product_id for c in cards],
        pending_actions=[],
        mode=ConversationMode.PRODUCT_BROWSING,
        current_category="tableware" if leaf_category in ("plates", "bowls", "spoons", "forks") else "drinkware",
        current_subcategory=subcategory_label,
    )
    return payload, ctx


def _basket_edit_apply(message_lower: str, basket: BasketRecommendation) -> BasketRecommendation:
    """
    Very small deterministic basket editor for demo:
    - remove <plates|bowls|cups|cutlery|spoons>
    - set <plates|bowls|cups|cutlery|spoons> to <N> packs
    - add <plates|bowls|cups|cutlery|spoons> (adds 1 pack of a best matching product)
    """
    if not basket or not basket.items:
        return basket

    # Category keyword mapping
    kw_to_cat = {
        "plates": "plates",
        "plate": "plates",
        "bowls": "bowls",
        "bowl": "bowls",
        "cups": "cups",
        "cup": "cups",
        "cutlery": "spoons",
        "spoons": "spoons",
        "spoon": "spoons",
        "forks": "forks",
        "fork": "forks",
    }

    target_cat = None
    for kw, cat in kw_to_cat.items():
        if kw in message_lower:
            target_cat = cat
            break

    if not target_cat:
        return basket

    # Remove
    if "remove" in message_lower or "delete" in message_lower:
        new_items = [it for it in basket.items if it.category != target_cat]
        return BasketRecommendation(
            items=new_items,
            total_people=basket.total_people,
            estimated_coverage=basket.estimated_coverage,
            total_price_cents=basket.total_price_cents,
        )

    # Update packs to N
    m = re.search(r"\b(\d+)\b", message_lower)
    if any(w in message_lower for w in ["pack", "packs", "qty", "quantity"]) and m:
        n = max(1, int(m.group(1)))
        new_items = []
        for it in basket.items:
            if it.category == target_cat:
                total_units = n * int(it.units_per_pack or 1)
                new_items.append(
                    it.model_copy(
                        update={
                            "packs": n,
                            "total_units": total_units,
                        }
                    )
                )
            else:
                new_items.append(it)
        return BasketRecommendation(
            items=new_items,
            total_people=basket.total_people,
            estimated_coverage=basket.estimated_coverage,
            total_price_cents=basket.total_price_cents,
        )

    # Add category (1 pack) by picking a product from inventory
    if "add" in message_lower or "include" in message_lower:
        candidates = clickhouse_client.fetch_products_for_category(category=target_cat, eco_preference="high")
        if not candidates:
            return basket
        prod = candidates[0]
        pack_size = int(prod.get("pack_size", 1) or 1)
        price_cents = int(prod.get("price_cents", 0) or 0)
        new_item = BasketItem(
            product_id=str(prod.get("product_id") or ""),
            title=str(prod.get("title") or ""),
            category=str(prod.get("category") or target_cat),
            packs=1,
            units_per_pack=pack_size,
            total_units=pack_size,
            price_cents_per_pack=price_cents,
        )
        return BasketRecommendation(
            items=basket.items + [new_item],
            total_people=basket.total_people,
            estimated_coverage=basket.estimated_coverage,
            total_price_cents=basket.total_price_cents + price_cents,
        )

    return basket

def _basket_to_product_cards(basket: BasketRecommendation) -> List[ExternalProductCard]:
    cards: List[ExternalProductCard] = []
    for idx, item in enumerate(basket.items, start=1):
        meta = clickhouse_client.get_product_by_internal_id(item.product_id) or {}

        variant_gid = str(meta.get("variant_gid") or item.product_id)
        product_gid = str(meta.get("product_gid") or variant_gid)
        handle = str(meta.get("handle") or "")
        product_url = f"/products/{handle}" if handle else ""

        price = (item.price_cents_per_pack or 0) / 100.0
        pack_size = int(item.units_per_pack or 1)
        packs = int(item.packs or 1)
        total_units = int(item.total_units or (pack_size * packs))

        # Basic badges / benefits from category
        badges: List[str] = ["eco-friendly"]
        if item.category.lower().startswith("plate"):
            step_name = "Plates"
        elif item.category.lower().startswith("bowl"):
            step_name = "Bowls"
        elif item.category.lower().startswith("spoon"):
            step_name = "Cutlery"
        elif item.category.lower().startswith("cup"):
            step_name = "Cups"
        else:
            step_name = item.category.title()

        key_benefits: List[str] = []
        if "plates" in item.category:
            key_benefits.append("Sturdy compostable plates, suitable for parties.")
        if "bowls" in item.category:
            key_benefits.append("Great for curries, dals, and desserts.")
        if "spoons" in item.category or "forks" in item.category:
            key_benefits.append("Compostable cutlery for easy clean-up.")

        statistics = f"Recommended for ~{basket.total_people} guests with a small backup."

        cards.append(
            ExternalProductCard(
                product_id=product_gid,
                variant_id=variant_gid,
                title=item.title,
                description="EcoSoul party supply recommendation.",
                price=price,
                currency="USD",
                image_url=meta.get("image_url") or None,
                product_url=product_url,
                badges=badges,
                options=[],
                selected_options={},
                quantity=packs,
                pack_size=pack_size,
                packs_recommended=packs,
                total_units=total_units,
                key_benefits=key_benefits,
                statistics=statistics,
            )
        )

    return cards


@router.post("/chat", response_model=ExternalChatResponse)
def external_chat(request: ChatRequest) -> ExternalChatResponse:
    """
    Shopify-facing chat endpoint supporting:
    - PARTY_PLANNING (existing party-size based flow)
    - PRODUCT_BROWSING / PRODUCT_SEARCH (simple browse/search over inventory)
    """
    session = _get_or_create_session(request.session_id)

    text = (request.message or "").strip()
    lower = text.lower()

    # Special case: user asking for different options after a party recommendation
    if session.mode == ConversationMode.PARTY_PLANNING and session.last_basket and any(
        w in lower for w in ["different", "other", "more options", "show options", "show more options"]
    ):
        leaf = _infer_party_item_category(lower)
        if leaf:
            label = leaf.title() if leaf != "cups" else "Cups"
            payload, ctx = _build_product_list_payload(
                message=f"Here are some other {label.lower()} options you can consider:",
                leaf_category=leaf,
                subcategory_label=label,
                quick_replies=["Back to party plan", "Browse products"],
            )
            # carry over party context
            ctx.party_size = session.party_plan.party_size
            ctx.detected_disposables = session.party_plan.disposables_needed or []
            ctx.estimated_coverage = session.last_basket.estimated_coverage
            return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

    # Mode selection by keywords (must happen before any welcome return)
    if any(kw in lower for kw in ["plan a party", "party", "birthday"]):
        session.mode = ConversationMode.PARTY_PLANNING
    elif any(kw in lower for kw in ["browse products", "browse", "products", "tableware", "drinkware", "kitchenware", "personal care"]):
        session.mode = ConversationMode.PRODUCT_BROWSING

    # Initial welcome if we don't know what the user wants yet
    if not session.mode and not session.party_plan.party_size and not session.current_category:
        payload = ExternalChatPayload(
            message="Hi! I'm your EcoSoul assistant. Would you like to plan a party or browse products?",
            type="welcome",
            suggested_products=[],
            cart_items=session.cart_items or [],
            cart_permalink=_build_cart_permalink(session.cart_items or []),
            quick_replies=["Plan a party", "Browse products"],
            suggested_questions=[
                "Help me plan a birthday party",
                "Show me tableware products",
            ],
        )
        ctx = ExternalConversationContext(
            detected_intent=Intent.UNKNOWN.value,
            detected_disposables=session.party_plan.disposables_needed or [],
            party_size=session.party_plan.party_size,
            estimated_coverage={},
            products_discussed=[],
            pending_actions=[],
            mode=session.mode,
            current_category=session.current_category,
            current_subcategory=session.current_subcategory,
        )
        return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

    # PARTY PLANNING
    if session.mode == ConversationMode.PARTY_PLANNING or not session.mode:
        # If we already have a basket and the user is confirming, don't recalc – just
        # return the same recommendation with cart payload (for demo / integration).
        if session.last_basket and any(
            kw in lower
            for kw in [
                "yes",
                "looks good",
                "sounds good",
                "add all",
                "add everything",
                "ok add",
                "okay add",
                "please provide",
                "proceed",
                "go ahead",
            ]
        ):
            cards = _basket_to_product_cards(session.last_basket)
            cart_items: List[dict] = []
            for c in cards:
                vid = _gid_to_numeric_id(c.variant_id)
                qty = int(c.quantity or c.packs_recommended or 1)
                if vid:
                    cart_items.append({"id": int(vid) if vid.isdigit() else vid, "quantity": max(1, qty)})
            cart_permalink = _build_cart_permalink(cart_items)
            payload = ExternalChatPayload(
                message="Great, your party set is ready. The frontend can now use these cart_items or the cart_permalink to add everything to the Shopify cart.",
                type="party_recommendation",
                suggested_products=cards,
                cart_items=cart_items,
                cart_permalink=cart_permalink,
                quick_replies=["Plan another party", "Browse products"],
            )
            ctx = ExternalConversationContext(
                detected_intent=Intent.CONFIRM_ADD_TO_CART.value,
                detected_disposables=session.party_plan.disposables_needed or [],
                party_size=session.party_plan.party_size,
                estimated_coverage=session.last_basket.estimated_coverage,
                products_discussed=[c.product_id for c in cards],
                pending_actions=[],
                mode=session.mode,
                current_category=session.current_category,
                current_subcategory=session.current_subcategory,
            )
            return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

        # Basket editing commands for demo (if we already have a basket)
        if session.last_basket and any(w in lower for w in ["remove", "delete", "add", "include", "packs", "pack", "quantity", "qty"]):
            edited = _basket_edit_apply(lower, session.last_basket)
            session.last_basket = edited
            cards = _basket_to_product_cards(edited)
            cart_items: List[dict] = []
            for c in cards:
                vid = _gid_to_numeric_id(c.variant_id)
                qty = int(c.quantity or c.packs_recommended or 1)
                if vid:
                    cart_items.append({"id": int(vid) if vid.isdigit() else vid, "quantity": max(1, qty)})
            cart_permalink = _build_cart_permalink(cart_items)
            payload = ExternalChatPayload(
                message="Updated your party basket. Would you like to add everything to cart or change anything else?",
                type="party_recommendation",
                suggested_products=cards,
                cart_items=cart_items,
                cart_permalink=cart_permalink,
                quick_replies=["Add all to cart", "Adjust quantities", "Remove an item", "Add more items"],
            )
            ctx = ExternalConversationContext(
                detected_intent=Intent.EDIT_QUANTITY.value,
                detected_disposables=session.party_plan.disposables_needed or [],
                party_size=session.party_plan.party_size,
                estimated_coverage=edited.estimated_coverage,
                products_discussed=[c.product_id for c in cards],
                pending_actions=["awaiting_cart_confirmation"],
                mode=session.mode,
                current_category=session.current_category,
                current_subcategory=session.current_subcategory,
            )
            return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

        extraction = llm_client.extract_intent_and_slots(request.message, session.party_plan)
        intent: Intent = extraction["intent"]
        slots = extraction["slots"] or {}

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

        missing_slots = [
            s
            for s in (extraction.get("missing_slots") or [])
            if str(s).lower() not in ("eco_preference", "budget_per_person")
        ]

        # If we STILL don't know party_size, prioritize asking that first.
        if not session.party_plan.party_size:
            if intent == Intent.SMALL_TALK or intent == Intent.UNKNOWN or missing_slots:
                payload = ExternalChatPayload(
                    message="Hi! I'm your EcoSoul party planning assistant. How many guests are you expecting?",
                    type="question",
                    suggested_products=[],
                    cart_items=session.cart_items or [],
                    cart_permalink=_build_cart_permalink(session.cart_items or []),
                    quick_replies=["10 guests", "25 guests", "50 guests", "More than 100"],
                    suggested_questions=[
                        "What products do you recommend?",
                        "Tell me about eco-friendly options",
                    ],
                )
                ctx = ExternalConversationContext(
                    detected_intent=intent.value if isinstance(intent, Intent) else str(intent),
                    detected_disposables=session.party_plan.disposables_needed or [],
                    party_size=session.party_plan.party_size,
                    estimated_coverage={},
                    products_discussed=[],
                    pending_actions=[],
                    mode=session.mode,
                    current_category=session.current_category,
                    current_subcategory=session.current_subcategory,
                )
                return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

        recommendation = build_recommendation(session.party_plan)
        if not recommendation:
            payload = ExternalChatPayload(
                message="I need a bit more information before I can recommend exact products. For how many guests is this party?",
                type="clarification",
                suggested_products=[],
                quick_replies=["10 guests", "25 guests", "50 guests", "More than 100"],
            )
            ctx = ExternalConversationContext(
                detected_intent=intent.value if isinstance(intent, Intent) else str(intent),
                detected_disposables=session.party_plan.disposables_needed or [],
                party_size=session.party_plan.party_size,
                estimated_coverage={},
                products_discussed=[],
                pending_actions=["awaiting_details"],
                mode=session.mode,
                current_category=session.current_category,
                current_subcategory=session.current_subcategory,
            )
            return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

        session.last_basket = recommendation
        session.step = ConversationStep.AWAITING_CONFIRMATION

        cards = _basket_to_product_cards(recommendation)
        # Build cart payload (numeric variant ids) and optional permalink
        cart_items: List[dict] = []
        for c in cards:
            vid = _gid_to_numeric_id(c.variant_id)
            qty = int(c.quantity or c.packs_recommended or 1)
            if vid:
                cart_items.append({"id": int(vid) if vid.isdigit() else vid, "quantity": max(1, qty)})
        cart_permalink = _build_cart_permalink(cart_items)

        msg = f"For {session.party_plan.party_size} guests, here’s an eco-friendly recommendation with a small backup so you don’t run out."
        payload = ExternalChatPayload(
            message=msg,
            type="party_recommendation",
            suggested_products=cards,
            cart_items=cart_items,
            cart_permalink=cart_permalink,
            quick_replies=["Add all to cart", "Adjust quantities", "Show more options"],
            suggested_questions=[
                f"What if I have {(session.party_plan.party_size or 0) + 10} guests?",
                "Do you have matching bowls?",
                "Tell me about the eco benefits",
            ],
        )
        ctx = ExternalConversationContext(
            detected_intent=intent.value if isinstance(intent, Intent) else str(intent),
            detected_disposables=session.party_plan.disposables_needed or [],
            party_size=session.party_plan.party_size,
            estimated_coverage=recommendation.estimated_coverage,
            products_discussed=[c.product_id for c in cards],
            pending_actions=["awaiting_cart_confirmation"],
            mode=session.mode,
            current_category=session.current_category,
            current_subcategory=session.current_subcategory,
        )
        return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

    # PRODUCT BROWSING / SIMPLE SEARCH
    session.mode = session.mode or ConversationMode.PRODUCT_BROWSING

    if lower in ("different category", "change category", "back", "categories", "show categories"):
        session.current_category = None
        session.current_subcategory = None
        payload = ExternalChatPayload(
            message="Sure — what category would you like to explore?",
            type="category_menu",
            suggested_products=[],
            quick_replies=_category_menu_quick_replies(),
        )
        ctx = ExternalConversationContext(
            detected_intent=Intent.UNKNOWN.value,
            detected_disposables=session.party_plan.disposables_needed or [],
            party_size=session.party_plan.party_size,
            estimated_coverage={},
            products_discussed=[],
            pending_actions=[],
            mode=session.mode,
            current_category=None,
            current_subcategory=None,
        )
        return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

    # Category selection
    inferred = _infer_main_category(lower)
    if inferred:
        session.current_category = inferred
        # reset subcategory on category change
        session.current_subcategory = None

    # If user typed a category name exactly (quick replies), normalize it
    if lower in ("personal care", "personal_care"):
        session.current_category = "personal care"

    # If still no category, show category menu
    if not session.current_category:
        payload = ExternalChatPayload(
            message="What category would you like to explore?",
            type="category_menu",
            suggested_products=[],
            cart_items=session.cart_items or [],
            cart_permalink=_build_cart_permalink(session.cart_items or []),
            quick_replies=_category_menu_quick_replies(),
        )
        ctx = ExternalConversationContext(
            detected_intent=Intent.UNKNOWN.value,
            detected_disposables=session.party_plan.disposables_needed or [],
            party_size=session.party_plan.party_size,
            estimated_coverage={},
            products_discussed=[],
            pending_actions=[],
            mode=session.mode,
            current_category=None,
            current_subcategory=None,
        )
        return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

    # Subcategory selection menu
    if not session.current_subcategory:
        subs = _subcategories_for(session.current_category)
        # If user picked a subcategory, set it; otherwise show menu
        if lower in {s.lower() for s in subs}:
            session.current_subcategory = next((s for s in subs if s.lower() == lower), None)
        else:
            payload = ExternalChatPayload(
                message=f"What type of {session.current_category} are you looking for?",
                type="subcategory_menu",
                suggested_products=[],
                cart_items=session.cart_items or [],
                cart_permalink=_build_cart_permalink(session.cart_items or []),
                quick_replies=subs[:6],
            )
            ctx = ExternalConversationContext(
                detected_intent=Intent.UNKNOWN.value,
                detected_disposables=session.party_plan.disposables_needed or [],
                party_size=session.party_plan.party_size,
                estimated_coverage={},
                products_discussed=[],
                pending_actions=[],
                mode=session.mode,
                current_category=session.current_category,
                current_subcategory=None,
            )
            return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)

    # Product list for selected subcategory (fallback search if user typed a query)
    leaf_category = _infer_leaf_category_from_subcategory(session.current_category, session.current_subcategory or "")
    # If the user typed a query, use it; else show items for selected leaf category
    q = "" if lower in ("show more products", "show more", "next") else text
    products = clickhouse_client.search_products(
        query=q or None,
        category=leaf_category,
        limit=10,
    )

    cards: List[ExternalProductCard] = []
    for p in products:
        price = (int(p.get("price_cents", 0)) or 0) / 100.0
        pack_size = int(p.get("pack_size", 1) or 1)
        variant_numeric = _gid_to_numeric_id(str(p.get("variant_gid") or ""))
        cards.append(
            ExternalProductCard(
                product_id=str(p.get("product_gid") or p.get("product_id") or ""),
                variant_id=variant_numeric or str(p.get("variant_gid") or p.get("product_id") or ""),
                title=p.get("title", ""),
                description="EcoSoul product",
                price=price,
                currency="USD",
                image_url=p.get("image_url") or None,
                product_url=f"/products/{p.get('handle','')}",
                badges=["eco-friendly"],
                options=[],
                selected_options={},
                quantity=1,
                pack_size=pack_size,
                packs_recommended=1,
                total_units=pack_size,
                key_benefits=[],
                statistics=None,
            )
        )

    cart_items = [{"id": int(c.variant_id) if str(c.variant_id).isdigit() else c.variant_id, "quantity": 1} for c in cards if c.variant_id]
    cart_permalink = _build_cart_permalink(cart_items[:6])  # keep it short for browsing
    subcategory_label = session.current_subcategory or "products"
    if not cards:
        # User asked for a specific category (e.g. Cutlery) but no inventory available
        payload = ExternalChatPayload(
            message=_out_of_stock_message(subcategory_label),
            type="product_list",
            suggested_products=[],
            cart_items=session.cart_items or [],
            cart_permalink=_build_cart_permalink(session.cart_items or []),
            quick_replies=["Explore other products", "Different category", "Plan a party"],
        )
    else:
        payload = ExternalChatPayload(
            message=f"Here are some {subcategory_label} you can explore:",
            type="product_list",
            suggested_products=cards,
            cart_items=session.cart_items or [],
            cart_permalink=_build_cart_permalink(session.cart_items or []) or cart_permalink,
            quick_replies=["Show more products", "Different category", "Plan a party"],
        )
    ctx = ExternalConversationContext(
        detected_intent=Intent.UNKNOWN.value,
        detected_disposables=session.party_plan.disposables_needed or [],
        party_size=session.party_plan.party_size,
        estimated_coverage={},
        products_discussed=[c.product_id for c in cards],
        pending_actions=[],
        mode=session.mode,
        current_category=session.current_category,
        current_subcategory=session.current_subcategory,
    )
    return ExternalChatResponse(success=True, session_id=request.session_id, response=payload, conversation_context=ctx)


@router.post("/cart/add", response_model=ExternalCartAddResponse)
def external_cart_add(request: ExternalCartAddRequest) -> ExternalCartAddResponse:
    """
    Add items to Shopify cart. If cart_token is sent (from browser cookie or GET /cart.js),
    or SHOPIFY_CART_TOKEN is set in .env, items are added via Shopify Ajax API. Otherwise returns a stub response.
    """
    session = _get_or_create_session(request.session_id)
    session.cart_items = _merge_cart_items(session.cart_items or [], request.items or [], action=request.cart_action or "add")

    cart_token = request.cart_token or settings.SHOPIFY_CART_TOKEN
    if cart_token and request.items:
        ok, cart_info, msg = shopify_client.add_items_via_ajax(request.items, cart_token)
        if ok:
            return ExternalCartAddResponse(
                success=True,
                cart=cart_info,
                message=msg,
                next_suggestions=[
                    "Would you like to add matching bowls?",
                    "Check out our eco-friendly cutlery",
                ],
            )
        return ExternalCartAddResponse(success=False, cart={}, message=msg, next_suggestions=[])

    items_count = sum(int(i.get("quantity", 1)) for i in (session.cart_items or []))
    domain = (settings.SHOPIFY_STORE_DOMAIN or "https://yourstore.com").rstrip("/")
    cart = {
        "cart_id": "placeholder-cart-id",
        "cart_url": f"{domain}/cart",
        "checkout_url": f"{domain}/checkout",
        "items_count": items_count,
        "total_price": 0.0,
        "items": session.cart_items or [],
        "cart_permalink": _build_cart_permalink(session.cart_items or []),
    }
    msg = "Updated cart (send cart_token to add to real Shopify cart)."
    return ExternalCartAddResponse(success=True, cart=cart, message=msg, next_suggestions=[])


@router.post("/cart/add-multiple", response_model=ExternalCartAddResponse)
def external_cart_add_multiple(request: ExternalCartAddMultipleRequest) -> ExternalCartAddResponse:
    """
    Add multiple items at once (e.g. full party set). Uses cart_token from body or SHOPIFY_CART_TOKEN in .env.
    """
    session = _get_or_create_session(request.session_id)
    session.cart_items = _merge_cart_items(session.cart_items or [], request.items or [], action="add")

    cart_token = request.cart_token or settings.SHOPIFY_CART_TOKEN
    if cart_token and request.items:
        ok, cart_info, msg = shopify_client.add_items_via_ajax(request.items, cart_token)
        if ok:
            return ExternalCartAddResponse(
                success=True,
                cart=cart_info,
                message=msg,
                next_suggestions=[
                    "Check out our eco-friendly cups",
                    "Would you like matching napkins?",
                ],
            )
        return ExternalCartAddResponse(success=False, cart={}, message=msg, next_suggestions=[])

    items_count = sum(int(i.get("quantity", 1)) for i in (session.cart_items or []))
    domain = (settings.SHOPIFY_STORE_DOMAIN or "https://yourstore.com").rstrip("/")
    cart = {
        "cart_id": "placeholder-cart-id",
        "cart_url": f"{domain}/cart",
        "checkout_url": f"{domain}/checkout",
        "items_count": items_count,
        "total_price": 0.0,
        "items": session.cart_items or [],
        "cart_permalink": _build_cart_permalink(session.cart_items or []),
    }
    msg = "Updated cart (send cart_token to add to real Shopify cart)."
    return ExternalCartAddResponse(success=True, cart=cart, message=msg, next_suggestions=[])


@router.get("/session/{session_id}", response_model=ExternalSessionResponse)
def external_get_session(session_id: str) -> ExternalSessionResponse:
    session = _get_or_create_session(session_id)
    summary = {
        "party_size": session.party_plan.party_size,
        "menu_type": session.party_plan.menu_type,
        "disposables_needed": session.party_plan.disposables_needed,
        "products_recommended": [],
        "products_added_to_cart": [],
        "pending_questions": [],
    }
    return ExternalSessionResponse(session_id=session_id, customer_info=None, conversation_summary=summary)


@router.delete("/session/{session_id}")
def external_clear_session(session_id: str) -> dict:
    if session_id in EXTERNAL_SESSIONS:
        del EXTERNAL_SESSIONS[session_id]
    return {"success": True, "message": "Session cleared"}

