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

