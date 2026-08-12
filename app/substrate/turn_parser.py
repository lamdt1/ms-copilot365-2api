import json
import logging
from typing import Generator, Tuple, Optional

logger = logging.getLogger(__name__)

RECORD_SEPARATOR = "\x1e"


class TurnParser:
    def __init__(self):
        self.buffer = ""

    def feed(self, chunk: str) -> Generator[Tuple[str, dict], None, None]:
        """
        Feeds incoming WebSocket packet data, splits by Record Separator (0x1E),
        and yields parsed (event_type, payload) tuples.
        """
        self.buffer += chunk
        while RECORD_SEPARATOR in self.buffer:
            frame_str, self.buffer = self.buffer.split(RECORD_SEPARATOR, 1)
            frame_str = frame_str.strip()
            if not frame_str:
                continue

            try:
                frame = json.loads(frame_str)
                yield from self._parse_frame(frame)
            except json.JSONDecodeError as exc:
                logger.error("TurnParser: JSON decode error for frame '%s': %s", frame_str, exc)

    def _parse_frame(self, frame: dict) -> Generator[Tuple[str, dict], None, None]:
        """
        Interpret SignalR frame target and parameters.
        """
        ftype = frame.get("type")

        # Heartbeat Ping from Server
        if ftype == 6:
            yield "ping", {}
            return

        # Handshake / Empty response
        if not ftype or ftype == 0:
            yield "handshake", frame
            return

        # Completion marker (direct WS path)
        if ftype == 3:
            yield "done", frame.get("result", {})
            return

        # Browser WS stream completion (SignalR type 2 = StreamItemMessage / invocation result)
        # The M365 browser signals end-of-stream with type:2 containing the final item
        if ftype == 2:
            item = frame.get("item", {})
            if isinstance(item, dict):
                # Use firstNewMessageIndex to look only at current turn's messages
                messages = item.get("messages", [])
                first_new = item.get("firstNewMessageIndex", 0)
                new_messages = messages[first_new:] if first_new and first_new < len(messages) else messages
                skip_types = ("Progress", "InternalSearchQuery", "InternalSearchResult", "Disengaged")
                # Find the LAST bot message with real text
                for msg in reversed(new_messages):
                    if msg.get("author") == "bot" and msg.get("text"):
                        t = msg["text"]
                        if not t.startswith("{") and msg.get("messageType") not in skip_types:
                            logger.info("TurnParser: Extracted bot text from type:2 item (%d chars)", len(t))
                            yield "text", {"text": t, "is_full": True}
                            break
            yield "done", item if isinstance(item, dict) else {}
            return

        # Error frame from server (SignalR type 7)
        if ftype == 7:
            error_msg = frame.get("error", "Substrate SignalR session error")
            yield "error", {"message": error_msg}
            return

        # Core stream message updates
        if ftype == 1 and frame.get("target") == "update":
            arguments = frame.get("arguments", [])
            for arg in arguments:
                yield from self._parse_argument(arg)

    def _parse_argument(self, arg: dict) -> Generator[Tuple[str, dict], None, None]:
        """
        Parses Sydney/Substrate update payload message type structures.
        """
        msg_type = arg.get("messageType")

        # 1. Check for disengaged (safety filter triggered)
        if msg_type == "Disengaged" or arg.get("offense") == "Offensive":
            yield "disengaged", {
                "reason": arg.get("text", "Safety filter triggered."),
                "dea_score": arg.get("deaScore", 1.0)
            }
            return

        # 2. Thinking progress state
        if msg_type == "Progress":
            yield "think", {"text": arg.get("text", "")}
            return

        # 3. Designer Images
        if msg_type == "GraphicArt":
            # Extract designer URL
            att = arg.get("attachments", [])
            image_urls = []
            for item in att:
                if item.get("contentType", "").startswith("image/"):
                    image_urls.append(item.get("contentUrl"))
            if image_urls:
                yield "image", {"urls": image_urls}
            return

        # 4a. Browser WS: messages array format — author/text are NESTED in messages[0]
        #     e.g. {"messages": [{"author": "bot", "text": "Hi! Nice to meet you.", ...}]}
        messages = arg.get("messages")
        if messages and isinstance(messages, list):
            skip_types = ("Progress", "InternalSearchQuery", "InternalSearchResult", "Disengaged")
            for msg in messages:
                if msg.get("author") == "bot" and msg.get("text"):
                    t = msg["text"]
                    if msg.get("messageType") not in skip_types and not t.startswith("{"):
                        logger.info("TurnParser: is_full text from nested messages (%d chars), starts=%r",
                                    len(t), t[:40])
                        yield "text", {"text": t, "is_full": True}
                        return
            return  # messages frame but no valid bot text — skip

        # 4b. Direct WS: flat format — author/text are at arg level
        #     e.g. {"author": "bot", "text": "Hi!", ...}
        if arg.get("author") == "bot":
            text = arg.get("text")
            if text:
                logger.info("TurnParser: is_full text from flat arg (%d chars), starts=%r", len(text), text[:40])
                yield "text", {"text": text, "is_full": True}
                return

        # 5. Standard delta cursor updates (streaming tokens, may be padded)
        cursor_text = arg.get("writeAtCursor")
        if cursor_text:
            yield "text", {"text": cursor_text}
            return
