"""
SmolDealz — Continuous Chat Pipeline Entrypoint
Run from project root:
  uv run -m src.run                  (Python Diff-Router chat loop)
  uv run -m src.run --orchestrator   (OrchestratorAgent chat loop)
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from src.state import artifacts, clear_offers, reset_artifacts
from src.schemas import BestDeal, UserIntent
from src.agents.intent_agent import intent_agent
from src.agents.item_agent import item_agent, close_browser, search_all_stores_parallel
from src.agents.eval_agent import eval_agent
from src.agents.orchestrator_agent import orchestrator_agent


def _decide_route(old_intent: UserIntent | None, new_intent: UserIntent) -> str:
    """Determine which pipeline stages to run based on what changed since the previous turn."""
    if old_intent is None:
        return "full_search"

    same_region = old_intent.region.strip().lower() == new_intent.region.strip().lower()
    same_category = old_intent.category.strip().lower() == new_intent.category.strip().lower()
    same_item = old_intent.item.strip().lower() == new_intent.item.strip().lower()

    if same_region and same_category and same_item and artifacts.get("offers"):
        return "re_evaluate"
    if same_region and same_category and artifacts.get("stores"):
        return "search_same_stores"
    return "full_search"


def _print_results(item: str, qty: int, currency: str) -> None:
    """Print the winning BestDeal and the top 5 offers summary."""
    print("\n" + "=" * 55)
    print("🎯  RESULT")
    print("=" * 55)
    deal = artifacts.get("best_deal")
    if isinstance(deal, BestDeal):
        print(f"🏆  Best deal for {qty}x {deal.matched_item}:")
        print(f"    Store : {deal.store}")
        print(f"    Price : {currency} {deal.unit_price} each")
        print(f"    Total : {currency} {deal.total_price}")
        print(f"    Stock : {deal.stock}")
        print(f"    Link  : {deal.url}")
        if deal.screenshot_path:
            print(f"    Proof : {deal.screenshot_path}")
        if deal.reasoning:
            print(f"    Why   : {deal.reasoning}")
    elif isinstance(deal, dict) and "error" in deal:
        print(f"❌  {deal['error']}")
    elif isinstance(deal, dict) and deal:
        print(f"🏆  Best deal for {qty}x {item}:")
        print(f"    Store : {deal.get('store')}")
        print(f"    Price : {currency} {deal.get('unit_price')} each")
        print(f"    Total : {currency} {deal.get('total_price')}")
        print(f"    Stock : {deal.get('stock')}")
        print(f"    Link  : {deal.get('url')}")
        if deal.get("screenshot_path"):
            print(f"    Proof : {deal.get('screenshot_path')}")
    else:
        print("❌  No deal selected.")

    _print_top_deals(artifacts.get("offers", []), qty=qty, default_currency=currency, limit=5)


def _print_top_deals(offers: list, qty: int = 1, default_currency: str = "LKR", limit: int = 5) -> None:
    """Print up to `limit` valid offers sorted by availability and price with clickable URLs."""
    priced = []
    for o in offers:
        price = o.price if hasattr(o, "price") else o.get("price", 0.0)
        if price <= 0:
            continue
        in_stock = o.in_stock if hasattr(o, "in_stock") else o.get("in_stock", True)
        stock_qty = o.stock_quantity if hasattr(o, "stock_quantity") else o.get("stock_quantity")
        screenshot_path = o.screenshot_path if hasattr(o, "screenshot_path") else o.get("screenshot_path")
        price_source = o.price_source if hasattr(o, "price_source") else o.get("price_source")
        priced.append({
            "store": o.store if hasattr(o, "store") else o.get("store", ""),
            "title": o.title if hasattr(o, "title") else o.get("title", ""),
            "unit_price": price,
            "total_price": round(price * qty, 2),
            "currency": (o.currency if hasattr(o, "currency") else o.get("currency")) or default_currency,
            "in_stock": in_stock,
            "stock_qty": stock_qty,
            "url": o.url if hasattr(o, "url") else o.get("url", ""),
            "screenshot_path": screenshot_path,
            "price_source": price_source,
        })

    if not priced:
        return

    priced.sort(key=lambda x: (not x["in_stock"], x["total_price"]))
    top = priced[:limit]

    print("\n" + "-" * 55)
    print(f"📋  TOP {len(top)} DEALS SUMMARY (qty={qty})")
    print("-" * 55)
    for rank, d in enumerate(top, start=1):
        stock_label = (
            f"In Stock ({d['stock_qty']})" if d["in_stock"] and d["stock_qty"] is not None
            else ("In Stock" if d["in_stock"] else "Out of Stock")
        )
        src_label = f" [{d['price_source']}]" if d.get("price_source") else ""
        print(f"{rank}. [{d['store']}] {d['title']}")
        print(f"   Price : {d['currency']} {d['unit_price']} each | Total: {d['currency']} {d['total_price']} | {stock_label}{src_label}")
        print(f"   Link  : {d['url']}")
        if d.get("screenshot_path"):
            print(f"   Proof : {d['screenshot_path']}")


def run_turn(user_input: str):
    """Run one conversational turn using the Python Diff-Router to skip redundant steps."""
    old_intent = artifacts.get("intent") if isinstance(artifacts.get("intent"), UserIntent) else None

    print("\n" + "=" * 55)
    print("🤖  SmolDealz Pipeline")
    print("=" * 55)

    # ── 1. Parse intent (context-aware on follow-ups) ─────────
    print(f"\n[1/3] IntentFinderAgent ← '{user_input}'")
    if old_intent is not None:
        intent_prompt = (
            f"Previous intent: {old_intent.model_dump()}. "
            f"New user message: '{user_input}'. "
            "Treat this as a follow-up update if it modifies quantity, budget, item, or region; "
            "preserve unchanged fields from Previous intent, then call save_intent once."
        )
    else:
        intent_prompt = (
            f"Parse this request and save the intent: '{user_input}'. "
            "Ask a clarifying question first if anything is ambiguous, then call save_intent once."
        )
    intent_agent.run(intent_prompt)
    intent = artifacts.get("intent")
    print(f"      ✅ intent = {intent}")

    if not isinstance(intent, UserIntent):
        print("❌  Could not parse a valid UserIntent.")
        return artifacts

    route = _decide_route(old_intent, intent)
    print(f"      🔀 route = {route}")

    # ── 2. Find items (skipped or store-reused per route) ─────
    if route == "re_evaluate":
        print("\n[2/3] ParallelScraper ⏭️  SKIPPED (same item & region; reusing existing offers)")
        artifacts["best_deal"] = {}
    else:
        force_refresh = route == "full_search"
        if force_refresh:
            artifacts["stores"] = []
        clear_offers()

        print(f"\n[2/3] ParallelScraper ← item='{intent.item}', region='{intent.region}', force_refresh={force_refresh}")
        try:
            search_all_stores_parallel(
                item=intent.item,
                region=intent.region,
                force_refresh=force_refresh,
            )
        finally:
            close_browser()

    print(f"      ✅ stores = {[s.name for s in artifacts.get('stores', [])]}")
    print(f"      ✅ offers count = {len(artifacts.get('offers', []))}")

    # ── 3. Evaluate ───────────────────────────────────────────
    offers = artifacts.get("offers", [])
    if not offers:
        artifacts["best_deal"] = {"error": f"No offers found for '{intent.item}' in '{intent.region}'."}
    else:
        budget_note = (
            f" within a max total budget of {intent.max_budget} {intent.currency}"
            if intent.max_budget is not None
            else ""
        )
        print(f"\n[3/3] EvaluationAgent ← {len(offers)} offers, need qty={intent.quantity}{budget_note}")
        eval_agent.run(
            f"Evaluate the offers for '{intent.item}' (quantity={intent.quantity}{budget_note}). "
            "Call get_sorted_offers(), verify the cheapest matching offer, "
            "and call select_best_deal(offer_index=..., reasoning=...) (or save_no_deal if none qualify)."
        )
    print(f"      ✅ best_deal = {artifacts['best_deal']}")

    _print_results(intent.item, intent.quantity, intent.currency)
    return artifacts


def run(user_input: str):
    """Single-shot entrypoint (resets state, then runs one turn)."""
    reset_artifacts()
    return run_turn(user_input)


def chat_loop(use_orchestrator: bool = False) -> None:
    """Continuous interactive chat loop."""
    reset_artifacts()
    mode_label = "OrchestratorAgent" if use_orchestrator else "Diff-Router"
    print(f"\n💬 SmolDealz Chat ({mode_label}) — type 'exit' or 'quit' to stop.")

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("Bye!")
            break

        if use_orchestrator:
            try:
                response = orchestrator_agent.run(user_input, reset=False)
                print(f"\n🤖 {response}")
            finally:
                close_browser()
            intent = artifacts.get("intent")
            if isinstance(intent, UserIntent) and artifacts.get("offers"):
                _print_results(intent.item, intent.quantity, intent.currency)
        else:
            run_turn(user_input)


if __name__ == "__main__":
    use_orch = "--orchestrator" in sys.argv
    chat_loop(use_orchestrator=use_orch)
