from enum import Enum
from typing import List, Optional, Dict, Any

from pydantic import BaseModel


class Intent(str, Enum):
    PLAN_EVENT = "PLAN_EVENT"
    ADJUST_PLAN = "ADJUST_PLAN"
    CONFIRM_ADD_TO_CART = "CONFIRM_ADD_TO_CART"
    EDIT_QUANTITY = "EDIT_QUANTITY"
    CHECK_CART = "CHECK_CART"
    CHECKOUT = "CHECKOUT"
    SMALL_TALK = "SMALL_TALK"
    UNKNOWN = "UNKNOWN"


class ConversationStep(str, Enum):
    PLANNING = "PLANNING"
    AWAITING_DETAILS = "AWAITING_DETAILS"
    RECOMMENDED = "RECOMMENDED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CART_APPLIED = "CART_APPLIED"


class ConversationMode(str, Enum):
    """
    High-level mode for the external chatbot flow.
    """
    PARTY_PLANNING = "PARTY_PLANNING"
    PRODUCT_BROWSING = "PRODUCT_BROWSING"
    PRODUCT_SEARCH = "PRODUCT_SEARCH"


class PartyPlan(BaseModel):
    party_size: Optional[int] = None
    event_type: Optional[str] = None
    location: Optional[str] = None
    menu_type: Optional[str] = None  # "starters_only", "full_meal", etc.
    courses: List[str] = []  # ["starters", "main", "dessert"]
    disposables_needed: List[str] = []  # ["plates", "bowls", "spoons", "cups", ...]
    # Explicit per-person quantities (e.g. 1 plate, 2 spoons, 3 bowls per guest)
    plates_per_person: Optional[int] = None
    spoons_per_person: Optional[int] = None
    bowls_per_person: Optional[int] = None
    cups_per_person: Optional[int] = None
    budget_per_person: Optional[float] = None
    eco_preference: Optional[str] = None  # unused: we only sell eco-friendly


class BasketItem(BaseModel):
    product_id: str
    title: str
    category: str
    packs: int
    units_per_pack: int
    total_units: int
    price_cents_per_pack: int
    currency: str = "INR"


class BasketRecommendation(BaseModel):
    items: List[BasketItem]
    total_people: int
    estimated_coverage: Dict[str, int]
    total_price_cents: int


class SessionState(BaseModel):
    session_id: str
    party_plan: PartyPlan = PartyPlan()
    last_basket: Optional[BasketRecommendation] = None
    step: ConversationStep = ConversationStep.PLANNING
    # External chatbot state
    mode: Optional[ConversationMode] = None
    current_category: Optional[str] = None
    current_subcategory: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str
    message: str
    conversation_history: Optional[List[Dict[str, Any]]] = None


class Action(BaseModel):
    type: str
    payload: Dict[str, Any] = {}


class ChatResponse(BaseModel):
    response: str
    data: Dict[str, Any] = {}
    actions: List[Action] = []


class CartApplyRequest(BaseModel):
    session_id: str
    basket: BasketRecommendation


class CartApplyResponse(BaseModel):
    cart_id: Optional[str]
    checkout_url: Optional[str]
    message: str


# ---------- External API models (Shopify-facing) ----------

class ExternalProductCard(BaseModel):
    """
    Product card format for external (Shopify) consumers, inspired by
    the L'Occitane-style chatbot responses.
    """

    product_id: str  # Shopify product GID or numeric id when available
    variant_id: str  # Shopify variant GID or numeric id when available
    title: str
    description: str = ""
    price: float
    compare_at_price: Optional[float] = None
    currency: str = "USD"
    image_url: Optional[str] = None
    product_url: str = ""  # /products/handle
    badges: List[str] = []
    options: List[Dict[str, Any]] = []
    selected_options: Dict[str, str] = {}
    # Recommendation-specific fields
    quantity: int = 1  # packs to add
    pack_size: int
    packs_recommended: int
    total_units: int
    subscription_available: bool = False
    subscription_discount: Optional[str] = None
    key_benefits: List[str] = []
    statistics: Optional[str] = None


class ExternalRoutineStep(BaseModel):
    step: int
    step_name: str
    product: ExternalProductCard


class ExternalChatPayload(BaseModel):
    """
    Inner "response" object for ExternalChatResponse.
    """

    message: str
    type: str
    suggested_products: List[ExternalProductCard] = []
    # Shopify cart payload-ready items (numeric variant ids)
    cart_items: List[Dict[str, Any]] = []
    # Optional one-click cart URL
    cart_permalink: Optional[str] = None
    routine_name: Optional[str] = None
    routine_steps: List[ExternalRoutineStep] = []
    routine_total: Optional[float] = None
    routine_savings: Optional[float] = None
    add_all_to_cart_button: Optional[bool] = None
    quick_replies: List[str] = []
    suggested_questions: List[str] = []


class ExternalConversationContext(BaseModel):
    detected_intent: Optional[str] = None
    detected_disposables: List[str] = []
    party_size: Optional[int] = None
    estimated_coverage: Dict[str, int] = {}
    products_discussed: List[str] = []
    pending_actions: List[str] = []
    mode: Optional[ConversationMode] = None
    current_category: Optional[str] = None
    current_subcategory: Optional[str] = None


class ExternalChatResponse(BaseModel):
    success: bool
    session_id: str
    response: ExternalChatPayload
    conversation_context: ExternalConversationContext


class ExternalCartAddRequest(BaseModel):
    session_id: str
    items: List[Dict[str, Any]]
    cart_action: str = "add"
    checkout_after_add: bool = False
    customer_id: Optional[str] = None
    # Cart token from Shopify (browser cookie "cart" or from GET /cart.js). If provided, backend will add items to that cart via Shopify Ajax API.
    cart_token: Optional[str] = None


class ExternalCartAddResponse(BaseModel):
    success: bool
    cart: Dict[str, Any]
    message: str
    next_suggestions: List[str] = []


class ExternalCartAddMultipleRequest(BaseModel):
    session_id: str
    items: List[Dict[str, Any]]
    routine_id: Optional[str] = None
    apply_routine_discount: bool = False
    # Cart token from Shopify (browser cookie "cart" or from GET /cart.js). If provided, backend will add items to that cart.
    cart_token: Optional[str] = None


class ExternalSessionResponse(BaseModel):
    session_id: str
    customer_info: Optional[Dict[str, Any]] = None
    conversation_summary: Dict[str, Any]

