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


async def get_designer_token() -> str:
    """
    Fetches the designerappservice token using the refresh_token from token_store.
    Caches the token in-memory until expiration.
    """
    now = time.time()
    if _designer_token_cache["token"] and now < _designer_token_cache["expires_at"]:
        return _designer_token_cache["token"]

    refresh_token = token_store.refresh_token
    if not refresh_token:
        raise ValueError("No refresh_token available to mint designer token.")

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

        logger.error("Failed to fetch designer token: %d %s", resp.status_code, resp.text)
        raise RuntimeError(f"Could not mint designer token. Status: {resp.status_code}")


async def fetch_image_as_base64(url: str, designer_token: str) -> str:
    """
    Fetches the image bytes from Substrate/Designer and converts them to a Markdown base64 embed.
    """
    headers = {
        "Authorization": f"Bearer {designer_token}"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error("Image artifact fetch failed: %d %s", resp.status_code, resp.text)
            return f"\n[Error: Failed to load image from {url}]\n"

        content_type = resp.headers.get("content-type", "image/png")
        b64_data = base64.b64encode(resp.content).decode("utf-8")

        return f"\n![Generated Image](data:{content_type};base64,{b64_data})\n"


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