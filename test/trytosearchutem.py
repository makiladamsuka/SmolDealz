import asyncio
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

async def search_via_direct_url():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        query = input("ENter the produt name: ")
        encoded_query = quote_plus(query) 
        
        search_url = f"https://tronic.lk/?s={encoded_query}&post_type=product"
        
        print(f"Directly navigating to: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded")

        print("Landed directly on search results!")
        await page.screenshot(path="search_results.png")
        input("Press enter to continoue")
        await browser.close()

asyncio.run(search_via_direct_url())