"""
SmolDealz — Stores Registry
===========================
Seeded regional stores (verified live) + helper utilities to persist dynamically discovered stores.
"""

from typing import List
from src.schemas import DiscoveredStore

# Verified live regional seed stores with known search URL templates
SEED_STORES_BY_REGION: dict[str, List[DiscoveredStore]] = {
    "sri lanka": [
        DiscoveredStore(
            name="Tronic.lk",
            url="https://tronic.lk",
            reason="Major Sri Lankan electronics & robotics component supplier",
            search_url_template="https://tronic.lk/shop/products?query={query}",
        ),
        DiscoveredStore(
            name="Duino.lk",
            url="https://www.duino.lk",
            reason="Dedicated Sri Lankan Arduino, sensor, and DIY electronics store",
            search_url_template="https://www.duino.lk/?s={query}&post_type=product",
        ),
        DiscoveredStore(
            name="Daraz.lk",
            url="https://www.daraz.lk",
            reason="Major Sri Lankan online marketplace with electronics & module sellers",
            search_url_template="https://www.daraz.lk/catalog/?q={query}",
        ),
        DiscoveredStore(
            name="Scion Electronics",
            url="https://scionelectronics.com",
            reason="Sri Lankan electronics & microcontroller supplier",
            search_url_template="https://scionelectronics.com/?s={query}&post_type=product",
        ),
    ],
}

# Populated dynamically per run
DISCOVERED_STORES: List[DiscoveredStore] = []


def get_seed_stores(region: str) -> List[DiscoveredStore]:
    """Return curated, verified seed stores for a region if available."""
    key = (region or "").strip().lower()
    return [s.model_copy() for s in SEED_STORES_BY_REGION.get(key, [])]


def save_discovered_store(store: DiscoveredStore) -> None:
    """Adds a store to the registry if not already present."""
    clean_url = store.url.rstrip("/").lower()
    for existing in DISCOVERED_STORES:
        if existing.url.rstrip("/").lower() == clean_url:
            return
    DISCOVERED_STORES.append(store)


def get_all_stores() -> List[DiscoveredStore]:
    """Returns the list of all dynamically discovered stores."""
    return list(DISCOVERED_STORES)
