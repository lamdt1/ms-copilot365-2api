import asyncio
import json
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
from app.translator.openai_to_substrate import translate_openai_request
from app.tools.engine import resolve_tool_strategy
from app.substrate.ws_client import SubstrateWSClient
from app.substrate.image import should_generate_image, get_designer_token, fetch_image_as_base64, classify_image_failure
from app.formatters.openai_sse import (
    format_openai_chunk,
    format_openai_done,
    format_openai_response
)
from app.utils import compute_text_delta

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

MAX_RETRIES_DISENGAGED = 3


def _build_retry_ws_url(session_id: str, conversation_id: str) -> str:
    """
    Build a WS URL for retry using the intercepted browser URL as a template.
    Substitutes fresh session/conversation IDs and the current access_token,
    but preserves variants, source, product, agentHost, licenseType, scenario.
    Falls back to build_ws_url if no intercepted URL is available.
    """
    from urllib.parse import urlparse, parse_qs, urlencode
    from app.substrate.payload_builder import build_ws_url

    template = token_store.intercepted_ws_url
    if not template:
        return build_ws_url(
            token_store.oid, token_store.tid,
            token_store.access_token, session_id, conversation_id
        )

    try:
        parsed = urlparse(template)
        params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        # Replace session-specific and auth params
        session_nodash = session_id.replace("-", "")
        params["chatsessionid"] = session_nodash
        params["XRoutingParameterSessionKey"] = session_nodash
        params["clientrequestid"] = session_nodash
        params["X-SessionId"] = session_id
        params["ConversationId"] = conversation_id
        params["access_token"] = token_store.access_token
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(params)}"
    except Exception as exc:
        logger.warning("_build_retry_ws_url: parse error %s, falling back to build_ws_url", exc)
        return build_ws_url(
            token_store.oid, token_store.tid,
            token_store.access_token, session_id, conversation_id
        )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

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

    model = body.get("model", "m365-copilot")
    stream = body.get("stream", False)
    tools = body.get("tools")
    messages = body.get("messages", [])

    # Session management
    persistent_id = request.headers.get("X-M365-Session-Id")
    # Support model suffix :persist
    if not persistent_id and model.endswith(":persist"):
        persistent_id = str(uuid.uuid4())
        model = model.replace(":persist", "")

    session_id, conversation_id, is_start, msg_count = session_manager.get_or_create_context(persistent_id)

    # Translate OpenAI messages → substrate prompt
    final_text, tone = translate_openai_request(body)

    # Detect Image Generation Intent
    generate_images = should_generate_image(final_text, tools)

    # Resolve tool calling strategy
    if generate_images:
        agent_id, augmented_prompt, tool_parser = None, final_text, None
    else:
        agent_id, augmented_prompt, tool_parser = await resolve_tool_strategy(tools, final_text)

    # Map model name → tone override
    tone_override = settings.MODEL_TONE_MAP.get(model)
    if tone_override:
        tone = tone_override

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        return StreamingResponse(
            _stream_response(
                chat_id=chat_id,
                model=model,
                prompt=augmented_prompt,
                tone=tone,
                session_id=session_id,
                conversation_id=conversation_id,
                is_start=is_start,
                agent_id=agent_id,
                tool_parser=tool_parser,
                persistent_id=persistent_id,
                generate_images=generate_images,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
    else:
        return await _non_stream_response(
            chat_id=chat_id,
            model=model,
            prompt=augmented_prompt,
            tone=tone,
            session_id=session_id,
            conversation_id=conversation_id,
            is_start=is_start,
            agent_id=agent_id,
            tool_parser=tool_parser,
            persistent_id=persistent_id,
            generate_images=generate_images,
        )


async def _stream_response(
    chat_id: str,
    model: str,
    prompt: str,
    tone: str,
    session_id: str,
    conversation_id: str,
    is_start: bool,
    agent_id: Optional[str],
    tool_parser,
    persistent_id: Optional[str],
    generate_images: bool = False,
):
    """
    Async generator producing SSE chunks for streaming mode.
    """
    # Initial role delta
    yield format_openai_chunk(chat_id, model, {"role": "assistant", "content": ""})

    retries = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    finish_reason = "stop"
    tool_call_index = 0
    tool_call_id_counter = 0

    image_received = False
    text_buffer = ""

    while retries <= MAX_RETRIES_DISENGAGED:
        should_retry = False

        try:
            await asyncio.wait_for(websocket_semaphore.acquire(), timeout=settings.SEMAPHORE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.warning("chat: Semaphore acquire timeout after %s sec. Returning 429.", settings.SEMAPHORE_TIMEOUT_SEC)
            yield format_openai_chunk(chat_id, model, {
                "content": "\n[Server is busy. Please try again later.]\n"
            })
            yield format_openai_done()
            return

        try:
            # Re-validate token before each attempt (covers token-expired retry edge case)
            if not token_store.is_valid:
                yield format_openai_chunk(chat_id, model, {
                    "content": "\n[Token expired mid-request. Please wait for auto-refresh.]\n"
                })
                break

            client = SubstrateWSClient(
                oid=token_store.oid,
                tid=token_store.tid,
                access_token=token_store.access_token,
                session_id=session_id,
                conversation_id=conversation_id,
            )

            # Wrap entire stream in a global timeout (per-message timeout alone is insufficient
            # for very long context where server may queue requests for extended periods)
            try:
                stream = client.stream_chat(
                    prompt=prompt,
                    tone=tone,
                    agent_id=agent_id,
                    is_start=is_start,
                    generate_images=generate_images,
                )
                async with asyncio.timeout(settings.WS_TIMEOUT_SEC):
                    async for ev_type, payload in stream:
                        if ev_type == "text":
                            delta, text_buffer = compute_text_delta(payload, text_buffer)

                            # Route through tool parser if active
                            if tool_parser:
                                for tc_type, tc_data in tool_parser.feed(delta):
                                    if tc_type == "content":
                                        yield format_openai_chunk(chat_id, model, {"content": tc_data["text"]})
                                        usage["completion_tokens"] += 1
                                    elif tc_type == "tool_name":
                                        call_id = f"call_{uuid.uuid4().hex[:8]}"
                                        yield format_openai_chunk(chat_id, model, {
                                            "tool_calls": [{
                                                "index": tool_call_index,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {"name": tc_data["name"], "arguments": ""}
                                            }]
                                        })
                                        finish_reason = "tool_calls"
                                    elif tc_type == "tool_args":
                                        yield format_openai_chunk(chat_id, model, {
                                            "tool_calls": [{
                                                "index": tool_call_index,
                                                "function": {"arguments": tc_data["arguments"]}
                                            }]
                                        })
                                        tool_call_index += 1
                            else:
                                if delta:
                                    yield format_openai_chunk(chat_id, model, {"content": delta})
                                    usage["completion_tokens"] += 1

                        elif ev_type == "think":
                            yield format_openai_chunk(chat_id, model, {"reasoning_content": payload.get("text", "")})

                        elif ev_type == "image":
                            urls = payload.get("urls", [])
                            if urls:
                                image_received = True
                                try:
                                    token = await get_designer_token()
                                    for url in urls:
                                        md = await fetch_image_as_base64(url, token)
                                        yield format_openai_chunk(chat_id, model, {"content": md})
                                        usage["completion_tokens"] += len(md.split())
                                except Exception as exc:
                                    logger.error("Error fetching images: %s", exc)
                                    yield format_openai_chunk(chat_id, model, {
                                        "content": f"\n[Error fetching image: {str(exc)}]\n"
                                    })

                        elif ev_type == "image_b64":
                            # Base64 image data embedded directly (feature.EnableBase64DataInMessageAnnotations)
                            for b64_data, content_type in payload.get("images", []):
                                image_received = True
                                md = f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                                yield format_openai_chunk(chat_id, model, {"content": md})
                                usage["completion_tokens"] += 1

                        elif ev_type == "disengaged":
                            retries += 1
                            if retries <= MAX_RETRIES_DISENGAGED:
                                logger.warning("chat: Disengaged (attempt %d), re-rolling conversation", retries)
                                conversation_id = str(uuid.uuid4())
                                is_start = True
                                should_retry = True
                                break  # break inner loop, retry in while
                            else:
                                yield format_openai_chunk(chat_id, model, {
                                    "content": f"\n[Disengaged: {payload.get('reason', 'Safety filter triggered')}]"
                                })
                                finish_reason = "stop"

                        elif ev_type == "done":
                            # Extract usage from result if available
                            result = payload
                            if isinstance(result, dict):
                                conv_msgs = result.get("conversationMessages", 0)
                                dea = result.get("deaScore", 0)
                                usage["x_m365_conversation_messages"] = conv_msgs
                                usage["x_m365_dea_score"] = dea
                            break

                        elif ev_type == "error":
                            err_msg = payload.get("message", "Unknown error")
                            if ("connection_closed" in err_msg or "Connection closed" in err_msg) and not text_buffer:
                                logger.warning("chat: Stream direct WS error, waiting 2s then falling back to Camoufox browser...")
                                await asyncio.sleep(2.0)  # Simple backoff
                                from app.browser.camoufox_manager import camoufox_manager
                                browser_gen = camoufox_manager.stream_chat_browser(prompt)
                                try:
                                    async for b_ev_type, b_payload in browser_gen:
                                        if b_ev_type == "text":
                                            delta, text_buffer = compute_text_delta(b_payload, text_buffer)
                                            if delta:
                                                yield format_openai_chunk(chat_id, model, {"content": delta})
                                                usage["completion_tokens"] += 1
                                        elif b_ev_type == "think":
                                            yield format_openai_chunk(chat_id, model, {"reasoning_content": b_payload.get("text", "")})
                                        elif b_ev_type == "image":
                                            urls = b_payload.get("urls", [])
                                            if urls:
                                                image_received = True
                                                try:
                                                    token = await get_designer_token()
                                                    for url in urls:
                                                        md = await fetch_image_as_base64(url, token)
                                                        yield format_openai_chunk(chat_id, model, {"content": md})
                                                        usage["completion_tokens"] += len(md.split())
                                                except Exception as exc:
                                                    logger.error("Error fetching browser fallback image: %s", exc)
                                                    yield format_openai_chunk(chat_id, model, {
                                                        "content": f"\n[Error fetching image: {str(exc)}]\n"
                                                    })
                                        elif b_ev_type == "image_b64":
                                            for b64_data, content_type in b_payload.get("images", []):
                                                image_received = True
                                                md = f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                                                yield format_openai_chunk(chat_id, model, {"content": md})
                                                usage["completion_tokens"] += 1
                                        elif b_ev_type == "done":
                                            break
                                        elif b_ev_type == "error":
                                            yield format_openai_chunk(chat_id, model, {
                                                "content": f"\n[Error: {b_payload.get('message', 'Unknown error')}]"
                                            })
                                            break
                                finally:
                                    await browser_gen.aclose()
                            else:
                                yield format_openai_chunk(chat_id, model, {
                                    "content": f"\n[Error: {err_msg}]"
                                })
                            break

            except asyncio.TimeoutError:
                logger.error("chat: Global WS stream timeout after %s sec", settings.WS_TIMEOUT_SEC)
                if not text_buffer:
                    yield format_openai_chunk(chat_id, model, {
                        "content": "\n[Request timed out. The server took too long to respond.]\n"
                    })

        finally:
            websocket_semaphore.release()

        if not should_retry:
            break

    # Emit image generation failure inline if no image was returned
    if generate_images and not image_received:
        reason = classify_image_failure(text_buffer)
        msg = ""
        if reason == "quota_exceeded":
            msg = "\n[Image generation quota exceeded. Try again tomorrow.]\n"
        elif reason == "capacity":
            msg = "\n[Image generation temporarily unavailable. Try again later.]\n"
        elif reason == "content_filtered":
            msg = "\n[Image blocked by content policy.]\n"

        if msg:
            yield format_openai_chunk(chat_id, model, {"content": msg})
            usage["completion_tokens"] += len(msg.split())

    # Increment session message counter
    if persistent_id:
        session_manager.increment_msg_count(persistent_id)

    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

    # Final chunk with finish_reason and usage
    yield format_openai_chunk(chat_id, model, {}, finish_reason=finish_reason, usage=usage)
    yield format_openai_done()


async def _non_stream_response(
    chat_id: str,
    model: str,
    prompt: str,
    tone: str,
    session_id: str,
    conversation_id: str,
    is_start: bool,
    agent_id: Optional[str],
    tool_parser,
    persistent_id: Optional[str],
    generate_images: bool = False,
):
    """
    Collects the full response and returns a single JSON object.
    """
    full_content = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    finish_reason = "stop"
    image_received = False
    text_buffer = ""

    try:
        await asyncio.wait_for(websocket_semaphore.acquire(), timeout=settings.SEMAPHORE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"message": "Server is busy, too many concurrent requests.", "code": 503}},
            headers={"Retry-After": "10"},
        )

    try:
        # Re-validate token before attempt
        if not token_store.is_valid:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"message": "Token expired. Please wait for auto-refresh.", "code": 503}},
            )

        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        browser_fallback_used = False

        try:
            stream = client.stream_chat(
                prompt=prompt,
                tone=tone,
                agent_id=agent_id,
                is_start=is_start,
                generate_images=generate_images,
            )
            async with asyncio.timeout(settings.WS_TIMEOUT_SEC):
                async for ev_type, payload in stream:
                    if ev_type == "text":
                        delta, text_buffer = compute_text_delta(payload, text_buffer)
                        full_content += delta
                    elif ev_type == "image":
                        urls = payload.get("urls", [])
                        if urls:
                            image_received = True
                            try:
                                token = await get_designer_token()
                                for url in urls:
                                    md = await fetch_image_as_base64(url, token)
                                    full_content += md
                            except Exception as exc:
                                logger.error("Error fetching images in non-stream: %s", exc)
                                full_content += f"\n[Error fetching image: {str(exc)}]\n"
                    elif ev_type == "image_b64":
                        # Base64 image data embedded directly (feature.EnableBase64DataInMessageAnnotations)
                        for b64_data, content_type in payload.get("images", []):
                            image_received = True
                            full_content += f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                    elif ev_type == "done":

                        result = payload
                        if isinstance(result, dict):
                            usage["x_m365_conversation_messages"] = result.get("conversationMessages", 0)
                            usage["x_m365_dea_score"] = result.get("deaScore", 0)
                        break
                    elif ev_type == "error":
                        error_msg = payload.get('message', 'Unknown error')
                        if not full_content and ("Connection closed" in error_msg or "connection_closed" in error_msg):
                            logger.warning("chat: Non-stream direct WS error, falling back to Camoufox browser...")
                            browser_fallback_used = True
                            from app.browser.camoufox_manager import camoufox_manager
                            browser_gen = camoufox_manager.stream_chat_browser(prompt)
                            try:
                                async for b_ev_type, b_payload in browser_gen:
                                    if b_ev_type == "text":
                                        delta, text_buffer = compute_text_delta(b_payload, text_buffer)
                                        full_content += delta
                                    elif b_ev_type == "image":
                                        urls = b_payload.get("urls", [])
                                        if urls:
                                            image_received = True
                                            try:
                                                token = await get_designer_token()
                                                for url in urls:
                                                    md = await fetch_image_as_base64(url, token)
                                                    full_content += md
                                            except Exception as exc:
                                                logger.error("Error fetching browser fallback image: %s", exc)
                                                full_content += f"\n[Error fetching image: {str(exc)}]\n"
                                    elif b_ev_type == "image_b64":
                                        for b64_data, content_type in b_payload.get("images", []):
                                            image_received = True
                                            full_content += f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                                    elif b_ev_type == "done":
                                        break
                                    elif b_ev_type == "error":
                                        break
                            finally:
                                await browser_gen.aclose()
                        # Only raise if we genuinely have no content after all attempts
                        if not full_content:
                            raise HTTPException(
                                status_code=status.HTTP_502_BAD_GATEWAY,
                                detail={
                                    "error": {
                                        "message": f"Substrate service error: {error_msg}",
                                        "type": "api_error",
                                        "code": "substrate_error"
                                    }
                                }
                            )
                        break

        except asyncio.TimeoutError:
            logger.error("chat: Non-stream global WS timeout after %s sec", settings.WS_TIMEOUT_SEC)
            if not full_content:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail={"error": {"message": "Request timed out waiting for Substrate response.", "code": 504}},
                )

    finally:
        websocket_semaphore.release()

    if generate_images and not image_received:
        reason = classify_image_failure(text_buffer)
        if reason == "quota_exceeded":
            full_content += "\n[Image generation quota exceeded. Try again tomorrow.]\n"
        elif reason == "capacity":
            full_content += "\n[Image generation temporarily unavailable. Try again later.]\n"
        elif reason == "content_filtered":
            full_content += "\n[Image blocked by content policy.]\n"

    if persistent_id:
        session_manager.increment_msg_count(persistent_id)

    usage["completion_tokens"] = len(full_content.split())
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

    return JSONResponse(format_openai_response(
        chat_id=chat_id,
        model=model,
        content=full_content,
        finish_reason=finish_reason,
        usage=usage
    ))
