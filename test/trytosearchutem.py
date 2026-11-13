import asyncio
import re
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, Page

class ResilientProductFinder:
    def __init__(self, page: Page):
        self.page = page

    async def verify_and_extract_products(self, query: str) -> tuple[bool, list[dict]]:
        """
        Verifies if products on the page are relevant matches for `query`:
        1. Checks for direct product page redirect.
        2. Checks for negative phrases ('no products found', '0 results').
        3. Extracts product links.
        4. Filters by keyword relevance so 'popular/recommended' products aren't falsely counted.
        """
        # Signal 1: Direct single-product page redirect
        if "/product/" in self.page.url:
            has_buy_button = await self.page.locator(
                "button:has-text('Add to Cart'), .single_add_to_cart_button, button[type='submit']"
            ).count() > 0
            if has_buy_button:
                title = await self.page.title()
                return True, [{"title": title, "url": self.page.url}]

        # Signal 2: Check for negative text on the page
        negative_patterns = [
            "no products found",
            "no results found",
            "0 results",
            "nothing found",
            "did not match any products",
            "sorry, but nothing matched"
        ]
        body_text = (await self.page.inner_text("body")).lower()
        for phrase in negative_patterns:
            if phrase in body_text:
                print(f"      [Info] Page states: '{phrase}'")
                return False, []

        # Signal 3: Extract product candidate links
        raw_items = await self.page.locator(
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
            return False, []

        # Signal 4: Keyword relevance filter (filters out 'recommended' products)
        query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 1]
        relevant_items = []
        for item in raw_items:
            title_lower = item["title"].lower()
            # Item is considered relevant if at least one significant query term is in the title
            if any(word in title_lower for word in query_words):
                relevant_items.append(item)

        is_found = len(relevant_items) > 0
        return is_found, relevant_items

    async def search(self, base_url: str, query: str, direct_url_pattern: str = None) -> list[dict]:
        """
        Executes the 3-step fallback search for `query` on `base_url`:
        Step 1: Direct URL with encoded query.
        Step 2: On-site Search Bar UI interaction.
        Step 3: External Search fallback.
        """
        print(f"\n========================================================")
        print(f"🔍 Searching for: '{query}' on {base_url}")
        print(f"========================================================")

        # -------------------------------------------------------------
        # STEP 1: Direct URL Search
        # -------------------------------------------------------------
        if direct_url_pattern:
            direct_url = direct_url_pattern.format(query=quote_plus(query))
        else:
            direct_url = f"{base_url.rstrip('/')}/?s={quote_plus(query)}&post_type=product"

        print(f"👉 [Step 1] Trying Direct Search URL: {direct_url}")
        try:
            res = await self.page.goto(direct_url, timeout=25000, wait_until="domcontentloaded")
            if res and res.status == 200:
                is_found, products = await self.verify_and_extract_products(query)
                if is_found:
                    print(f"   ✅ [Step 1 Success] Found {len(products)} matching product(s) via Direct URL!")
                    return products
            print("   ⚠️ [Step 1 Missed] No relevant products matched via direct URL.")
        except Exception as e:
            print(f"   ⚠️ [Step 1 Error] {e}")

        # -------------------------------------------------------------
        # STEP 2: On-Site Search Bar (UI Interaction)
        # -------------------------------------------------------------
        print(f"\n👉 [Step 2] Falling back to On-Site Search Bar at {base_url}...")
        try:
            await self.page.goto(base_url, timeout=25000, wait_until="domcontentloaded")

            search_selectors = [
                "input[name='query']",              # Custom stores (TRONIC.LK)
                "input[name='s']",                  # WordPress / WooCommerce
                "input[type='search']",             # Standard HTML5 search
                "input[placeholder*='search' i]",   # Any input with 'search' in placeholder
                "input[name*='search']",            # Any input named search
                "#search",                          # Search id
            ]

            input_element = None
            for selector in search_selectors:
                loc = self.page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible():
                    input_element = loc
                    print(f"   Found search bar using: '{selector}'")
                    break

            if input_element:
                print(f"   Typing '{query}' and pressing Enter...")
                await input_element.fill(query)
                await input_element.press("Enter")
                await self.page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)  # Allow dynamic results to render

                is_found, products = await self.verify_and_extract_products(query)
                if is_found:
                    print(f"   ✅ [Step 2 Success] Found {len(products)} matching product(s) via Search Bar!")
                    return products
                print("   ⚠️ [Step 2 Missed] Search bar submitted, but no relevant products found.")
            else:
                print("   ⚠️ [Step 2 Missed] No visible search bar on homepage.")
        except Exception as e:
            print(f"   ⚠️ [Step 2 Error] {e}")

        # -------------------------------------------------------------
        # STEP 3: External Search Engine Fallback
        # -------------------------------------------------------------
        print(f"\n👉 [Step 3] Falling back to External Search Engine...")
        try:
            from urllib.parse import urlparse
            domain = urlparse(base_url).netloc
            ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(f'{domain} {query}')}"
            
            await self.page.goto(ddg_url, timeout=20000, wait_until="domcontentloaded")
            raw_links = await self.page.locator(".result__url, a.result__snippet").evaluate_all(
                "elements => elements.map(e => e.href || e.innerText.trim())"
            )
            matched = [link for link in raw_links if domain in link and "/product" in link]
            if matched:
                print(f"   ✅ [Step 3 Success] Found {len(matched)} direct external link(s) for {domain}!")
                return [{"title": f"External Link: {query}", "url": url} for url in matched]
            print("   ⚠️ [Step 3 Missed] No direct product links found in external search.")
        except Exception as e:
            print(f"   ⚠️ [Step 3 Error] {e}")

        print(f"\n❌ Final Result: Product '{query}' could NOT be found on {base_url}.")
        return []

async def main():
    # Prompt the user for input
    print("\n" + "="*50)
    print("   🛒 SmolDealz Multi-Step Product Finder")
    print("="*50)
    user_query = input("Enter the product name to search (e.g., VL53L0X, ESP32, SG90): ").strip()
    
    if not user_query:
        user_query = "VL53L0X"
        print(f"Defaulting to '{user_query}'...")

    async with async_playwright() as p:
        # Launch browser (headless=False so you can watch each step happen live)
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        finder = ResilientProductFinder(page)

        # Run search on TRONIC.LK
        results = await finder.search(
            base_url="https://tronic.lk",
            query=user_query,
            direct_url_pattern="https://tronic.lk/shop/products?query={query}"
        )

        print("\n" + "="*50)
        print(f"📋 RESULTS FOR '{user_query}':")
        print("="*50)
        if results:
            for idx, item in enumerate(results, start=1):
                print(f" {idx}. {item['title']}\n    🔗 {item['url']}")
        else:
            print(f" No matching products found for '{user_query}'.")

        input("\nPress [Enter] to close the browser...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
