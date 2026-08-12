import asyncio
import logging
from camoufox import AsyncCamoufox
from app.config import settings
from app.browser.token_interceptor import WS_INTERCEPT_SCRIPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("browser_test")


async def main():
    logger.info("Starting AsyncCamoufox to capture real chat invocation...")
    async with AsyncCamoufox(
        headless=False,
        persistent_context=True,
        user_data_dir=settings.CAMOUFOX_USER_DATA_DIR,
        geoip=True
    ) as browser:
        context = await browser.start()
        page = context.pages[0] if context.pages else await context.new_page()

        await page.expose_binding(
            "__onSydneyFrameIntercepted",
            lambda source, data: logger.info("REAL BROWSER WS SEND FRAME: %s", data.get("data"))
        )
        await page.expose_binding(
            "__onSydneyTokenIntercepted",
            lambda source, data: logger.info("TOKEN INTERCEPTED: %s", data.get("url"))
        )

        await page.add_init_script(WS_INTERCEPT_SCRIPT)

        logger.info("Navigating to https://m365.cloud.microsoft...")
        await page.goto("https://m365.cloud.microsoft", wait_until="load", timeout=60000)

        await asyncio.sleep(5.0)

        # Find prompt box
        selector = "textarea, [contenteditable='true'], input[placeholder*='Copilot']"
        elem = await page.query_selector(selector)
        if elem:
            logger.info("Found prompt input element! Typing message...")
            await elem.focus()
            await elem.fill("Hello, test 123")
            await asyncio.sleep(1.0)
            await page.keyboard.press("Enter")
            logger.info("Pressed Enter! Waiting 10 seconds to log intercepted WS frames...")
            await asyncio.sleep(10.0)
        else:
            logger.error("Could not find prompt element on page!")

if __name__ == "__main__":
    asyncio.run(main())
