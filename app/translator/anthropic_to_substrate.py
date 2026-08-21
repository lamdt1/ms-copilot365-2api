from typing import Dict, Any, Tuple

from app.translator.fold_conversation import fold_conversation, combine_text


def translate_anthropic_request(request_body: Dict[str, Any]) -> Tuple[str, str]:
    """
    Translates an Anthropic messages payload into (final_text, tone).
    """
    model = request_body.get("model", "claude-sonnet")
    messages = request_body.get("messages", [])
    system = request_body.get("system", "")

    # Inject system prompt into messages structure so fold_conversation processes it
    if system:
        messages.insert(0, {"role": "system", "content": system})

    tone = "Claude_Sonnet"
    if "opus" in model.lower():
        tone = "Claude_Opus"
    elif "quick" in model.lower():
        tone = "Gpt_Quick"
    elif "think" in model.lower() or "reasoning" in model.lower():
        tone = "Reasoning"

    additional_context, last_user_message = fold_conversation(messages)

    final_text = combine_text(additional_context, last_user_message)

    return final_text, tone
