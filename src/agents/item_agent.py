# ══════════════════════════════════════════════
#  AGENT 2 — ItemFinderAgent
#  Job: search stores and write offers to board
# ══════════════════════════════════════════════
from agents.model import model
from state import artifacts
from smolagents import tool, ToolCallingAgent

@tool
def search_store(store_name: str, item: str) -> str:
    """Search a store for an item and return a fake product listing.

    Args:
        store_name: Name of the store to search (e.g. 'tronics', 'maker.lk').
        item:       Product to search for (e.g. 'mg90 servo').
    """
    # --- SAMPLE DATA (replace with real Playwright scraper) ---
    sample_db = {
        "tronics": {
            "mg90 servo": {"price": 1000, "stock": 8,  "url": "https://tronic.lk/mg90"},
            "sg90 servo": {"price":  750, "stock": 15, "url": "https://tronic.lk/sg90"},
        },
        "maker.lk": {
            "mg90 servo": {"price":  950, "stock": 2,  "url": "https://maker.lk/mg90"},
            "sg90 servo": {"price":  700, "stock": 20, "url": "https://maker.lk/sg90"},
        },
        "nilambara": {
            "mg90 servo": {"price":  980, "stock": 5,  "url": "https://nilambara.lk/mg90"},
            "sg90 servo": {"price":  720, "stock": 10, "url": "https://nilambara.lk/sg90"},
        },
    }
    # -----------------------------------------------------------

    result = sample_db.get(store_name, {}).get(item.lower())
    if not result:
        return f"No listing found for '{item}' at {store_name}."

    offer = {
        "store": store_name,
        "item":  item,
        "price": result["price"],
        "stock": result["stock"],
        "url":   result["url"],
    }
    artifacts["offers"].append(offer)
    return f"Found: {offer}"


@tool
def get_stores_to_search() -> str:
    """Return the list of stores to check, based on the current intent region.

    No args needed — reads region from the shared blackboard.
    """
    region = artifacts["intent"].get("region", "Sri Lanka")
    # In production: load from data/stores.json filtered by region
    stores = ["tronics", "maker.lk", "nilambara"]
    return f"Stores for '{region}': {stores}"


item_agent = ToolCallingAgent(
    tools=[get_stores_to_search, search_store],
    model=model,
    name="ItemFinderAgent",
    description="Searches all regional stores for the item in artifacts['intent'] and writes results to artifacts['offers'].",
    max_steps=6,
)
