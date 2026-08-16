"""
OpenAI Responses API (/v1/responses) — thin wrapper over chat completions.
Uses same substrate pipeline and delegates output formatting.
"""
import asyncio
import uuid
from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse

from app.auth import verify_api_key
from app.core.token_store import token_store
from app.core.session_manager import session_manager
from app.core.rate_limiter import websocket_semaphore
from app.translator.openai_to_substrate import translate_openai_request
from app.substrate.ws_client import SubstrateWSClient
from app.config import settings
from app.utils import compute_text_delta

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/v1/responses")
async def openai_responses(request: Request):
    body = await request.json()

    if not token_store.is_valid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"message": "Token not ready", "type": "service_unavailable", "code": 503}}
        )

    model = body.get("model", "m365-copilot")
    session_id, conversation_id, is_start, _ = session_manager.get_or_create_context()

    # Translate request — OpenAI Responses API uses same messages format
    final_text, tone = translate_openai_request(body)
    tone_override = settings.MODEL_TONE_MAP.get(model)
    if tone_override:
        tone = tone_override

    full_content = ""

    try:
        await asyncio.wait_for(websocket_semaphore.acquire(), timeout=settings.SEMAPHORE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"message": "Server is busy, too many concurrent requests.", "code": 503}},
            headers={"Retry-After": "10"},
        )

    try:
        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        async for ev_type, payload in client.stream_chat(prompt=final_text, tone=tone, is_start=is_start):
            if ev_type == "text":
                delta, full_content = compute_text_delta(payload, full_content)
            elif ev_type in ("done", "error"):
                break
    finally:
        websocket_semaphore.release()

    resp_id = f"resp_{uuid.uuid4().hex[:12]}"
    return JSONResponse({
        "id": resp_id,
        "object": "response",
        "model": model,
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_content}]
        }],
        "usage": {
            "input_tokens": 0,
            "output_tokens": len(full_content.split()),
            "total_tokens": len(full_content.split())
        }
    })
