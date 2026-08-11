import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse

from app.auth import verify_api_key
from app.config import settings
from app.core.token_store import token_store
from app.core.session_manager import session_manager
from app.core.rate_limiter import websocket_semaphore
from app.translator.anthropic_to_substrate import translate_anthropic_request
from app.substrate.ws_client import SubstrateWSClient
from app.formatters.anthropic_sse import (
    build_message_start,
    build_content_block_start,
    build_content_block_delta,
    build_content_block_stop,
    build_message_delta,
    build_message_stop,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()

    if not token_store.is_valid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "message": "Token not ready. Please login via noVNC or wait for auto-refresh.",
                    "type": "service_unavailable",
                    "code": 503
                }
            }
        )

    model = body.get("model", "m365-copilot")
    stream = body.get("stream", False)

    session_id, conversation_id, is_start, _ = session_manager.get_or_create_context()

    final_text, tone = translate_anthropic_request(body)
    tone_override = settings.MODEL_TONE_MAP.get(model)
    if tone_override:
        tone = tone_override

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    if stream:
        return StreamingResponse(
            _stream_anthropic(msg_id, model, final_text, tone, session_id, conversation_id, is_start),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        return await _non_stream_anthropic(msg_id, model, final_text, tone, session_id, conversation_id, is_start)


async def _stream_anthropic(msg_id, model, prompt, tone, session_id, conversation_id, is_start):
    yield build_message_start(msg_id, model)
    yield build_content_block_start(0)

    async with websocket_semaphore:
        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        async for ev_type, payload in client.stream_chat(prompt=prompt, tone=tone, is_start=is_start):
            if ev_type == "text":
                yield build_content_block_delta(payload.get("text", ""))
            elif ev_type == "done":
                break
            elif ev_type == "error":
                yield build_content_block_delta(f"\n[Error: {payload.get('message', '')}]")
                break

    yield build_content_block_stop(0)
    yield build_message_delta("end_turn")
    yield build_message_stop()


async def _non_stream_anthropic(msg_id, model, prompt, tone, session_id, conversation_id, is_start):
    full_content = ""

    async with websocket_semaphore:
        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        async for ev_type, payload in client.stream_chat(prompt=prompt, tone=tone, is_start=is_start):
            if ev_type == "text":
                full_content += payload.get("text", "")
            elif ev_type == "done":
                break

    return JSONResponse({
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": full_content}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": len(full_content.split())}
    })
