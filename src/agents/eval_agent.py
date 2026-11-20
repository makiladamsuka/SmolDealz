# ══════════════════════════════════════════════
#  AGENT 3 — EvaluationAgent
#  Job: sort/filter offers in Python, pick best match
# ══════════════════════════════════════════════
from pydantic import ValidationError
from smolagents import ToolCallingAgent, tool

from src.agents.model import model
from src.schemas import BestDeal, UserIntent
from src.state import artifacts


def _intent_params() -> tuple[str, int, float | None, str]:
    intent = artifacts.get("intent")
    if isinstance(intent, UserIntent):
        return intent.item, intent.quantity, intent.max_budget, intent.currency
    if isinstance(intent, dict):
        return (
            intent.get("item", "product"),
            int(intent.get("quantity", 1)),
            intent.get("max_budget"),
            intent.get("currency", "LKR"),
        )
    return "product", 1, None, "LKR"


@tool
def get_sorted_offers() -> str:
    """Filter eligible offers (in stock, valid price, enough stock) and return them sorted cheapest-first.
    No args needed — reads directly from artifacts['intent'] and artifacts['offers'].
    """
    item, qty, max_budget, currency = _intent_params()
    offers = artifacts.get("offers", [])
    if not offers:
        return "No offers on blackboard. Call save_no_deal and then final_answer."

    eligible = []
    for idx, o in enumerate(offers):
        price = o.price if hasattr(o, "price") else o.get("price", 0.0)
        in_stock = o.in_stock if hasattr(o, "in_stock") else o.get("in_stock", True)
        stock_qty = o.stock_quantity if hasattr(o, "stock_quantity") else o.get("stock_quantity")
        if price <= 0 or not in_stock:
            continue
        if stock_qty is not None and stock_qty < qty:
            continue

        total_price = round(price * qty, 2)
        within_budget = max_budget is None or total_price <= max_budget
        eligible.append({
            "offer_index": idx,
            "store": o.store if hasattr(o, "store") else o.get("store", ""),
            "title": o.title if hasattr(o, "title") else o.get("title", ""),
            "unit_price": price,
            "total_price": total_price,
            "currency": o.currency if hasattr(o, "currency") else o.get("currency", currency),
            "stock": stock_qty if stock_qty is not None else "In Stock",
            "within_budget": within_budget,
            "snippet": ((o.snippet if hasattr(o, "snippet") else o.get("snippet")) or "")[:120],
        })

    if not eligible:
        return (
            f"0 eligible offers for '{item}' (qty={qty}) after filtering out price<=0, "
            "out-of-stock, or insufficient stock. Call save_no_deal and then final_answer."
        )

    # Sort: within-budget first, then lowest total_price
    eligible.sort(key=lambda x: (not x["within_budget"], x["total_price"]))

    budget_str = f"{max_budget} {currency}" if max_budget is not None else "None"
    lines = [
        f"Target: '{item}' | Qty: {qty} | Max Budget: {budget_str}",
        "Sorted Candidates (cheapest first):",
    ]
    for c in eligible:
        budget_flag = "OK" if c["within_budget"] else "OVER_BUDGET"
        lines.append(
            f"- offer_index={c['offer_index']} | {c['currency']} {c['total_price']} total "
            f"({c['unit_price']} each) | [{budget_flag}] | Store: {c['store']} | "
            f"Stock: {c['stock']} | Title: {c['title']} | Snippet: {c['snippet']}"
        )
    return "\n".join(lines)


@tool
def select_best_deal(offer_index: int, reasoning: str = "") -> str:
    """Select the winning offer by its offer_index and save BestDeal to artifacts['best_deal'].

    Args:
        offer_index: The exact offer_index from get_sorted_offers().
        reasoning: Short explanation of why this offer was chosen.
    """
    offers = artifacts.get("offers", [])
    if offer_index < 0 or offer_index >= len(offers):
        return f"ERROR: offer_index={offer_index} out of range (0..{len(offers) - 1})."

    item, qty, max_budget, _ = _intent_params()
    o = offers[offer_index]
    unit_price = o.price if hasattr(o, "price") else o["price"]
    total_price = round(unit_price * qty, 2)
    if max_budget is not None and total_price > max_budget:
        return (
            f"REJECTED: offer_index={offer_index} total_price={total_price} exceeds "
            f"max_budget={max_budget}. Pick a cheaper within-budget offer or call save_no_deal."
        )

    stock_qty = o.stock_quantity if hasattr(o, "stock_quantity") else o.get("stock_quantity")
    screenshot_path = o.screenshot_path if hasattr(o, "screenshot_path") else o.get("screenshot_path")
    try:
        deal = BestDeal(
            matched_item=o.title if hasattr(o, "title") else o.get("title", item),
            store=o.store if hasattr(o, "store") else o["store"],
            unit_price=unit_price,
            total_price=total_price,
            stock=stock_qty if stock_qty is not None else "In Stock",
            url=o.url if hasattr(o, "url") else o["url"],
            reasoning=reasoning or None,
            screenshot_path=screenshot_path,
        )
        artifacts["best_deal"] = deal
        return (
            f"SUCCESS: Saved BestDeal for offer_index={offer_index}. "
            "Do NOT call select_best_deal again — immediately call final_answer now."
        )
    except ValidationError as e:
        return f"VALIDATION ERROR: {e}"


@tool
def save_no_deal(reason: str) -> str:
    """Record why no eligible deal could be selected in artifacts['best_deal'].

    Args:
        reason: Clear explanation (e.g. out of stock, over budget, no matching offers).
    """
    artifacts["best_deal"] = {"error": reason}
    return f"RECORDED NO DEAL: {reason}. Immediately call final_answer now."


instructions = """You are an e-commerce deal evaluation agent.
1. Call `get_sorted_offers()` first — it already filters out invalid/out-of-stock offers and sorts them cheapest-first.
2. Inspect the top candidate(s): pick the FIRST (cheapest) `offer_index` that is `[OK]` (within budget) and whose `Title`/`Snippet` genuinely matches the target product (not an accessory, cable, or wrong model).
3. Call `select_best_deal(offer_index=..., reasoning=...)` ONCE (or `save_no_deal(...)` if none qualify).
4. On the next step, call `final_answer` EXACTLY ONCE with a brief summary.
"""

eval_agent = ToolCallingAgent(
    tools=[get_sorted_offers, select_best_deal, save_no_deal],
    model=model,
    name="EvaluationAgent",
    description="Sorts eligible offers by price, verifies product match, and saves BestDeal.",
    instructions=instructions,
    max_steps=4,
)
