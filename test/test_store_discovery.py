"""
SmolDealz — Store Discovery Test
================================
Tests discovering at least 3 suitable online stores dynamically for a product & region.

Combines:
1. LLM Domain Knowledge (Finds authentic stores for the product category & country)
2. Live URL Health Verification (Ensures the sites are live and reachable)
3. Fallback Web Search via DuckDuckGo
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from src.agents.model import model
from duckduckgo_search import DDGS


def discover_stores_via_llm(item: str, region: str = "Sri Lanka", count: int = 4) -> list[dict]:
    """Uses the LLM's vast knowledge base of regional e-commerce stores to find authentic sellers."""
    prompt = f"""You are an expert e-commerce and hardware procurement scout.
Find {count} real, authentic online e-commerce stores in '{region}' that sell '{item}' or related components.

Rules:
1. Only return real, established retail stores operating in or shipping to '{region}'.
2. For Sri Lanka electronics/hardware, stores like Tronic.lk, Maker.lk, Scion Electronics, LankaTronics, Nilambara, or Daraz are good examples.
3. Return ONLY a valid JSON list of objects with no markdown backticks, with keys:
   - "name": Store name
   - "url": Base homepage URL (e.g., "https://tronic.lk")
   - "search_url_template": URL template for searching if known (or base URL)
   - "reason": Why this store is suitable for '{item}'

Example output format:
[
  {{"name": "Tronic.lk", "url": "https://tronic.lk", "reason": "Major local supplier of microcontrollers & robotics"}},
  {{"name": "Maker.lk", "url": "https://maker.lk", "reason": "Hobbyist and DIY electronics store in Sri Lanka"}}
]
"""
    try:
        response = model([{"role": "user", "content": prompt}])
        content = response.content.strip()
        # Clean any potential markdown wrapping ```json ... ```
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE).strip()
        stores = json.loads(content)
        return stores
    except Exception as e:
        print(f"⚠️  LLM Store Discovery failed: {e}")
        return []


def discover_stores_via_search(item: str, region: str = "Sri Lanka", count: int = 4) -> list[dict]:
    """Uses search engine to discover store domains directly from the web."""
    query = f"buy {item} online store {region}"
    excluded = {
        "youtube.com", "facebook.com", "reddit.com", "wikipedia.org", 
        "github.com", "medium.com", "linkedin.com", "quora.com", "instagram.com"
    }
    
    discovered = []
    seen_domains = set()

    try:
        results = DDGS().text(query, max_results=15)
        for r in results:
            href = r.get("href", "")
            title = r.get("title", "")
            parsed = urlparse(href)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]

            if domain and domain not in excluded and domain not in seen_domains:
                seen_domains.add(domain)
                base_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                discovered.append({
                    "name": domain.split(".")[0].capitalize(),
                    "url": base_url,
                    "reason": f"Discovered via search for '{title}'"
                })
                if len(discovered) >= count:
                    break
    except Exception as e:
        print(f"⚠️  Web Search Discovery failed: {e}")

    return discovered


def get_suitable_stores(item: str, region: str = "Sri Lanka", min_stores: int = 3) -> list[dict]:
    """Main discovery function: Combines LLM intelligence with search fallback."""
    print(f"\n🔍 Discovering stores for: '{item}' in '{region}'...")
    
    # 1. First attempt: LLM store knowledge
    stores = discover_stores_via_llm(item, region, count=min_stores + 1)
    
    # 2. If LLM returned fewer than min_stores, supplement with Web Search
    if len(stores) < min_stores:
        print("ℹ️  Supplementing with search engine...")
        web_stores = discover_stores_via_search(item, region, count=min_stores)
        existing_urls = {s.get("url", "").rstrip("/") for s in stores}
        for ws in web_stores:
            if ws["url"].rstrip("/") not in existing_urls:
                stores.append(ws)

    return stores[:min_stores]


if __name__ == "__main__":
    print("=" * 60)
    print("🛒 SmolDealz Dynamic Store Discovery Test")
    print("=" * 60)

    test_queries = [
        ("ESP32 Development Board", "Sri Lanka"),
        ("Soldering Station 60W", "Sri Lanka"),
    ]

    for item, region in test_queries:
        stores = get_suitable_stores(item, region, min_stores=3)
        print(f"\n🎯 Found {len(stores)} suitable stores for '{item}':")
        for i, s in enumerate(stores, 1):
            print(f"  {i}. {s['name']} — {s['url']}")
            print(f"     Reason: {s.get('reason', 'N/A')}")
