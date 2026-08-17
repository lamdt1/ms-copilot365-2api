import json
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


def fold_conversation(messages: List[Dict[str, Any]]) -> Tuple[List[str], str]:
    """
    Folds a multi-turn messages list into:
      1. A list of system instruction strings/additional context.
      2. A final combined user prompt text.
    """
    system_instructions: List[str] = []
    prior_transcript: List[str] = []
    conversation_history: List[str] = []
    tool_id_to_name: Dict[str, str] = {}

    last_user_message = ""

    # Parse and distribute based on role
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        # Normalize content to string (handle structured list types if sent by client)
        # Also extract Anthropic-style tool_use / tool_result blocks
        tool_uses = []
        tool_results = []

        if isinstance(content, list):
            content_str = ""
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    content_str += block.get("text", "")
                elif block_type == "tool_use":
                    tool_uses.append(block)
                elif block_type == "tool_result":
                    tr_content = block.get("content", "")
                    if isinstance(tr_content, list):
                        tr_content = "".join(
                            b.get("text", "") for b in tr_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    tool_results.append({
                        "tool_call_id": block.get("tool_use_id", f"call_{i}"),
                        "content": tr_content,
                    })
            content = content_str
        elif content is None:
            content = ""

        # Last user message will be processed separately if it is the final item
        is_last = (i == len(messages) - 1)

        if role == "system":
            system_instructions.append(content)
        elif role == "user":
            # Handle Anthropic tool_result blocks in user messages
            if tool_results:
                for tr in tool_results:
                    t_name = tool_id_to_name.get(tr["tool_call_id"], "unknown")
                    tr_str = f'<tool_response tool_call_id="{tr["tool_call_id"]}" name="{t_name}">\n{tr["content"]}\n</tool_response>'
                    prior_transcript.append(tr_str)
                    conversation_history.append(json.dumps({"role": "user", "content": tr_str}))
            if is_last and not tool_results:
                last_user_message = content
            elif content:
                prior_transcript.append(f"User: {content}")
                conversation_history.append(json.dumps({"role": "user", "content": content}))
        elif role == "assistant":
            # Handle assistant messages, check if it had tool calls (OpenAI or Anthropic format)
            tool_calls = msg.get("tool_calls")
            if tool_uses:
                # Anthropic format: content blocks with type=tool_use
                for tu in tool_uses:
                    if tu.get("id") and tu.get("name"):
                        tool_id_to_name[tu["id"]] = tu["name"]
                tc_list = [{"name": tu.get("name"), "arguments": tu.get("input", {})} for tu in tool_uses]
                tc_str = "<tool_call>\n" + json.dumps(tc_list) + "\n</tool_call>"
                full = (content + "\n" + tc_str) if content else tc_str
                prior_transcript.append(f"Assistant: {full}")
                conversation_history.append(json.dumps({"role": "assistant", "content": full}))
            elif tool_calls:
                for tc in tool_calls:
                    if tc.get("id"):
                        tool_id_to_name[tc["id"]] = tc.get("function", {}).get("name", "unknown")
                tc_str = "<tool_call>\n" + json.dumps(tool_calls) + "\n</tool_call>"
                prior_transcript.append(f"Assistant: {tc_str}")
                conversation_history.append(json.dumps({"role": "assistant", "content": tc_str}))
            elif content:
                prior_transcript.append(f"Assistant: {content}")
                conversation_history.append(json.dumps({"role": "assistant", "content": content}))
        elif role == "tool":
            # Handle tool response (OpenAI format)
            tool_call_id = msg.get("tool_call_id", f"call_{i}")
            name = msg.get("name", "unknown")
            tr_str = f'<tool_response tool_call_id="{tool_call_id}" name="{name}">\n{content}\n</tool_response>'
            prior_transcript.append(tr_str)
            conversation_history.append(json.dumps({"role": "user", "content": tr_str}))

    # Compile additional context
    additional_context: List[str] = []

    if system_instructions:
        additional_context.append("System instructions:\n" + "\n".join(system_instructions))

    if prior_transcript:
        additional_context.append("Prior conversation transcript:\n" + "\n".join(prior_transcript))

    # Fallback default if last user message is empty
    if not last_user_message and prior_transcript:
        # If the last message was a tool result, prompt Copilot to analyze it
        last_user_message = "Please analyze the tool output above and continue."

    return additional_context, last_user_message


def combine_text(additional_context: List[str], prompt: str) -> str:
    """
    Joins system instruction and history prefixes with the final user prompt.
    """
    if not additional_context:
        return prompt

    prefix = "\n\n".join(additional_context)
    return f"{prefix}\n\n# Current message\n{prompt}"
