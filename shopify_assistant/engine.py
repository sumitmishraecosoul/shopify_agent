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

    # Explicit per-person quantities take priority
    if getattr(plan, "plates_per_person", None) is not None and plan.plates_per_person is not None:
        required["plates"] = people * max(0, int(plan.plates_per_person))
    if getattr(plan, "spoons_per_person", None) is not None and plan.spoons_per_person is not None:
        required["spoons"] = people * max(0, int(plan.spoons_per_person))
    if getattr(plan, "bowls_per_person", None) is not None and plan.bowls_per_person is not None:
        required["bowls"] = people * max(0, int(plan.bowls_per_person))
    if getattr(plan, "cups_per_person", None) is not None and plan.cups_per_person is not None:
        required["cups"] = people * max(0, int(plan.cups_per_person))

    if required:
        # Add ~10% backup (at least 1 extra) so recommendation is slightly above minimum
        with_backup: Dict[str, int] = {}
        for cat, units in required.items():
            extra = max(1, (units * 10) // 100)
            with_backup[cat] = units + extra
        return with_backup

    # Fallback heuristic
    disposables = plan.disposables_needed or ["plates", "bowls", "spoons"]
    for item in disposables:
        if item == "plates":
            factor = 1.3 if plan.menu_type == "full_meal" else 1.0
        elif item == "bowls":
            factor = 1.0 if plan.menu_type == "full_meal" else 0.5
        elif item == "spoons":
            factor = 2.0 if "dessert" in (plan.courses or []) else 1.0
        else:
            factor = 1.0
        required[item] = int(round(people * factor))
    return required


def _select_packs(required_units: int, products: List[dict]) -> List[dict]:
    """
    Very basic greedy pack selection: choose cheapest pack_size first.
    This is intentionally simple for v1.
    """
    if required_units <= 0 or not products:
        return []

    # Sort by price per unit ascending
    enriched = []
    for p in products:
        pack_size = int(p.get("pack_size", 1))
        price_cents = int(p.get("price_cents", 0))
        if pack_size <= 0:
            continue
        price_per_unit = price_cents / pack_size if pack_size else price_cents
        enriched.append((price_per_unit, p))

    enriched.sort(key=lambda x: x[0])
    selected: List[dict] = []
    remaining = required_units

    for _, prod in enriched:
        pack_size = int(prod.get("pack_size", 1))
        if pack_size <= 0:
            continue
        packs = max(1, remaining // pack_size)
        selected.append({**prod, "packs": packs})
        remaining -= packs * pack_size
        if remaining <= 0:
            break

    return selected


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

