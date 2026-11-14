"""
SmolDealz — Bare-Minimum Agent Flow Boilerplate
================================================
3 agents, 1 shared blackboard (plain dict), sample data only.
No real browsing. No Pydantic. Just the skeleton.

Flow:
  User Input
      │
      ▼
  IntentFinderAgent  ──writes──▶  artifacts["intent"]
      │
      ▼
  ItemFinderAgent    ──writes──▶  artifacts["offers"]
      │
      ▼
  EvaluationAgent    ──writes──▶  artifacts["best_deal"]
"""

import os
from dotenv import load_dotenv
from smolagents import ToolCallingAgent, LiteLLMModel, tool
from state import artifacts
from src.agents import eval_agent, intent_agent, item_agent

load_dotenv()





# ══════════════════════════════════════════════
#  ORCHESTRATOR  — runs the pipeline in order
# ══════════════════════════════════════════════

def run(user_input: str):
    print("\n" + "="*55)
    print("🤖  SmolDealz — Basic Flow")
    print("="*55)

    # ── Step 1: Parse intent ──────────────────────────────────
    print(f"\n[1/3] IntentFinderAgent ← '{user_input}'")
    intent_agent.run(
        f"Parse this request and save the intent: '{user_input}'. "
        "Ask a clarifying question if needed, then call save_intent."
    )
    print(f"      ✅ artifacts['intent'] = {artifacts['intent']}")

    # ── Step 2: Find items ────────────────────────────────────
    print(f"\n[2/3] ItemFinderAgent ← intent: {artifacts['intent']}")
    item   = artifacts["intent"].get("item", user_input)
    item_agent.run(
        f"Search all regional stores for '{item}'. "
        "First call get_stores_to_search, then call search_store for each store."
    )
    print(f"      ✅ artifacts['offers'] = {artifacts['offers']}")

    # ── Step 3: Evaluate ──────────────────────────────────────
    qty = artifacts["intent"].get("quantity", 1)
    print(f"\n[3/3] EvaluationAgent ← {len(artifacts['offers'])} offers, need qty={qty}")
    eval_agent.run(
        f"I need {qty} units. Call summarize_offers to see what's available, "
        f"then call find_best_deal with required_qty={qty}."
    )
    print(f"      ✅ artifacts['best_deal'] = {artifacts['best_deal']}")

    # ── Final result ──────────────────────────────────────────
    print("\n" + "="*55)
    print("🎯  RESULT")
    print("="*55)
    deal = artifacts["best_deal"]
    if "error" in deal:
        print(f"❌  {deal['error']}")
    else:
        print(f"🏆  Best deal for {qty}x {item}:")
        print(f"    Store:  {deal['store']}")
        print(f"    Price:  Rs.{deal['unit_price']} each")
        print(f"    Total:  Rs.{deal['total_price']}")
        print(f"    Stock:  {deal['stock']} units")
        print(f"    Link:   {deal['url']}")

    return artifacts


if __name__ == "__main__":
    run("I need 3 mg90 servo motors in Sri Lanka under Rs. 3500")
