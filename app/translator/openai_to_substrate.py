from typing import Dict, Any, Tuple

from app.translator.fold_conversation import fold_conversation, combine_text


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

    additional_context, last_user_message = fold_conversation(messages)

    final_text = combine_text(additional_context, last_user_message)

    return final_text, tone
