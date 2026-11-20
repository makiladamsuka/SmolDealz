# ══════════════════════════════════════════════
#  AGENT 2 — ItemFinderAgent
#  Job: discover stores, run tiered search, save offers
# ══════════════════════════════════════════════

import atexit
from contextlib import contextmanager
import json
import re
import threading
from urllib.parse import quote_plus, urlparse
from dotenv import load_dotenv
import httpx
from playwright.sync_api import sync_playwright
from smolagents import tool, ToolCallingAgent
from duckduckgo_search import DDGS

load_dotenv()

from src.agents.model import model
from src.state import artifacts
from src.schemas import DiscoveredStore, ProductOffer
from src.stores import save_discovered_store, get_all_stores, get_seed_stores

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_thread_local = threading.local()


def close_browser() -> None:
    """Close the current thread's Playwright browser session if open."""
    browser = getattr(_thread_local, "browser", None)
    pw = getattr(_thread_local, "pw", None)
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
        _thread_local.browser = None
    if pw is not None:
        try:
            pw.stop()
        except Exception:
            pass
        _thread_local.pw = None


atexit.register(close_browser)


@contextmanager
def _browser_page():
    """Yield a fresh Playwright page from a thread-local Chromium browser instance."""
    if getattr(_thread_local, "pw", None) is None:
        _thread_local.pw = sync_playwright().start()
    browser = getattr(_thread_local, "browser", None)
    if browser is None or not browser.is_connected():
        browser = _thread_local.pw.chromium.launch(headless=True)
        _thread_local.browser = browser

    context = browser.new_context(user_agent=_USER_AGENT)
    page = context.new_page()
    try:
        yield page
    finally:
        try:
            context.close()
        except Exception:
            pass


# =====================================================================
# 1. STORE DISCOVERY LOGIC (LLM + Search Engine Fallback)
# =====================================================================

def _discover_stores_via_llm(item: str, region: str = "Sri Lanka", count: int = 4) -> list[dict]:
    """Uses LLM domain knowledge to find specialized retail stores for any product & region."""
    prompt = f"""You are an expert global e-commerce and hardware/retail procurement scout.
Identify {count} real, authentic online consumer e-commerce stores operating in '{region}' that specialize in selling '{item}' or its exact product category.

Rules:
1. Prioritize SPECIALIZED category retailers (e.g., for sensors, microcontrollers, servos, or DIY hardware: dedicated electronics/robotics component stores in '{region}', NOT generic supermarkets, grocery stores, gift portals, or classifieds).
2. Only return genuine online stores with an active product catalog/search in '{region}' (you may include at most 1 major general marketplace like Daraz if relevant, but lead with specialist stores).
3. Do NOT return supermarkets (like cargills, keells), telecom ISP portals (like slt.lk), gift hamper services (like kapruka), classifieds (like ikman), or government sites.
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
    query = f'"{item}" price {region}'
    excluded = {
        "youtube.com", "facebook.com", "reddit.com", "wikipedia.org",
        "github.com", "medium.com", "linkedin.com", "quora.com", "instagram.com",
        "pinterest.com", "dictionary.cambridge.org", "merriam-webster.com",
        "wiktionary.org", "britannica.com",
    }
    discovered = []
    seen_domains = set()

    try:
        keywords = [w.lower() for w in re.findall(r"\w+", item) if len(w) > 2]
        results = DDGS().text(query, max_results=12)
        for r in results:
            href = r.get("href", "")
            title = r.get("title", "")
            body = r.get("body", "")
            text_blob = f"{title} {body}".lower()
            if keywords and not any(k in text_blob for k in keywords):
                continue

            parsed = urlparse(href)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if any(domain.startswith(p) for p in ("forum.", "support.", "answers.", "docs.")):
                continue

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


def _is_store_reachable(url: str) -> bool:
    """Quickly verify a candidate store homepage resolves and responds over HTTP(S)."""
    try:
        with httpx.Client(
            timeout=5.0,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = client.head(url)
            if resp.status_code in (405, 403):
                resp = client.get(url)
            return resp.status_code < 500
    except Exception:
        return False


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


def _default_currency() -> str:
    """Return currency from artifacts['intent'] if set, otherwise 'LKR'."""
    intent = artifacts.get("intent")
    if hasattr(intent, "currency") and intent.currency:
        return intent.currency
    if isinstance(intent, dict) and intent.get("currency"):
        return intent["currency"]
    return "LKR"


def _infer_currency(price_raw: str) -> str:
    """Infer ISO currency code from a scraped price string, falling back to intent currency."""
    text = (price_raw or "").upper()
    if "LKR" in text or "RS" in text or "රු" in text:
        return "LKR"
    if "USD" in text or "$" in text:
        return "USD"
    if "EUR" in text or "€" in text:
        return "EUR"
    if "GBP" in text or "£" in text:
        return "GBP"
    if "INR" in text or "₹" in text:
        return "INR"
    return _default_currency()


# =====================================================================
# 3. SMOLAGENTS TOOLS: DISCOVERY + 3 TIERS + INSPECTION + SAVE
# =====================================================================

@tool
def discover_stores(item: str, region: str = "Sri Lanka", force_refresh: bool = False) -> str:
    """Discover specialized online stores that sell this item in the given region.
    Reuses existing blackboard stores if already populated unless force_refresh=True.

    Args:
        item: The product name (e.g. 'ESP32 development board', 'MG90S servo').
        region: The geographical shopping region (default: 'Sri Lanka').
        force_refresh: Set True only if switching region/category and existing stores must be replaced.
    """
    existing_stores = artifacts.get("stores") or []
    if existing_stores and not force_refresh:
        summary = [{"name": s.name, "url": s.url, "reason": s.reason} for s in existing_stores]
        return (
            f"Reusing {len(existing_stores)} already-discovered stores on blackboard (skipped discovery):\n"
            f"{json.dumps(summary, indent=2)}"
        )

    seed_stores = get_seed_stores(region)
    discovered_models: list[DiscoveredStore] = []
    seen_domains = set()

    for s in seed_stores:
        domain = urlparse(s.url).netloc.lower().removeprefix("www.")
        if domain and domain not in seen_domains:
            seen_domains.add(domain)
            discovered_models.append(s)
            save_discovered_store(s)

    if len(discovered_models) < 3:
        llm_stores = _discover_stores_via_llm(item, region, count=4)
        search_stores = _discover_stores_via_search(item, region, count=4)
        for s in llm_stores + search_stores:
            url = (s.get("url") or "").strip()
            if not url:
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                if not _is_store_reachable(url):
                    continue
                store_obj = DiscoveredStore(
                    name=s.get("name", "Store"),
                    url=url,
                    reason=s.get("reason"),
                    search_url_template=s.get("search_url_template"),
                )
                discovered_models.append(store_obj)
                save_discovered_store(store_obj)
            if len(discovered_models) >= 4:
                break

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
    with _browser_page() as page:
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
            err_msg = str(e)
            if any(
                sig in err_msg
                for sig in ("ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT", "Timeout")
            ):
                return json.dumps({
                    "tier": "Tier 1: Direct URL",
                    "status": "UNREACHABLE",
                    "error": f"{type(e).__name__}: store is unreachable",
                    "action_advice": "Store URL is unreachable. STOP — do NOT call Tier 2 or Tier 3 for this store; move to the next store.",
                })
            return json.dumps({
                "tier": "Tier 1: Direct URL",
                "status": "ERROR",
                "error": f"{type(e).__name__}: store search failed",
            })


@tool
def search_tier2_onsite_search_bar(store_url: str, search_query: str) -> str:
    """Tier 2: On-site Search Bar interaction. Navigates to homepage, types query into search input, and presses Enter.

    Args:
        store_url: Base store URL (e.g. 'https://tronic.lk', 'https://maker.lk').
        search_query: Keywords to type into the search bar.
    """
    with _browser_page() as page:
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


@tool
def search_tier3_web_search(store_domain: str, search_query: str) -> str:
    """Tier 3: External Search Engine Fallback. Discovers indexed product pages on the store domain via DuckDuckGo.

    Args:
        store_domain: Domain name of the store (e.g. 'tronic.lk', 'maker.lk').
        search_query: Search keywords (e.g. 'ESP32 development board').
    """
    with _browser_page() as page:
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


def _extract_structured_price(page) -> tuple[float, str | None]:
    """Extract (price, currency) from JSON-LD Product/Offer or meta price tags."""
    try:
        scripts = page.locator("script[type='application/ld+json']").all_inner_texts()
        for raw in scripts:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if "@graph" in node and isinstance(node["@graph"], list):
                    nodes.extend(node["@graph"])
                    continue
                offers = node.get("offers")
                if not offers:
                    continue
                offer_list = offers if isinstance(offers, list) else [offers]
                for off in offer_list:
                    if not isinstance(off, dict):
                        continue
                    raw_val = off.get("price") or off.get("lowPrice")
                    if raw_val is not None:
                        val = _clean_numeric_price(str(raw_val))
                        if val > 0:
                            curr = off.get("priceCurrency")
                            return val, (str(curr).upper() if curr else None)
    except Exception:
        pass

    for meta_sel in [
        "meta[property='product:price:amount']",
        "meta[property='og:price:amount']",
        "meta[itemprop='price']",
    ]:
        try:
            loc = page.locator(meta_sel).first
            if loc.count() > 0:
                val = _clean_numeric_price(loc.get_attribute("content") or "")
                if val > 0:
                    curr_loc = page.locator(
                        "meta[property='product:price:currency'], meta[property='og:price:currency'], meta[itemprop='priceCurrency']"
                    ).first
                    curr = curr_loc.get_attribute("content") if curr_loc.count() > 0 else None
                    return val, (str(curr).upper() if curr else None)
        except Exception:
            pass

    return 0.0, None


@tool
def inspect_product_details(product_url: str, store_name: str = "") -> str:
    """Dig Deep: Navigates to a specific product page URL to extract full specifications, price, stock status, and description.

    Args:
        product_url: Exact URL of the product to inspect.
        store_name: Name of the store (e.g. 'Tronic.lk', 'Daraz.lk').
    """
    with _browser_page() as page:
        try:
            page.goto(product_url, timeout=25000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    ".pdp-price, .pdp-v2-product-price, .woocommerce-Price-amount, .price .amount, .product__price, [class*='price']",
                    timeout=5000,
                )
            except Exception:
                pass
            title = page.title()

            # 1. Try CSS Selectors (Daraz, WooCommerce product summary, Shopify, etc.)
            price_raw = ""
            numeric_price = 0.0
            for sel in [
                ".pdp-price", ".pdp-price_type_normal", ".pdp-v2-product-price", "[class*='pdp-price']",
                ".summary .price .amount", ".entry-summary .price .amount", "p.price .woocommerce-Price-amount",
                ".woocommerce-Price-amount", ".price .amount", "p.price",
                ".price-item--regular", ".product__price", ".price", ".product-price", "[class*='price']"
            ]:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    txt = loc.inner_text().strip()
                    if txt and any(c.isdigit() for c in txt):
                        candidate_price = _clean_numeric_price(txt)
                        if candidate_price > 0:
                            price_raw = txt
                            numeric_price = candidate_price
                            break

            structured_currency = None

            # 2. Structured JSON-LD / meta price fallback (reliable on SPAs & multi-variant pages)
            if numeric_price == 0.0:
                numeric_price, structured_currency = _extract_structured_price(page)

            # 3. Visible-text regex fallback (avoids matching $1/USD inside raw <script>/JSON)
            if numeric_price == 0.0:
                try:
                    visible_text = page.inner_text("body")
                except Exception:
                    visible_text = ""
                matches = re.findall(
                    r"(?:Rs\.?|LKR|රු\.?)\s*[\d,]+(?:\.\d+)?|(?:\$|USD|EUR|€|GBP|£)\s*[\d,]+(?:\.\d+)?",
                    visible_text,
                    re.IGNORECASE,
                )
                if matches:
                    price_raw = matches[0]
                    numeric_price = _clean_numeric_price(price_raw)

            currency = structured_currency or _infer_currency(price_raw)

            # Stock extraction heuristics (status + numeric quantity if shown)
            stock_raw = "In Stock"
            stock_loc = page.locator(
                ".stock, .availability, [class*='stock'], :text-matches('In Stock|Out of Stock|available|left', 'i')"
            ).first
            if stock_loc.count() > 0:
                stock_raw = stock_loc.inner_text().strip()

            is_in_stock = "out of stock" not in stock_raw.lower() and "sold out" not in stock_raw.lower()
            stock_quantity = None
            if is_in_stock:
                qty_match = re.search(r"(\d+)\s*(?:in stock|available|left|units?|pcs?)", stock_raw, re.IGNORECASE)
                if not qty_match:
                    qty_match = re.search(r"^\s*(\d+)\s*$", stock_raw)
                if qty_match:
                    stock_quantity = int(qty_match.group(1))

            # Snippet / short description extraction
            snippet = None
            for desc_sel in [
                ".woocommerce-product-details__short-description",
                ".product-short-description",
                "[class*='short-description']",
                ".pdp-product-desc",
                ".product-description",
            ]:
                dloc = page.locator(desc_sel).first
                if dloc.count() > 0:
                    dtxt = re.sub(r"\s+", " ", dloc.inner_text()).strip()
                    if len(dtxt) > 10:
                        snippet = dtxt[:250]
                        break
            if not snippet:
                meta_desc = page.locator("meta[name='description']").first
                if meta_desc.count() > 0:
                    content_attr = (meta_desc.get_attribute("content") or "").strip()
                    if content_attr:
                        snippet = re.sub(r"\s+", " ", content_attr)[:250]

            result = {
                "store": store_name or urlparse(product_url).netloc,
                "title": title,
                "price": numeric_price,
                "currency": currency,
                "in_stock": is_in_stock,
                "stock_quantity": stock_quantity,
                "snippet": snippet,
                "url": product_url,
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({
                "store": store_name,
                "error": f"Failed to inspect page: {type(e).__name__}"
            })


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
    matched_item: str | None = None
) -> str:
    """Save a verified product offer found from a store to the shared blackboard.

    Args:
        store: Store name or domain (e.g. 'tronic.lk', 'maker.lk').
        title: Exact product title from the store.
        price: Unit price as a number/float in local currency.
        url: Direct product link.
        currency: ISO currency code (e.g. 'LKR', 'USD'). Defaults to intent currency.
        in_stock: True if available for purchase, False if out of stock.
        stock_quantity: Exact stock quantity count if known, otherwise None.
        snippet: Key specifications or description snippet.
        matched_item: The search term this offer satisfies (e.g. 'ESP32 development board').
    """
    try:
        # Guard against LLM converting null stock_quantity into 0 when in_stock=True
        normalized_stock_qty = None if (in_stock and stock_quantity == 0) else stock_quantity
        offer = ProductOffer(
            store=store,
            title=title,
            price=price,
            currency=currency or _default_currency(),
            in_stock=in_stock,
            stock_quantity=normalized_stock_qty,
            url=url,
            snippet=snippet,
            matched_item=matched_item,
        )
        norm_key = (offer.store.strip().lower(), offer.url.strip().rstrip("/"))
        for i, existing in enumerate(artifacts["offers"]):
            existing_key = (existing.store.strip().lower(), existing.url.strip().rstrip("/"))
            if existing_key == norm_key:
                artifacts["offers"][i] = offer
                return f"SUCCESS: Updated existing offer on blackboard artifacts['offers']: {offer.model_dump()}"

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
