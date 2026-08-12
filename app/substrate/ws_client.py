import asyncio
import json
import logging
from typing import AsyncGenerator, List, Optional, Callable

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, ConnectionClosedError

from app.substrate.payload_builder import build_ws_url, build_chat_invocation, build_metrics_frame
from app.substrate.turn_parser import TurnParser, RECORD_SEPARATOR

logger = logging.getLogger(__name__)


class SubstrateWSClient:
    def __init__(
        self,
        oid: str,
        tid: str,
        access_token: str,
        session_id: str,
        conversation_id: str,
        ws_factory: Optional[Callable] = None,
        ws_url_override: Optional[str] = None,
        cookie_header: Optional[str] = None,
    ):
        self.oid = oid
        self.tid = tid
        self.access_token = access_token
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.ws_factory = ws_factory or websockets.connect
        self.ws_url_override = ws_url_override
        self.cookie_header = cookie_header or ""
        self.ws = None

    async def stream_chat(
        self,
        prompt: str,
        tone: str = "magic",
        agent_id: Optional[str] = None,
        is_start: bool = True,
        timeout_sec: float = 120.0,
        generate_images: bool = False
    ) -> AsyncGenerator[tuple[str, dict], None]:
        """
        Connects to Substrate WebSocket and streams parsed chat events.
        """
        url = self.ws_url_override or build_ws_url(
            self.oid,
            self.tid,
            self.access_token,
            self.session_id,
            self.conversation_id
        )

        logger.debug("SubstrateWSClient: Connecting to %s", url.split("?")[0])

        # Build extra headers (include cookies if available for authenticated retry)
        extra_headers = {}
        if self.cookie_header:
            extra_headers["Cookie"] = self.cookie_header
            logger.debug("SubstrateWSClient: Using %d cookie chars from browser", len(self.cookie_header))

        try:
            async with self.ws_factory(
                url,
                origin="https://m365.cloud.microsoft",
                user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
                additional_headers=extra_headers or None,
            ) as ws:
                self.ws = ws

                # 1. Perform SignalR Handshake
                handshake = {"protocol": "json", "version": 1}
                await ws.send(json.dumps(handshake) + RECORD_SEPARATOR)

                # Wait for Handshake ACK
                parser = TurnParser()
                ack_received = False

                while not ack_received:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    for ev_type, payload in parser.feed(msg):
                        if ev_type == "handshake":
                            ack_received = True
                            break

                logger.debug("SubstrateWSClient: SignalR Handshake complete")

                # 1b. Send SignalR Ping (type:6) — browser always does this before chat invocation
                await ws.send(json.dumps({"type": 6}) + RECORD_SEPARATOR)
                logger.debug("SubstrateWSClient: Sent initial Ping (type:6)")

                # 2. Send Chat Invocation & Metrics frames combined
                chat_frame = build_chat_invocation(
                    prompt=prompt,
                    conversation_id=self.conversation_id,
                    tone=tone,
                    agent_id=agent_id,
                    is_start=is_start,
                    generate_images=generate_images
                )
                metrics_frame = build_metrics_frame()

                payload = (
                    json.dumps(chat_frame) + RECORD_SEPARATOR +
                    json.dumps(metrics_frame) + RECORD_SEPARATOR
                )
                await ws.send(payload)
                logger.debug("SubstrateWSClient: Sent Chat Invocation & Metrics frames")

                # 3. Read Stream
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=timeout_sec)
                    except asyncio.TimeoutError:
                        logger.error("SubstrateWSClient: Read timed out after %d seconds", timeout_sec)
                        yield "error", {"message": "substrate_timeout"}
                        break

                    if not msg:
                        break

                    logger.info("SubstrateWSClient: Raw WS message: %r", msg[:500])

                    # Feed parser and yield events
                    for ev_type, payload in parser.feed(msg):
                        if ev_type == "ping":
                            # Respond with type 6 heartbeat payload
                            await ws.send(json.dumps({"type": 6}) + RECORD_SEPARATOR)
                            logger.debug("SubstrateWSClient: Responded to ping heartbeat")
                        else:
                            if ev_type == "error" and "Connection closed with an error" in payload.get("message", ""):
                                logger.warning("SubstrateWSClient: SignalR Type 7 session error detected. Triggering token nudge refresh...")
                                try:
                                    from app.browser.camoufox_manager import camoufox_manager
                                    asyncio.create_task(camoufox_manager.nudge_refresh())
                                except Exception as exc:
                                    logger.error("SubstrateWSClient: Failed to schedule nudge refresh: %s", exc)
                            yield ev_type, payload

                        if ev_type == "done":
                            return

        except (ConnectionClosedOK, ConnectionClosedError, ConnectionClosed) as exc:
            rcvd_code = exc.rcvd.code if getattr(exc, "rcvd", None) else getattr(exc, "code", 1000)
            rcvd_reason = exc.rcvd.reason if getattr(exc, "rcvd", None) else getattr(exc, "reason", "")
            if isinstance(exc, ConnectionClosedOK) or rcvd_code in (1000, 1001):
                logger.debug("SubstrateWSClient: Connection closed cleanly: %s", exc)
            else:
                logger.warning("SubstrateWSClient: Connection closed with error: %s", exc)
                yield "error", {"message": f"connection_closed: {rcvd_code} {rcvd_reason}"}
        except Exception as exc:
            logger.error("SubstrateWSClient: WS unexpected exception: %s", exc)
            yield "error", {"message": str(exc)}
        finally:
            self.ws = None
            logger.debug("SubstrateWSClient: Connection finalized")
