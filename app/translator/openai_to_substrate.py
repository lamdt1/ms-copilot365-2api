from typing import Dict, Any, List, Tuple
import logging

from app.translator.fold_conversation import fold_conversation, combine_text

logger = logging.getLogger(__name__)

# Max non-system messages to send to M365 (prevents context overflow / slow responses)
MAX_CONTEXT_MESSAGES = 10


def _prune_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep all system messages + the last MAX_CONTEXT_MESSAGES non-system messages.
    Always keeps the last user message (the current request).
    """
    if len(messages) <= MAX_CONTEXT_MESSAGES + 2:  # +2 for system messages
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= MAX_CONTEXT_MESSAGES:
        return messages

    # Keep last MAX_CONTEXT_MESSAGES non-system messages
    pruned = system_msgs + non_system[-MAX_CONTEXT_MESSAGES:]
    logger.info(
        "translate: pruned conversation %d→%d messages (kept last %d)",
        len(messages), len(pruned), MAX_CONTEXT_MESSAGES
    )
    return pruned


def translate_openai_request(request_body: Dict[str, Any]) -> Tuple[str, str]:
    """
    Translates an OpenAI chat completions payload into (final_text, tone).
    """
    model = request_body.get("model", "m365-copilot")
    messages = request_body.get("messages", [])

    # Extract tone hint from model name if any
    tone = "magic"
    if "quick" in model.lower():
        tone = "Gpt_Quick"
    elif "think" in model.lower() or "reasoning" in model.lower():
        tone = "Reasoning"
    elif "claude" in model.lower() and "opus" in model.lower():
        tone = "Claude_Opus"
    elif "claude" in model.lower():
        tone = "Claude_Sonnet"

    # Prune conversation to avoid overwhelming M365 with large contexts
    messages = _prune_messages(messages)

    additional_context, last_user_message = fold_conversation(messages)

    final_text = combine_text(additional_context, last_user_message)

    return final_text, tone
