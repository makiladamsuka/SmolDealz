import os
import re
import json
from urllib.parse import quote_plus, urlparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from smolagents import ToolCallingAgent, LiteLLMModel, tool

load_dotenv()

# =====================================================================
# CORE EXTRACTION & VERIFICATION LOGIC (From trytosearchutem.py)
# =====================================================================

def _verify_and_filter_candidates(page, query: str) -> tuple[str, list[dict], str]:
    """
    Evaluates page signals and filters candidates:
    Returns (status, items, feedback)
    """
    # Signal 1: Direct single-product page redirect
    if "/product/" in page.url:
        has_buy_button = page.locator(
            "button:has-text('Add to Cart'), .single_add_to_cart_button, button[type='submit']"
        ).count() > 0
        if has_buy_button:
            title = page.title()
            return (
                "SUCCESS",
                [{"title": title, "url": page.url}],
                "Direct single-product page detected with active buy/cart button."
            )

    # Signal 2: Check for negative text on the page
    negative_patterns = [
        "no products found",
        "no results found",
        "0 results",
        "nothing found",
        "did not match any products",
        "sorry, but nothing matched"
    ]
    try:
        body_text = page.inner_text("body").lower()
        for phrase in negative_patterns:
            if phrase in body_text:
                return (
                    "ZERO_RESULTS",
                    [],
                    f"Page explicitly stated: '{phrase}'. Store has no exact match under this query."
                )
    except Exception:
        pass

    # Signal 3: Extract raw product candidate links
    raw_items = page.locator(
        "a[href*='/product/'], a[href*='/product'], .product a, .product-card a"
    ).evaluate_all(
        """elements => {
            const results = [];
            const seen = new Set();
            for (const el of elements) {
                const text = el.innerText.trim();
                const href = el.href;
                if (text.length > 3 && !seen.has(href)) {
                    seen.add(href);
                    results.push({ title: text, url: href });
                }
            }
            return results;
        }"""
    )

    if not raw_items:
        return "ZERO_RESULTS", [], "No product links found on the page."

    # Signal 4: Keyword relevance filter (filters out 'recommended / featured' products)
    query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 1]
    relevant_items = []
    for item in raw_items:
        title_lower = item["title"].lower()
        if any(word in title_lower for word in query_words):
            relevant_items.append(item)

    if relevant_items:
        return (
            "SUCCESS",
            relevant_items[:6],
            f"Found {len(relevant_items)} relevant products matching keywords: {query_words}"
        )
    else:
        return (
            "IRRELEVANT_CANDIDATES",
            [],
            f"Page returned {len(raw_items)} links, but NONE matched keywords {query_words} (likely default featured/recommended items)."
        )


# =====================================================================
# SMOLAGENTS TOOLS: 3 TIERS + DEEP INSPECTION
# =====================================================================

@tool
def search_tier1_direct_url(store_url: str, search_query: str) -> str:
    """Tier 1: Fast direct URL search. Navigates directly to the store search query URL.
    Args:
        store_url: Base store URL (e.g. 'https://tronic.lk').
        search_query: Search term (e.g. 'ESP32', 'VL53L0X', 'brushless motor').
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            if "tronic.lk" in store_url:
                target_url = f"https://tronic.lk/shop/products?query={quote_plus(search_query)}"
            else:
                target_url = f"{store_url.rstrip('/')}/?s={quote_plus(search_query)}&post_type=product"

            page.goto(target_url, timeout=25000, wait_until="domcontentloaded")
            status, items, feedback = _verify_and_filter_candidates(page, search_query)

            result = {
                "tier": "Tier 1: Direct URL",
                "target_url": target_url,
                "status": status,
                "matches_count": len(items),
                "items": items,
                "evaluation": feedback,
                "action_advice": (
                    "Matches found! You can inspect product details or proceed."
                    if status == "SUCCESS"
                    else "Tier 1 missed. Recommend calling 'search_tier2_onsite_search_bar' or trying an alternate query synonym."
                )
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "tier": "Tier 1: Direct URL",
                "status": "ERROR",
                "error": str(e),
                "action_advice": "Escalate to Tier 2."
            })
        finally:
            browser.close()


@tool
def search_tier2_onsite_search_bar(store_url: str, search_query: str) -> str:
    """Tier 2: On-site Search Bar interaction. Navigates to homepage, types query into search input, and presses Enter.
    Args:
        store_url: Base store URL (e.g. 'https://tronic.lk').
        search_query: Keywords to type into the search bar.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(store_url, timeout=25000, wait_until="domcontentloaded")
            search_selectors = [
                "input[name='query']",
                "input[name='s']",
                "input[type='search']",
                "input[placeholder*='search' i]",
                "input[name*='search']",
                "#search"
            ]
            input_element = None
            for sel in search_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    input_element = loc
                    break

            if not input_element:
                return json.dumps({
                    "tier": "Tier 2: Search Bar",
                    "status": "SEARCH_BAR_NOT_FOUND",
                    "evaluation": "No visible search input found on homepage.",
                    "action_advice": "Escalate to Tier 3: External Search Engine."
                })

            input_element.fill(search_query)
            input_element.press("Enter")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)  # Wait for JS hydration

            status, items, feedback = _verify_and_filter_candidates(page, search_query)
            result = {
                "tier": "Tier 2: Search Bar",
                "query_used": search_query,
                "status": status,
                "matches_count": len(items),
                "items": items,
                "evaluation": feedback,
                "action_advice": (
                    "Matches found! Inspect details if needed."
                    if status == "SUCCESS"
                    else "Tier 2 did not produce relevant items. Escalate to 'search_tier3_web_search'."
                )
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "tier": "Tier 2: Search Bar",
                "status": "ERROR",
                "error": str(e),
                "action_advice": "Escalate to Tier 3."
            })
        finally:
            browser.close()


@tool
def search_tier3_web_search(store_domain: str, search_query: str) -> str:
    """Tier 3: External Search Engine Fallback. Uses DuckDuckGo to discover indexed product pages on the store domain.
    Args:
        store_domain: Domain name of the store (e.g. 'tronic.lk').
        search_query: Search keywords (e.g. 'VL53L0X Time of Flight sensor').
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(f'{store_domain} {search_query}')}"
            page.goto(ddg_url, timeout=20000, wait_until="domcontentloaded")

            links = page.locator(".result__title a").evaluate_all(
                "elements => elements.map(e => ({ title: e.innerText.trim(), url: e.href }))"
            )
            # Filter to links belonging to target store
            matched = [item for item in links if store_domain in item["url"]]

            result = {
                "tier": "Tier 3: External Search Engine",
                "store_domain": store_domain,
                "matches_count": len(matched),
                "items": matched[:5],
                "evaluation": f"Found {len(matched)} external indexed links on {store_domain}.",
                "action_advice": (
                    "Candidate URLs found from external index. Use 'inspect_product_details' to verify specifications and stock."
                    if matched else "No indexed product links found on external engine."
                )
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "tier": "Tier 3: External Search",
                "status": "ERROR",
                "error": str(e),
                "action_advice": "No further search tiers available."
            })
        finally:
            browser.close()


@tool
def inspect_product_details(product_url: str) -> str:
    """Dig Deep: Navigates to a specific product page URL to extract full specifications, price, stock status, and description.
    Args:
        product_url: Exact URL of the product to inspect.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(product_url, timeout=25000, wait_until="domcontentloaded")
            title = page.title()

            # Price extraction heuristics
            price = "Unknown"
            price_loc = page.locator(".price, .product-price, [class*='price']").first
            if price_loc.count() > 0:
                price = price_loc.inner_text().strip()

            # Stock extraction heuristics
            stock = "Available / Check page"
            stock_loc = page.locator(".stock, .availability, [class*='stock'], :text-matches('In Stock|Out of Stock', 'i')").first
            if stock_loc.count() > 0:
                stock = stock_loc.inner_text().strip()

            # Description / Specs snippet
            desc_loc = page.locator(".description, #tab-description, .product-details, .specifications, [class*='desc']").first
            desc_text = desc_loc.inner_text().strip()[:400] if desc_loc.count() > 0 else "No dedicated specs section found."

            return json.dumps({
                "action": "inspect_product_details",
                "product_url": product_url,
                "page_title": title,
                "price": price,
                "stock": stock,
                "snippet": desc_text
            }, indent=2)
        except Exception as e:
            return json.dumps({
                "action": "inspect_product_details",
                "error": str(e)
            })
        finally:
            browser.close()


# =====================================================================
# AGENT SETUP WITH EVALUATION & DIGGING DEEPER RULES
# =====================================================================

def build_procurement_agent():
    api_key = os.getenv("OPENROUTER_API_KEY")
    model_id = os.getenv("OPENROUTER_MODEL", "openrouter/nvidia/nemotron-3.5-lightning:free")
    
    model = LiteLLMModel(
        model_id=model_id,
        api_key=api_key
    )

    system_instructions = """You are SmolDealz, an intelligent hardware procurement agent.
Your objective is to find, evaluate, and verify products across store tiers.

TIERED WORKFLOW & EVALUATION PROTOCOL:
1. Start with Tier 1: Call `search_tier1_direct_url(store_url, search_query)`.
2. Evaluate Observation:
   - Check the `status` and `items` in the tool output.
   - If status is 'ZERO_RESULTS' or 'IRRELEVANT_CANDIDATES' (e.g. query returned unrelated featured items):
     * DO NOT GIVE UP.
     * Evaluate WHY it failed (e.g., query too specific, or store needs UI input).
     * DIG DEEPER: Call `search_tier2_onsite_search_bar` with the query or an intelligent synonym (e.g. 'VL53L0X' -> 'Time of Flight sensor').
3. Escalate to Tier 3:
   - If Tier 2 also yields 0 or poor matches, escalate to `search_tier3_web_search(store_domain, search_query)`.
4. DIG DEEPER WITH PRODUCT VERIFICATION:
   - When candidate links are found, if there is any ambiguity (e.g., differentiating a motor from an ESC, verifying KV rating, checking stock or price), call `inspect_product_details(product_url)` on the most relevant candidates.
5. Final Verdict:
   - Present a clear markdown summary detailing:
     * Search strategy & evaluation trail (which tiers were invoked and why).
     * Verified matching products with title, price, stock, and link.
"""

    agent = ToolCallingAgent(
        tools=[
            search_tier1_direct_url,
            search_tier2_onsite_search_bar,
            search_tier3_web_search,
            inspect_product_details
        ],
        model=model,
        instructions=system_instructions,
        max_steps=8
    )
    return agent


if __name__ == "__main__":
    print("\n" + "="*60)
    print("🤖 SmolDealz Tiered ToolCallingAgent")
    print("="*60)
    user_query = input("Enter hardware query (e.g. 'Find ESP32 on tronic.lk'): ").strip()
    if not user_query:
        user_query = "Find ESP32 development board on tronic.lk. Verify price and in-stock status."

    agent = build_procurement_agent()
    print(f"\n🚀 Running Agent for: '{user_query}'\n")
    verdict = agent.run(user_query)
    print("\n" + "="*60)
    print("🎯 FINAL AGENT VERDICT:")
    print("="*60)
    print(verdict)

