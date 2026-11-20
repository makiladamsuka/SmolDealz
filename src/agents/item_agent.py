# ══════════════════════════════════════════════
#  AGENT 2 — ItemFinderAgent
#  Job: discover stores, run parallel async search & LLM extraction, save offers + screenshots
# ══════════════════════════════════════════════

import json
from dotenv import load_dotenv
from smolagents import ToolCallingAgent, tool

load_dotenv()

from src.agents.model import model
from src.scraper import (
    clean_numeric_price,
    default_currency,
    ensure_discovered_stores,
    inspect_single_url_async,
    run_async,
    scrape_all_stores_async,
    upsert_offer_to_blackboard,
)
from src.state import artifacts


def close_browser() -> None:
    """No-op kept for backward compatibility (async browser lifecycle is scoped per scrape run)."""
    pass


# =====================================================================
# SMOLAGENTS TOOLS: PARALLEL SCRAPER + SINGLE-URL DEBUG HELPERS
# =====================================================================

@tool
def search_all_stores_parallel(
    item: str,
    region: str = "Sri Lanka",
    force_refresh: bool = False,
) -> str:
    """Discover regional stores (or reuse existing blackboard stores), search all stores concurrently
    in parallel headless browsers, extract verified product prices/stock, capture annotated proof
    screenshots, and save all valid offers (price > 0) to artifacts['offers'].

    Args:
        item: The product search query (e.g. 'ultrasonic sensor', 'ESP32 development board').
        region: Target shopping region (default: 'Sri Lanka').
        force_refresh: Set True only if switching region/category and existing stores must be replaced.
    """
    report = run_async(
        scrape_all_stores_async(
            item=item,
            region=region,
            force_refresh_stores=force_refresh,
        )
    )
    return json.dumps(report, indent=2)


@tool
def discover_stores(item: str, region: str = "Sri Lanka", force_refresh: bool = False) -> str:
    """Discover specialized online stores that sell this item in the given region.
    Reuses existing blackboard stores if already populated unless force_refresh=True.

    Args:
        item: The product name (e.g. 'ESP32 development board', 'MG90S servo').
        region: The geographical shopping region (default: 'Sri Lanka').
        force_refresh: Set True only if switching region/category and existing stores must be replaced.
    """
    stores = ensure_discovered_stores(item=item, region=region, force_refresh=force_refresh)
    summary = [{"name": s.name, "url": s.url, "reason": s.reason} for s in stores]
    return f"Discovered/active stores on blackboard ({len(stores)}):\n{json.dumps(summary, indent=2)}"


@tool
def inspect_product_details(product_url: str, store_name: str = "") -> str:
    """Navigate to a specific product page URL to extract specifications, verified price, stock status,
    price_source, and capture an annotated proof screenshot.

    Args:
        product_url: Exact URL of the product to inspect.
        store_name: Name of the store (e.g. 'Tronic.lk', 'Daraz.lk').
    """
    details = run_async(inspect_single_url_async(product_url=product_url, store_name=store_name))
    return json.dumps(details, indent=2)


@tool
def save_product_offer(
    store: str,
    title: str,
    price: float,
    url: str,
    currency: str | None = None,
    in_stock: bool = True,
    stock_quantity: int | None = None,
    snippet: str | None = None,
    matched_item: str | None = None,
    screenshot_path: str | None = None,
    price_source: str | None = None,
) -> str:
    """Save a verified product offer (with price > 0) to the shared blackboard.

    Args:
        store: Store name or domain (e.g. 'tronic.lk', 'duino.lk').
        title: Exact product title from the store.
        price: Unit price as a positive number/float in local currency (must be > 0).
        url: Direct product link.
        currency: ISO currency code (e.g. 'LKR', 'USD'). Defaults to intent currency.
        in_stock: True if available for purchase, False if out of stock.
        stock_quantity: Exact stock quantity count if known, otherwise None.
        snippet: Key specifications or description snippet.
        matched_item: The search term this offer satisfies.
        screenshot_path: Optional path to the annotated proof screenshot PNG.
        price_source: Optional extraction method label (e.g. 'json-ld', 'llm-text').
    """
    numeric_price = clean_numeric_price(price)
    if numeric_price <= 0:
        return f"REJECTED: Cannot save offer '{title}' from '{store}' with invalid price={price} (must be > 0)."

    saved = upsert_offer_to_blackboard({
        "store": store,
        "title": title,
        "price": numeric_price,
        "currency": currency or default_currency(),
        "in_stock": in_stock,
        "stock_quantity": stock_quantity,
        "url": url,
        "snippet": snippet,
        "matched_item": matched_item,
        "screenshot_path": screenshot_path,
        "price_source": price_source,
    })
    if saved is None:
        return "ERROR: Offer was rejected because price <= 0."
    return f"SUCCESS: Saved offer to blackboard artifacts['offers']: {saved.model_dump()}"


# =====================================================================
# AGENT INITIALIZATION
# =====================================================================

item_agent = ToolCallingAgent(
    tools=[
        search_all_stores_parallel,
        discover_stores,
        inspect_product_details,
        save_product_offer,
    ],
    model=model,
    name="ItemFinderAgent",
    description="Discovers regional stores and runs parallel async browser workers to extract verified product offers and proof screenshots.",
    instructions="""You are an expert hardware and product procurement agent.
Your objective is to find real product listings, verified prices, and visual proof screenshots across regional stores.

WORKFLOW (FAST 1-STEP PARALLEL EXECUTION):
1. Call `search_all_stores_parallel(item=..., region=..., force_refresh=...)` ONCE.
   - This single tool automatically discovers/reuses regional stores, searches all stores concurrently in parallel headless browsers, extracts verified prices using JSON-LD and LLM page analysis, captures annotated proof screenshots, and saves all valid offers (price > 0) directly to `artifacts['offers']`.
2. Immediately call `final_answer` ONCE summarizing the stores searched, offers found (including price, stock status, price_source, and screenshot_path).
   - Do NOT re-call `save_product_offer` for offers already saved by `search_all_stores_parallel`.
   - NEVER save any offer with `price <= 0`.
""",
    max_steps=4,
)
