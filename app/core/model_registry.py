"""
app/core/model_registry.py

Dynamic model registry that reflects the authenticated M365 Copilot account's
actual entitlements. Parses the licenseType from the intercepted WebSocket URL
and filters MODEL_TONE_MAP to only expose models the account can use.

Singleton: import model_registry from this module.
"""
import logging
import time
from typing import Optional
from urllib.parse import urlparse, parse_qs

from app.config import settings

logger = logging.getLogger(__name__)

# Unix epoch timestamp used as a stable "created" value for all model entries
_MODEL_CREATED_TS = 1719360000


class ModelRegistry:
    """
    In-memory registry of available models for the current M365 account.

    State:
        _models:        Cached list of OpenAI-compatible model dicts.
                        Empty until the first WS URL is intercepted; thereafter
                        always contains at least one entry.
        _license_type:  Detected licenseType string (e.g. "Starter", "Premium").
        _scenario:      Detected scenario string (e.g. "OfficeWebIncludedCopilot").
        _last_updated:  Epoch time of the last successful update.
    """

    def __init__(self) -> None:
        self._models: list[dict] = []
        self._license_type: Optional[str] = None
        self._scenario: Optional[str] = None
        self._last_updated: Optional[float] = None

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def license_type(self) -> Optional[str]:
        """Detected licenseType from the last intercepted WS URL, or None."""
        return self._license_type

    def get_models(self) -> list[dict]:
        """
        Return the current filtered model list.

        Falls back to the full MODEL_TONE_MAP when no WS URL has been
        intercepted yet.  Guarantees a non-empty list is always returned.
        """
        if not self._models:
            return self._build_fallback_models()
        return list(self._models)

    def update_from_ws_url(self, ws_url: str) -> None:
        """
        Parse licenseType / scenario from the Chathub WebSocket URL, filter
        MODEL_TONE_MAP accordingly, update the cached model list, and log any
        changes.

        Called by TokenStore.update_tokens() whenever a new WS URL arrives
        (browser login or token refresh).
        """
        if not ws_url:
            return

        try:
            license_type, scenario = self._parse_license_from_url(ws_url)
            new_models = self._filter_models_by_license(license_type)

            # Guarantee non-empty
            if not new_models:
                logger.warning(
                    "ModelRegistry: filtering by licenseType=%r produced zero models; "
                    "falling back to full model list",
                    license_type,
                )
                new_models = self._build_fallback_models()

            self._log_diff(new_models)

            self._models = new_models
            self._license_type = license_type
            self._scenario = scenario
            self._last_updated = time.time()

            logger.info(
                "ModelRegistry: Updated model list (%d models) for licenseType=%r scenario=%r",
                len(self._models),
                self._license_type,
                self._scenario,
            )

        except Exception as exc:
            logger.error(
                "ModelRegistry: update_from_ws_url failed (%s); keeping previous model list",
                exc,
            )

    # ── private helpers ───────────────────────────────────────────────────────

    def _build_fallback_models(self) -> list[dict]:
        """
        Build the full model list from MODEL_TONE_MAP + MODEL_DESCRIPTIONS.
        Used as both the pre-login fallback and the fail-open response for
        unknown license types.
        """
        return [
            self._make_entry(model_id)
            for model_id in settings.MODEL_TONE_MAP
        ]

    def _parse_license_from_url(self, ws_url: str) -> tuple[Optional[str], Optional[str]]:
        """
        Extract ``licenseType`` and ``scenario`` query parameters from a
        substrate.office.com Chathub WebSocket URL.

        Returns (license_type, scenario) — either may be None if the parameter
        is absent.
        """
        try:
            parsed = urlparse(ws_url)
            params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
            license_type = params.get("licenseType") or None
            scenario = params.get("scenario") or None
            logger.debug(
                "ModelRegistry: Parsed licenseType=%r scenario=%r from WS URL",
                license_type, scenario,
            )
            return license_type, scenario
        except Exception as exc:
            logger.warning("ModelRegistry: failed to parse WS URL (%s)", exc)
            return None, None

    def _filter_models_by_license(self, license_type: Optional[str]) -> list[dict]:
        """
        Return model entries whose tones are allowed for *license_type*.

        Falls open (returns all models) when:
        - license_type is None (pre-login / parse failure)
        - license_type is not in LICENSE_TONE_MAP (unknown tier)
        """
        if not license_type or license_type not in settings.LICENSE_TONE_MAP:
            if license_type:
                logger.warning(
                    "ModelRegistry: Unknown licenseType=%r — returning full model list (fail-open)",
                    license_type,
                )
            return self._build_fallback_models()

        allowed_tones = set(settings.LICENSE_TONE_MAP[license_type])
        filtered = [
            self._make_entry(model_id)
            for model_id, tone in settings.MODEL_TONE_MAP.items()
            if tone in allowed_tones
        ]
        return filtered

    def _make_entry(self, model_id: str) -> dict:
        """Build a single OpenAI-compatible model dict from config."""
        meta = settings.MODEL_DESCRIPTIONS.get(model_id, {})
        return {
            "id": model_id,
            "object": "model",
            "created": _MODEL_CREATED_TS,
            "owned_by": meta.get("owned_by", "microsoft"),
            "description": meta.get("description", ""),
        }

    def _log_diff(self, new_models: list[dict]) -> None:
        """Log added / removed models compared to the current cached list."""
        if not self._models:
            return  # First update — no diff to show

        old_ids = {m["id"] for m in self._models}
        new_ids = {m["id"] for m in new_models}

        added = new_ids - old_ids
        removed = old_ids - new_ids

        if added:
            logger.info("ModelRegistry: Models added: %s", sorted(added))
        if removed:
            logger.info("ModelRegistry: Models removed: %s", sorted(removed))
        if not added and not removed:
            logger.debug("ModelRegistry: Model list unchanged (%d models)", len(new_models))


# Singleton — import and use this instance everywhere
model_registry = ModelRegistry()
