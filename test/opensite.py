import asyncio
from playwright.async_api import async_playwright

async def inspect_site(context, url: str, index: int):
    """Opens a single website in its own page, reads its title, and captures a screenshot."""
    page = await context.new_page()
    print(f"[{index}] 🚀 Starting: {url}")
    try:
        # Navigate concurrently (wait for DOM content ready)
        response = await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        title = await page.title()
        status = response.status if response else "Unknown"
        print(f"[{index}] ✅ Loaded [{status}]: {title} ({url})")

        # Take a screenshot as visual proof
        screenshot_name = f"site_{index}_proof.png"
        await page.screenshot(path=screenshot_name)
        print(f"[{index}] 📸 Saved screenshot to: {screenshot_name}")

    except Exception as e:
        print(f"[{index}] ❌ Failed to load {url}: {e}")
    finally:
        await page.close()

async def open_multiple_sites(urls: list[str], headless: bool = False):
    """Spins up a single Chromium browser and scrapes multiple URLs concurrently."""
    print(f"🌐 Launching browser for {len(urls)} sites concurrently...")
    async with async_playwright() as p:
        # Set headless=False so you can watch them load in real-time
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Run all scraping tasks in parallel
        tasks = [inspect_site(context, url, idx + 1) for idx, url in enumerate(urls)]
        await asyncio.gather(*tasks)

        # Brief pause so you can view open state before exiting
        if not headless:
            await asyncio.sleep(2)

        await browser.close()
        print("✨ All sites processed successfully!")

if __name__ == "__main__":
    target_urls = [
        "https://tronic.lk",
        "https://scionelectronics.com",
    ]
    asyncio.run(open_multiple_sites(target_urls, headless=False))
