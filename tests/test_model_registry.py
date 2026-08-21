"""
tests/test_model_registry.py

Unit tests for ModelRegistry (app/core/model_registry.py).

Run:
    pytest tests/test_model_registry.py -v
"""
import pytest
from unittest.mock import patch

from app.core.model_registry import ModelRegistry
from app.config import settings


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_registry() -> ModelRegistry:
    """Return a fresh (uninitialized) registry for each test."""
    return ModelRegistry()


def _ws_url(license_type: str = "Standard", scenario: str = "OfficeWebPaidCopilot") -> str:
    return (
        f"wss://substrate.office.com/m365Copilot/Chathub/oid@tid"
        f"?licenseType={license_type}&scenario={scenario}&access_token=fake"
    )


# ── _parse_license_from_url ───────────────────────────────────────────────────

class TestParseLicenseFromUrl:
    def test_starter(self):
        r = _make_registry()
        lt, sc = r._parse_license_from_url(_ws_url("Starter", "OfficeWebIncludedCopilot"))
        assert lt == "Starter"
        assert sc == "OfficeWebIncludedCopilot"

    def test_standard(self):
        r = _make_registry()
        lt, sc = r._parse_license_from_url(_ws_url("Standard", "OfficeWebPaidCopilot"))
        assert lt == "Standard"
        assert sc == "OfficeWebPaidCopilot"

    def test_premium(self):
        r = _make_registry()
        lt, sc = r._parse_license_from_url(_ws_url("Premium", "OfficeWebPaidCopilot"))
        assert lt == "Premium"

    def test_malformed_url_returns_none(self):
        r = _make_registry()
        lt, sc = r._parse_license_from_url("not-a-url")
        # Should not raise; may return None or empty
        assert lt is None or isinstance(lt, str)

    def test_url_without_license_type(self):
        r = _make_registry()
        url = "wss://substrate.office.com/m365Copilot/Chathub/oid@tid?access_token=fake"
        lt, sc = r._parse_license_from_url(url)
        assert lt is None

    def test_empty_url(self):
        r = _make_registry()
        lt, sc = r._parse_license_from_url("")
        assert lt is None


# ── _filter_models_by_license ─────────────────────────────────────────────────

class TestFilterModelsByLicense:
    def test_starter_has_only_basic_tones(self):
        r = _make_registry()
        models = r._filter_models_by_license("Starter")
        ids = {m["id"] for m in models}
        # Starter: magic + Gpt_Quick only
        assert "m365-copilot" in ids   # tone: magic
        assert "m365-quick" in ids     # tone: Gpt_Quick
        assert "m365-think-deeper" not in ids  # tone: Reasoning
        assert "claude-sonnet" not in ids      # tone: Claude_Sonnet

    def test_standard_has_reasoning(self):
        r = _make_registry()
        models = r._filter_models_by_license("Standard")
        ids = {m["id"] for m in models}
        assert "m365-think-deeper" in ids
        assert "claude-sonnet" not in ids

    def test_premium_has_all_models(self):
        r = _make_registry()
        models = r._filter_models_by_license("Premium")
        ids = {m["id"] for m in models}
        assert "m365-copilot" in ids
        assert "m365-quick" in ids
        assert "m365-think-deeper" in ids
        assert "claude-sonnet" in ids

    def test_unknown_license_fails_open(self):
        r = _make_registry()
        models = r._filter_models_by_license("UnknownTier")
        # Should return full fallback set
        assert len(models) == len(settings.MODEL_TONE_MAP)

    def test_none_license_fails_open(self):
        r = _make_registry()
        models = r._filter_models_by_license(None)
        assert len(models) == len(settings.MODEL_TONE_MAP)


# ── get_models fallback ────────────────────────────────────────────────────────

class TestGetModels:
    def test_returns_fallback_when_uninitialized(self):
        r = _make_registry()
        models = r.get_models()
        # Should return all models from MODEL_TONE_MAP (full fallback)
        assert len(models) == len(settings.MODEL_TONE_MAP)

    def test_never_returns_empty_list(self):
        r = _make_registry()
        # Simulate a bad state where _models is explicitly set to empty
        r._models = []
        result = r.get_models()
        assert len(result) > 0

    def test_model_entries_have_required_fields(self):
        r = _make_registry()
        models = r.get_models()
        for m in models:
            assert "id" in m
            assert "object" in m
            assert m["object"] == "model"
            assert "created" in m
            assert "owned_by" in m
            assert "description" in m

    def test_returns_cached_after_update(self):
        r = _make_registry()
        r.update_from_ws_url(_ws_url("Starter"))
        models = r.get_models()
        ids = {m["id"] for m in models}
        assert "m365-think-deeper" not in ids


# ── update_from_ws_url ────────────────────────────────────────────────────────

class TestUpdateFromWsUrl:
    def test_updates_license_type(self):
        r = _make_registry()
        r.update_from_ws_url(_ws_url("Premium"))
        assert r.license_type == "Premium"

    def test_updates_model_list(self):
        r = _make_registry()
        r.update_from_ws_url(_ws_url("Starter"))
        models = r.get_models()
        assert len(models) == 2  # magic + Gpt_Quick only

    def test_handles_empty_url_gracefully(self):
        r = _make_registry()
        r.update_from_ws_url("")   # Should not raise
        assert r.license_type is None

    def test_handles_exception_gracefully(self):
        r = _make_registry()
        r.update_from_ws_url(_ws_url("Starter"))
        original_models = r.get_models()

        # Patch _parse_license_from_url to raise
        with patch.object(r, "_parse_license_from_url", side_effect=RuntimeError("boom")):
            r.update_from_ws_url(_ws_url("Premium"))

        # Model list should be unchanged (kept previous)
        assert r.get_models() == original_models

    def test_model_list_never_empty_after_update(self):
        r = _make_registry()
        # Even for unknown license type, list should not be empty
        r.update_from_ws_url(_ws_url("SomeWeirdTier"))
        assert len(r.get_models()) > 0

    def test_log_diff_on_change(self, caplog):
        import logging
        r = _make_registry()
        r.update_from_ws_url(_ws_url("Premium"))
        with caplog.at_level(logging.INFO, logger="app.core.model_registry"):
            r.update_from_ws_url(_ws_url("Starter"))
        assert any("removed" in rec.message.lower() for rec in caplog.records)
