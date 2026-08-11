import asyncio
import json
import logging
from typing import AsyncGenerator, List, Optional, Callable

import websockets

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
        ws_factory: Optional[Callable] = None
    ):
        self.oid = oid
        self.tid = tid
        self.access_token = access_token
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.ws_factory = ws_factory or websockets.connect
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
        url = build_ws_url(
            self.oid,
            self.tid,
            self.access_token,
            self.session_id,
            self.conversation_id
        )

        logger.debug("SubstrateWSClient: Connecting to %s", url.split("?")[0])

        try:
            async with self.ws_factory(
                url,
                origin="https://m365.cloud.microsoft",
                user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
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

                    # Feed parser and yield events
                    for ev_type, payload in parser.feed(msg):
                        if ev_type == "ping":
                            # Respond with type 6 heartbeat payload
                            await ws.send(json.dumps({"type": 6}) + RECORD_SEPARATOR)
                            logger.debug("SubstrateWSClient: Responded to ping heartbeat")
                        else:
                            yield ev_type, payload

                        if ev_type == "done":
                            return

        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("SubstrateWSClient: Connection closed: %s", exc)
            yield "error", {"message": f"connection_closed: {exc.code} {exc.reason}"}
        except Exception as exc:
            logger.error("SubstrateWSClient: WS unexpected exception: %s", exc)
            yield "error", {"message": str(exc)}
        finally:
            self.ws = None
            logger.debug("SubstrateWSClient: Connection finalized")
