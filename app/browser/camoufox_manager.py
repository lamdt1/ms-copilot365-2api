import asyncio
import logging
from typing import Optional

from camoufox import AsyncCamoufox

from app.config import settings
from app.core.token_store import token_store
from app.browser.token_interceptor import WS_INTERCEPT_SCRIPT

logger = logging.getLogger(__name__)


class CamoufoxManager:
    def __init__(self):
        self.browser: Optional[AsyncCamoufox] = None
        self.context = None
        self.page = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.token_ready_event = asyncio.Event()

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_browser())
        logger.info("CamoufoxManager: Starting browser monitor task...")

        # Wait up to 10 seconds for initial check of old valid token
        if token_store.is_valid:
            logger.info("CamoufoxManager: Valid cached token found. Flagging API as ready.")
            self.token_ready_event.set()

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self._close_browser()
        logger.info("CamoufoxManager: Browser monitor task stopped.")

    async def _close_browser(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser and hasattr(self.browser, "__aexit__"):
                await self.browser.__aexit__(None, None, None)
        except Exception as exc:
            logger.debug("CamoufoxManager: Browser close exception (normal during exit): %s", exc)
        finally:
            self.page = None
            self.context = None
            self.browser = None

    async def _monitor_browser(self):
        """
        Keeps the Camoufox engine running.
        If browser closes or crashes, it automatically respawns it.
        """
        while self._running:
            try:
                # If token is valid, we can run headless immediately.
                # If token is invalid/empty, we MUST run headful (headful = CAMOUFOX_HEADLESS=False)
                # so the user can interact via noVNC to log in.
                headless = settings.CAMOUFOX_HEADLESS
                if not token_store.is_valid:
                    logger.info("CamoufoxManager: No valid token. Forcing headful mode for login...")
                    headless = False
                elif settings.CAMOUFOX_AUTO_HEADLESS:
                    logger.info("CamoufoxManager: Token is valid. Running browser in headless mode.")
                    headless = True

                await self._launch_instance(headless)

                # Keep loop alive while browser exists
                while self._running and self.browser and self.page:
                    await asyncio.sleep(5.0)
                    # Check if browser was closed externally
                    if self.page.is_closed():
                        logger.warning("CamoufoxManager: Browser page closed externally. Restarting...")
                        break

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("CamoufoxManager: Browser process exception: %s. Retrying in 10s...", exc)
                await self._close_browser()
                await asyncio.sleep(10.0)

    async def _launch_instance(self, headless: bool):
        await self._close_browser()

        logger.info(
            "CamoufoxManager: Launching Camoufox (headless=%s, profile=%s)",
            headless,
            settings.CAMOUFOX_USER_DATA_DIR
        )

        # Setup persistent context
        self.browser = AsyncCamoufox(
            headless=headless,
            persistent_context=True,
            user_data_dir=settings.CAMOUFOX_USER_DATA_DIR,
            display=settings.DISPLAY,
            # Skip WebRTC leaks and geolocation block
            geoip=True
        )

        self.context = await self.browser.start()
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

        # Expose token callback to JS environment
        await self.page.expose_binding(
            "__onSydneyTokenIntercepted",
            lambda source, data: self._handle_intercepted_token(data)
        )

        # Inject interceptor script before load
        await self.page.add_init_script(WS_INTERCEPT_SCRIPT)

        # Navigate to M365 Copilot
        logger.info("CamoufoxManager: Navigating to https://m365.cloud.microsoft...")
        await self.page.goto("https://m365.cloud.microsoft", wait_until="load", timeout=60000)

        # Wait a bit to let websockets connect
        await asyncio.sleep(5.0)

    def _handle_intercepted_token(self, data: dict):
        """
        Receives intercepted credentials from Firefox page and persists them.
        """
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")

        if access_token:
            logger.info("CamoufoxManager: Intercepted fresh access_token")
            token_store.set_tokens(access_token, refresh_token)
            self.token_ready_event.set()

            # If we were in headful mode, we should trigger a browser restart
            # to switch into resource-saving headless mode.
            if settings.CAMOUFOX_AUTO_HEADLESS and not settings.CAMOUFOX_HEADLESS:
                # Schedule headless switch
                asyncio.create_task(self._switch_to_headless())

    async def _switch_to_headless(self):
        logger.info("CamoufoxManager: Switching browser to headless mode...")
        # Closing page triggers respawn loop in _monitor_browser, which will
        # now see that token_store.is_valid is True, and launch headless.
        await self._close_browser()

    async def nudge_refresh(self) -> bool:
        """
        Taps space + backspace inside the chat text box to force Sydney web app
        to reconnect its websocket and fetch a new access token.
        """
        if not self.page or self.page.is_closed():
            logger.warning("CamoufoxManager: Nudge failed, browser not active")
            return False

        logger.info("CamoufoxManager: Executing Nudge refresh on Copilot page...")
        try:
            # Look for copilot chat prompt input box
            # Usually matches textareas or contenteditables
            selector = "textarea, [contenteditable='true'], input[placeholder*='Copilot']"
            element = await self.page.query_selector(selector)
            if element:
                await element.focus()
                await element.type(" ")
                await asyncio.sleep(0.5)
                await self.page.keyboard.press("Backspace")
                logger.info("CamoufoxManager: Nudge keys sent successfully. Awaiting token capture...")
                # Wait up to 15 seconds for token update
                for _ in range(30):
                    await asyncio.sleep(0.5)
                    if token_store.is_valid and token_store.seconds_remaining > 3000:
                        return True
            else:
                logger.warning("CamoufoxManager: Chat input element not found for Nudge")
                # Fallback: reload page
                await self.page.reload()
                await asyncio.sleep(10.0)
                if token_store.is_valid:
                    return True
        except Exception as exc:
            logger.error("CamoufoxManager: Nudge operation failed: %s", exc)

        return False


camoufox_manager = CamoufoxManager()
