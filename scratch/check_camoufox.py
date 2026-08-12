import asyncio
import logging
from app.browser.camoufox_manager import camoufox_manager
from app.core.token_store import token_store

logging.basicConfig(level=logging.INFO)


async def capture_browser():
    print("Starting Camoufox...")
    await camoufox_manager.start()

    # Wait for page to load
    await asyncio.sleep(10.0)

    if camoufox_manager.page:
        print(f"Current page URL: {camoufox_manager.page.url}")
        print("Executing Nudge to capture live WebSocket connection...")
        success = await camoufox_manager.nudge_refresh()
        print("Nudge success:", success)
        print("Token store valid:", token_store.is_valid)
        print("Token store exp:", token_store.exp)
    else:
        print("Page is None!")

if __name__ == "__main__":
    asyncio.run(capture_browser())
