import asyncio
from playwright.async_api import async_playwright

async def test_search():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Test 1: Typing into the search bar
        await page.goto("https://tronic.lk", wait_until="domcontentloaded")
        print("Page loaded. Locating search bar...")
        # The input has name="query" or placeholder="Search..."
        search_input = page.locator("input[name='query']")
        await search_input.fill("VL53L0X")
        await search_input.press("Enter")
        await page.wait_for_load_state("domcontentloaded")
        print(f"After pressing Enter, URL is: {page.url}")
        
        # Check products found
        titles = await page.locator(".product h4, .product-title, .product a, .card-title").all_inner_texts()
        print(f"Found {len(titles)} titles. First few: {titles[:5]}")
        
        await browser.close()

asyncio.run(test_search())
