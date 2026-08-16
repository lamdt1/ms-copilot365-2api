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

                # Extract image URLs/data from GraphicArt messages (yield before done)
                for msg in new_messages:
                    is_graphic = (
                        msg.get("messageType") == "GraphicArt"
                        or msg.get("contentType") == "GraphicArt"
                    )
                    if is_graphic:
                        logger.debug("TurnParser: type:2 GraphicArt msg keys: %s", list(msg.keys()))
                        image_urls = []
                        image_b64 = []

                        # Format A: attachments[].contentUrl
                        for a in msg.get("attachments", []):
                            ct = a.get("contentType", "")
                            if ct.startswith("image/") and a.get("contentUrl"):
                                image_urls.append(a["contentUrl"])
                            for k in ("content", "data", "base64"):
                                if a.get(k) and not a.get("contentUrl"):
                                    image_b64.append((a[k], ct or "image/png"))

                        # Format B: contentGenerationProgressList[].ImageReferenceUrls
                        for prog in msg.get("contentGenerationProgressList", []):
                            image_urls.extend(prog.get("ImageReferenceUrls", []))
                            for k in ("base64", "data", "imageData", "ImageData"):
                                if prog.get(k):
                                    image_b64.append((prog[k], prog.get("contentType", "image/png")))

                        # Format C: messageAnnotations / imageAnnotations
                        for ann_key in ("messageAnnotations", "imageAnnotations", "annotations"):
                            for ann in msg.get(ann_key, []):
                                ann_type = ann.get("type", "")
                                if "image" in ann_type.lower() or ann.get("contentType", "").startswith("image/"):
                                    logger.debug("TurnParser: type:2 image annotation keys: %s", list(ann.keys()))
                                    url = ann.get("contentUrl") or ann.get("url")
                                    if url:
                                        image_urls.append(url)
                                    for k in ("content", "data", "base64", "imageData"):
                                        if ann.get(k):
                                            image_b64.append((ann[k], ann.get("contentType", "image/png")))

                        if image_urls:
                            logger.debug("TurnParser: Extracted %d image URL(s) from type:2 GraphicArt", len(image_urls))
                            yield "image", {"urls": [u for u in image_urls if u]}
                        elif image_b64:
                            logger.debug("TurnParser: Extracted %d base64 image(s) from type:2 GraphicArt", len(image_b64))
                            yield "image_b64", {"images": image_b64}

                # Find the LAST bot message with real text
                for msg in reversed(new_messages):
                    if msg.get("author") == "bot" and msg.get("text"):
                        t = msg["text"]
                        if not t.startswith("{") and msg.get("messageType") not in skip_types:
                            logger.debug("TurnParser: Extracted bot text from type:2 item (%d chars)", len(t))
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

        # 3. Designer Images — multiple formats:
        # Format A (direct WS): messageType=GraphicArt with attachments[].contentUrl
        # Format B (browser): contentType=GraphicArt with contentGenerationProgressList[].ImageReferenceUrls
        # Format C (new): messageAnnotations or imageAnnotations with base64 data (feature.EnableBase64DataInMessageAnnotations)
        if msg_type == "GraphicArt" or arg.get("contentType") == "GraphicArt":
            logger.debug("TurnParser: GraphicArt arg keys: %s", list(arg.keys()))
            logger.debug("TurnParser: GraphicArt full arg: %s", json.dumps(arg, default=str)[:2000])
            image_urls = []
            image_b64 = []

            # Format A: attachments[].contentUrl
            for item in arg.get("attachments", []):
                ct = item.get("contentType", "")
                if ct.startswith("image/"):
                    url = item.get("contentUrl")
                    if url:
                        image_urls.append(url)
                # Format C-variant: attachment with base64 data inline
                b64 = item.get("content") or item.get("data") or item.get("base64")
                if b64 and not url:
                    image_b64.append((b64, ct or "image/png"))

            # Format B: contentGenerationProgressList[].ImageReferenceUrls
            for prog in arg.get("contentGenerationProgressList", []):
                image_urls.extend(prog.get("ImageReferenceUrls", []))
                # Also check for base64 inside progress items
                for k in ("base64", "data", "imageData", "ImageData"):
                    if prog.get(k):
                        image_b64.append((prog[k], prog.get("contentType", "image/png")))

            # Format C: messageAnnotations / imageAnnotations (EnableBase64DataInMessageAnnotations)
            for ann_key in ("messageAnnotations", "imageAnnotations", "annotations"):
                for ann in arg.get(ann_key, []):
                    ann_type = ann.get("type", "")
                    if "image" in ann_type.lower() or ann.get("contentType", "").startswith("image/"):
                        logger.debug("TurnParser: Found image annotation: %s", list(ann.keys()))
                        url = ann.get("contentUrl") or ann.get("url")
                        if url:
                            image_urls.append(url)
                        for k in ("content", "data", "base64", "imageData"):
                            if ann.get(k):
                                image_b64.append((ann[k], ann.get("contentType", "image/png")))

            if image_urls:
                yield "image", {"urls": [u for u in image_urls if u]}
            elif image_b64:
                yield "image_b64", {"images": image_b64}
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
                        logger.debug("TurnParser: is_full text from nested messages (%d chars), starts=%r",
                                    len(t), t[:40])
                        yield "text", {"text": t, "is_full": True}
                        return
            return  # messages frame but no valid bot text — skip

        # 4b. Direct WS: flat format — author/text are at arg level
        #     e.g. {"author": "bot", "text": "Hi!", ...}
        if arg.get("author") == "bot":
            text = arg.get("text")
            if text:
                logger.debug("TurnParser: is_full text from flat arg (%d chars), starts=%r", len(text), text[:40])
                yield "text", {"text": text, "is_full": True}
                return

        # 5. Standard delta cursor updates (streaming tokens, may be padded)
        cursor_text = arg.get("writeAtCursor")
        if cursor_text:
            yield "text", {"text": cursor_text}
            return
