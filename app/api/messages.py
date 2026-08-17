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
from app.utils import compute_text_delta, get_external_base_url
from app.substrate.image import (
    get_designer_token,
    save_image_locally,
    save_b64_image_locally,
    fetch_image_as_base64
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
            _stream_anthropic(msg_id, model, final_text, tone, session_id, conversation_id, is_start, get_external_base_url(request)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        return await _non_stream_anthropic(msg_id, model, final_text, tone, session_id, conversation_id, is_start, get_external_base_url(request))


async def _stream_anthropic(msg_id, model, prompt, tone, session_id, conversation_id, is_start, base_url):
    yield build_message_start(msg_id, model)
    yield build_content_block_start(0)

    text_buffer = ""
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
                delta, text_buffer = compute_text_delta(payload, text_buffer)
                if delta:
                    yield build_content_block_delta(delta)
            elif ev_type == "image":
                urls = payload.get("urls", [])
                if urls:
                    try:
                        token = await get_designer_token()
                        for url in urls:
                            filename = await save_image_locally(url, token)
                            if filename:
                                md = f"\n![Generated Image]({base_url}/images/{filename})\n"
                            else:
                                md = await fetch_image_as_base64(url, token)
                            yield build_content_block_delta(md)
                    except Exception as exc:
                        yield build_content_block_delta(f"\n[Error fetching image: {exc}]\n")
            elif ev_type == "image_b64":
                for b64_data, content_type in payload.get("images", []):
                    filename = save_b64_image_locally(b64_data, content_type)
                    if filename:
                        md = f"\n![Generated Image]({base_url}/images/{filename})\n"
                    else:
                        md = f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                    yield build_content_block_delta(md)
            elif ev_type == "done":
                break
            elif ev_type == "error":
                yield build_content_block_delta(f"\n[Error: {payload.get('message', '')}]")
                break

    yield build_content_block_stop(0)
    yield build_message_delta("end_turn")
    yield build_message_stop()


async def _non_stream_anthropic(msg_id, model, prompt, tone, session_id, conversation_id, is_start, base_url):
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
                delta, full_content = compute_text_delta(payload, full_content)
            elif ev_type == "image":
                urls = payload.get("urls", [])
                if urls:
                    try:
                        token = await get_designer_token()
                        for url in urls:
                            filename = await save_image_locally(url, token)
                            if filename:
                                full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                            else:
                                full_content += await fetch_image_as_base64(url, token)
                    except Exception as exc:
                        full_content += f"\n[Error fetching image: {exc}]\n"
            elif ev_type == "image_b64":
                for b64_data, content_type in payload.get("images", []):
                    filename = save_b64_image_locally(b64_data, content_type)
                    if filename:
                        full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                    else:
                        full_content += f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
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
