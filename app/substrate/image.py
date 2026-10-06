import base64
import logging
import mimetypes
import os
import time
import uuid
import httpx
from typing import Optional, List, Dict, Any

from app.config import settings
from app.core.token_store import token_store

logger = logging.getLogger(__name__)

async def get_designer_token() -> str | None:
    """
    Fetches the designerappservice token using the refresh_token from token_store.
    Uses acquire_designer_token() from app.core.token_refresh.
    Returns None if unavailable.
    """
    from app.core.token_refresh import acquire_designer_token
    return await acquire_designer_token()


async def fetch_image_as_base64(url: str, designer_token: str | None) -> str:
    """
    Fetches the image bytes and converts them to a Markdown base64 embed.
    Fallback chain:
    - If URL has fileToken (self-authenticated Designer URL): try no-auth first
    - Otherwise: designer_token → main access_token → no auth → browser fetch
    """
    from app.browser.camoufox_manager import camoufox_manager

    # Designer URLs with fileToken are self-authenticated — Authorization header causes 401
    is_file_token_url = "fileToken=" in url or "filetoken=" in url.lower()

    if is_file_token_url:
        tokens_to_try = [("none", None)]
    else:
        tokens_to_try = [
            ("designer", designer_token),
            ("access", token_store.access_token),
            ("none", None),
        ]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for label, token in tokens_to_try:
            if label == "designer" and not token:
                continue
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
                    b64_data = base64.b64encode(resp.content).decode("utf-8")
                    logger.info("fetch_image_as_base64: OK with auth=%s (%d bytes)", label, len(resp.content))
                    return f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"
                else:
                    logger.warning("fetch_image_as_base64: %d with auth=%s, trying next...", resp.status_code, label)
            except Exception as exc:
                logger.warning("fetch_image_as_base64: error with auth=%s: %s, trying next...", label, exc)

    # Final fallback: use browser's authenticated session (has cookies)
    logger.info("fetch_image_as_base64: trying browser fetch for %s", url[:80])
    result = await camoufox_manager.fetch_image_via_browser(url)
    if result:
        b64_data, content_type = result
        return f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"

    logger.error("fetch_image_as_base64: all strategies failed for %s", url[:80])
    return f"\n[View Generated Image]({url})\n"



async def fetch_raw_image_base64(url: str, designer_token: str | None) -> tuple[str, str]:
    """
    Fetches the image bytes from Substrate/Designer and returns (base64_str, content_type).
    Fallback chain:
    - If URL has fileToken (self-authenticated Designer URL): try no-auth first
    - Otherwise: designer_token → main access_token → no auth → browser fetch
    """
    from app.browser.camoufox_manager import camoufox_manager

    # Designer URLs with fileToken are self-authenticated — Authorization header causes 401
    is_file_token_url = "fileToken=" in url or "filetoken=" in url.lower()

    if is_file_token_url:
        tokens_to_try = [("none", None)]
    else:
        tokens_to_try = [
            ("designer", designer_token),
            ("access", token_store.access_token),
            ("none", None),
        ]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for label, token in tokens_to_try:
            if label == "designer" and not token:
                continue
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
                    b64_data = base64.b64encode(resp.content).decode("utf-8")
                    logger.info("fetch_raw_image_base64: OK with auth=%s (%d bytes)", label, len(resp.content))
                    return b64_data, content_type
                else:
                    logger.warning("fetch_raw_image_base64: %d with auth=%s", resp.status_code, label)
            except Exception as exc:
                logger.warning("fetch_raw_image_base64: error with auth=%s: %s", label, exc)

    # Final fallback: use browser's authenticated session (has cookies)
    logger.info("fetch_raw_image_base64: trying browser fetch for %s", url[:80])
    result = await camoufox_manager.fetch_image_via_browser(url)
    if result:
        return result  # (b64_str, content_type)

    raise RuntimeError(f"Failed to load image from {url} (all auth strategies failed)")



def should_generate_image(prompt: str, tools: Optional[List[Dict[str, Any]]]) -> bool:
    """
    Detects if the user is requesting image generation via prompt text or tool schemas.
    """
    # 1. Check tools
    if tools:
        for tool in tools:
            name = tool.get("function", {}).get("name", "").lower()
            if any(k in name for k in ["image", "draw", "paint", "picture"]):
                return True

    # 2. Check prompt
    prompt_lower = prompt.lower()
    keywords = [
        "generate image", "create image", "draw", "paint",
        "vẽ", "tạo ảnh", "tao anh"
    ]
    for k in keywords:
        if k in prompt_lower:
            return True

    return False


def classify_image_failure(text: str) -> str:
    """
    Classifies the failure reason based on text emitted when 0 images are returned.
    """
    text_lower = text.lower()

    if any(k in text_lower for k in [
        "can't generate any more images today",
        "reached your image limit",
        "try again tomorrow"
    ]):
        return "quota_exceeded"

    if any(k in text_lower for k in [
        "trouble creating image",
        "couldn't generate image",
        "try again later"
    ]):
        return "capacity"

    if any(k in text_lower for k in [
        "against policy",
        "guideline",
        "unable to create that image"
    ]):
        return "content_filtered"

    return "no_image"


def _ext_from_content_type(content_type: str) -> str:
    ext = mimetypes.guess_extension(content_type)
    return ext if ext else ".png"


async def save_image_locally(url: str, designer_token: str | None) -> str | None:
    """
    Downloads the image via fetch_raw_image_base64 and writes it to the local generated_images directory.
    Returns the generated filename if successful, otherwise None.
    """
    try:
        b64_str, content_type = await fetch_raw_image_base64(url, designer_token)
        return save_b64_image_locally(b64_str, content_type)
    except Exception as exc:
        logger.error("save_image_locally: failed to save %s: %s", url[:80], exc)
        return None


def save_b64_image_locally(b64_data: str, content_type: str) -> str | None:
    """
    Decodes base64 string and writes it to the local generated_images directory.
    Returns the generated filename if successful, otherwise None.
    """
    from app.config import settings
    try:
        data = base64.b64decode(b64_data)
        ext = _ext_from_content_type(content_type)
        filename = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(settings.IMAGE_DOWNLOAD_DIR, filename)
        with open(path, "wb") as f:
            f.write(data)
        return filename
    except Exception as exc:
        logger.error("save_b64_image_locally: failed: %s", exc)
        return None
