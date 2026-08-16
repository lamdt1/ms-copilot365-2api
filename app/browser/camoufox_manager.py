import asyncio
import logging
from typing import AsyncGenerator, Optional

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
        self._active_recv_queue: Optional[asyncio.Queue] = None  # single slot, replaces list
        self._browser_lock = asyncio.Lock()  # Prevent concurrent browser interactions (nudge vs stream)
        self._browser_stream_lock = asyncio.Lock()  # Serialize stream_chat_browser calls (1 at a time)
        self._page_ready = False  # True once browser has Copilot chat UI loaded

    def register_recv_listener(self, queue: asyncio.Queue):
        self.recv_listeners.append(queue)

    def unregister_recv_listener(self, queue: asyncio.Queue):
        if queue in self.recv_listeners:
            self.recv_listeners.remove(queue)

    async def get_auth_cookies(self) -> str:
        """
        Returns browser cookies for office.com domains as a Cookie header string.
        These are required for authenticated direct WS connections to substrate.office.com.
        """
        if not self.context:
            return ""
        try:
            cookies = await self.context.cookies()
            relevant = [
                c for c in cookies
                if any(d in c.get("domain", "") for d in ["office.com", "microsoft.com", "microsoftonline.com"])
            ]
            return "; ".join(f"{c['name']}={c['value']}" for c in relevant)
        except Exception as exc:
            logger.debug("get_auth_cookies: failed: %s", exc)
            return ""

    def _handle_recv_frame(self, data: dict):
        frame_str = data.get("data", "")
        q = self._active_recv_queue
        if frame_str and q is not None:
            logger.info("_handle_recv_frame: routing frame[:80]=%r", frame_str[:80])
            try:
                q.put_nowait(frame_str)
            except Exception:
                pass

    async def stream_chat_browser(self, prompt: str) -> AsyncGenerator[tuple[str, dict], None]:
        """
        Submits prompt via Camoufox browser and streams response frames via JS injection.

        JS expose_binding (__onSydneyRecvFrame) captures WS recv frames reliably in Camoufox.
        M365 browser sends multiple intermediate messages[] frames with growing text;
        the last one before the type:2/type:3 done signal contains the complete response.

        Serialized via _browser_stream_lock: the single browser tab cannot handle
        multiple concurrent chat submissions. Concurrent requests wait in line.
        """
        if not self.page or self.page.is_closed():
            yield "error", {"message": "Browser page not available"}
            return

        # Wait for browser page to be ready
        if not self._page_ready:
            logger.warning("stream_chat_browser: Browser page not ready yet, waiting up to 30s...")
            for _ in range(60):
                await asyncio.sleep(0.5)
                if self._page_ready:
                    break
            else:
                yield "error", {"message": "Browser not ready within timeout"}
                return

        # Serialize: only one stream_chat_browser at a time.
        # Concurrent callers wait here; this prevents _active_recv_queue race conditions.
        if self._browser_stream_lock.locked():
            logger.warning("stream_chat_browser: Browser busy, waiting for current stream to finish...")

        async with self._browser_stream_lock:
            # Single-slot queue — register BEFORE acquiring browser_lock so frames
            # arriving while nudge_refresh holds the lock are still buffered.
            queue: asyncio.Queue = asyncio.Queue()
            self._active_recv_queue = queue
            from app.substrate.turn_parser import TurnParser
            parser = TurnParser()
            last_full_text = ""   # updated by messages[{author:bot,text:...}] frames
            delta_text = ""        # accumulated from writeAtCursor delta frames (fallback)
            pending_images: list = []  # image events collected during the stream

            try:
                async with self._browser_lock:
                    selector = "textarea, [contenteditable='true'], input[placeholder*='Copilot']"
                    element = await self.page.query_selector(selector)
                    if not element:
                        yield "error", {"message": "Copilot prompt element not found on page"}
                        return

                    await element.focus()
                    await element.fill(prompt)
                    await self.page.keyboard.press("Enter")
                    logger.info("stream_chat_browser: Prompt submitted, draining WS frames...")

                # Drain queue — may include stale nudge frames then actual response frames
                timeout_sec = settings.BROWSER_TIMEOUT_SEC
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=timeout_sec)
                    except asyncio.TimeoutError:
                        logger.error("stream_chat_browser: Timeout waiting for response")
                        final = last_full_text or delta_text
                        if final:
                            yield "text", {"text": final}
                        for img_ev_type, img_payload in pending_images:
                            yield img_ev_type, img_payload
                        if not final and not pending_images:
                            yield "error", {"message": "browser_stream_timeout"}
                        break

                    for ev_type, payload in parser.feed(msg):
                        if ev_type == "ping":
                            continue

                        if ev_type == "text":
                            if payload.get("is_full"):
                                t = payload.get("text", "")
                                if t:
                                    last_full_text = t  # Keep updating — last one wins
                            else:
                                # Accumulate writeAtCursor deltas as fallback
                                delta_text += payload.get("text", "")
                        elif ev_type in ("image", "image_b64"):
                            # Collect image events — yield after text but before done
                            pending_images.append((ev_type, payload))
                        elif ev_type == "done":
                            final = last_full_text or delta_text
                            if not final and not pending_images:
                                logger.debug("stream_chat_browser: Skipping stale done (no content yet)")
                                continue
                            if final:
                                logger.info(
                                    "stream_chat_browser: Done — emitting %d chars, starts=%r",
                                    len(final), final[:40]
                                )
                                yield "text", {"text": final}
                            for img_ev_type, img_payload in pending_images:
                                yield img_ev_type, img_payload
                            yield ev_type, payload
                            return
                        elif ev_type == "error":
                            yield ev_type, payload
                            return
                        else:
                            yield ev_type, payload
            finally:
                # Clear the slot only if it's still ours (not replaced by a newer request)
                if self._active_recv_queue is queue:
                    self._active_recv_queue = None


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
            if self.page:
                try:
                    await self.page.close()
                except Exception:
                    pass
            if self.context:
                try:
                    await self.context.close()
                except Exception:
                    pass
            if self.browser:
                try:
                    if hasattr(self.browser, "__aexit__"):
                        await self.browser.__aexit__(None, None, None)
                except Exception:
                    pass
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
            geoip=True
        )

        self.context = await self.browser.start()
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()

        # Expose token callback to JS environment
        await self.page.expose_binding(
            "__onSydneyTokenIntercepted",
            lambda source, data: self._handle_intercepted_token(data)
        )
        await self.page.expose_binding(
            "__onSydneyFrameIntercepted",
            lambda source, data: logger.info("Intercepted Browser WS Send Frame: %s", data.get("data", "")[:2000])
        )
        await self.page.expose_binding(
            "__onSydneyRecvFrame",
            lambda source, data: self._handle_recv_frame(data)
        )

        # Inject interceptor script before load
        await self.page.add_init_script(WS_INTERCEPT_SCRIPT)

        # Navigate directly to M365 Copilot chat page
        logger.info("CamoufoxManager: Navigating to https://m365.cloud.microsoft/chat...")
        await self.page.goto("https://m365.cloud.microsoft/chat", wait_until="load", timeout=60000)

        # Wait for chat input to appear (up to 20 seconds)
        self._page_ready = False
        try:
            await self.page.wait_for_selector(
                "textarea, [contenteditable='true'], [data-tid='ckeditor-input']",
                timeout=20000
            )
            logger.info("CamoufoxManager: Copilot chat input is ready.")
        except Exception:
            logger.warning("CamoufoxManager: Chat input selector timed out, waiting 5s fallback...")
            await asyncio.sleep(5.0)

        # Extra settle time for WS to connect
        await asyncio.sleep(3.0)
        self._page_ready = True
        logger.info("CamoufoxManager: Browser page ready for chat interactions.")

    def _handle_intercepted_token(self, data: dict):
        """
        Receives intercepted credentials from Firefox page and persists them.
        """
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        url = data.get("url")

        if access_token:
            logger.info("CamoufoxManager: Intercepted fresh access_token. Intercepted WS URL: %s", url)
            token_store.update_tokens(access_token, refresh_token, ws_url=url)
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
        Skips silently if browser is already busy (e.g. stream_chat_browser running).
        """
        if not self.page or self.page.is_closed():
            logger.warning("CamoufoxManager: Nudge failed, browser not active")
            return False

        # Non-blocking: skip if stream_chat_browser holds the lock
        if self._browser_lock.locked():
            logger.debug("CamoufoxManager: Nudge skipped — browser lock held by another operation")
            return False

        logger.info("CamoufoxManager: Executing Nudge refresh on Copilot page...")
        try:
            async with self._browser_lock:
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
