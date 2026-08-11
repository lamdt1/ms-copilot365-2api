import json
import re
from typing import Generator, Tuple, Optional, Dict, Any

# Matches standard tool description injections
TOOL_XML_OPEN = "<tool_call>"
TOOL_XML_CLOSE = "</tool_call>"

# Matches fenced code block variants if they happen, e.g. ```bash \n cmd \n ```
FENCED_CODE_BLOCK_RE = re.compile(r"```(bash|shell|run_command)\n(.*?)```", re.DOTALL)


class ToolCallStreamParser:
    def __init__(self):
        self.buffer = ""
        self.in_tool_call = False
        self.tool_call_buffer = ""
        self.emitted_name = False
        self.call_id = "call_0"
        self.tool_name = ""

    def feed(self, chunk: str) -> Generator[Tuple[str, Optional[Dict[str, Any]]], None, None]:
        """
        Feeds a text delta chunk from Sydney, checks state, and yields:
          - ("content", {"text": chunk}) for normal assistant output.
          - ("tool_name", {"name": tool_name}) when tool tag opens and name is extracted.
          - ("tool_args", {"arguments": json_arguments_str}) when tool tag closes.
        """
        self.buffer += chunk

        while True:
            # ── STATE: OUTSIDE OF TOOL CALL ──────────────────────────────────
            if not self.in_tool_call:
                # Find if open tag appears
                idx = self.buffer.find(TOOL_XML_OPEN)
                if idx == -1:
                    # No open tag. Look for partial open tags to avoid emitting them early
                    partial_len = self._check_partial_tag(self.buffer, TOOL_XML_OPEN)
                    if partial_len > 0:
                        emit_text = self.buffer[:-partial_len]
                        self.buffer = self.buffer[-partial_len:]
                    else:
                        emit_text = self.buffer
                        self.buffer = ""

                    if emit_text:
                        # Check if it has a complete fenced code block we can parse right away
                        # (in case the model didn't use XML tags but used markdown blocks)
                        match = FENCED_CODE_BLOCK_RE.search(emit_text)
                        if match:
                            lang = match.group(1)
                            cmd = match.group(2).strip()
                            yield "tool_name", {"name": "execute_bash"}
                            yield "tool_args", {"arguments": json.dumps({"command": cmd})}
                            # Yield text surrounding the codeblock as normal content
                            before = emit_text[:match.start()]
                            after = emit_text[match.end():]
                            if before:
                                yield "content", {"text": before}
                            if after:
                                yield "content", {"text": after}
                        else:
                            yield "content", {"text": emit_text}
                    break
                else:
                    # Found start of tool tag. Yield whatever came before it.
                    before_text = self.buffer[:idx]
                    if before_text:
                        yield "content", {"text": before_text}

                    # Advance state
                    self.in_tool_call = True
                    self.tool_call_buffer = ""
                    self.emitted_name = False
                    self.tool_name = ""
                    self.buffer = self.buffer[idx + len(TOOL_XML_OPEN):]

            # ── STATE: INSIDE OF TOOL CALL ───────────────────────────────────
            else:
                idx = self.buffer.find(TOOL_XML_CLOSE)
                if idx == -1:
                    # XML tool JSON payload is still streaming in
                    self.tool_call_buffer += self.buffer
                    self.buffer = ""

                    # Try to parse name early from partial json to notify client
                    if not self.emitted_name:
                        name = self._extract_name_early(self.tool_call_buffer)
                        if name:
                            self.tool_name = name
                            self.emitted_name = True
                            yield "tool_name", {"name": name}
                    break
                else:
                    # Found end of tool tag.
                    self.tool_call_buffer += self.buffer[:idx]
                    self.buffer = self.buffer[idx + len(TOOL_XML_CLOSE):]

                    # Parse final payload
                    payload = self.tool_call_buffer.strip()
                    try:
                        data = json.loads(payload)
                        name = data.get("name", self.tool_name or "unknown")
                        args = data.get("arguments", {})
                        if isinstance(args, dict):
                            args_str = json.dumps(args)
                        else:
                            args_str = str(args)

                        if not self.emitted_name:
                            yield "tool_name", {"name": name}
                        yield "tool_args", {"arguments": args_str}
                    except json.JSONDecodeError:
                        # Fallback parsing in case of bad JSON formatting
                        name = self.tool_name or "unknown"
                        yield "tool_name", {"name": name}
                        yield "tool_args", {"arguments": json.dumps({"raw_response": payload})}

                    self.in_tool_call = False
                    self.tool_call_buffer = ""
                    self.emitted_name = False
                    self.tool_name = ""

    def _check_partial_tag(self, s: str, tag: str) -> int:
        """
        Returns length of suffix in s that partially matches tag.
        """
        for i in range(1, len(tag)):
            if s.endswith(tag[:i]):
                return i
        return 0

    def _extract_name_early(self, buffered: str) -> Optional[str]:
        """
        Attempts to read tool name from partial JSON string.
        """
        # Matches e.g. "name": "something" or "name":"something"
        match = re.search(r'"name"\s*:\s*"([^"]+)"', buffered)
        if match:
            return match.group(1)
        return None
