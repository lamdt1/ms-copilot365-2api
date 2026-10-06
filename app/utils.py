import re

# Copilot citation markers e.g. citeturn3search31, citeturn0search5, +1, [1]
_CITATION_RE = re.compile(r'citeturn\d+\w*|\+\d+|\[\d+\]', re.IGNORECASE)

_REASONING_PREFIXES = (
    "đang sắp xếp",
    "đang tìm kiếm",
    "đang xử lý",
    "đang suy nghĩ",
    "đang tạo",
    "đang phân tích",
    "thinking",
    "working on it",
    "searching",
    "generating",
    "analyzing",
    "organizing",
    "copilot said:",
    "copilot said",
    "you said:",
    "you said",
)


def _is_reasoning_or_status_line(line: str) -> bool:
    """Check if a line of text is a Copilot reasoning/status/placeholder indicator."""
    if not line:
        return True
    l_lower = line.strip().lower()
    return any(l_lower.startswith(prefix) for prefix in _REASONING_PREFIXES)


def _strip_citations(text: str) -> str:
    """
    Remove Copilot inline citation markers and strip any reasoning/status/placeholder lines from text.
    """
    if not text:
        return ""
    # Strip citation markers
    text = _CITATION_RE.sub('', text)

    # Filter out status/reasoning lines
    lines = text.split("\n")
    cleaned_lines = [line for line in lines if not _is_reasoning_or_status_line(line)]
    return "\n".join(cleaned_lines)


def compute_text_delta(payload: dict, text_buffer: str) -> tuple[str, str]:
    """
    Computes the delta text to yield and the updated full text buffer.
    Handles both incremental (writeAtCursor) and full cumulative text (is_full=True) events.
    Citation markers and reasoning/status indicators are stripped before returning.
    """
    raw_text = payload.get("text", "")

    if payload.get("is_full"):
        text = _strip_citations(raw_text).strip()
        clean_buffer = _strip_citations(text_buffer)
        if text.startswith(clean_buffer):
            delta = text[len(clean_buffer):]
        elif clean_buffer.startswith(text):
            # Server text shrank or reset prefix, no new delta
            delta = ""
        else:
            # If prefix changed (e.g. status removed), check if text ends with new content
            delta = text
        return delta, text
    else:
        # Incremental delta
        delta = _strip_citations(raw_text)
        return delta, text_buffer + delta


def get_external_base_url(request) -> str:
    """
    Build the external-facing base URL from proxy headers (Cloudflare Tunnel, nginx, etc.).
    Falls back to request.base_url for local dev.
    Returns e.g. 'https://my-domain.com' (no trailing slash).
    """
    proto = (
        request.headers.get("x-forwarded-proto")
        or request.url.scheme
        or "http"
    )
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"
