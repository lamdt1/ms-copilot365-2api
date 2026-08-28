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
from app.tools.engine import (
    resolve_tool_strategy, needs_auto_bash, get_bash_tool_name,
    _is_init_command, _has_bash_context,
)
from app.substrate.ws_client import SubstrateWSClient
from app.substrate.image import (
    should_generate_image,
    get_designer_token,
    fetch_image_as_base64,
    classify_image_failure,
    save_image_locally,
    save_b64_image_locally
)
from app.formatters.openai_sse import (
    format_openai_chunk,
    format_openai_done,
    format_openai_response
)
from app.utils import compute_text_delta, get_external_base_url

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

MAX_RETRIES_DISENGAGED = 3

# Circuit breaker for SubstrateWSClient: when direct WS always fails (SignalR Type 7),
# skip it and go straight to browser for a period.
_ws_consecutive_failures = 0
_ws_circuit_open_until: float = 0.0  # epoch time when circuit re-closes
_WS_FAILURE_THRESHOLD = 3     # open circuit after this many consecutive failures
_WS_COOLDOWN_SEC = 45.0       # keep circuit open for 45 seconds


def reset_ws_circuit_breaker():
    """Reset the WS circuit breaker state so direct WS can be attempted again."""
    global _ws_consecutive_failures, _ws_circuit_open_until
    if _ws_consecutive_failures > 0 or _ws_circuit_open_until > 0:
        logger.info("chat: WS circuit breaker RESET (was %d failures, circuit closed)", _ws_consecutive_failures)
    _ws_consecutive_failures = 0
    _ws_circuit_open_until = 0.0


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

    # Validate token readiness: either direct JWT token is valid OR browser page is ready for fallback
    from app.browser.camoufox_manager import camoufox_manager
    if not token_store.is_valid and not (camoufox_manager.page and camoufox_manager._page_ready):
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

    # DEBUG: log incoming request details to diagnose auto-bash issues
    from app.tools.engine import _get_tool_name as _gtn
    tool_names_debug = [_gtn(t) for t in (tools or [])]
    last_roles = [m.get("role") for m in messages[-3:]]
    last_content_types = []
    for m in messages[-2:]:
        c = m.get("content", "")
        if isinstance(c, list):
            last_content_types.append([b.get("type") for b in c if isinstance(b, dict)])
        else:
            last_content_types.append(type(c).__name__)

    # Log Bash tool schema so we can verify our tool_call arguments match
    bash_schema_debug = None
    for t in (tools or []):
        if _gtn(t).lower() == "bash":
            func = t.get("function", t)
            bash_schema_debug = func.get("parameters") or func.get("input_schema")
            break

    # Log first 300 chars of last user message content
    last_msg = messages[-1] if messages else {}
    last_content_preview = str(last_msg.get("content", ""))[:300]

    logger.info("DEBUG chat: tools=%s | last_roles=%s | content_types=%s | stream=%s",
                tool_names_debug, last_roles, last_content_types, stream)
    logger.info("DEBUG bash_schema=%s | last_msg_preview=%s", bash_schema_debug, last_content_preview)

    # Short-circuit: Claude Code internal requests with no tools (transcript/summarization)
    # don't need M365 — return empty response immediately to free up browser bandwidth
    if tools is not None and len(tools) == 0:
        logger.info("chat: tools=[] request (internal/summarization) → returning empty response")
        _chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        if stream:
            async def _empty_stream():
                yield format_openai_chunk(_chat_id, model, {"role": "assistant", "content": ""})
                yield format_openai_chunk(_chat_id, model, {}, finish_reason="stop")
                yield format_openai_done()
            return StreamingResponse(_empty_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        else:
            return JSONResponse(format_openai_response(_chat_id, model, ""))

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

    # ── Auto-bash shortcut (OpenAI format) ────────────────────────────────
    # When bash tool is available and no file content exists yet, M365 would
    # refuse filesystem access. Return a bash tool_call directly so the
    # client can explore the project and send back real file content.
    _bash_name = get_bash_tool_name(tools or [], messages)
    if not generate_images and _bash_name:
        logger.info("auto-bash: returning %s tool_call (OpenAI format, bypass M365 refusal)", _bash_name)
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        bash_cmd = (
            # Working directory and structure
            "echo '=== CWD ===' && pwd && echo '' && "
            "echo '=== PROJECT STRUCTURE ===' && "
            "find . -maxdepth 4 -type f "
            "-not -path '*/node_modules/*' -not -path '*/.git/*' "
            "-not -path '*/__pycache__/*' -not -path '*/target/*' "
            "-not -path '*/dist/*' -not -path '*/build/*' "
            "-not -path '*/.venv/*' -not -path '*/venv/*' "
            "-not -name '*.pyc' -not -name '*.png' -not -name '*.jpg' "
            "2>/dev/null | sort | head -80 && "
            # README and config
            "echo '' && echo '=== README ===' && "
            "cat README.md 2>/dev/null || cat readme.md 2>/dev/null || echo '(no README)' && "
            "echo '' && echo '=== PACKAGE/CONFIG ===' && "
            "cat package.json 2>/dev/null || cat pyproject.toml 2>/dev/null || "
            "cat requirements.txt 2>/dev/null || echo '(no config found)' | head -50 && "
            # Main entry points (cat top source files)
            "echo '' && echo '=== SOURCE FILES ===' && "
            "for f in $(find . -maxdepth 4 -type f \\( "
            "-name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.go' "
            "-o -name '*.java' -o -name '*.rs' -o -name '*.cpp' -o -name '*.c' "
            "\\) -not -path '*/node_modules/*' -not -path '*/__pycache__/*' "
            "-not -path '*/.venv/*' -not -path '*/venv/*' "
            "2>/dev/null | head -15); do "
            "echo \"\\n--- FILE: $f ---\" && cat \"$f\" | head -150 && echo; done"
        )
        import json as _json
        tool_call_payload = {
            "id": call_id,
            "type": "function",
            "function": {"name": _bash_name, "arguments": _json.dumps({"command": bash_cmd})},
        }
        if stream:
            return StreamingResponse(
                _stream_auto_bash_openai(chat_id, model, tool_call_payload),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            from fastapi.responses import JSONResponse as _JSONResponse
            return _JSONResponse({
                "id": chat_id,
                "object": "chat.completion",
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call_payload],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
    # ──────────────────────────────────────────────────────────────────────

    # /init intercept: when Claude Code runs /init and bash context exists,
    # M365 cannot use Write tool. We buffer M365's text response and wrap
    # it as a Write tool_call ourselves so Claude Code can create CLAUDE.md.
    is_init = _is_init_command(messages)
    has_ctx = _has_bash_context(messages) if is_init else False
    if is_init and has_ctx and stream:
        write_tool = next((t for t in tool_names_debug if t.lower() == "write"), "Write")
        logger.info("chat: /init+bash_context detected → will wrap M365 response as %s tool_call", write_tool)
        # Ask M365 for plain CLAUDE.md content (no tool injection)
        init_prompt = (
            final_text
            + "\n\nIMPORTANT: Respond with ONLY raw markdown content for CLAUDE.md. "
            "Do NOT say you cannot access files — you have the codebase above. "
            "Start directly with '# ' heading. No preamble, no explanation."
        )
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        m365_gen = _stream_response(
            chat_id=chat_id, model=model, prompt=init_prompt, tone=tone,
            session_id=session_id, conversation_id=conversation_id,
            is_start=is_start, agent_id=None, tool_parser=None,
            persistent_id=persistent_id, generate_images=False,
            base_url=get_external_base_url(request),
        )
        return StreamingResponse(
            _stream_init_as_write_tool(chat_id, model, write_tool, m365_gen, messages=messages),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
                base_url=get_external_base_url(request),
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
            base_url=get_external_base_url(request),
        )


def _generate_claude_md_from_bash_context(messages: list) -> str:
    """
    Generate a meaningful CLAUDE.md from the bash output stored in conversation history.
    Extracts file listing, CWD, and source file snippets from our auto-bash markers.
    """
    bash_output = ""
    for msg in reversed(messages):
        content = msg.get("content", "")
        content_str = str(content) if not isinstance(content, str) else content
        if "=== CWD ===" in content_str or "=== SOURCE FILES ===" in content_str:
            bash_output = content_str
            break

    if not bash_output:
        return (
            "# Project Guide\n\n"
            "## Overview\n[Auto-generated template. Update with project-specific details.]\n\n"
            "## Development\n[Add build/run commands here]\n\n"
            "## Notes\n[Add notes for future Claude Code instances here]\n"
        )

    # Extract CWD
    cwd = ""
    if "=== CWD ===" in bash_output:
        for line in bash_output.split("\\n"):
            line = line.strip().strip("'")
            if line and not line.startswith("===") and not line.startswith("/dev") and len(line) > 1:
                if "/" in line or line.isalnum():
                    cwd = line
                    break

    # Extract file listing
    file_lines = []
    in_files = False
    for line in bash_output.split("\\n"):
        stripped = line.strip().strip("'")
        if "=== PROJECT STRUCTURE ===" in stripped:
            in_files = True
            continue
        if in_files and stripped.startswith("=== "):
            break
        if in_files and stripped.startswith("./") and len(file_lines) < 40:
            file_lines.append(f"- `{stripped}`")

    project_name = cwd.split("/")[-1] if "/" in cwd else (cwd or "Project")
    files_section = "\n".join(file_lines) if file_lines else "(file listing unavailable)"

    return (
        f"# {project_name}\n\n"
        "## Project Overview\n"
        f"This guide helps Claude Code operate in the `{project_name}` repository.\n\n"
        "## Project Structure\n"
        f"{files_section}\n\n"
        "## Common Development Commands\n"
        "```bash\n"
        "# Add build, test, and run commands here\n"
        "```\n\n"
        "## Architecture Notes\n"
        "[Add key architectural decisions and patterns here]\n\n"
        "## Working in This Repository\n"
        "- Review the project structure above before making changes\n"
        "- Follow existing code style and conventions\n"
        "- Run tests before submitting changes\n"
    )


async def _stream_init_as_write_tool(
    chat_id: str,
    model: str,
    write_tool_name: str,
    m365_gen,
    messages: list = None,
):
    """
    Wraps M365's streaming text response for /init as a Write tool_call SSE stream.
    Buffers all text from m365_gen (parsing SSE chunks), then emits a single
    Write tool_call so Claude Code can create CLAUDE.md.
    Falls back to generated content if M365 returns too little (<200 chars).
    """
    full_text = ""
    # Consume M365 stream and extract text deltas
    async for sse_line in m365_gen:
        if not sse_line or sse_line.strip() == "data: [DONE]":
            continue
        if sse_line.startswith("data: "):
            try:
                data = json.loads(sse_line[6:].strip())
                delta = data.get("choices", [{}])[0].get("delta", {})
                chunk = delta.get("content", "") or delta.get("reasoning_content", "") or ""
                full_text += chunk
            except Exception:
                pass

    logger.info("chat: /init collected %d chars from M365", len(full_text))

    # Fallback: if M365 returned too little (refusal), generate from bash context
    if len(full_text.strip()) < 200:
        logger.info("chat: /init M365 response too short — generating CLAUDE.md from bash context")
        full_text = _generate_claude_md_from_bash_context(messages or [])

    logger.info("chat: /init streaming Write tool_call with %d chars for CLAUDE.md", len(full_text))

    call_id = f"call_{uuid.uuid4().hex[:12]}"
    args = json.dumps({"file_path": "CLAUDE.md", "content": full_text.strip()})

    # Stream Write tool_call in proper OpenAI SSE format
    yield format_openai_chunk(chat_id, model, {"role": "assistant", "content": None})
    yield format_openai_chunk(chat_id, model, {
        "tool_calls": [{"index": 0, "id": call_id, "type": "function",
                        "function": {"name": write_tool_name, "arguments": ""}}]
    })
    # Stream args in small chunks (avoid large single delta)
    step = 200
    for i in range(0, len(args), step):
        yield format_openai_chunk(chat_id, model, {
            "tool_calls": [{"index": 0, "function": {"arguments": args[i:i + step]}}]
        })
    yield format_openai_chunk(chat_id, model, {}, finish_reason="tool_calls")
    yield format_openai_done()


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
    base_url: str = "",
):
    """
    Async generator producing SSE chunks for streaming mode.
    """
    # global must be declared before ANY use of these variables in this function
    global _ws_consecutive_failures, _ws_circuit_open_until

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
            # If token is invalid or circuit breaker is open, fall back straight to browser
            import time as _time_cb
            if not token_store.is_valid or _ws_circuit_open_until > _time_cb.monotonic():
                logger.info("chat: Token invalid or WS circuit open → streaming directly via browser")
                from app.browser.camoufox_manager import camoufox_manager
                browser_gen = camoufox_manager.stream_chat_browser(prompt)
                try:
                    async for b_ev_type, b_payload in browser_gen:
                        if b_ev_type == "text":
                            delta, text_buffer = compute_text_delta(b_payload, text_buffer)
                            if delta:
                                yield format_openai_chunk(chat_id, model, {"content": delta})
                                usage["completion_tokens"] += 1
                        elif b_ev_type == "done":
                            finish_reason = "stop"
                            break
                        elif b_ev_type == "error":
                            yield format_openai_chunk(chat_id, model, {
                                "content": f"\n[{b_payload.get('message', 'Browser error')}]\n"
                            })
                            break
                finally:
                    pass
                break

            # Use intercepted WS URL (captured from browser) and browser cookies
            # Without these, substrate rejects the connection with SignalR Type 7 error
            _ws_url = _build_retry_ws_url(session_id, conversation_id)
            from app.browser.camoufox_manager import camoufox_manager as _cm
            try:
                _cookie_header = await _cm.get_auth_cookies()
            except Exception:
                _cookie_header = ""
            client = SubstrateWSClient(
                oid=token_store.oid,
                tid=token_store.tid,
                access_token=token_store.access_token,
                session_id=session_id,
                conversation_id=conversation_id,
                ws_url_override=_ws_url,
                cookie_header=_cookie_header,
            )

            # Circuit breaker: skip direct WS and go straight to browser when broken
            import time as _time_cb
            if _ws_circuit_open_until > _time_cb.monotonic():
                logger.info("chat: WS circuit open → skipping direct WS, going to browser")
                from app.browser.camoufox_manager import camoufox_manager
                browser_gen = camoufox_manager.stream_chat_browser(prompt)
                try:
                    async for b_ev_type, b_payload in browser_gen:
                        if b_ev_type == "text":
                            delta, text_buffer = compute_text_delta(b_payload, text_buffer)
                            if delta:
                                yield format_openai_chunk(chat_id, model, {"content": delta})
                                usage["completion_tokens"] += 1
                        elif b_ev_type == "done":
                            finish_reason = "stop"
                            break
                        elif b_ev_type == "error":
                            yield format_openai_chunk(chat_id, model, {
                                "content": f"\n[{b_payload.get('message', 'Browser error')}]\n"
                            })
                            break
                finally:
                    pass
                break

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
                            reset_ws_circuit_breaker()
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
                                    tasks = [save_image_locally(url, token) for url in urls]
                                    filenames = await asyncio.gather(*tasks, return_exceptions=True)
                                    for url, filename in zip(urls, filenames):
                                        if isinstance(filename, Exception) or not filename:
                                            md = await fetch_image_as_base64(url, token)
                                        else:
                                            md = f"\n![Generated Image]({base_url}/images/{filename})\n"
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
                                filename = save_b64_image_locally(b64_data, content_type)
                                if filename:
                                    md = f"\n![Generated Image]({base_url}/images/{filename})\n"
                                else:
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
                            if (("connection_closed" in err_msg or "Connection closed" in err_msg) and not text_buffer):
                                import time as _time
                                _ws_consecutive_failures += 1
                                if _ws_consecutive_failures >= _WS_FAILURE_THRESHOLD:
                                    _ws_circuit_open_until = _time.monotonic() + _WS_COOLDOWN_SEC
                                    logger.warning(
                                        "chat: WS circuit breaker OPEN (%d failures) — browser-only for %.0fs",
                                        _ws_consecutive_failures, _WS_COOLDOWN_SEC
                                    )
                                logger.warning("chat: Stream direct WS error → falling back to Camoufox browser...")
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
                                                    tasks = [save_image_locally(url, token) for url in urls]
                                                    filenames = await asyncio.gather(*tasks, return_exceptions=True)
                                                    for url, filename in zip(urls, filenames):
                                                        if isinstance(filename, Exception) or not filename:
                                                            md = await fetch_image_as_base64(url, token)
                                                        else:
                                                            md = f"\n![Generated Image]({base_url}/images/{filename})\n"
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
                                                filename = save_b64_image_locally(b64_data, content_type)
                                                if filename:
                                                    md = f"\n![Generated Image]({base_url}/images/{filename})\n"
                                                else:
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
    base_url: str = "",
):
    """
    Collects the full response and returns a single JSON object.
    """
    global _ws_consecutive_failures, _ws_circuit_open_until
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
        from app.browser.camoufox_manager import camoufox_manager as _cm2
        _ws_url2 = _build_retry_ws_url(session_id, conversation_id)
        try:
            _cookie2 = await _cm2.get_auth_cookies()
        except Exception:
            _cookie2 = ""

        browser_fallback_used = False

        # Circuit breaker or token expired check for non-stream
        import time as _time_cb2
        if not token_store.is_valid or _ws_circuit_open_until > _time_cb2.monotonic():
            logger.info("chat: Token invalid or WS circuit open → skipping direct WS in non-stream, going to browser")
            browser_fallback_used = True
            browser_gen = _cm2.stream_chat_browser(prompt)
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
                                tasks = [save_image_locally(url, token) for url in urls]
                                filenames = await asyncio.gather(*tasks, return_exceptions=True)
                                for url, filename in zip(urls, filenames):
                                    if isinstance(filename, Exception) or not filename:
                                        full_content += await fetch_image_as_base64(url, token)
                                    else:
                                        full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                            except Exception as exc:
                                logger.error("Error fetching browser fallback image: %s", exc)
                                full_content += f"\n[Error fetching image: {str(exc)}]\n"
                    elif b_ev_type == "image_b64":
                        for b64_data, content_type in b_payload.get("images", []):
                            image_received = True
                            filename = save_b64_image_locally(b64_data, content_type)
                            if filename:
                                full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                            else:
                                full_content += f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                    elif b_ev_type == "done":
                        break
                    elif b_ev_type == "error":
                        break
            finally:
                await browser_gen.aclose()

        if not browser_fallback_used:
            client = SubstrateWSClient(
                oid=token_store.oid,
                tid=token_store.tid,
                access_token=token_store.access_token,
                session_id=session_id,
                conversation_id=conversation_id,
                ws_url_override=_ws_url2,
                cookie_header=_cookie2,
            )
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
                            reset_ws_circuit_breaker()
                            delta, text_buffer = compute_text_delta(payload, text_buffer)
                            full_content += delta
                        elif ev_type == "image":
                            urls = payload.get("urls", [])
                            if urls:
                                image_received = True
                                try:
                                    token = await get_designer_token()
                                    tasks = [save_image_locally(url, token) for url in urls]
                                    filenames = await asyncio.gather(*tasks, return_exceptions=True)
                                    for url, filename in zip(urls, filenames):
                                        if isinstance(filename, Exception) or not filename:
                                            full_content += await fetch_image_as_base64(url, token)
                                        else:
                                            full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                                except Exception as exc:
                                    logger.error("Error fetching images in non-stream: %s", exc)
                                    full_content += f"\n[Error fetching image: {str(exc)}]\n"
                        elif ev_type == "image_b64":
                            # Base64 image data embedded directly (feature.EnableBase64DataInMessageAnnotations)
                            for b64_data, content_type in payload.get("images", []):
                                image_received = True
                                filename = save_b64_image_locally(b64_data, content_type)
                                if filename:
                                    full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                                else:
                                    full_content += f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                        elif ev_type == "done":
                            reset_ws_circuit_breaker()
                            result = payload
                            if isinstance(result, dict):
                                usage["x_m365_conversation_messages"] = result.get("conversationMessages", 0)
                                usage["x_m365_dea_score"] = result.get("deaScore", 0)
                            break
                        elif ev_type == "error":
                            error_msg = payload.get('message', 'Unknown error')
                            if not full_content and ("Connection closed" in error_msg or "connection_closed" in error_msg):
                                import time as _time2
                                _ws_consecutive_failures += 1
                                if _ws_consecutive_failures >= _WS_FAILURE_THRESHOLD:
                                    _ws_circuit_open_until = _time2.monotonic() + _WS_COOLDOWN_SEC
                                    logger.warning(
                                        "chat: WS circuit breaker OPEN (%d failures) — browser-only for %.0fs",
                                        _ws_consecutive_failures, _WS_COOLDOWN_SEC
                                    )
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
                                                    tasks = [save_image_locally(url, token) for url in urls]
                                                    filenames = await asyncio.gather(*tasks, return_exceptions=True)
                                                    for url, filename in zip(urls, filenames):
                                                        if isinstance(filename, Exception) or not filename:
                                                            full_content += await fetch_image_as_base64(url, token)
                                                        else:
                                                            full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                                                except Exception as exc:
                                                    logger.error("Error fetching browser fallback image: %s", exc)
                                                    full_content += f"\n[Error fetching image: {str(exc)}]\n"
                                        elif b_ev_type == "image_b64":
                                            for b64_data, content_type in b_payload.get("images", []):
                                                image_received = True
                                                filename = save_b64_image_locally(b64_data, content_type)
                                                if filename:
                                                    full_content += f"\n![Generated Image]({base_url}/images/{filename})\n"
                                                else:
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


async def _stream_auto_bash_openai(chat_id: str, model: str, tool_call_payload: dict):
    """
    Streams a bash tool_call response in OpenAI SSE format.
    Used to bypass M365 Copilot refusals when Claude Code calls /v1/chat/completions.
    """
    # Initial role delta (content=null for tool_calls)
    yield format_openai_chunk(chat_id, model, {"role": "assistant", "content": None})

    # Tool call start: id, type, function name, empty arguments
    yield format_openai_chunk(chat_id, model, {
        "tool_calls": [{
            "index": 0,
            "id": tool_call_payload["id"],
            "type": "function",
            "function": {"name": tool_call_payload["function"]["name"], "arguments": ""},
        }]
    })

    # Stream the full arguments string
    yield format_openai_chunk(chat_id, model, {
        "tool_calls": [{
            "index": 0,
            "function": {"arguments": tool_call_payload["function"]["arguments"]},
        }]
    })

    # Final chunk with finish_reason="tool_calls" (empty delta)
    yield format_openai_chunk(chat_id, model, {}, finish_reason="tool_calls")

    # SSE done marker
    yield format_openai_done()

