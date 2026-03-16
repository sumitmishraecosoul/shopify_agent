import math
from typing import Dict, List

from .models import PartyPlan, BasketItem, BasketRecommendation
from .clickhouse_client import clickhouse_client


def _compute_required_units(plan: PartyPlan) -> Dict[str, int]:
    """
    Compute required units per category. Uses explicit per-person quantities
    when set (e.g. 1 plate, 2 spoons, 3 bowls per person); otherwise falls
    back to heuristic. Adds a small backup (10%) so we suggest slightly more.
    """
    if not plan.party_size:
        return {}

    people = plan.party_size
    required: Dict[str, int] = {}

    # Explicit per-person quantities take priority. If party planning didn't collect
    # these yet, default to 1 per person for common disposables.
    plates_pp = plan.plates_per_person if plan.plates_per_person is not None else 1
    bowls_pp = plan.bowls_per_person if plan.bowls_per_person is not None else 1
    cups_pp = plan.cups_per_person if plan.cups_per_person is not None else 1
    spoons_pp = plan.spoons_per_person if plan.spoons_per_person is not None else 1

    # When explicit per-person fields exist (or defaulted), use them.
    required["plates"] = people * max(0, int(plates_pp))
    required["bowls"] = people * max(0, int(bowls_pp))
    required["cups"] = people * max(0, int(cups_pp))
    required["spoons"] = people * max(0, int(spoons_pp))

    # Respect explicit disposables_needed if provided: only keep those categories.
    if plan.disposables_needed:
        keep = {str(x).lower() for x in plan.disposables_needed}
        required = {k: v for k, v in required.items() if k in keep}

    # Add ~10% backup (at least 1 extra) so recommendation is slightly above minimum
    with_backup: Dict[str, int] = {}
    for cat, units in required.items():
        extra = max(1, (units * 10) // 100)
        with_backup[cat] = units + extra
    return with_backup


def _select_packs(required_units: int, products: List[dict]) -> List[dict]:
    """
    Choose a pack option that covers required_units with minimal overage.
    Tie-break by total price. For v1 demo, we keep this to a single SKU per category.
    """
    if required_units <= 0 or not products:
        return []

    best = None
    best_score = None
    for p in products:
        pack_size = int(p.get("pack_size", 1) or 1)
        price_cents = int(p.get("price_cents", 0) or 0)
        if pack_size <= 0:
            continue

        packs = max(1, int(math.ceil(required_units / pack_size)))
        total_units = packs * pack_size
        overage = max(0, total_units - required_units)
        total_price = packs * price_cents

        score = (overage, total_price, -pack_size)
        if best_score is None or score < best_score:
            best_score = score
            best = {**p, "packs": packs}

    return [best] if best else []


def build_recommendation(plan: PartyPlan) -> BasketRecommendation | None:
    """
    Build a basket recommendation deterministically from the party plan.
    Returns None if not enough information.
    """
    if not plan.party_size:
        return None

    required_map = _compute_required_units(plan)
    if not required_map:
        return None

    items: List[BasketItem] = []
    estimated_coverage: Dict[str, int] = {}
    total_price_cents = 0

    # We only sell eco-friendly products; always prefer high eco score
    eco = (plan.eco_preference or "high").lower()

    for category, needed_units in required_map.items():
        products = clickhouse_client.fetch_products_for_category(
            category=category,
            eco_preference=eco,
        )
        selected = _select_packs(needed_units, products)
        if not selected:
            continue

        coverage = 0
        for prod in selected:
            packs = int(prod.get("packs", 1))
            pack_size = int(prod.get("pack_size", 1))
            total_units = packs * pack_size
            price_cents = int(prod.get("price_cents", 0)) * packs

            items.append(
                BasketItem(
                    product_id=str(prod.get("product_id")),
                    title=str(prod.get("title", "")),
                    category=str(prod.get("category", category)),
                    packs=packs,
                    units_per_pack=pack_size,
                    total_units=total_units,
                    price_cents_per_pack=int(prod.get("price_cents", 0)),
                )
            )
            coverage += total_units
            total_price_cents += price_cents

        estimated_coverage[category] = coverage

    if not items:
        return None

    return BasketRecommendation(
        items=items,
        total_people=plan.party_size or 0,
        estimated_coverage=estimated_coverage,
        total_price_cents=total_price_cents,
    )

