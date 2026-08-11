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

        # Completion marker
        if ftype == 3:
            yield "done", frame.get("result", {})
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

        # 4. Standard delta cursor updates (this contains streaming tokens)
        cursor_text = arg.get("writeAtCursor")
        if cursor_text:
            yield "text", {"text": cursor_text}
            return

        # 5. Fallback check on normal chat messages if text cursor not present
        if arg.get("author") == "bot":
            text = arg.get("text")
            if text:
                yield "text", {"text": text, "is_full": True}
