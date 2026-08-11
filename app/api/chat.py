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
from app.formatters.openai_sse import (
    format_openai_chunk,
    format_openai_done,
    format_openai_response
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

MAX_RETRIES_DISENGAGED = 3


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

    # Resolve tool calling strategy
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

    while retries <= MAX_RETRIES_DISENGAGED:
        async with websocket_semaphore:
            client = SubstrateWSClient(
                oid=token_store.oid,
                tid=token_store.tid,
                access_token=token_store.access_token,
                session_id=session_id,
                conversation_id=conversation_id,
            )

            async for ev_type, payload in client.stream_chat(
                prompt=prompt,
                tone=tone,
                agent_id=agent_id,
                is_start=is_start,
            ):
                if ev_type == "text":
                    text = payload.get("text", "")

                    # Route through tool parser if active
                    if tool_parser:
                        for tc_type, tc_data in tool_parser.feed(text):
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
                        yield format_openai_chunk(chat_id, model, {"content": text})
                        usage["completion_tokens"] += 1

                elif ev_type == "think":
                    yield format_openai_chunk(chat_id, model, {"reasoning_content": payload.get("text", "")})

                elif ev_type == "disengaged":
                    retries += 1
                    if retries <= MAX_RETRIES_DISENGAGED:
                        logger.warning("chat: Disengaged (attempt %d), re-rolling conversation", retries)
                        conversation_id = str(uuid.uuid4())
                        is_start = True
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
                    yield format_openai_chunk(chat_id, model, {
                        "content": f"\n[Error: {payload.get('message', 'Unknown error')}]"
                    })
                    break
            else:
                # while-else: inner for loop completed normally (no break from disengaged retry)
                break
        # if we broke out for disengaged retry, continue the while loop
        continue

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
):
    """
    Collects the full response and returns a single JSON object.
    """
    full_content = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    finish_reason = "stop"

    async with websocket_semaphore:
        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        async for ev_type, payload in client.stream_chat(
            prompt=prompt,
            tone=tone,
            agent_id=agent_id,
            is_start=is_start,
        ):
            if ev_type == "text":
                full_content += payload.get("text", "")
            elif ev_type == "done":
                result = payload
                if isinstance(result, dict):
                    usage["x_m365_conversation_messages"] = result.get("conversationMessages", 0)
                    usage["x_m365_dea_score"] = result.get("deaScore", 0)
                break
            elif ev_type == "error":
                full_content += f"\n[Error: {payload.get('message', 'Unknown error')}]"
                break

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
