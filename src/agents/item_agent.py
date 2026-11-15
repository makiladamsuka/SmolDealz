# ══════════════════════════════════════════════
#  AGENT 2 — ItemFinderAgent
#  Job: discover stores, run tiered search, save offers
# ══════════════════════════════════════════════

import json
import re
from urllib.parse import quote_plus, urlparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from smolagents import tool, ToolCallingAgent
from duckduckgo_search import DDGS

load_dotenv()

from src.agents.model import model
from src.state import artifacts
from src.schemas import DiscoveredStore, ProductOffer
from src.stores import save_discovered_store, get_all_stores


# =====================================================================
# 1. STORE DISCOVERY LOGIC (LLM + Search Engine Fallback)
# =====================================================================

def _discover_stores_via_llm(item: str, region: str = "Sri Lanka", count: int = 3) -> list[dict]:
    """Uses LLM domain knowledge to find authentic retail stores for any product & region."""
    prompt = f"""You are an expert global e-commerce and retail procurement scout.
Identify {count} real, authentic online consumer e-commerce stores operating in '{region}' that sell '{item}' or products in that category.

Rules:
1. Only return genuine retail e-commerce stores that sell to consumers and have an active product search/catalog in '{region}'.
2. The stores must be relevant to the specific product category of '{item}' (e.g., tech gadgets, electronics, hardware, general retail marketplaces).
3. Do NOT return corporate telecom ISP portals (like slt.lk), gift hamper services, or government sites.
4. Return ONLY a valid JSON list of objects with no markdown formatting:
   - "name": Store brand name
   - "url": Base homepage URL (e.g. "https://store-domain.com")
   - "search_url_template": Direct search query URL template with '{{query}}' if known (e.g. "https://store-domain.com/?s={{query}}")
   - "reason": Why this store is suitable for '{item}' in '{region}'
"""
    try:
        response = model([{"role": "user", "content": prompt}])
        content = response.content.strip()
        # Clean markdown code blocks
        clean = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()

        # Robust JSON extraction that ignores any conversational text before or after JSON
        pos_bracket = clean.find("[")
        pos_brace = clean.find("{")

        if pos_bracket != -1 and (pos_brace == -1 or pos_bracket < pos_brace):
            data, _ = json.JSONDecoder().raw_decode(clean[pos_bracket:])
            if isinstance(data, list):
                return data
        elif pos_brace != -1:
            data, _ = json.JSONDecoder().raw_decode(clean[pos_brace:])
            if isinstance(data, dict):
                for k in ["stores", "results", "data"]:
                    if k in data and isinstance(data[k], list):
                        return data[k]
                return [data]
            elif isinstance(data, list):
                return data

        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  LLM Store Discovery failed: {e}")
        return []


def _discover_stores_via_search(item: str, region: str = "Sri Lanka", count: int = 3) -> list[dict]:
    """Uses DuckDuckGo search to discover store domains directly from the web."""
    query = f"buy {item} online store {region}"
    excluded = {
        "youtube.com", "facebook.com", "reddit.com", "wikipedia.org",
        "github.com", "medium.com", "linkedin.com", "quora.com", "instagram.com"
    }
    discovered = []
    seen_domains = set()

    try:
        results = DDGS().text(query, max_results=12)
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
                    "reason": f"Discovered via search for '{title}'",
                    "search_url_template": f"{base_url}/?s={{query}}&post_type=product"
                })
                if len(discovered) >= count:
                    break
    except Exception as e:
        print(f"⚠️  Search Discovery failed: {e}")

    return discovered


# =====================================================================
# 2. PAGE INSPECTION & RELEVANCE FILTERING
# =====================================================================

def _verify_and_filter_candidates(page, query: str) -> tuple[str, list[dict], str]:
    """Evaluates page signals and filters candidates: returns (status, items, feedback)."""
    # Signal 1: Direct single-product page redirect
    if "/product/" in page.url or "/shop/product/" in page.url:
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
        "a[href*='/product/'], a[href*='/product'], .product a, .product-card a, .woocommerce-loop-product__link"
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
            f"Page returned {len(raw_items)} links, but none matched keywords {query_words}."
        )


def _clean_numeric_price(price_raw: str) -> float:
    """Extracts a clean float price from messy strings like 'Rs. 1,250.00'."""
    clean = price_raw.replace(" ", "").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", clean)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 0.0


# =====================================================================
# 3. SMOLAGENTS TOOLS: DISCOVERY + 3 TIERS + INSPECTION + SAVE
# =====================================================================

@tool
def discover_stores(item: str, region: str = "Sri Lanka") -> str:
    """Discover at least 3 authentic online stores that sell this item in the given region.
    Saves the discovered stores into the pipeline blackboard and registry.

    Args:
        item: The product name (e.g. 'ESP32 development board', 'MG90S servo').
        region: The geographical shopping region (default: 'Sri Lanka').
    """
    stores_data = _discover_stores_via_llm(item, region, count=3)
    if len(stores_data) < 3:
        search_stores = _discover_stores_via_search(item, region, count=3)
        existing = {s["url"].rstrip("/").lower() for s in stores_data}
        for s in search_stores:
            if s["url"].rstrip("/").lower() not in existing:
                stores_data.append(s)

    discovered_models = []
    for s in stores_data:
        store_obj = DiscoveredStore(
            name=s.get("name", "Store"),
            url=s.get("url", ""),
            reason=s.get("reason"),
            search_url_template=s.get("search_url_template"),
        )
        discovered_models.append(store_obj)
        save_discovered_store(store_obj)

    artifacts["stores"] = discovered_models

    summary = [
        {"name": s.name, "url": s.url, "reason": s.reason}
        for s in discovered_models
    ]
    return f"Successfully discovered and saved {len(discovered_models)} stores to blackboard:\n{json.dumps(summary, indent=2)}"


@tool
def search_tier1_direct_url(store_url: str, search_query: str) -> str:
    """Tier 1: Fast direct URL search. Navigates directly to the store search query URL.

    Args:
        store_url: Base store URL (e.g. 'https://tronic.lk', 'https://maker.lk').
        search_query: Search keywords (e.g. 'ESP32', 'MG90S').
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            # Check if a custom search_url_template was discovered for this store
            target_url = None
            clean_store = store_url.rstrip("/").lower()
            for s in artifacts.get("stores", []):
                if s.url.rstrip("/").lower() in clean_store:
                    if s.search_url_template and "{query}" in s.search_url_template:
                        target_url = s.search_url_template.replace("{query}", quote_plus(search_query))
                        break

            # Fallback to standard universal search URL pattern
            if not target_url:
                target_url = f"{store_url.rstrip('/')}/?s={quote_plus(search_query)}&post_type=product"

            page.goto(target_url, timeout=25000, wait_until="domcontentloaded")
            status, items, feedback = _verify_and_filter_candidates(page, search_query)

            result = {
                "tier": "Tier 1: Direct URL",
                "status": status,
                "matches_count": len(items),
                "items": items[:4],
                "action_advice": (
                    "SUCCESS: Products found! STOP searching this store — do NOT run Tier 2 or Tier 3. Inspect candidates or move to next store."
                    if status == "SUCCESS"
                    else "Tier 1 returned 0 results. Escalate to Tier 2: Search Bar."
                ),
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "tier": "Tier 1: Direct URL",
                "status": "ERROR",
                "error": f"{type(e).__name__}: store search failed",
            })
        finally:
            browser.close()


@tool
def search_tier2_onsite_search_bar(store_url: str, search_query: str) -> str:
    """Tier 2: On-site Search Bar interaction. Navigates to homepage, types query into search input, and presses Enter.

    Args:
        store_url: Base store URL (e.g. 'https://tronic.lk', 'https://maker.lk').
        search_query: Keywords to type into the search bar.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            page.goto(store_url, timeout=25000, wait_until="domcontentloaded")
            search_selectors = [
                "input[name='query']",
                "input[name='s']",
                "input[type='search']",
                "input[placeholder*='search' i]",
                "input[name*='search']",
                "#search",
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
                    "action_advice": "Escalate to Tier 3: External Web Search.",
                })

            input_element.fill(search_query)
            input_element.press("Enter")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)

            status, items, feedback = _verify_and_filter_candidates(page, search_query)
            result = {
                "tier": "Tier 2: Search Bar",
                "status": status,
                "matches_count": len(items),
                "items": items[:4],
                "action_advice": (
                    "SUCCESS: Products found! STOP searching this store — do NOT run Tier 3. Inspect candidates or move to next store."
                    if status == "SUCCESS"
                    else "Tier 2 returned 0 results. Escalate to Tier 3: External Search."
                ),
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "tier": "Tier 2: Search Bar",
                "status": "ERROR",
                "error": f"{type(e).__name__}: search bar input failed",
            })
        finally:
            browser.close()


@tool
def search_tier3_web_search(store_domain: str, search_query: str) -> str:
    """Tier 3: External Search Engine Fallback. Discovers indexed product pages on the store domain via DuckDuckGo.

    Args:
        store_domain: Domain name of the store (e.g. 'tronic.lk', 'maker.lk').
        search_query: Search keywords (e.g. 'ESP32 development board').
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(f'{store_domain} {search_query}')}"
            page.goto(ddg_url, timeout=20000, wait_until="domcontentloaded")

            links = page.locator(".result__title a").evaluate_all(
                "elements => elements.map(e => ({ title: e.innerText.trim(), url: e.href }))"
            )
            matched = [item for item in links if store_domain in item["url"]]

            result = {
                "tier": "Tier 3: External Search Engine",
                "store_domain": store_domain,
                "matches_count": len(matched),
                "items": matched[:4],
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "tier": "Tier 3: External Search",
                "status": "ERROR",
                "error": f"{type(e).__name__}: search failed",
            })
        finally:
            browser.close()


@tool
def inspect_product_details(product_url: str, store_name: str = "") -> str:
    """Dig Deep: Navigates to a specific product page URL to extract full specifications, price, stock status, and description.

    Args:
        product_url: Exact URL of the product to inspect.
        store_name: Name of the store (e.g. 'Tronic.lk', 'Daraz.lk').
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            page.goto(product_url, timeout=25000, wait_until="domcontentloaded")
            title = page.title()

            # 1. Try CSS Selectors (Daraz, WooCommerce, Shopify, etc.)
            price_raw = ""
            for sel in [
                ".pdp-price", ".pdp-price_type_normal", ".pdp-v2-product-price", "[class*='pdp-price']",
                ".woocommerce-Price-amount", ".price .amount", "p.price",
                ".price-item--regular", ".product__price", ".price", ".product-price", "[class*='price']"
            ]:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    txt = loc.inner_text().strip()
                    if txt and any(c.isdigit() for c in txt):
                        price_raw = txt
                        break

            # 2. Universal HTML regex fallback (covers SPAs like Daraz where price is in content/state)
            numeric_price = _clean_numeric_price(price_raw)
            if numeric_price == 0.0:
                html = page.content()
                matches = re.findall(r"(?:Rs\.?|LKR)\s*[\d,]+(?:\.\d+)?", html, re.IGNORECASE)
                if matches:
                    price_raw = matches[0]
                    numeric_price = _clean_numeric_price(price_raw)

            # Stock extraction heuristics
            stock_raw = "In Stock"
            stock_loc = page.locator(
                ".stock, .availability, [class*='stock'], :text-matches('In Stock|Out of Stock', 'i')"
            ).first
            if stock_loc.count() > 0:
                stock_raw = stock_loc.inner_text().strip()

            is_in_stock = "out of stock" not in stock_raw.lower()

            result = {
                "store": store_name or urlparse(product_url).netloc,
                "title": title,
                "price": numeric_price,
                "currency": "LKR",
                "in_stock": is_in_stock,
                "url": product_url
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "store": store_name,
                "error": f"Failed to inspect page: {type(e).__name__}"
            })
        finally:
            browser.close()


@tool
def save_product_offer(
    store: str,
    title: str,
    price: float,
    url: str,
    in_stock: bool = True,
    stock_quantity: int | None = None,
    snippet: str | None = None,
    matched_item: str | None = None
) -> str:
    """Save a verified product offer found from a store to the shared blackboard.

    Args:
        store: Store name or domain (e.g. 'tronic.lk', 'maker.lk').
        title: Exact product title from the store.
        price: Unit price as a number/float in local currency.
        url: Direct product link.
        in_stock: True if available for purchase, False if out of stock.
        stock_quantity: Exact stock quantity count if known, otherwise None.
        snippet: Key specifications or description snippet.
        matched_item: The search term this offer satisfies (e.g. 'ESP32 development board').
    """
    try:
        offer = ProductOffer(
            store=store,
            title=title,
            price=price,
            in_stock=in_stock,
            stock_quantity=stock_quantity,
            url=url,
            snippet=snippet,
            matched_item=matched_item
        )
        artifacts["offers"].append(offer)
        return f"SUCCESS: Saved offer to blackboard artifacts['offers']: {offer.model_dump()}"
    except Exception as e:
        return f"ERROR saving product offer: {e}"


# =====================================================================
# 4. AGENT INITIALIZATION
# =====================================================================

item_agent = ToolCallingAgent(
    tools=[
        discover_stores,
        search_tier1_direct_url,
        search_tier2_onsite_search_bar,
        search_tier3_web_search,
        inspect_product_details,
        save_product_offer
    ],
    model=model,
    name="ItemFinderAgent",
    description="Discovers regional stores and uses tiered search to find, inspect, and save authentic product offers.",
    instructions="""You are an expert hardware and product procurement agent.
Your objective is to find real product listings and prices across regional stores.

WORKFLOW & EARLY-EXIT RULES:
1. STORE DISCOVERY:
   - Call `discover_stores(item, region)` to get 3 authentic candidate stores.
2. TIERED SEARCH & EARLY-EXIT:
   - For each discovered store:
     * Try `search_tier1_direct_url(store_url, search_query)`.
     * EARLY EXIT: If Tier 1 finds products (status == 'SUCCESS'), DO NOT call Tier 2 or Tier 3 for that store!
     * Only call Tier 2 if Tier 1 returned ZERO_RESULTS or ERROR.
     * EARLY EXIT: If Tier 2 finds products, DO NOT call Tier 3 for that store!
     * Only call Tier 3 if BOTH Tier 1 and Tier 2 failed.
3. INSPECTION:
   - Inspect only the top 2-3 most relevant product links per store with `inspect_product_details(product_url, store_name)`.
4. RECORD OFFERS:
   - For each verified product with price > 0, call `save_product_offer(...)` to record it to the blackboard.
5. FINISH PROTOCOL (CRITICAL):
   - Once at least 2-3 stores have been searched and offers recorded, call `final_answer` EXACTLY ONCE with a summary.
   - NEVER make multiple parallel calls to 'final_answer'.
   - Do NOT call any other tool when calling 'final_answer'.
""",
    max_steps=12,
)


if __name__ == "__main__":
    test_item = input("enter intent")
    test_region = "Sri Lanka"
    print(f"\n🚀 Running ItemFinderAgent for '{test_item}' in '{test_region}'...\n")

    item_agent.run(
        f"Find product listings and prices for '{test_item}' in '{test_region}'. "
        "First discover stores, then search each store using the tiered protocol, "
        "inspect product details, and save the verified offers."
    )

    print("\n" + "="*50)
    print(" BlackBoard artifacts['stores']:")
    print("="*50)
    for s in artifacts.get("stores", []):
        print(f"- {s.name}: {s.url}")

    print("\n" + "="*50)
    print(" BlackBoard artifacts['offers']:")
    print("="*50)
    for o in artifacts.get("offers", []):
        print(f"- [{o.store}] {o.title} — Rs.{o.price} | Stock: {o.in_stock} | {o.url}")
