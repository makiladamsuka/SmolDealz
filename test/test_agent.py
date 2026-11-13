import os
import asyncio
import re
from urllib.parse import quote_plus
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from smolagents import CodeAgent, LiteLLMModel, tool

# Load environment variables (.env)
load_dotenv()

# =====================================================================
# 1. PLAYWRIGHT HELPER FUNCTIONS (Running headless browser)
# =====================================================================

async def _playwright_direct_url(store_url: str, query: str) -> list[dict]:
    """Navigates to direct search URL and collects product cards."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            if "tronic.lk" in store_url:
                target_url = f"https://tronic.lk/shop/products?query={quote_plus(query)}"
            else:
                target_url = f"{store_url.rstrip('/')}/?s={quote_plus(query)}&post_type=product"
            
            await page.goto(target_url, timeout=25000, wait_until="domcontentloaded")
            items = await page.locator("a[href*='/product/'], a[href*='/product'], .product a").evaluate_all(
                """elements => {
                    const results = [];
                    const seen = new Set();
                    for (const el of elements) {
                        const title = el.innerText.trim();
                        const href = el.href;
                        if (title.length > 3 && !seen.has(href)) {
                            seen.add(href);
                            results.push({ title: title, url: href });
                        }
                    }
                    return results.slice(0, 8);
                }"""
            )
            return items
        except Exception as e:
            return [{"error": str(e)}]
        finally:
            await browser.close()

async def _playwright_search_bar(store_url: str, query: str) -> list[dict]:
    """Opens homepage, types query into the search bar, and collects product cards."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(store_url, timeout=25000, wait_until="domcontentloaded")
            selectors = [
                "input[name='query']",
                "input[name='s']",
                "input[type='search']",
                "input[placeholder*='search' i]"
            ]
            for sel in selectors:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.fill(query)
                    await loc.press("Enter")
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1.5)
                    break
            
            items = await page.locator("a[href*='/product/'], a[href*='/product'], .product a").evaluate_all(
                """elements => {
                    const results = [];
                    const seen = new Set();
                    for (const el of elements) {
                        const title = el.innerText.trim();
                        const href = el.href;
                        if (title.length > 3 && !seen.has(href)) {
                            seen.add(href);
                            results.push({ title: title, url: href });
                        }
                    }
                    return results.slice(0, 8);
                }"""
            )
            return items
        except Exception as e:
            return [{"error": str(e)}]
        finally:
            await browser.close()

async def _playwright_web_search(store_domain: str, query: str) -> list[dict]:
    """Uses DuckDuckGo to search for indexed pages matching the store domain."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(f'{store_domain} {query}')}"
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            raw_links = await page.locator(".result__title a").evaluate_all(
                """elements => elements.map(e => ({ title: e.innerText.trim(), url: e.href }))"""
            )
            filtered = [item for item in raw_links if store_domain in item["url"]]
            return filtered[:5]
        except Exception as e:
            return [{"error": str(e)}]
        finally:
            await browser.close()


# =====================================================================
# 2. SMOLAGENTS TOOLS
# =====================================================================

@tool
def search_store_direct_url(store_url: str, search_query: str) -> str:
    """Searches a store using its direct query URL. Fast.
    Args:
        store_url: Base URL (e.g. 'https://tronic.lk').
        search_query: Search term (e.g. 'brushless motor', 'A2212').
    """
    results = asyncio.run(_playwright_direct_url(store_url, search_query))
    return str(results)

@tool
def search_store_on_site_bar(store_url: str, search_query: str) -> str:
    """Navigates to store homepage, locates search input, and presses Enter.
    Args:
        store_url: Base URL (e.g. 'https://tronic.lk').
        search_query: Search term to type into the search bar.
    """
    results = asyncio.run(_playwright_search_bar(store_url, search_query))
    return str(results)

@tool
def search_web_for_store_products(store_domain: str, search_query: str) -> str:
    """Performs web search (DuckDuckGo) to discover indexed product pages on the store.
    Args:
        store_domain: The store domain name (e.g. 'tronic.lk').
        search_query: Technical search keywords (e.g. '1400KV brushless motor').
    """
    results = asyncio.run(_playwright_web_search(store_domain, search_query))
    return str(results)


# =====================================================================
# 3. AGENT INITIALIZATION & EXECUTION
# =====================================================================

def run_procurement_agent(user_request: str):
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if not openrouter_key:
        raise ValueError("OPENROUTER_API_KEY not found in .env file!")

    # Model configuration
    # Note: If your OpenRouter account has paid credits, you can use:
    # "openrouter/meta-llama/llama-3.3-70b-instruct" or "openrouter/google/gemini-2.5-flash"
    # For free tier on OpenRouter, we use the active free model:
    model_id = os.getenv("OPENROUTER_MODEL", "openrouter/nvidia/nemotron-3.5-lightning:free")
    print(f"🤖 Initializing CodeAgent with model: {model_id}")

    model = LiteLLMModel(
        model_id=model_id,
        api_key=openrouter_key
    )

    system_prompt = """You are SmolDealz, an autonomous hardware procurement agent.
Your goal is to find the exact product matching the user's specification.

CRITICAL HARDWARE VERIFICATION RULES:
1. DISTINGUISH CORE PARTS FROM ACCESSORIES:
   - If user asks for a 'brushless motor', DO NOT accept:
     * Motor Controllers / Speed Controllers (ESCs)
     * Motor Drivers / Driver modules
     * Propellers or motor mounts
   - Only accept actual MOTOR units.

2. SPECIFICATION CHECKING:
   - If the user specifies ratings (e.g., '1400KV', '3.3V', 'ESP32-S3'), verify the product title has matching or compatible ratings.

3. WORKFLOW:
   - Step 1: Formulate technical search queries (e.g., '1400kv motor', 'A2212 1400kv', 'BLDC motor').
   - Step 2: Call `search_store_direct_url` or `search_store_on_site_bar`.
   - Step 3: Inspect the titles. If results are contaminated with controllers/drivers or don't match KV rating, try `search_web_for_store_products` with refined terms.
   - Step 4: Return a clean Markdown list of verified matching products and direct URLs.
"""

    agent = CodeAgent(
        tools=[search_store_direct_url, search_store_on_site_bar, search_web_for_store_products],
        model=model,
        instructions=system_prompt,
        max_steps=5
    )

    print(f"\n🚀 Running Agent for request: '{user_request}'\n")
    response = agent.run(user_request)
    return response

if __name__ == "__main__":
    prompt = input("Enter hardware to find (e.g. '1400kv brushless motor on tronic.lk'): ").strip()
    if not prompt:
        prompt = "Find a 1400kv brushless motor on tronic.lk. Do NOT return motor controllers or drivers."
    
    result = run_procurement_agent(prompt)
    print("\n" + "="*50)
    print("🎯 AGENT FINAL VERDICT:")
    print("="*50)
    print(result)

