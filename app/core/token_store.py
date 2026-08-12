import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from jose import jwt, JWTError

from app.config import settings

logger = logging.getLogger(__name__)

TOKENS_FILE = Path(os.getenv("CAMOUFOX_USER_DATA_DIR", "/app/data")).parent / "tokens.json"


def _mask(token: str) -> str:
    return token[:8] + "..." if token else "<none>"


class TokenStore:
    def __init__(self, path: Path = TOKENS_FILE):
        self.path = path
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._claims: dict = {}
        self._last_refreshed: Optional[float] = None
        self.intercepted_ws_url: Optional[str] = None  # full URL from browser intercept
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self._access_token = data.get("access_token")
                self._refresh_token = data.get("refresh_token")
                self._last_refreshed = data.get("last_refreshed")
                if self._access_token:
                    self._decode_claims(self._access_token)
                logger.info("TokenStore: loaded from %s (token %s)", self.path, _mask(self._access_token or ""))
            except Exception as exc:
                logger.warning("TokenStore: failed to load %s: %s", self.path, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "last_refreshed": self._last_refreshed,
        }))
        logger.debug("TokenStore: saved (token %s)", _mask(self._access_token or ""))

    # ── claims ────────────────────────────────────────────────────────────────

    def _decode_claims(self, token: str) -> None:
        try:
            self._claims = jwt.get_unverified_claims(token)
            if settings.LOG_TOKEN_CLAIMS:
                logger.debug("TokenStore: claims = %s", self._claims)
        except JWTError as exc:
            logger.warning("TokenStore: JWT decode failed: %s", exc)
            self._claims = {}

    # ── accessors ─────────────────────────────────────────────────────────────

    @property
    def access_token(self) -> Optional[str]:
        return self._access_token

    @property
    def refresh_token(self) -> Optional[str]:
        return self._refresh_token

    @property
    def oid(self) -> Optional[str]:
        return self._claims.get("oid")

    @property
    def tid(self) -> Optional[str]:
        return self._claims.get("tid")

    @property
    def exp(self) -> Optional[int]:
        return self._claims.get("exp")

    @property
    def upn(self) -> Optional[str]:
        return self._claims.get("upn") or self._claims.get("unique_name")

    @property
    def is_valid(self) -> bool:
        if not self._access_token or not self.exp:
            return False
        return time.time() < self.exp

    @property
    def seconds_remaining(self) -> int:
        if not self.exp:
            return 0
        return max(0, int(self.exp - time.time()))

    @property
    def last_refreshed(self) -> Optional[float]:
        return self._last_refreshed

    def set_tokens(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        self._access_token = access_token
        if refresh_token:
            self._refresh_token = refresh_token
        self._last_refreshed = time.time()
        self._decode_claims(access_token)
        self.save()
        logger.info("TokenStore: tokens updated (token %s, exp=%s)", _mask(access_token), self.exp)

    # alias for compatibility
    def update_tokens(self, access_token: str, refresh_token: Optional[str] = None, ws_url: Optional[str] = None) -> None:
        self.set_tokens(access_token, refresh_token)
        if ws_url:
            self.intercepted_ws_url = ws_url
            logger.debug("TokenStore: intercepted_ws_url stored (%d chars)", len(ws_url))


# singleton
token_store = TokenStore()
