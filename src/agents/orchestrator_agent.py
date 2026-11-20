# ══════════════════════════════════════════════
#  ORCHESTRATOR AGENT
#  Job: manage multi-turn chat, skip redundant stages
# ══════════════════════════════════════════════
import json
from smolagents import ToolCallingAgent, tool

from src.agents.model import model
from src.agents.intent_agent import intent_agent
from src.agents.item_agent import item_agent
from src.agents.eval_agent import eval_agent
from src.state import artifacts, clear_offers, reset_artifacts


@tool
def get_blackboard_state() -> str:
    """Inspect current intent, discovered stores, offers count, and best_deal on the blackboard."""
    intent = artifacts.get("intent")
    intent_data = intent.model_dump() if hasattr(intent, "model_dump") else intent
    stores = [{"name": s.name, "url": s.url} for s in artifacts.get("stores", [])]
    offers_count = len(artifacts.get("offers", []))
    deal = artifacts.get("best_deal")
    deal_data = deal.model_dump() if hasattr(deal, "model_dump") else deal
    return json.dumps(
        {
            "intent": intent_data,
            "stores": stores,
            "offers_count": offers_count,
            "best_deal": deal_data,
        },
        indent=2,
    )


@tool
def clear_offers_for_new_item() -> str:
    """Clear old offers and best_deal while keeping discovered stores for the same region/category."""
    clear_offers()
    return "Cleared offers and best_deal; kept existing stores on blackboard."


@tool
def reset_all_state() -> str:
    """Reset intent, stores, offers, and best_deal when switching to a new region or category."""
    reset_artifacts()
    return "Reset all blackboard state."


instructions = """You are the SmolDealz Orchestrator managing 3 specialist agents:
- IntentFinderAgent: parses user shopping requests and updates artifacts['intent'].
- ItemFinderAgent: discovers stores and scrapes product offers into artifacts['offers'].
- EvaluationAgent: sorts offers and picks the best deal into artifacts['best_deal'].

Follow these routing rules on every user turn:
1. Call `get_blackboard_state()` first to see what is already on the blackboard.
2. Always run `IntentFinderAgent` when the user requests a product or changes quantity/budget/region (pass the previous intent if one exists).
3. Skip redundant work based on what changed:
   - Pure Q&A about existing results (e.g. "why did you pick that?", "compare the offers"): answer directly using `get_blackboard_state()` without calling sub-agents.
   - Only quantity or max_budget changed (same item & region, offers_count > 0): skip `ItemFinderAgent` and run ONLY `EvaluationAgent`.
   - Same region & category, different item (stores already exist): call `clear_offers_for_new_item()`, then run `ItemFinderAgent` instructing it to skip `discover_stores` and search the existing stores, then run `EvaluationAgent`.
   - First turn, or region/category changed: call `reset_all_state()` (if switching), then run `IntentFinderAgent` -> `ItemFinderAgent` -> `EvaluationAgent`.
4. After `EvaluationAgent` finishes (or after answering Q&A), call `final_answer` ONCE with a clear summary.
"""

orchestrator_agent = ToolCallingAgent(
    tools=[get_blackboard_state, clear_offers_for_new_item, reset_all_state],
    managed_agents=[intent_agent, item_agent, eval_agent],
    model=model,
    name="OrchestratorAgent",
    description="Top-level conversational orchestrator that routes turns to IntentFinderAgent, ItemFinderAgent, and EvaluationAgent while skipping redundant steps.",
    instructions=instructions,
    max_steps=8,
)
