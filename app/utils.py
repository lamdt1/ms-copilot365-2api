def compute_text_delta(payload: dict, text_buffer: str) -> tuple[str, str]:
    """
    Computes the delta text to yield and the updated full text buffer.
    Handles both incremental (writeAtCursor) and full cumulative text (is_full=True) events.
    """
    text = payload.get("text", "")
    if payload.get("is_full"):
        if text.startswith(text_buffer):
            delta = text[len(text_buffer):]
        else:
            delta = text  # server rewrote the response
        return delta, text
    else:
        # Incremental delta
        return text, text_buffer + text
