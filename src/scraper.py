"""
SmolDealz — Async Parallel Store Scraper, Hybrid LLM Price Extractor & Visual Proof Engine
==========================================================================================
Replaces the sequential LLM tool loop and brittle global CSS wildcards with:
1. True parallel async store workers (playwright.async_api + playwright-stealth).
2. 2-Tier Hybrid Extraction:
   - Tier 1 (Fast Path, 0 tokens): Structured JSON-LD Product/Offer & OpenGraph/meta price tags.
   - Tier 2 (LLM Cleaned-Page Path, 1 call): Strips nav/header/footer/sidebar/related products,
     formats tables cleanly, and extracts {title, price, currency, in_stock, stock_quantity, snippet}
     via a single structured LLM call (plus optional Vision LLM fallback on the screenshot).
3. Annotated Visual Proof Screenshots saved to data/screenshots/ with red bounding boxes around
   the extracted price & stock elements.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote_plus, urlparse

from duckduckgo_search import DDGS
import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

from src.agents.model import model
from src.schemas import DiscoveredStore, ProductOffer
from src.state import artifacts
from src.stores import get_seed_stores, save_discovered_store

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SCREENSHOT_DIR = Path("data/screenshots")
_STEALTH = Stealth()

# Concurrency controls
MAX_CONCURRENT_STORES = 4
MAX_PRODUCTS_PER_STORE = 2

# Non-product URL path fragments to exclude from candidate product links
_NON_PRODUCT_URL_PATTERNS = (
    "/product-category/",
    "/category/",
    "/categories/",
    "/product-tag/",
    "/tag/",
    "/cart",
    "/checkout",
    "/my-account",
    "/account",
    "/wishlist",
    "/login",
    "/register",
    "/contact",
    "/about",
    "/privacy",
    "/terms",
    "/blog",
    "?add-to-cart=",
    "/page/",
)


# =====================================================================
# 1. NUMERIC & CURRENCY HELPERS
# =====================================================================

def clean_numeric_price(price_raw: str | int | float | None) -> float:
    """Extract a clean float price from strings like 'Rs. 1,250.00' or 280."""
    if price_raw is None:
        return 0.0
    if isinstance(price_raw, (int, float)):
        return float(price_raw) if price_raw > 0 else 0.0
    clean = str(price_raw).replace(" ", "").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", clean)
    if match:
        try:
            val = float(match.group(1))
            return val if val > 0 else 0.0
        except ValueError:
            pass
    return 0.0


def default_currency() -> str:
    """Return currency from artifacts['intent'] if set, otherwise 'LKR'."""
    intent = artifacts.get("intent")
    if hasattr(intent, "currency") and intent.currency:
        return intent.currency
    if isinstance(intent, dict) and intent.get("currency"):
        return intent["currency"]
    return "LKR"


def infer_currency(price_raw: str | None) -> str:
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
    return default_currency()


def _slugify(text: str, max_len: int = 36) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").lower()).strip("_")
    return (slug[:max_len] or "item").rstrip("_")


def _is_valid_product_url(url: str, base_domain: str = "") -> bool:
    """Return True if URL looks like a real product page rather than a category/cart/nav link."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    lower = url.lower()
    if any(pat in lower for pat in _NON_PRODUCT_URL_PATTERNS):
        return False
    parsed = urlparse(lower)
    if base_domain and base_domain not in parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    if not path or path == "":
        return False
    return True


# =====================================================================
# 2. STORE DISCOVERY (Seed Registry + LLM / DDG Fallback)
# =====================================================================

def _discover_stores_via_llm(item: str, region: str = "Sri Lanka", count: int = 4) -> list[dict]:
    prompt = f"""You are an expert global e-commerce and hardware/retail procurement scout.
Identify {count} real, authentic online consumer e-commerce stores operating in '{region}' that specialize in selling '{item}' or its exact product category.

Rules:
1. Prioritize SPECIALIZED category retailers (e.g., for sensors, microcontrollers, servos, or DIY hardware: dedicated electronics/robotics component stores in '{region}').
2. Only return genuine online stores with an active product catalog/search in '{region}'.
3. Return ONLY a valid JSON list of objects with keys: "name", "url", "search_url_template", "reason".
"""
    try:
        response = model([{"role": "user", "content": prompt}])
        content = response.content.strip()
        clean = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()
        pos_bracket = clean.find("[")
        if pos_bracket != -1:
            data, _ = json.JSONDecoder().raw_decode(clean[pos_bracket:])
            if isinstance(data, list):
                return data
        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  LLM Store Discovery failed: {e}")
        return []


def _discover_stores_via_search(item: str, region: str = "Sri Lanka", count: int = 3) -> list[dict]:
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
            domain = parsed.netloc.lower().removeprefix("www.")
            if any(domain.startswith(p) for p in ("forum.", "support.", "answers.", "docs.")):
                continue
            if domain and domain not in excluded and domain not in seen_domains:
                seen_domains.add(domain)
                base_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                discovered.append({
                    "name": domain.split(".")[0].capitalize(),
                    "url": base_url,
                    "reason": f"Discovered via search for '{title}'",
                    "search_url_template": f"{base_url}/?s={{query}}&post_type=product",
                })
                if len(discovered) >= count:
                    break
    except Exception as e:
        print(f"⚠️  Search Discovery failed: {e}")
    return discovered


def _is_store_reachable(url: str) -> bool:
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


def ensure_discovered_stores(item: str, region: str = "Sri Lanka", force_refresh: bool = False) -> list[DiscoveredStore]:
    """Ensure artifacts['stores'] is populated with verified regional stores."""
    existing_stores = artifacts.get("stores") or []
    if existing_stores and not force_refresh:
        return existing_stores

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
    return discovered_models


# =====================================================================
# 3. CANDIDATE FILTERING & TIERED SEARCH (ASYNC)
# =====================================================================

async def _verify_and_filter_candidates_async(page: Page, query: str, store_domain: str = "") -> tuple[str, list[dict], str]:
    """Evaluate page signals and return (status, filtered_product_items, feedback)."""
    current_url = page.url
    if ("/product/" in current_url or "/shop/product/" in current_url) and _is_valid_product_url(current_url):
        has_buy = await page.locator(
            "button:has-text('Add to Cart'), .single_add_to_cart_button, button[type='submit'], table"
        ).count() > 0
        if has_buy:
            title = await page.title()
            return (
                "SUCCESS",
                [{"title": title, "url": current_url}],
                "Direct single-product page detected.",
            )

    negative_patterns = (
        "no products found",
        "no results found",
        "0 results",
        "nothing found",
        "did not match any products",
        "sorry, but nothing matched",
        "no products were found",
    )
    try:
        body_text = (await page.inner_text("body")).lower()
        for phrase in negative_patterns:
            if phrase in body_text:
                return ("ZERO_RESULTS", [], f"Page explicitly stated: '{phrase}'.")
    except Exception:
        pass

    raw_items = await page.locator(
        "a[href*='/product/'], a[href*='/products/'], .product a, .product-card a, "
        ".woocommerce-loop-product__link, a[href*='/shop/']"
    ).evaluate_all(
        """elements => {
            const results = [];
            const seen = new Set();
            for (const el of elements) {
                const text = (el.innerText || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ');
                const href = el.href;
                if (text.length > 3 && href && !seen.has(href)) {
                    seen.add(href);
                    results.push({ title: text, url: href });
                }
            }
            return results;
        }"""
    )

    # Filter out category, tag, cart, and non-product pages
    valid_items = [
        item for item in raw_items
        if _is_valid_product_url(item["url"], store_domain)
    ]

    if not valid_items:
        return ("ZERO_RESULTS", [], "No valid product links found on the page.")

    query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 1]
    scored_items: list[tuple[int, dict]] = []
    for item in valid_items:
        title_lower = item["title"].lower()
        url_lower = item["url"].lower()
        matched_words = sum(1 for w in query_words if w in title_lower or w in url_lower)
        if matched_words > 0:
            penalty = 0
            # Penalize automotive bumpers/reverse parking kits when searching general electronics sensors
            if any(k in title_lower for k in ("lexus", "bumper", "parktronic", "car reverse")) and "car" not in query.lower():
                penalty += 3
            # Penalize brackets/holders/cases/cables unless explicitly requested
            if any(k in title_lower for k in ("holder", "bracket", "mount", "case", "cover", "cable")) and not any(
                k in query.lower() for k in ("holder", "bracket", "mount", "case", "cover", "cable")
            ):
                penalty += 2
            # Slight bonus for standard module/sensor keywords
            bonus = 1 if ("module" in title_lower or "hc-sr04" in title_lower) else 0
            scored_items.append((matched_words * 10 + bonus - penalty, item))

    if scored_items:
        scored_items.sort(key=lambda x: x[0], reverse=True)
        ranked = [item for _, item in scored_items[:6]]
        return (
            "SUCCESS",
            ranked,
            f"Found {len(scored_items)} relevant products matching {query_words}.",
        )

    return (
        "IRRELEVANT_CANDIDATES",
        [],
        f"Page returned {len(valid_items)} product links, but none matched keywords {query_words}.",
    )


async def _search_tier1_async(context: BrowserContext, store: DiscoveredStore, search_query: str) -> list[dict]:
    """Tier 1: Fast direct search URL."""
    page = await context.new_page()
    await _STEALTH.apply_stealth_async(page)
    domain = urlparse(store.url).netloc.lower().removeprefix("www.")
    try:
        if store.search_url_template and "{query}" in store.search_url_template:
            target_url = store.search_url_template.replace("{query}", quote_plus(search_query))
        else:
            target_url = f"{store.url.rstrip('/')}/?s={quote_plus(search_query)}&post_type=product"

        await page.goto(target_url, timeout=22000, wait_until="domcontentloaded")
        # Wait briefly for SPA product cards (like Daraz catalog) to render
        try:
            await page.wait_for_selector(
                "a[href*='/product/'], a[href*='/products/'], .product, .product-card",
                timeout=5000,
            )
        except Exception:
            pass

        status, items, _ = await _verify_and_filter_candidates_async(page, search_query, domain)
        return items if status == "SUCCESS" else []
    except Exception:
        return []
    finally:
        await page.close()


async def _search_tier2_async(context: BrowserContext, store: DiscoveredStore, search_query: str) -> list[dict]:
    """Tier 2: On-site Search Bar interaction."""
    page = await context.new_page()
    await _STEALTH.apply_stealth_async(page)
    domain = urlparse(store.url).netloc.lower().removeprefix("www.")
    try:
        await page.goto(store.url, timeout=22000, wait_until="domcontentloaded")
        search_selectors = (
            "input[name='query']",
            "input[name='s']",
            "input[name='q']",
            "input[type='search']",
            "input[placeholder*='search' i]",
            "input[name*='search' i]",
            "#search",
        )
        input_element = None
        for sel in search_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                input_element = loc
                break

        if not input_element:
            return []

        await input_element.fill(search_query)
        await input_element.press("Enter")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        status, items, _ = await _verify_and_filter_candidates_async(page, search_query, domain)
        return items if status == "SUCCESS" else []
    except Exception:
        return []
    finally:
        await page.close()


async def _search_tier3_ddg_async(store: DiscoveredStore, search_query: str) -> list[dict]:
    """Tier 3: External DuckDuckGo search scoped to the store domain."""
    domain = urlparse(store.url).netloc.lower().removeprefix("www.")
    query_words = [w.lower() for w in re.findall(r"\w+", search_query) if len(w) > 1]

    def _run_ddg() -> list[dict]:
        matched = []
        seen = set()
        try:
            results = DDGS().text(f"site:{domain} {search_query}", max_results=8)
            for r in results:
                href = r.get("href", "")
                title = (r.get("title") or "").strip()
                if not _is_valid_product_url(href, domain):
                    continue
                if href in seen:
                    continue
                blob = f"{title} {href}".lower()
                if query_words and not any(w in blob for w in query_words):
                    continue
                seen.add(href)
                matched.append({"title": title or href, "url": href})
        except Exception:
            pass
        return matched[:4]

    return await asyncio.to_thread(_run_ddg)


async def search_store_tiered_async(
    context: BrowserContext,
    store: DiscoveredStore,
    search_query: str,
) -> tuple[str, list[dict]]:
    """Run Tier 1 -> Tier 2 -> Tier 3 with immediate early-exit for a single store."""
    items = await _search_tier1_async(context, store, search_query)
    if items:
        return "Tier 1 (Direct URL)", items

    items = await _search_tier2_async(context, store, search_query)
    if items:
        return "Tier 2 (Search Bar)", items

    items = await _search_tier3_ddg_async(store, search_query)
    if items:
        return "Tier 3 (Web Search)", items

    return "NONE", []


# =====================================================================
# 4. 2-TIER HYBRID EXTRACTION (JSON-LD -> LLM CLEANED-PAGE / VISION)
# =====================================================================

async def _extract_structured_price_async(page: Page) -> tuple[float, str | None, bool | None, str | None]:
    """Tier 1 Fast Path: Extract (price, currency, in_stock, raw_price_str) from JSON-LD or meta tags."""
    try:
        scripts = await page.locator("script[type='application/ld+json']").all_inner_texts()
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
                        val = clean_numeric_price(raw_val)
                        if val > 0:
                            curr = off.get("priceCurrency")
                            avail = str(off.get("availability") or "").lower()
                            in_stock = None
                            if "outofstock" in avail or "soldout" in avail:
                                in_stock = False
                            elif "instock" in avail:
                                in_stock = True
                            return val, (str(curr).upper() if curr else None), in_stock, str(raw_val)
    except Exception:
        pass

    for meta_sel in (
        "meta[property='product:price:amount']",
        "meta[property='og:price:amount']",
        "meta[itemprop='price']",
    ):
        try:
            loc = page.locator(meta_sel).first
            if await loc.count() > 0:
                content_val = await loc.get_attribute("content") or ""
                val = clean_numeric_price(content_val)
                if val > 0:
                    curr_loc = page.locator(
                        "meta[property='product:price:currency'], "
                        "meta[property='og:price:currency'], "
                        "meta[itemprop='priceCurrency']"
                    ).first
                    curr = await curr_loc.get_attribute("content") if await curr_loc.count() > 0 else None
                    return val, (str(curr).upper() if curr else None), None, content_val
        except Exception:
            pass

    return 0.0, None, None, None


async def _extract_clean_product_context_async(page: Page) -> dict:
    """Strip header/footer/nav/sidebar/related-product clutter in JS and extract compact product text."""
    return await page.evaluate(
        """() => {
            const title = document.title || '';
            const h1El = document.querySelector('h1, .product_title, .pdp-mod-product-badge-title, [class*="product-title"]');
            const heading = h1El ? h1El.innerText.trim() : title;

            // Extract any explicit key-value table rows (like Tronic.lk Price / In Stock table)
            const tablePairs = [];
            document.querySelectorAll('table tr').forEach(tr => {
                if (tr.closest('.related, .upsells, footer, header, nav, aside')) return;
                const cells = Array.from(tr.querySelectorAll('th, td')).map(c => c.innerText.trim().replace(/\\s+/g, ' '));
                if (cells.length >= 2 && cells[0].length < 40 && cells[1].length < 160) {
                    tablePairs.push(`${cells[0]}: ${cells[1]}`);
                }
            });

            // Clone body and remove non-product clutter
            const clone = document.body.cloneNode(true);
            const clutterSelectors = [
                'script', 'style', 'noscript', 'svg', 'iframe',
                'header', 'footer', 'nav', 'aside',
                '.related', '.upsells', '.cross-sells',
                '[class*="related"]', '[class*="recommend"]', '[class*="upsell"]',
                '[class*="sidebar"]', '[class*="footer"]', '[class*="header"]',
                '[class*="navbar"]', '[class*="menu"]', '[class*="cookie"]',
                '[class*="newsletter"]', '[class*="review"]'
            ];
            clutterSelectors.forEach(sel => {
                try {
                    clone.querySelectorAll(sel).forEach(el => el.remove());
                } catch (e) {}
            });

            // Prefer main product container if available
            const mainRoot = clone.querySelector(
                '.product, .entry-summary, .summary, #product, main, [class*="pdp-block"], [class*="product-detail"]'
            ) || clone;

            const cleanText = (mainRoot.innerText || clone.innerText || '')
                .replace(/\\s+/g, ' ')
                .trim()
                .slice(0, 2400);

            return {
                title: title,
                heading: heading,
                tableText: tablePairs.slice(0, 20).join(' | '),
                mainText: cleanText
            };
        }"""
    )


def _extract_via_llm_text(url: str, page_ctx: dict, fallback_currency: str) -> dict:
    """Tier 2: Single structured LLM call on cleaned product page text."""
    prompt = f"""Extract the exact main product details from this e-commerce product page text.
Ignore any related products, shipping banners, or accessories.

URL: {url}
Page Title: {page_ctx.get('title', '')}
Main Heading: {page_ctx.get('heading', '')}
Product Table Rows: {page_ctx.get('tableText', 'None')}
Main Product Text:
{page_ctx.get('mainText', '')}

Return ONLY a valid JSON object (no markdown fences) with these exact keys:
{{
  "title": "Exact product name",
  "price": <unit price as a float number, or 0.0 if not found>,
  "price_text": "<exact price string as shown on the page, e.g. 'Rs.280.00' or 'Rs. 595'>",
  "currency": "{fallback_currency}",
  "in_stock": <true or false>,
  "stock_quantity": <integer stock count if explicitly stated, otherwise null>,
  "snippet": "<1-2 sentence product summary or specs>"
}}"""
    try:
        response = model([{"role": "user", "content": prompt}])
        content = (response.content or "").strip()
        clean = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()
        pos_brace = clean.find("{")
        if pos_brace != -1:
            data, _ = json.JSONDecoder().raw_decode(clean[pos_brace:])
            if isinstance(data, dict):
                return data
        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  LLM text extraction error on {url}: {e}")
        return {}


def _extract_via_llm_vision(url: str, screenshot_bytes: bytes, fallback_currency: str) -> dict:
    """Optional Tier 2b: Extract product price & stock directly from the page screenshot using a Vision LLM."""
    b64_img = base64.b64encode(screenshot_bytes).decode("utf-8")
    prompt = (
        f"Read the main product title, unit price, currency (default {fallback_currency}), and stock status "
        f"from this product page screenshot ({url}). Ignore related products. "
        'Return ONLY JSON: {"title": str, "price": float, "price_text": str, "currency": str, "in_stock": bool, "stock_quantity": int|null, "snippet": str}'
    )
    try:
        response = model([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                ],
            }
        ])
        content = (response.content or "").strip()
        pos_brace = content.find("{")
        if pos_brace != -1:
            data, _ = json.JSONDecoder().raw_decode(content[pos_brace:])
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"⚠️  Vision fallback error on {url}: {e}")
    return {}


async def _extract_scoped_dom_fallback_async(page: Page, page_ctx: dict) -> tuple[float, str, bool]:
    """Deterministic scoped fallback (table rows & main PDP price) if LLM is rate-limited."""
    table_text = page_ctx.get("tableText") or ""
    price_val = 0.0
    price_raw = ""
    in_stock = True

    # 1. Check explicit table pairs ("Price: Rs.280.00", "In Stock: Yes/No")
    price_row_match = re.search(r"(?:^|\|\s*)Price\s*:\s*([^|]+)", table_text, re.IGNORECASE)
    if price_row_match:
        price_raw = price_row_match.group(1).strip()
        price_val = clean_numeric_price(price_raw)

    stock_row_match = re.search(r"(?:^|\|\s*)(?:In\s*Stock|Availability|Stock)\s*:\s*([^|]+)", table_text, re.IGNORECASE)
    if stock_row_match:
        st_val = stock_row_match.group(1).strip().lower()
        if any(w in st_val for w in ("no", "out of stock", "sold out", "0")):
            in_stock = False
        elif any(w in st_val for w in ("yes", "in stock", "available")):
            in_stock = True

    # 2. Scoped main-product selectors (strictly excluding .related / .upsells / sidebar)
    if price_val <= 0:
        for sel in (
            ".pdp-mod-product-price .pdp-price_type_normal",
            ".pdp-product-price .pdp-price",
            ".pdp-price",
            ".summary p.price ins .woocommerce-Price-amount",
            ".summary p.price .woocommerce-Price-amount",
            ".entry-summary p.price .woocommerce-Price-amount",
            ".product-info-price .price",
            ".product__price",
        ):
            loc = page.locator(sel).first
            if await loc.count() > 0:
                txt = (await loc.inner_text()).strip()
                cand = clean_numeric_price(txt)
                if cand > 0:
                    price_val = cand
                    price_raw = txt
                    break

    # 3. Clean mainText regex fallback (only on clutter-stripped product text)
    if price_val <= 0:
        main_text = page_ctx.get("mainText") or ""
        m = re.search(
            r"(?:Rs\.?|LKR|රු\.?)\s*[\d,]+(?:\.\d+)?|(?:\$|USD|EUR|€|GBP|£)\s*[\d,]+(?:\.\d+)?",
            main_text,
            re.IGNORECASE,
        )
        if m:
            price_raw = m.group(0)
            price_val = clean_numeric_price(price_raw)

    main_lower = (page_ctx.get("mainText") or "").lower()
    if "out of stock" in main_lower or "sold out" in main_lower:
        if not stock_row_match:
            in_stock = False

    return price_val, price_raw, in_stock


# =====================================================================
# 5. VISUAL PROOF SCREENSHOTS (WITH RED BOUNDING BOXES)
# =====================================================================

async def _capture_annotated_screenshot_async(
    page: Page,
    store_name: str,
    product_title: str,
    numeric_price: float,
    currency: str,
    in_stock: bool,
    price_text_hint: str = "",
) -> str | None:
    """Highlight the extracted price & stock elements in red and save a timestamped PNG screenshot."""
    try:
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        formatted_int = f"{int(numeric_price):,}" if numeric_price >= 1 else f"{numeric_price:.2f}"
        raw_int = str(int(numeric_price)) if numeric_price >= 1 else f"{numeric_price:.2f}"

        await page.evaluate(
            """({ priceHint, formattedInt, rawInt, badgeText, inStock }) => {
                // Remove obstructive cookie popups before screenshot
                document.querySelectorAll('[class*="cookie"], [id*="cookie"]').forEach(el => {
                    if (el.innerText && el.innerText.toLowerCase().includes('cookie')) {
                        el.style.display = 'none';
                    }
                });

                const styleHighlight = (el) => {
                    if (!el) return;
                    el.style.setProperty('outline', '3px solid #ef4444', 'important');
                    el.style.setProperty('outline-offset', '3px', 'important');
                    el.style.setProperty('background-color', 'rgba(239, 68, 68, 0.14)', 'important');
                    el.style.setProperty('border-radius', '4px', 'important');
                    el.style.setProperty('box-shadow', '0 0 12px rgba(239, 68, 68, 0.45)', 'important');
                };

                // 1. Find the DOM node displaying the main price
                let targetEl = null;
                const candidates = Array.from(document.querySelectorAll(
                    '.pdp-price, .summary .price, .entry-summary .price, table tr, .product__price, span, td, p, div'
                ));

                for (const el of candidates) {
                    if (el.closest('.related, .upsells, footer, header, nav, aside')) continue;
                    const txt = (el.innerText || '').trim();
                    if (!txt || txt.length > 90) continue;
                    if (
                        (priceHint && txt.includes(priceHint)) ||
                        (formattedInt && txt.includes(formattedInt) && /(rs|lkr|\\$|usd|price)/i.test(txt)) ||
                        (rawInt && txt.includes(rawInt) && /(rs|lkr|\\$|usd)/i.test(txt))
                    ) {
                        targetEl = el;
                        break;
                    }
                }

                if (targetEl) {
                    styleHighlight(targetEl);
                    try {
                        targetEl.scrollIntoView({ behavior: 'instant', block: 'center' });
                    } catch (e) {}
                }

                // 2. Also highlight stock row/badge if visible
                for (const el of candidates) {
                    if (el.closest('.related, .upsells, footer, header, nav, aside')) continue;
                    const txt = (el.innerText || '').trim();
                    if (txt.length > 0 && txt.length < 50 && /^(in stock|out of stock|yes|no|\\d+\\s+in stock)$/i.test(txt)) {
                        styleHighlight(el.closest('tr') || el);
                        break;
                    }
                }

                // 3. Inject a verification stamp banner at top-right
                const stamp = document.createElement('div');
                stamp.textContent = badgeText;
                stamp.style.cssText = [
                    'position: fixed',
                    'top: 12px',
                    'right: 12px',
                    'z-index: 2147483647',
                    'background: ' + (inStock ? '#16a34a' : '#dc2626'),
                    'color: #ffffff',
                    'font-family: system-ui, -apple-system, sans-serif',
                    'font-size: 13px',
                    'font-weight: 700',
                    'padding: 8px 14px',
                    'border-radius: 6px',
                    'box-shadow: 0 4px 12px rgba(0,0,0,0.28)',
                    'border: 2px solid #ffffff'
                ].join(';');
                document.body.appendChild(stamp);
            }""",
            {
                "priceHint": (price_text_hint or "").strip(),
                "formattedInt": formatted_int,
                "rawInt": raw_int,
                "badgeText": (
                    f"SmolDealz Verified • {store_name} • {currency} {numeric_price:,.2f} • "
                    f"{'IN STOCK' if in_stock else 'OUT OF STOCK'}"
                ),
                "inStock": in_stock,
            },
        )
        await page.wait_for_timeout(250)

        ts = int(time.time() * 1000) % 1000000
        filename = f"{_slugify(store_name, 18)}_{_slugify(product_title, 28)}_{ts}.png"
        filepath = _SCREENSHOT_DIR / filename
        await page.screenshot(path=str(filepath), full_page=False)
        return filepath.as_posix()
    except Exception as e:
        print(f"⚠️  Failed to capture screenshot for {product_title}: {e}")
        return None


# =====================================================================
# 6. SINGLE PRODUCT INSPECTION & PARALLEL STORE WORKER POOL
# =====================================================================

async def inspect_product_page_async(
    context: BrowserContext,
    product_url: str,
    store_name: str = "",
    matched_item: str | None = None,
    capture_screenshot: bool = True,
) -> dict:
    """Navigate to a product page, run 2-Tier Hybrid Extraction, and capture an annotated proof screenshot."""
    page = await context.new_page()
    await _STEALTH.apply_stealth_async(page)
    fallback_curr = default_currency()
    domain = store_name or urlparse(product_url).netloc.lower().removeprefix("www.")

    try:
        await page.goto(product_url, timeout=25000, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(
                ".pdp-price, .woocommerce-Price-amount, table, .product, .entry-summary, main",
                timeout=7000,
            )
        except Exception:
            pass
        # Allow SPA dynamic price hydration (Daraz / React storefronts)
        if "daraz." in product_url.lower():
            try:
                await page.wait_for_selector(".pdp-price, [class*='pdp-price']", timeout=7000)
            except Exception:
                pass
        await page.wait_for_timeout(600)

        page_ctx = await _extract_clean_product_context_async(page)
        title = page_ctx.get("heading") or page_ctx.get("title") or await page.title()

        # ── Tier 1: Structured JSON-LD / Meta Tags (0 LLM tokens) ──
        numeric_price, struct_curr, struct_stock, price_raw = await _extract_structured_price_async(page)
        price_source = "json-ld" if numeric_price > 0 else None

        in_stock = True if struct_stock is None else struct_stock
        stock_quantity: int | None = None
        snippet: str | None = None
        currency = struct_curr or fallback_curr

        # ── Tier 2: Cleaned Page Text -> 1 Structured LLM Call ─────
        if numeric_price <= 0:
            llm_data = await asyncio.to_thread(_extract_via_llm_text, product_url, page_ctx, fallback_curr)
            llm_price = clean_numeric_price(llm_data.get("price") or llm_data.get("price_text"))
            if llm_price > 0:
                numeric_price = llm_price
                price_raw = str(llm_data.get("price_text") or llm_price)
                price_source = "llm-text"
                if llm_data.get("title"):
                    title = str(llm_data["title"]).strip()
                if llm_data.get("currency"):
                    currency = infer_currency(str(llm_data["currency"]))
                if isinstance(llm_data.get("in_stock"), bool):
                    in_stock = llm_data["in_stock"]
                if isinstance(llm_data.get("stock_quantity"), int) and llm_data["stock_quantity"] > 0:
                    stock_quantity = llm_data["stock_quantity"]
                if llm_data.get("snippet"):
                    snippet = str(llm_data["snippet"]).strip()[:250]

        # ── Tier 2b (Optional): Vision LLM Fallback on Screenshot ──
        if numeric_price <= 0 and os.getenv("ENABLE_VISION_FALLBACK", "").lower() in ("1", "true", "yes"):
            raw_png = await page.screenshot(full_page=False)
            vis_data = await asyncio.to_thread(_extract_via_llm_vision, product_url, raw_png, fallback_curr)
            vis_price = clean_numeric_price(vis_data.get("price") or vis_data.get("price_text"))
            if vis_price > 0:
                numeric_price = vis_price
                price_raw = str(vis_data.get("price_text") or vis_price)
                price_source = "llm-vision"
                if vis_data.get("title"):
                    title = str(vis_data["title"]).strip()
                if isinstance(vis_data.get("in_stock"), bool):
                    in_stock = vis_data["in_stock"]
                if vis_data.get("snippet"):
                    snippet = str(vis_data["snippet"]).strip()[:250]

        # ── Deterministic Scoped Fallback (if LLM rate-limited) ────
        if numeric_price <= 0:
            dom_price, dom_raw, dom_stock = await _extract_scoped_dom_fallback_async(page, page_ctx)
            if dom_price > 0:
                numeric_price = dom_price
                price_raw = dom_raw
                in_stock = dom_stock
                price_source = "scoped-dom"
                currency = infer_currency(dom_raw)

        # Refine stock status from explicit table or main product text when JSON-LD didn't specify
        if struct_stock is None and price_source in ("json-ld", "scoped-dom"):
            _, _, dom_stock = await _extract_scoped_dom_fallback_async(page, page_ctx)
            in_stock = dom_stock

        # Extract snippet if not populated by LLM
        if not snippet:
            meta_desc = page.locator("meta[name='description']").first
            if await meta_desc.count() > 0:
                c_attr = (await meta_desc.get_attribute("content") or "").strip()
                if c_attr:
                    snippet = re.sub(r"\s+", " ", c_attr)[:250]
            if not snippet and page_ctx.get("mainText"):
                snippet = page_ctx["mainText"][:220]

        screenshot_path = None
        if capture_screenshot and numeric_price > 0:
            screenshot_path = await _capture_annotated_screenshot_async(
                page=page,
                store_name=domain,
                product_title=title,
                numeric_price=numeric_price,
                currency=currency,
                in_stock=in_stock,
                price_text_hint=str(price_raw or ""),
            )

        return {
            "store": domain,
            "title": title,
            "price": numeric_price,
            "currency": currency,
            "in_stock": in_stock,
            "stock_quantity": stock_quantity,
            "snippet": snippet,
            "url": product_url,
            "matched_item": matched_item,
            "screenshot_path": screenshot_path,
            "price_source": price_source or "unmatched",
        }
    except Exception as e:
        return {
            "store": domain,
            "url": product_url,
            "price": 0.0,
            "error": f"Failed to inspect page: {type(e).__name__}: {e}",
        }
    finally:
        await page.close()


def upsert_offer_to_blackboard(offer_data: dict) -> ProductOffer | None:
    """Validate and save a scraped offer with price > 0 to artifacts['offers']."""
    price = clean_numeric_price(offer_data.get("price"))
    if price <= 0:
        return None

    in_stock = bool(offer_data.get("in_stock", True))
    stock_qty = offer_data.get("stock_quantity")
    normalized_stock_qty = None if (in_stock and stock_qty == 0) else stock_qty

    offer = ProductOffer(
        store=offer_data.get("store") or urlparse(offer_data.get("url", "")).netloc,
        title=offer_data.get("title") or "Product",
        price=price,
        currency=offer_data.get("currency") or default_currency(),
        in_stock=in_stock,
        stock_quantity=normalized_stock_qty,
        url=offer_data["url"],
        snippet=offer_data.get("snippet"),
        matched_item=offer_data.get("matched_item"),
        screenshot_path=offer_data.get("screenshot_path"),
        price_source=offer_data.get("price_source"),
    )

    norm_key = (offer.store.strip().lower(), offer.url.strip().rstrip("/"))
    for i, existing in enumerate(artifacts["offers"]):
        existing_key = (existing.store.strip().lower(), existing.url.strip().rstrip("/"))
        if existing_key == norm_key:
            artifacts["offers"][i] = offer
            return offer

    artifacts["offers"].append(offer)
    return offer


async def _scrape_single_store_worker_async(
    browser: Browser,
    semaphore: asyncio.Semaphore,
    store: DiscoveredStore,
    search_query: str,
    max_products: int = MAX_PRODUCTS_PER_STORE,
) -> dict:
    """Isolated async worker for one store: runs tiered search -> inspects top candidates -> saves offers."""
    async with semaphore:
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 860},
        )
        store_domain = urlparse(store.url).netloc.lower().removeprefix("www.")
        inspected_offers = []
        in_stock_count = 0
        try:
            tier_used, candidates = await search_store_tiered_async(context, store, search_query)
            for cand in candidates[: max(max_products * 2, 4)]:
                details = await inspect_product_page_async(
                    context=context,
                    product_url=cand["url"],
                    store_name=store_domain,
                    matched_item=search_query,
                    capture_screenshot=True,
                )
                saved = upsert_offer_to_blackboard(details)
                if saved is not None:
                    inspected_offers.append(saved.model_dump())
                    if saved.in_stock:
                        in_stock_count += 1
                    if in_stock_count >= max_products:
                        break
            return {
                "store": store.name,
                "domain": store_domain,
                "tier_used": tier_used,
                "candidates_found": len(candidates),
                "valid_offers_saved": len(inspected_offers),
                "offers": inspected_offers,
            }
        except Exception as e:
            return {
                "store": store.name,
                "domain": store_domain,
                "tier_used": "ERROR",
                "candidates_found": 0,
                "valid_offers_saved": 0,
                "error": f"{type(e).__name__}: {e}",
            }
        finally:
            await context.close()


async def scrape_all_stores_async(
    item: str,
    region: str = "Sri Lanka",
    force_refresh_stores: bool = False,
    max_products_per_store: int = MAX_PRODUCTS_PER_STORE,
) -> dict:
    """Discover/reuse stores and scrape them concurrently using an async Playwright worker pool."""
    t0 = time.perf_counter()
    stores = await asyncio.to_thread(ensure_discovered_stores, item, region, force_refresh_stores)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_STORES)
        try:
            tasks = [
                _scrape_single_store_worker_async(
                    browser=browser,
                    semaphore=semaphore,
                    store=store,
                    search_query=item,
                    max_products=max_products_per_store,
                )
                for store in stores
            ]
            store_reports = await asyncio.gather(*tasks)
        finally:
            await browser.close()

    elapsed = round(time.perf_counter() - t0, 2)
    return {
        "item": item,
        "region": region,
        "elapsed_seconds": elapsed,
        "stores_searched": len(stores),
        "total_valid_offers": len(artifacts.get("offers", [])),
        "store_reports": store_reports,
    }


async def inspect_single_url_async(product_url: str, store_name: str = "") -> dict:
    """Helper to inspect a single product URL standalone (with stealth + screenshot)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 860},
        )
        try:
            return await inspect_product_page_async(
                context=context,
                product_url=product_url,
                store_name=store_name,
                capture_screenshot=True,
            )
        finally:
            await context.close()
            await browser.close()


def run_async(coro):
    """Run an async coroutine safely from synchronous code (even if an event loop exists)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
