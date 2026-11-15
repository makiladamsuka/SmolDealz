"""
SmolDealz — Stores Registry
===========================
Helper utilities to persist dynamically discovered stores.
Zero hardcoding — starts empty and dynamically populates as the agent discovers stores.
"""

from typing import List
from src.schemas import DiscoveredStore

# Starts completely empty — populated dynamically by the agent
DISCOVERED_STORES: List[DiscoveredStore] = []


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

