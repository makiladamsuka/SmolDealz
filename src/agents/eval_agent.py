# ══════════════════════════════════════════════
#  AGENT 3 — EvaluationAgent
#  Job: filter offers, check qty/budget, save BestDeal
# ══════════════════════════════════════════════
from pydantic import ValidationError
from smolagents import CodeAgent, tool

from src.agents.model import model
from src.schemas import BestDeal, UserIntent
from src.state import artifacts


@tool
def get_evaluation_context() -> dict:
    """Return the user's intent and all scraped offers as plain Python dicts."""
    intent = artifacts.get("intent")
    intent_dict = intent.model_dump() if isinstance(intent, UserIntent) else (intent or {})

    offers = []
    for idx, offer in enumerate(artifacts.get("offers", [])):
        offer_dict = offer.model_dump() if hasattr(offer, "model_dump") else dict(offer)
        offer_dict["index"] = idx
        offers.append(offer_dict)

    return {"intent": intent_dict, "offers": offers}


@tool
def save_best_deal(
    matched_item: str,
    store: str,
    unit_price: float,
    total_price: float,
    url: str,
    stock: int | str = "In Stock",
    reasoning: str = "",
) -> str:
    """Validate and save the winning BestDeal to artifacts['best_deal'].

    Args:
        matched_item: Product name evaluated.
        store: Winning store name.
        unit_price: Unit price in local currency.
        total_price: Total price for the required quantity.
        url: Direct product link.
        stock: Exact stock count if known, or 'In Stock'.
        reasoning: Why this deal was selected (price, stock, budget fit, relevance).
    """
    try:
        deal = BestDeal(
            matched_item=matched_item,
            store=store,
            unit_price=unit_price,
            total_price=total_price,
            stock=stock,
            url=url,
            reasoning=reasoning or None,
        )
        artifacts["best_deal"] = deal
        return f"SUCCESS: Saved BestDeal to artifacts['best_deal']: {deal.model_dump()}"
    except ValidationError as e:
        return f"VALIDATION ERROR: {e}"


@tool
def save_no_deal(reason: str) -> str:
    """Record why no eligible deal could be selected in artifacts['best_deal'].

    Args:
        reason: Clear explanation (e.g. out of stock, over budget, no matching offers).
    """
    artifacts["best_deal"] = {"error": reason}
    return f"Recorded no deal: {reason}"


instructions = """You are an expert e-commerce deal evaluation agent.
Your job is to pick the best valid deal from scraped offers on the blackboard.

Follow these steps:
1. Call `ctx = get_evaluation_context()` first to inspect `ctx["intent"]` and `ctx["offers"]`.
2. Print `ctx["intent"]` and each offer's `index`, `store`, `title`, `price`, `currency`, `in_stock`, `stock_quantity`, and `snippet`.
3. Filter out offers that:
   - have `price <= 0`,
   - have `in_stock` is False,
   - have `stock_quantity` not None and `stock_quantity < quantity`,
   - clearly do NOT match `intent["item"]` or `intent["specs"]` (e.g. accessories, cables, wrong model).
4. For remaining eligible offers, compute `total_price = o["price"] * quantity`.
   - If `intent.get("max_budget")` is set, prefer offers where `total_price <= max_budget`.
   - If no eligible offer fits `max_budget`, call `save_no_deal(...)` explaining the cheapest eligible total vs the budget.
5. Select the lowest `total_price` eligible offer and call `save_best_deal(...)` with `stock=o["stock_quantity"] if o["stock_quantity"] is not None else "In Stock"` and concise `reasoning`.
6. Always wrap every Python step — including `final_answer("...")` — inside `<code>...</code>` tags.
"""

eval_agent = CodeAgent(
    tools=[get_evaluation_context, save_best_deal, save_no_deal],
    model=model,
    name="EvaluationAgent",
    description="Reads artifacts['intent'] and artifacts['offers'], filters by relevance/stock/budget, and saves BestDeal.",
    instructions=instructions,
    max_steps=4,
)
