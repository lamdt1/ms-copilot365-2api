import time
import uuid
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.core.token_store import token_store
from app.core.rate_limiter import websocket_semaphore
from app.substrate.ws_client import SubstrateWSClient
from app.substrate.image import (
    get_designer_token,
    fetch_raw_image_base64,
    classify_image_failure
)
from app.browser.camoufox_manager import camoufox_manager

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., description="A text description of the desired image(s).")
    model: Optional[str] = Field("dall-e-3", description="The model to use for image generation.")
    n: Optional[int] = Field(1, ge=1, le=10, description="The number of images to generate.")
    quality: Optional[str] = Field("standard", description="The quality of the image.")
    response_format: Optional[str] = Field("url", description="The format in which generated images are returned. Must be 'url' or 'b64_json'.")
    size: Optional[str] = Field("1024x1024", description="The size of the generated images.")
    style: Optional[str] = Field("vivid", description="The style of the generated images.")
    user: Optional[str] = Field(None, description="A unique identifier representing your end-user.")


@router.post("/v1/images/generations")
async def generate_images(request: ImageGenerationRequest):
    # Validate token readiness
    if not token_store.is_valid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "message": "Token not ready. Please login via noVNC at http://localhost:6080 or wait for auto-refresh.",
                    "type": "service_unavailable",
                    "code": 503,
                    "retry_after": 10
                }
            }
        )

    session_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())

    image_urls: List[str] = []
    text_buffer = ""

    async with websocket_semaphore:
        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        ws_error_occurred = False
        async for ev_type, payload in client.stream_chat(
            prompt=request.prompt,
            tone="magic",
            is_start=True,
            generate_images=True,
        ):
            if ev_type == "text":
                text_buffer += payload.get("text", "")
            elif ev_type == "image":
                urls = payload.get("urls", [])
                image_urls.extend(urls)
            elif ev_type == "done":
                break
            elif ev_type == "error":
                error_msg = payload.get("message", "")
                logger.error("Error during image generation stream: %s", payload)
                # Fallback to Camoufox browser when WS connection fails (type:7)
                if not image_urls and ("Connection closed" in error_msg or "connection_closed" in error_msg):
                    ws_error_occurred = True
                break

    # Browser fallback: submit prompt via Camoufox and parse image events from browser WS
    if ws_error_occurred and not image_urls:
        logger.warning("images: Direct WS failed, falling back to Camoufox browser stream...")
        browser_gen = camoufox_manager.stream_chat_browser(request.prompt)
        try:
            async for b_ev_type, b_payload in browser_gen:
                if b_ev_type == "text":
                    text_buffer += b_payload.get("text", "")
                elif b_ev_type == "image":
                    image_urls.extend(b_payload.get("urls", []))
                elif b_ev_type == "done":
                    break
                elif b_ev_type == "error":
                    logger.error("images: Camoufox fallback error: %s", b_payload)
                    break
        finally:
            await browser_gen.aclose()

    if image_urls:
        designer_token = await get_designer_token()
        if designer_token is None:
            logger.warning("images: designer token unavailable, will try fallback auth for image fetch")

        data_list: List[Dict[str, str]] = []
        for url in image_urls[:request.n]:
            try:
                b64_str, content_type = await fetch_raw_image_base64(url, designer_token)
                if request.response_format == "b64_json":
                    data_list.append({"b64_json": b64_str})
                else:
                    data_list.append({"url": f"data:{content_type};base64,{b64_str}"})
            except Exception as exc:
                logger.error("Failed to fetch image artifact %s: %s", url, exc)

        if data_list:
            return JSONResponse({
                "created": int(time.time()),
                "data": data_list
            })

    # If 0 images returned, classify failure reason
    reason = classify_image_failure(text_buffer)
    if reason == "quota_exceeded":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "message": "Image generation quota exceeded for today. Please try again tomorrow.",
                    "type": "requests_error",
                    "code": "quota_exceeded"
                }
            }
        )
    elif reason == "content_filtered":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "message": "Your image prompt was blocked by safety policy.",
                    "type": "invalid_request_error",
                    "code": "content_policy_violation"
                }
            }
        )
    elif reason == "capacity":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "message": "Image generation service is temporarily unavailable. Please try again later.",
                    "type": "service_unavailable",
                    "code": "capacity_exceeded"
                }
            }
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "message": "No image was returned by backend model.",
                    "type": "api_error",
                    "code": "image_generation_failed"
                }
            }
        )
