import json
from typing import Dict, Any


def format_anthropic_event(event: str, data: Dict[str, Any]) -> str:
    """
    Formats a single SSE line for Anthropic messages API.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def build_message_start(msg_id: str, model: str) -> str:
    return format_anthropic_event("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    })


def build_content_block_start(index: int = 0) -> str:
    return format_anthropic_event("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""}
    })


def build_tool_use_block_start(index: int, tool_use_id: str, name: str) -> str:
    return format_anthropic_event("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {
            "type": "tool_use",
            "id": tool_use_id,
            "name": name,
            "input": {}
        }
    })


def build_input_json_delta(partial_json: str, index: int = 0) -> str:
    return format_anthropic_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial_json}
    })


def build_content_block_delta(text: str, index: int = 0) -> str:
    return format_anthropic_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text}
    })


def build_content_block_stop(index: int = 0) -> str:
    return format_anthropic_event("content_block_stop", {
        "type": "content_block_stop",
        "index": index
    })


def build_message_delta(stop_reason: str = "end_turn") -> str:
    return format_anthropic_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason},
        "usage": {"output_tokens": 0}
    })


def build_message_stop() -> str:
    return format_anthropic_event("message_stop", {
        "type": "message_stop"
    })
