import json
import uuid
import logging
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse

from app.auth import verify_api_key
from app.config import settings
from app.core.token_store import token_store
from app.core.session_manager import session_manager
from app.core.rate_limiter import websocket_semaphore
from app.translator.anthropic_to_substrate import translate_anthropic_request
from app.tools.engine import resolve_tool_strategy, needs_auto_bash
from app.substrate.ws_client import SubstrateWSClient
from app.formatters.anthropic_sse import (
    build_message_start,
    build_content_block_start,
    build_tool_use_block_start,
    build_content_block_delta,
    build_input_json_delta,
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
    tools = body.get("tools", [])
    messages = body.get("messages", [])

    session_id, conversation_id, is_start, _ = session_manager.get_or_create_context()

    final_text, tone = translate_anthropic_request(body)
    tone_override = settings.MODEL_TONE_MAP.get(model)
    if tone_override:
        tone = tone_override

    # ── Auto-bash shortcut ─────────────────────────────────────────────────
    # When bash tool is available and no file content exists yet, M365 would
    # refuse filesystem access. Instead, return a bash tool_use directly so
    # Claude Code can explore the project and send back real file content.
    if needs_auto_bash(tools, messages):
        logger.info("auto-bash: returning initial bash tool_use (bypass M365 refusal)")
        call_id = f"toolu_{uuid.uuid4().hex[:12]}"
        bash_cmd = (
            "pwd && echo '---FILES---' && "
            "find . -maxdepth 4 -type f \\( "
            "-name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' "
            "-o -name '*.vue' -o -name '*.go' -o -name '*.rs' -o -name '*.java' "
            "-o -name '*.cpp' -o -name '*.c' -o -name '*.rb' -o -name '*.php' "
            "\\) 2>/dev/null | grep -v node_modules | grep -v '.git' | head -80"
        )
        if stream:
            return StreamingResponse(
                _stream_auto_bash(msg_id, model, call_id, bash_cmd),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )
        else:
            return _non_stream_auto_bash(msg_id, model, call_id, bash_cmd)
    # ──────────────────────────────────────────────────────────────────────

    # Resolve tool strategy (XML injection + stream parser)
    _agent_id, augmented_prompt, tool_parser = await resolve_tool_strategy(tools, final_text)

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    base_url = get_external_base_url(request)

    if stream:
        return StreamingResponse(
            _stream_anthropic(msg_id, model, augmented_prompt, tone, session_id, conversation_id, is_start, base_url, tool_parser),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        return await _non_stream_anthropic(msg_id, model, augmented_prompt, tone, session_id, conversation_id, is_start, base_url, tool_parser)



async def _stream_anthropic(msg_id, model, prompt, tone, session_id, conversation_id, is_start, base_url, tool_parser=None):
    yield build_message_start(msg_id, model)

    text_buffer = ""
    tool_call_index = 0
    has_tool_call = False
    text_block_open = False

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
                    if tool_parser:
                        for tc_type, tc_data in tool_parser.feed(delta):
                            if tc_type == "content":
                                if not text_block_open:
                                    yield build_content_block_start(tool_call_index)
                                    text_block_open = True
                                yield build_content_block_delta(tc_data["text"], tool_call_index)
                            elif tc_type == "tool_name":
                                # Close text block if open
                                if text_block_open:
                                    yield build_content_block_stop(tool_call_index)
                                    tool_call_index += 1
                                    text_block_open = False
                                call_id = f"toolu_{uuid.uuid4().hex[:12]}"
                                yield build_tool_use_block_start(tool_call_index, call_id, tc_data["name"])
                                has_tool_call = True
                            elif tc_type == "tool_args":
                                yield build_input_json_delta(tc_data["arguments"], tool_call_index)
                                yield build_content_block_stop(tool_call_index)
                                tool_call_index += 1
                    else:
                        if not text_block_open:
                            yield build_content_block_start(tool_call_index)
                            text_block_open = True
                        yield build_content_block_delta(delta)

            elif ev_type == "image":
                urls = payload.get("urls", [])
                if urls:
                    try:
                        token = await get_designer_token()
                        tasks = [save_image_locally(url, token) for url in urls]
                        filenames = await asyncio.gather(*tasks, return_exceptions=True)
                        for url, filename in zip(urls, filenames):
                            if isinstance(filename, Exception) or not filename:
                                md = await fetch_image_as_base64(url, token)
                            else:
                                md = f"\n![Generated Image]({base_url}/images/{filename})\n"
                            if not text_block_open:
                                yield build_content_block_start(tool_call_index)
                                text_block_open = True
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
                    if not text_block_open:
                        yield build_content_block_start(tool_call_index)
                        text_block_open = True
                    yield build_content_block_delta(md)
            elif ev_type == "done":
                break
            elif ev_type == "error":
                if not text_block_open:
                    yield build_content_block_start(tool_call_index)
                    text_block_open = True
                yield build_content_block_delta(f"\n[Error: {payload.get('message', '')}]")
                break

    if text_block_open:
        yield build_content_block_stop(tool_call_index)

    stop_reason = "tool_use" if has_tool_call else "end_turn"
    yield build_message_delta(stop_reason)
    yield build_message_stop()


async def _non_stream_anthropic(msg_id, model, prompt, tone, session_id, conversation_id, is_start, base_url, tool_parser=None):
    full_text = ""
    tool_calls = []  # list of {id, name, arguments_str}

    async with websocket_semaphore:
        client = SubstrateWSClient(
            oid=token_store.oid,
            tid=token_store.tid,
            access_token=token_store.access_token,
            session_id=session_id,
            conversation_id=conversation_id,
        )

        text_buffer = ""
        pending_tool: dict = {}

        async for ev_type, payload in client.stream_chat(prompt=prompt, tone=tone, is_start=is_start):
            if ev_type == "text":
                delta, text_buffer = compute_text_delta(payload, text_buffer)
                if delta:
                    if tool_parser:
                        for tc_type, tc_data in tool_parser.feed(delta):
                            if tc_type == "content":
                                full_text += tc_data["text"]
                            elif tc_type == "tool_name":
                                pending_tool = {
                                    "id": f"toolu_{uuid.uuid4().hex[:12]}",
                                    "name": tc_data["name"],
                                    "arguments_str": ""
                                }
                            elif tc_type == "tool_args":
                                if pending_tool:
                                    pending_tool["arguments_str"] = tc_data["arguments"]
                                    tool_calls.append(pending_tool)
                                    pending_tool = {}
                    else:
                        full_text += delta
            elif ev_type == "image":
                urls = payload.get("urls", [])
                if urls:
                    try:
                        token = await get_designer_token()
                        tasks = [save_image_locally(url, token) for url in urls]
                        filenames = await asyncio.gather(*tasks, return_exceptions=True)
                        for url, filename in zip(urls, filenames):
                            if isinstance(filename, Exception) or not filename:
                                full_text += await fetch_image_as_base64(url, token)
                            else:
                                full_text += f"\n![Generated Image]({base_url}/images/{filename})\n"
                    except Exception as exc:
                        full_text += f"\n[Error fetching image: {exc}]\n"
            elif ev_type == "image_b64":
                for b64_data, content_type in payload.get("images", []):
                    filename = save_b64_image_locally(b64_data, content_type)
                    if filename:
                        full_text += f"\n![Generated Image]({base_url}/images/{filename})\n"
                    else:
                        full_text += f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
            elif ev_type == "done":
                break

    # Build content blocks for response
    content_blocks = []
    if full_text:
        content_blocks.append({"type": "text", "text": full_text})
    for tc in tool_calls:
        try:
            input_obj = json.loads(tc["arguments_str"])
        except Exception:
            input_obj = {"raw": tc["arguments_str"]}
        content_blocks.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": input_obj,
        })

    stop_reason = "tool_use" if tool_calls else "end_turn"

    return JSONResponse({
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks or [{"type": "text", "text": ""}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 0, "output_tokens": len(full_text.split())}
    })


async def _stream_auto_bash(msg_id: str, model: str, call_id: str, bash_cmd: str):
    """
    Streams a bash tool_use response in Anthropic SSE format.
    Used to bypass M365 Copilot refusals for initial filesystem exploration.
    """
    import json as _json
    yield build_message_start(msg_id, model)
    yield build_tool_use_block_start(0, call_id, "bash")
    yield build_input_json_delta(_json.dumps({"command": bash_cmd}), 0)
    yield build_content_block_stop(0)
    yield build_message_delta("tool_use")
    yield build_message_stop()


def _non_stream_auto_bash(msg_id: str, model: str, call_id: str, bash_cmd: str):
    """
    Returns a bash tool_use response in Anthropic JSON format (non-streaming).
    Used to bypass M365 Copilot refusals for initial filesystem exploration.
    """
    return JSONResponse({
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "tool_use",
                "id": call_id,
                "name": "bash",
                "input": {"command": bash_cmd},
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 0, "output_tokens": 10},
    })
