"""
SmolDealz — Pipeline Entrypoint
Run from project root:  uv run src/run.py
"""
from dotenv import load_dotenv
load_dotenv()

from state import artifacts
from agents.intent_agent import intent_agent
from agents.item_agent import item_agent
from agents.eval_agent import eval_agent


def run(user_input: str):
    print("\n" + "=" * 55)
    print("🤖  SmolDealz Pipeline")
    print("=" * 55)

    # ── 1. Parse intent ───────────────────────────────────────
    print(f"\n[1/3] IntentFinderAgent ← '{user_input}'")
    intent_agent.run(
        f"Parse this request and save the intent: '{user_input}'. "
        "Ask a clarifying question first if anything is ambiguous, then call save_intent once."
    )
    print(f"      ✅ intent = {artifacts['intent']}")

    # ── 2. Find items ─────────────────────────────────────────
    intent = artifacts.get("intent")
    item = intent.item if hasattr(intent, "item") else getattr(intent, "get", lambda k, d: user_input)("item", user_input)
    region = intent.region if hasattr(intent, "region") else "Sri Lanka"
    print(f"\n[2/3] ItemFinderAgent ← item='{item}', region='{region}'")
    item_agent.run(
        f"Find product listings and prices for '{item}' in '{region}'. "
        "First call discover_stores, then search each store using the tiered protocol, "
        "inspect product details, and save verified offers using save_product_offer."
    )
    print(f"      ✅ stores = {[s.name for s in artifacts.get('stores', [])]}")
    print(f"      ✅ offers count = {len(artifacts.get('offers', []))}")

    # ── 3. Evaluate ───────────────────────────────────────────
    qty = artifacts["intent"].get("quantity", 1)
    print(f"\n[3/3] EvaluationAgent ← {len(artifacts['offers'])} offers, need qty={qty}")
    eval_agent.run(
        f"I need {qty} units. "
        "Call summarize_offers to review what's available, "
        f"then call find_best_deal with required_qty={qty}."
    )
    print(f"      ✅ best_deal = {artifacts['best_deal']}")

    # ── Result ────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("🎯  RESULT")
    print("=" * 55)
    deal = artifacts["best_deal"]
    if "error" in deal:
        print(f"❌  {deal['error']}")
    else:
        print(f"🏆  Best deal for {qty}x {item}:")
        print(f"    Store : {deal['store']}")
        print(f"    Price : Rs.{deal['unit_price']} each")
        print(f"    Total : Rs.{deal['total_price']}")
        print(f"    Stock : {deal['stock']} units available")
        print(f"    Link  : {deal['url']}")

    return artifacts


if __name__ == "__main__":
    user_input = input("What are you looking for? ").strip()
    if not user_input:
        user_input = "I need 3 mg90 servo motors in Sri Lanka under Rs. 3500"
    run(user_input)
