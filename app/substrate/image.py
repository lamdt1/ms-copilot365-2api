import base64
import logging
import time
import httpx
from typing import Optional, List, Dict, Any

from app.config import settings
from app.core.token_store import token_store

logger = logging.getLogger(__name__)

# Simple in-memory cache for designer token
_designer_token_cache: Dict[str, Any] = {
    "token": None,
    "expires_at": 0
}


async def get_designer_token() -> str | None:
    """
    Fetches the designerappservice token using the refresh_token from token_store.
    Caches the token in-memory until expiration.
    Returns None if unavailable (e.g. tenant doesn't support designer scope).
    """
    now = time.time()
    if _designer_token_cache["token"] and now < _designer_token_cache["expires_at"]:
        return _designer_token_cache["token"]

    refresh_token = token_store.refresh_token
    if not refresh_token:
        logger.warning("get_designer_token: no refresh_token available, skipping.")
        return None

    tenant_id = token_store.tid or settings.MODEL_TONE_MAP.get("m365_tenant_id", "common")
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    payload = {
        "grant_type": "refresh_token",
        "scope": "https://designerappservice.office.com/.default",
        "refresh_token": refresh_token,
        "client_id": "c0ab8ce9-e9a0-42e7-b064-33d422df41f1",
        "SKU": "msal.js.browser",
        "VER": "5.9.0",
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://m365.cloud.microsoft",
        "Referer": "https://m365.cloud.microsoft/",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            logger.info("Fetching new designerappservice token...")
            resp = await client.post(url, data=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                new_access_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                if new_access_token:
                    _designer_token_cache["token"] = new_access_token
                    # Buffer 5 minutes
                    _designer_token_cache["expires_at"] = now + expires_in - 300
                    return new_access_token

            logger.warning("get_designer_token: failed %d — %s", resp.status_code, resp.text[:200])
            return None
    except Exception as exc:
        logger.error("get_designer_token: exception: %s", exc)
        return None


async def fetch_image_as_base64(url: str, designer_token: str) -> str:
    """
    Fetches the image bytes from Substrate/Designer and converts them to a Markdown base64 embed.
    Fallback chain: designer_token → main access_token → no auth → markdown URL link
    """
    tokens_to_try = [
        ("designer", designer_token),
        ("access", token_store.access_token),
        ("none", None),
    ]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for label, token in tokens_to_try:
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

    # All attempts failed — return as clickable markdown link so user can open it
    logger.error("fetch_image_as_base64: all auth strategies failed for %s", url[:80])
    return f"\n[View Generated Image]({url})\n"


async def fetch_raw_image_base64(url: str, designer_token: str | None) -> tuple[str, str]:
    """
    Fetches the image bytes from Substrate/Designer and returns (base64_str, content_type).
    Fallback chain: designer_token → main access_token → no auth
    """
    tokens_to_try = [
        ("designer", designer_token),
        ("access", token_store.access_token),
        ("none", None),
    ]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for label, token in tokens_to_try:
            if label == "designer" and not token:
                continue  # skip if designer token unavailable
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