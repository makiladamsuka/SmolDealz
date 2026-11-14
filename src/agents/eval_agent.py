# ══════════════════════════════════════════════
#  AGENT 3 — EvaluationAgent
#  Job: pick the best deal from offers on board
# ══════════════════════════════════════════════
from agents.model import model
from state import artifacts
from smolagents import tool, ToolCallingAgent

@tool
def find_best_deal(required_qty: int) -> str:
    """Find the cheapest offer that has enough stock for the required quantity.

    Args:
        required_qty: Minimum stock needed (e.g. 3).
    """
    eligible = [o for o in artifacts["offers"] if o["stock"] >= required_qty]

    if not eligible:
        artifacts["best_deal"] = {"error": "No store has enough stock."}
        return "No eligible offers found."

    best = min(eligible, key=lambda o: o["price"])
    artifacts["best_deal"] = {
        "store":       best["store"],
        "unit_price":  best["price"],
        "total_price": best["price"] * required_qty,
        "stock":       best["stock"],
        "url":         best["url"],
    }
    return f"Best deal: {artifacts['best_deal']}"


@tool
def summarize_offers() -> str:
    """Return a markdown table of all offers found so far from the blackboard.
    No args needed — reads from artifacts['offers'].
    """
    if not artifacts["offers"]:
        return "No offers yet."

    header = "| Store | Price | Stock |\n|---|---|---|"
    rows = [f"| {o['store']} | Rs.{o['price']} | {o['stock']} units |"
            for o in artifacts["offers"]]
    return "\n".join([header] + rows)


eval_agent = ToolCallingAgent(
    tools=[summarize_offers, find_best_deal],
    model=model, 
    name="EvaluationAgent",
    description="Reads artifacts['offers'], filters by quantity, and writes the best deal to artifacts['best_deal'].",
    max_steps=3,
)