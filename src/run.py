"""
SmolDealz — Pipeline Entrypoint
Run from project root:  uv run -m src.run  (or python -m src.run)
"""
from dotenv import load_dotenv
load_dotenv()

from src.state import artifacts, reset_artifacts
from src.schemas import BestDeal, UserIntent
from src.agents.intent_agent import intent_agent
from src.agents.item_agent import item_agent, close_browser
from src.agents.eval_agent import eval_agent


def run(user_input: str):
    reset_artifacts()

    print("\n" + "=" * 55)
    print("🤖  SmolDealz Pipeline")
    print("=" * 55)

    # ── 1. Parse intent ───────────────────────────────────────
    print(f"\n[1/3] IntentFinderAgent ← '{user_input}'")
    intent_agent.run(
        f"Parse this request and save the intent: '{user_input}'. "
        "Ask a clarifying question first if anything is ambiguous, then call save_intent once."
    )
    intent = artifacts.get("intent")
    print(f"      ✅ intent = {intent}")

    if isinstance(intent, UserIntent):
        item = intent.item
        region = intent.region
        qty = intent.quantity
        max_budget = intent.max_budget
        currency = intent.currency
    elif isinstance(intent, dict):
        item = intent.get("item", user_input)
        region = intent.get("region", "Sri Lanka")
        qty = intent.get("quantity", 1)
        max_budget = intent.get("max_budget")
        currency = intent.get("currency", "LKR")
    else:
        item, region, qty, max_budget, currency = user_input, "Sri Lanka", 1, None, "LKR"

    # ── 2. Find items ─────────────────────────────────────────
    print(f"\n[2/3] ItemFinderAgent ← item='{item}', region='{region}'")
    try:
        item_agent.run(
            f"Find product listings and prices for '{item}' in '{region}'. "
            "First call discover_stores, then search each store using the tiered protocol, "
            "inspect product details, and save verified offers using save_product_offer."
        )
    finally:
        close_browser()

    print(f"      ✅ stores = {[s.name for s in artifacts.get('stores', [])]}")
    print(f"      ✅ offers count = {len(artifacts.get('offers', []))}")

    # ── 3. Evaluate ───────────────────────────────────────────
    offers = artifacts.get("offers", [])
    if not offers:
        artifacts["best_deal"] = {"error": f"No offers found for '{item}' in '{region}'."}
    else:
        budget_note = f" within a max total budget of {max_budget} {currency}" if max_budget is not None else ""
        print(f"\n[3/3] EvaluationAgent ← {len(offers)} offers, need qty={qty}{budget_note}")
        eval_agent.run(
            f"Evaluate the offers for '{item}' (quantity={qty}{budget_note}). "
            "Call get_sorted_offers(), verify the cheapest matching offer, "
            "and call select_best_deal(offer_index=..., reasoning=...) (or save_no_deal if none qualify)."
        )
    print(f"      ✅ best_deal = {artifacts['best_deal']}")

    # ── Result ────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("🎯  RESULT")
    print("=" * 55)
    deal = artifacts["best_deal"]
    if isinstance(deal, BestDeal):
        print(f"🏆  Best deal for {qty}x {deal.matched_item}:")
        print(f"    Store : {deal.store}")
        print(f"    Price : {currency} {deal.unit_price} each")
        print(f"    Total : {currency} {deal.total_price}")
        print(f"    Stock : {deal.stock}")
        print(f"    Link  : {deal.url}")
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
    else:
        print("❌  No deal selected.")

    _print_top_deals(offers, qty=qty, default_currency=currency, limit=5)

    return artifacts


def _print_top_deals(offers: list, qty: int = 1, default_currency: str = "LKR", limit: int = 5) -> None:
    """Print up to `limit` valid offers sorted by availability and price with clickable URLs."""
    priced = []
    for o in offers:
        price = o.price if hasattr(o, "price") else o.get("price", 0.0)
        if price <= 0:
            continue
        in_stock = o.in_stock if hasattr(o, "in_stock") else o.get("in_stock", True)
        stock_qty = o.stock_quantity if hasattr(o, "stock_quantity") else o.get("stock_quantity")
        priced.append({
            "store": o.store if hasattr(o, "store") else o.get("store", ""),
            "title": o.title if hasattr(o, "title") else o.get("title", ""),
            "unit_price": price,
            "total_price": round(price * qty, 2),
            "currency": (o.currency if hasattr(o, "currency") else o.get("currency")) or default_currency,
            "in_stock": in_stock,
            "stock_qty": stock_qty,
            "url": o.url if hasattr(o, "url") else o.get("url", ""),
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
        print(f"{rank}. [{d['store']}] {d['title']}")
        print(f"   Price : {d['currency']} {d['unit_price']} each | Total: {d['currency']} {d['total_price']} | {stock_label}")
        print(f"   Link  : {d['url']}")


if __name__ == "__main__":
    user_input = input("What are you looking for? ").strip()
    if not user_input:
        user_input = "I need 3 mg90 servo motors in Sri Lanka under Rs. 3500"
    run(user_input)
