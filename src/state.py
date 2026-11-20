artifacts = {
    "intent":    {},   # UserIntent
    "stores":    [],   # List[DiscoveredStore]
    "offers":    [],   # List[ProductOffer]
    "best_deal": {},   # BestDeal
}


def clear_offers() -> None:
    """Clear scraped offers and best_deal while preserving intent and discovered stores."""
    artifacts["offers"] = []
    artifacts["best_deal"] = {}


def reset_artifacts() -> None:
    """Reset the shared blackboard in-place between pipeline runs."""
    artifacts["intent"] = {}
    artifacts["stores"] = []
    artifacts["offers"] = []
    artifacts["best_deal"] = {}