import time
import json
from typing import Optional, List, Dict, Any


def format_openai_chunk(
    chat_id: str,
    model: str,
    delta: Dict[str, Any],
    finish_reason: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None
) -> str:
    """
    Formats a single SSE line for OpenAI completions API:
    data: {"id":..., "object":"chat.completion.chunk", ...}
    """
    payload = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason
            }
        ],
        "usage": usage
    }
    return f"data: {json.dumps(payload)}\n\n"


def format_openai_done() -> str:
    return "data: [DONE]\n\n"


def format_openai_response(
    chat_id: str,
    model: str,
    content: str,
    finish_reason: str = "stop",
    usage: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Formats a non-streaming OpenAI completions response.
    """
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": finish_reason
            }
        ],
        "usage": usage
    }
