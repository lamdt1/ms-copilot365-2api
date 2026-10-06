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
        self._image_cache: dict = {}  # url -> (bytes, content_type) intercepted from browser

    def _find_in_image_cache(self, url: str) -> tuple | None:
        """
        Find an image in cache by exact URL first, then by speCId parameter.
        Needed because browser may follow redirects, making response.url differ from WS frame URL.
        """
        from urllib.parse import urlparse, parse_qs

        # 1. Exact match
        if url in self._image_cache:
            logger.info("_find_in_image_cache: exact hit for %s", url[:80])
            return self._image_cache.pop(url)

        # 2. Fuzzy match by speCId (uniquely identifies each image generation)
        try:
            params = parse_qs(urlparse(url).query)
            spe_id = params.get("speCId", [None])[0]
            if spe_id:
                for cached_url in list(self._image_cache.keys()):
                    cached_params = parse_qs(urlparse(cached_url).query)
                    if cached_params.get("speCId", [None])[0] == spe_id:
                        logger.info("_find_in_image_cache: speCId match (%s)", spe_id)
                        return self._image_cache.pop(cached_url)
        except Exception as exc:
            logger.debug("_find_in_image_cache fuzzy: %s", exc)

        logger.warning("_find_in_image_cache: MISS for %s | cache keys: %s",
                       url[:60], [k[:60] for k in self._image_cache.keys()])
        return None

    def register_recv_listener(self, queue: asyncio.Queue):
        self.recv_listeners.append(queue)

    def unregister_recv_listener(self, queue: asyncio.Queue):
        if queue in self.recv_listeners:
            self.recv_listeners.remove(queue)

    @property
    def is_authenticated_page(self) -> bool:
        """
        Returns True ONLY if the browser page exists, is open, and is NOT redirected
        to a Microsoft login/auth screen (e.g. login.microsoftonline.com).
        """
        if not self.page or self.page.is_closed():
            return False
        url = (self.page.url or "").lower()
        if any(auth_domain in url for auth_domain in [
            "login.microsoftonline.com",
            "login.live.com",
            "login.microsoft.com",
            "/oauth2/",
            "/common/oauth2",
        ]):
            return False
        return any(valid_domain in url for valid_domain in [
            "m365.cloud.microsoft",
            "copilot.microsoft.com",
            "office.com",
            "bing.com",
        ])

    async def get_auth_cookies(self) -> str:
        """
        Returns browser cookies for Microsoft domains as a Cookie header string.
        Includes all MS-related domains needed for office.com, designer app, etc.
        """
        if not self.context:
            return ""
        try:
            cookies = await self.context.cookies()
            relevant = [
                c for c in cookies
                if any(d in c.get("domain", "") for d in [
                    "office.com", "microsoft.com", "microsoftonline.com",
                    "live.com", "cloud.microsoft", "officeapps.live.com",
                ])
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

    async def _scrape_dom_response(self) -> dict:
        """
        Scrapes the latest AI response text directly from the browser DOM.
        Used as a fail-safe fallback when WebSocket frame interception is unavailable.
        Filters out header metadata ('Copilot said:') and placeholder states ('Đang xử lý…').
        """
        if not self.page or self.page.is_closed():
            return {"text": "", "count": 0, "is_placeholder": True, "generating": False}

        js_script = """
        () => {
            const isReasoningOrPlaceholderLine = (line) => {
                if (!line) return true;
                const lower = line.trim().toLowerCase();
                const patterns = [
                    'đang sắp xếp', 'đang tìm kiếm', 'đang xử lý', 'đang suy nghĩ', 'đang tạo', 'đang phân tích',
                    'thinking', 'working on it', 'searching', 'generating', 'analyzing', 'organizing',
                    'copilot said:', 'copilot said', 'you said:', 'you said'
                ];
                for (const p of patterns) {
                    if (lower.startsWith(p)) return true;
                }
                return false;
            };

            const isPlaceholderText = (txt) => {
                if (!txt) return true;
                const lower = txt.trim().toLowerCase();
                if (isReasoningOrPlaceholderLine(lower)) return true;
                const placeholders = [
                    'đang xử lý', 'đang suy nghĩ', 'đang tìm kiếm', 'đang tạo',
                    'thinking', 'working on it', 'searching', 'generating', 'analyzing',
                    'copilot said:'
                ];
                let stripped = lower;
                for (const p of placeholders) {
                    stripped = stripped.replaceAll(p, '').replaceAll('…', '').replaceAll('.', '').replaceAll(' ', '');
                }
                return stripped.length === 0;
            };

            const getAssistantText = () => {
                const turnSelectors = [
                    '[data-content="ai-message"]',
                    '[data-author="assistant"]',
                    '[data-author="bot"]',
                    '[data-testid*="copilot-message"]',
                    '[data-testid*="assistant"]',
                    '.fui-ChatMessage',
                    'cib-message-group[data-author="bot"]',
                    'div[class*="messageContent"]',
                    'div[class*="chatMessage"]',
                    'div[class*="response-content"]',
                    'div[class*="bot-message"]',
                    'div[class*="copilot-message"]'
                ];

                let candidateTurns = [];
                for (const sel of turnSelectors) {
                    const els = Array.from(document.querySelectorAll(sel))
                        .filter(e => !e.closest('[data-author="user"], [data-content="user-message"], [data-tid*="user"]'));
                    if (els.length > 0) {
                        candidateTurns = els;
                        break;
                    }
                }

                if (candidateTurns.length === 0) {
                    const mdBodies = Array.from(document.querySelectorAll('.markdown-body, [data-tid*="message-content"]'))
                        .filter(e => !e.closest('[data-author="user"], [data-content="user-message"]'));
                    if (mdBodies.length > 0) {
                        candidateTurns = mdBodies;
                    }
                }

                if (candidateTurns.length > 0) {
                    const lastTurn = candidateTurns[candidateTurns.length - 1];
                    const bodyEl = lastTurn.querySelector('.markdown-body, .fui-ChatMessage__body, [class*="body"], [class*="content"]') || lastTurn;

                    const clone = bodyEl.cloneNode(true);
                    // Remove headers, authors, citations, and any reasoning/status/thought/search containers
                    const elementsToRemove = [
                        'header', '[class*="author"]', '[class*="Header"]', '[class*="citation"]', '[class*="attribution"]', '[data-tid*="header"]',
                        'details', 'summary', '[role="status"]',
                        '[class*="thought"]', '[class*="reasoning"]', '[class*="status"]', '[class*="activity"]', '[class*="progress"]', '[class*="search"]', '[class*="query"]',
                        '[data-tid*="thought"]', '[data-tid*="status"]', '[data-tid*="search"]', '[data-tid*="activity"]', '[data-testid*="thought"]', '[data-testid*="reasoning"]',
                        '.fui-Accordion', '.fui-AccordionItem', '.fui-AccordionHeader', 'sup'
                    ];
                    clone.querySelectorAll(elementsToRemove.join(',')).forEach(el => el.remove());

                    const rawText = (clone.innerText || clone.textContent || '').trim();

                    const lines = rawText.split('\\n')
                        .map(l => l.trim())
                        .filter(l => l.length > 0 && !isReasoningOrPlaceholderLine(l));

                    const cleanText = lines.join('\\n').trim();
                    const placeholder = isPlaceholderText(cleanText);

                    return {
                        text: placeholder ? '' : cleanText,
                        count: candidateTurns.length,
                        is_placeholder: placeholder
                    };
                }

                return { text: '', count: 0, is_placeholder: true };
            };

            const isGenerating = () => {
                const stopBtn = document.querySelector('button[aria-label*="Stop"], button[title*="Stop"], button[data-tid*="stop"], [data-testid*="stop"], .stop-button');
                if (stopBtn && stopBtn.offsetWidth > 0 && stopBtn.offsetHeight > 0) {
                    return true;
                }
                const typing = document.querySelector('[class*="typing"], [class*="cursor"], [data-tid*="typing"], .fui-Skeleton');
                return !!typing;
            };

            const res = getAssistantText();
            return {
                text: res.text,
                count: res.count,
                is_placeholder: res.is_placeholder,
                generating: isGenerating()
            };
        }
        """
        try:
            res = await self.page.evaluate(js_script)
            return res if isinstance(res, dict) else {"text": "", "count": 0, "is_placeholder": True, "generating": False}
        except Exception as exc:
            logger.debug("_scrape_dom_response exception: %s", exc)
            return {"text": "", "count": 0, "is_placeholder": True, "generating": False}

    async def fetch_image_via_browser(self, url: str) -> tuple[str, str] | None:
        """
        Returns image as (base64_str, content_type) using two strategies:
        1. Check _image_cache via speCId fuzzy match — browser intercepted the image when rendering
        2. Fallback: context.request.get() using Playwright's authenticated network stack
        """
        import base64

        # Strategy 1: use intercepted image from browser's own response (most reliable)
        cached = self._find_in_image_cache(url)
        if cached:
            body, content_type = cached
            b64_data = base64.b64encode(body).decode("utf-8")
            logger.info("fetch_image_via_browser: served from cache (%d bytes)", len(body))
            return b64_data, content_type

        # Strategy 2: use Playwright's context.request (browser's auth network stack, no CORS)
        if not self.context:
            logger.warning("fetch_image_via_browser: browser context not available")
            return None
        try:
            response = await self.context.request.get(
                url,
                headers={"Referer": "https://m365.cloud.microsoft/"}
            )
            logger.info("fetch_image_via_browser: context.request.get status=%d", response.status)
            if response.ok:
                body = await response.body()
                content_type = response.headers.get("content-type", "image/png").split(";")[0].strip()
                b64_data = base64.b64encode(body).decode("utf-8")
                logger.info("fetch_image_via_browser: OK via context.request (%d bytes)", len(body))
                return b64_data, content_type
            else:
                logger.warning("fetch_image_via_browser: HTTP %d for %s", response.status, url[:80])
                return None
        except Exception as exc:
            logger.error("fetch_image_via_browser: exception: %s", exc)
            return None


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

        # Fail fast if browser is sitting on Microsoft login screen
        if not self.is_authenticated_page:
            logger.warning("stream_chat_browser: Browser is on unauthenticated page: %s", self.page.url)
            yield "error", {"message": "Browser is unauthenticated. Please log in via noVNC at http://localhost:6080."}
            return

        # Wait for browser page to be ready
        if not self._page_ready:
            logger.warning("stream_chat_browser: Browser page not ready yet, waiting up to 10s...")
            for _ in range(20):
                await asyncio.sleep(0.5)
                if self._page_ready:
                    break
            else:
                yield "error", {"message": "Browser not ready within timeout. Please check noVNC login."}
                return

        # Serialize: only one stream_chat_browser at a time.
        # Concurrent callers wait here; this prevents _active_recv_queue race conditions.
        # Allow enough time (60s) for any ongoing browser stream or token refresh to finish.
        _lock_wait_sec = 60.0
        if self._browser_stream_lock.locked():
            logger.warning("stream_chat_browser: Browser busy, waiting up to %.0fs...", _lock_wait_sec)
        try:
            await asyncio.wait_for(self._browser_stream_lock.acquire(), timeout=_lock_wait_sec)
        except asyncio.TimeoutError:
            logger.error("stream_chat_browser: Timed out waiting for browser lock (%.0fs)", _lock_wait_sec)
            # Return retryable text (not bare error) so Claude Code sees a message
            yield "text", {"text": "[Server busy — please retry your last message in a moment.]"}
            yield "done", {}
            return

        try:
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
                # Capture baseline DOM before sending prompt
                baseline_dom = await self._scrape_dom_response()
                baseline_text = baseline_dom.get("text", "")
                baseline_count = baseline_dom.get("count", 0)

                async with self._browser_lock:
                    selector = "textarea, [contenteditable='true'], input[placeholder*='Copilot']"
                    element = await self.page.query_selector(selector)
                    if not element:
                        yield "error", {"message": "Copilot prompt element not found on page"}
                        return

                    await element.focus()
                    try:
                        await element.fill(prompt)
                    except Exception:
                        await element.type(prompt)
                    await asyncio.sleep(0.3)
                    await self.page.keyboard.press("Enter")

                    # Also attempt to click Submit / Send button if present
                    try:
                        send_btn = await self.page.query_selector("button[aria-label*='Submit'], button[aria-label*='Send'], button[title*='Submit'], button[title*='Send'], button[data-tid*='submit']")
                        if send_btn:
                            await send_btn.click()
                    except Exception:
                        pass

                    logger.info("stream_chat_browser: Prompt submitted, waiting for response...")

                # Dual stream strategy: try WS frames, fallback to DOM polling
                timeout_sec = settings.BROWSER_TIMEOUT_SEC
                first_frame_timeout = 15.0
                start_time = asyncio.get_running_loop().time()
                last_activity_time = start_time
                has_received_any_frame = False

                dom_text = ""
                dom_unchanged_ticks = 0

                while True:
                    now = asyncio.get_running_loop().time()
                    cur_timeout_limit = timeout_sec if has_received_any_frame else first_frame_timeout

                    if now - last_activity_time > cur_timeout_limit:
                        logger.warning("stream_chat_browser: Timeout waiting for stream (%.0fs)", cur_timeout_limit)
                        final = last_full_text or delta_text or dom_text
                        if final:
                            yield "text", {"text": final, "is_full": True}
                        for img_ev_type, img_payload in pending_images:
                            yield img_ev_type, img_payload
                        if not final and not pending_images:
                            yield "error", {"message": "browser_stream_timeout"}
                        break

                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                        has_received_any_frame = True
                        last_activity_time = now

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
                                final = last_full_text or delta_text or dom_text
                                if not final and not pending_images:
                                    logger.debug("stream_chat_browser: Skipping stale done (no content yet)")
                                    continue
                                if final:
                                    logger.info(
                                        "stream_chat_browser: Done — emitting %d chars, starts=%r",
                                        len(final), final[:40]
                                    )
                                    yield "text", {"text": final, "is_full": True}
                                for img_ev_type, img_payload in pending_images:
                                    yield img_ev_type, img_payload
                                yield ev_type, payload
                                return
                            elif ev_type == "error":
                                yield ev_type, payload
                                return
                            else:
                                yield ev_type, payload

                    except asyncio.TimeoutError:
                        # WS queue empty during this 0.5s interval -> Poll DOM for rendered response
                        dom_res = await self._scrape_dom_response()
                        current_dom_text = dom_res.get("text", "")
                        current_dom_count = dom_res.get("count", 0)
                        is_generating = dom_res.get("generating", False)
                        is_placeholder = dom_res.get("is_placeholder", True)

                        if is_placeholder:
                            # Still thinking/preparing... reset unchanged ticks so we don't exit prematurely
                            dom_unchanged_ticks = 0
                            continue

                        # New response is valid if count increased or text differs from baseline
                        is_new_content = (
                            current_dom_text
                            and not is_placeholder
                            and (current_dom_count > baseline_count or current_dom_text != baseline_text)
                        )

                        if is_new_content:
                            has_received_any_frame = True
                            last_activity_time = now

                            if current_dom_text != dom_text:
                                dom_text = current_dom_text
                                dom_unchanged_ticks = 0
                                # Progressive DOM stream yield if no WS text received yet
                                if not last_full_text and not delta_text:
                                    yield "text", {"text": dom_text, "is_full": True}
                            else:
                                dom_unchanged_ticks += 1

                            # Generation complete when stop button disappeared and text stabilized for 3s (6 ticks)
                            if not is_generating and dom_unchanged_ticks >= 6:
                                if not last_full_text and not delta_text:
                                    logger.info("stream_chat_browser: DOM stream completed — %d chars", len(dom_text))
                                    yield "text", {"text": dom_text, "is_full": True}
                                    yield "done", {}
                                    return
            finally:
                # Clear the slot only if it's still ours (not replaced by a newer request)
                if self._active_recv_queue is queue:
                    self._active_recv_queue = None

        finally:
            # Release the manually-acquired lock (acquired via wait_for, not async with)
            if self._browser_stream_lock.locked():
                self._browser_stream_lock.release()


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

        # Clean any stale lock files from previous crashes or ungraceful exits
        try:
            import os
            for lk in ["lock", ".parentlock"]:
                p = os.path.join(settings.CAMOUFOX_USER_DATA_DIR, lk)
                if os.path.exists(p) or os.path.islink(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        except Exception:
            pass

        # Setup persistent context
        self.browser = AsyncCamoufox(
            user_data_dir=settings.CAMOUFOX_USER_DATA_DIR,
            headless=headless,
            persistent_context=True,
            geoip=True
        )

        self.context = await self.browser.start()
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()

        # Expose token callback to JS environment on the page
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

        # Intercept Designer image responses as the browser loads them
        # This is the most reliable way to capture images — the browser handles all auth
        async def _on_image_response(response):
            try:
                if "designerapp.officeapps.live.com" in response.url and response.status == 200:
                    content_type = response.headers.get("content-type", "image/png").split(";")[0].strip()
                    if content_type.startswith("image/"):
                        body = await response.body()
                        if body:
                            self._image_cache[response.url] = (body, content_type)
                            logger.info(
                                "Browser intercepted image: %d bytes, type=%s, url=%s",
                                len(body), content_type, response.url[:80]
                            )
            except Exception as exc:
                logger.debug("_on_image_response: %s", exc)

        self.page.on("response", _on_image_response)

        # Inject interceptor script before load
        if hasattr(self.context, "add_init_script"):
            await self.context.add_init_script(WS_INTERCEPT_SCRIPT)
        await self.page.add_init_script(WS_INTERCEPT_SCRIPT)

        # Navigate directly to M365 Copilot chat page
        logger.info("CamoufoxManager: Navigating to https://m365.cloud.microsoft/chat...")
        try:
            await self.page.goto("https://m365.cloud.microsoft/chat", wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            logger.warning("CamoufoxManager: Page goto warning (will still check input selector): %s", exc)

        # Wait for chat input to appear (up to 20 seconds)
        self._page_ready = False
        try:
            await self.page.wait_for_selector(
                "textarea, [contenteditable='true'], [data-tid='ckeditor-input']",
                timeout=20000
            )
            if self.is_authenticated_page:
                logger.info("CamoufoxManager: Copilot chat input is ready and page is authenticated.")
                self._page_ready = True
            else:
                logger.warning("CamoufoxManager: Chat input selector matched, but browser is on login page: %s", self.page.url)
        except Exception:
            logger.warning("CamoufoxManager: Chat input selector timed out, waiting 5s fallback...")
            await asyncio.sleep(5.0)
            if self.is_authenticated_page:
                self._page_ready = True
                logger.warning("CamoufoxManager: Marked page ready despite timeout (page is authenticated).")
            else:
                logger.warning("CamoufoxManager: Page is unauthenticated. User must login via noVNC.")

        # Extra settle time for WS to connect
        await asyncio.sleep(3.0)
        if self._page_ready:
            logger.info("CamoufoxManager: Browser page ready for chat interactions.")

    def _handle_intercepted_token(self, data: dict):
        """
        Receives intercepted credentials from Firefox page and persists them.
        """
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        url = data.get("url")

        if access_token:
            is_ws = bool(url and ("ws://" in url or "wss://" in url or "Chathub" in url or "sydney" in url))
            logger.info(
                "CamoufoxManager: Intercepted fresh token (has_refresh=%s, is_ws=%s). URL: %s",
                bool(refresh_token),
                is_ws,
                (url or "")[:80]
            )
            token_store.update_tokens(access_token, refresh_token, ws_url=url if is_ws else None)
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

        if not self.is_authenticated_page:
            logger.debug("CamoufoxManager: Nudge skipped — browser is unauthenticated (URL: %s)", self.page.url)
            return False

        # Non-blocking: skip if stream_chat_browser holds the lock or is currently streaming
        if self._browser_lock.locked() or self._browser_stream_lock.locked():
            logger.debug("CamoufoxManager: Nudge skipped — browser is busy streaming or locked")
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
                    # Wait up to 5 seconds for token update via nudge keys
                    for _ in range(10):
                        await asyncio.sleep(0.5)
                        if token_store.is_valid and token_store.seconds_remaining > 3000:
                            try:
                                from app.api.chat import reset_ws_circuit_breaker
                                reset_ws_circuit_breaker()
                            except Exception:
                                pass
                            return True

                # If typing didn't trigger token refresh, reload the page to trigger new WebSocket connection
                logger.info("CamoufoxManager: Nudge keys did not refresh token. Reloading page...")
                try:
                    await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception as exc_reload:
                    logger.warning("CamoufoxManager: page.reload failed/timed out: %s. Navigating to chat URL...", exc_reload)
                    await self.page.goto("https://m365.cloud.microsoft/chat", wait_until="domcontentloaded", timeout=45000)

                for _ in range(30):
                    await asyncio.sleep(0.5)
                    if token_store.is_valid and token_store.seconds_remaining > 300:
                        try:
                            from app.api.chat import reset_ws_circuit_breaker
                            reset_ws_circuit_breaker()
                        except Exception:
                            pass
                        return True
        except Exception as exc:
            logger.error("CamoufoxManager: Nudge operation failed: %s", exc)

        return False


camoufox_manager = CamoufoxManager()
