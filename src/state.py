artifacts = {
    "intent":    {},   # UserIntent
    "stores":    [],   # List[DiscoveredStore]
    "offers":    [],   # List[ProductOffer]
    "best_deal": {},   # BestDeal
}


def reset_artifacts() -> None:
    """Reset the shared blackboard in-place between pipeline runs."""
    artifacts["intent"] = {}
    artifacts["stores"] = []
    artifacts["offers"] = []
    artifacts["best_deal"] = {}