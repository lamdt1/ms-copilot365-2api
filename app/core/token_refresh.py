import asyncio
import logging
import time
import httpx
from typing import Optional

from app.config import settings
from app.core.token_store import token_store

logger = logging.getLogger(__name__)


M365_SUBSTRATE_SCOPE = (
    "https://substrate.office.com/sydney/M365Chat.Read "
    "https://substrate.office.com/sydney/sydney.readwrite "
    "openid profile offline_access"
)
DESIGNER_SCOPE = "https://designerappservice.officeapps.live.com/.default openid profile offline_access"
CLIENT_ID = "c0ab8ce9-e9a0-42e7-b064-33d422df41f1"


async def refresh_via_entra_id() -> bool:
    """
    Refreshes the access token using the cached refresh token directly against Entra ID.
    Tries tenant-specific endpoint first, falling back to /common if needed.
    Returns True if refresh was successful.
    """
    refresh_token = token_store.refresh_token
    if not refresh_token:
        logger.warning("TokenRefresh: No refresh_token available in token_store")
        return False

    tenant_id = token_store.tid or settings.MODEL_TONE_MAP.get("m365_tenant_id", "common")
    endpoints = [f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"]
    if tenant_id != "common":
        endpoints.append("https://login.microsoftonline.com/common/oauth2/v2.0/token")

    payload = {
        "grant_type": "refresh_token",
        "scope": M365_SUBSTRATE_SCOPE,
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "SKU": "msal.js.browser",
        "VER": "5.9.0",
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://m365.cloud.microsoft",
        "Referer": "https://m365.cloud.microsoft/",
    }

    for url in endpoints:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                logger.info("TokenRefresh: Sending refresh token rotation request to Entra ID (%s)...", url)
                resp = await client.post(url, data=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    new_access_token = data.get("access_token")
                    new_refresh_token = data.get("refresh_token")
                    if new_access_token:
                        token_store.set_tokens(new_access_token, new_refresh_token)
                        logger.info("TokenRefresh: Successfully rotated tokens via Entra ID (%s)", url)
                        return True
                    else:
                        logger.error("TokenRefresh: Success response missing access_token from %s", url)
                else:
                    logger.error(
                        "TokenRefresh: Entra ID (%s) returned status %d: %s",
                        url,
                        resp.status_code,
                        resp.text
                    )
        except Exception as exc:
            logger.error("TokenRefresh: Entra ID request exception for %s: %s", url, exc)

    return False


async def acquire_designer_token() -> Optional[str]:
    """
    Acquires a dedicated access token for Microsoft Designer using the refresh token.
    Scope: https://designerappservice.officeapps.live.com/.default
    """
    if token_store.is_designer_token_valid:
        return token_store.designer_token

    refresh_token = token_store.refresh_token
    if not refresh_token:
        logger.warning("TokenRefresh: No refresh_token available for Designer token acquisition")
        return None

    tenant_id = token_store.tid or settings.MODEL_TONE_MAP.get("m365_tenant_id", "common")
    endpoints = [f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"]
    if tenant_id != "common":
        endpoints.append("https://login.microsoftonline.com/common/oauth2/v2.0/token")

    payload = {
        "grant_type": "refresh_token",
        "scope": DESIGNER_SCOPE,
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "SKU": "msal.js.browser",
        "VER": "5.9.0",
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://m365.cloud.microsoft",
        "Referer": "https://m365.cloud.microsoft/",
    }

    for url in endpoints:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                logger.info("TokenRefresh: Requesting Designer token from %s...", url)
                resp = await client.post(url, data=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    new_token = data.get("access_token")
                    expires_in = data.get("expires_in", 3600)
                    new_refresh = data.get("refresh_token")
                    if new_refresh:
                        token_store.set_tokens(token_store.access_token, new_refresh)
                    if new_token:
                        token_store.set_designer_token(new_token, expires_in=expires_in)
                        logger.info("TokenRefresh: Successfully acquired Designer token")
                        return new_token
                else:
                    logger.error("TokenRefresh: Designer token request (%s) status %d: %s", url, resp.status_code, resp.text)
        except Exception as exc:
            logger.error("TokenRefresh: Designer token exception for %s: %s", url, exc)

    return None


class TokenRefresher:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._nudge_callback = None

    def register_nudge_callback(self, callback):
        self._nudge_callback = callback

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TokenRefresher: background monitor task started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TokenRefresher: background monitor task stopped")

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(60.0)
                await self.check_and_refresh()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("TokenRefresher: error in check loop: %s", exc)

    async def check_and_refresh(self) -> bool:
        sec_remaining = token_store.seconds_remaining
        margin = settings.TOKEN_PREFETCH_MARGIN

        if not token_store.is_valid or sec_remaining < margin:
            logger.info(
                "TokenRefresher: Token invalid or expiring in %d seconds (margin %d). Triggering refresh...",
                sec_remaining,
                margin
            )
            # Try main Entra ID OAuth refresh first if refresh_token exists
            if token_store.refresh_token:
                success = await refresh_via_entra_id()
                if success:
                    return True

            # If OAuth fails (or no refresh_token), fallback to Camoufox nudge/reload
            if self._nudge_callback:
                logger.warning("TokenRefresher: Direct Entra ID rotation unavailable/failed. Triggering Camoufox nudge/reload...")
                try:
                    success_nudge = await self._nudge_callback()
                    if success_nudge:
                        logger.info("TokenRefresher: Successfully refreshed token via Camoufox nudge/reload")
                        return True
                except Exception as exc:
                    logger.error("TokenRefresher: Camoufox nudge error: %s", exc)

            logger.warning("TokenRefresher: Token expired and cannot be refreshed automatically (no valid refresh_token or nudge response)")
            return False

        return True


token_refresher = TokenRefresher()
