import re

# Copilot citation markers e.g. citeturn3search31, citeturn0search5
_CITATION_RE = re.compile(r'citeturn\d+\w*', re.IGNORECASE)


def _strip_citations(text: str) -> str:
    """Remove Copilot inline citation markers from text."""
    return _CITATION_RE.sub('', text)


def compute_text_delta(payload: dict, text_buffer: str) -> tuple[str, str]:
    """
    Computes the delta text to yield and the updated full text buffer.
    Handles both incremental (writeAtCursor) and full cumulative text (is_full=True) events.
    Citation markers (e.g. citeturn3search31) are stripped before returning.
    """
    text = payload.get("text", "")
    if payload.get("is_full"):
        if text.startswith(text_buffer):
            delta = text[len(text_buffer):]
        else:
            delta = text  # server rewrote the response
        return _strip_citations(delta), text
    else:
        # Incremental delta
        return _strip_citations(text), text_buffer + text
