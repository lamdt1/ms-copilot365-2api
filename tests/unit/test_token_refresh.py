import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.token_store import TokenStore
from app.core.token_refresh import (
    refresh_via_entra_id,
    acquire_designer_token,
    token_refresher,
    M365_SUBSTRATE_SCOPE,
    DESIGNER_SCOPE,
)


@pytest.fixture
def temp_token_store(tmp_path):
    tokens_file = tmp_path / "tokens.json"
    store = TokenStore(path=tokens_file)
    store.set_tokens("access_token_123", "refresh_token_456")
    return store


def test_token_store_designer_properties(temp_token_store):
    assert temp_token_store.designer_token is None
    assert temp_token_store.is_designer_token_valid is False

    temp_token_store.set_designer_token("designer_jwt_abc", expires_in=3600)
    assert temp_token_store.designer_token == "designer_jwt_abc"
    assert temp_token_store.is_designer_token_valid is True

    # Re-load from disk to verify persistence
    loaded = TokenStore(path=temp_token_store.path)
    assert loaded.designer_token == "designer_jwt_abc"
    assert loaded.is_designer_token_valid is True


@pytest.mark.asyncio
async def test_refresh_via_entra_id_success(temp_token_store):
    with patch("app.core.token_refresh.token_store", temp_token_store):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token_789",
            "refresh_token": "new_refresh_token_999",
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            success = await refresh_via_entra_id()

            assert success is True
            assert temp_token_store.access_token == "new_access_token_789"
            assert temp_token_store.refresh_token == "new_refresh_token_999"

            # Verify scope in payload
            call_kwargs = mock_client.post.call_args
            assert call_kwargs is not None
            payload = call_kwargs[1]["data"]
            assert payload["scope"] == M365_SUBSTRATE_SCOPE


@pytest.mark.asyncio
async def test_refresh_via_entra_id_tenant_fallback(temp_token_store):
    with patch("app.core.token_refresh.token_store", temp_token_store):
        # First call fails (tenant endpoint), second call succeeds (common endpoint)
        mock_fail = MagicMock()
        mock_fail.status_code = 400
        mock_fail.text = "Bad Request"

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "access_token": "common_access_token",
            "refresh_token": "common_refresh_token",
        }

        mock_client = AsyncMock()
        mock_client.post.side_effect = [mock_fail, mock_ok]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # Ensure tid is set so tenant endpoint is tried first before common fallback
            temp_token_store._claims = {"tid": "custom-tenant-id", "exp": int(time.time()) + 3600}
            success = await refresh_via_entra_id()

            assert success is True
            assert temp_token_store.access_token == "common_access_token"
            assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_acquire_designer_token_success(temp_token_store):
    with patch("app.core.token_refresh.token_store", temp_token_store):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "designer_token_xyz",
            "expires_in": 3600,
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            token = await acquire_designer_token()

            assert token == "designer_token_xyz"
            assert temp_token_store.designer_token == "designer_token_xyz"
            assert temp_token_store.is_designer_token_valid is True

            call_kwargs = mock_client.post.call_args
            payload = call_kwargs[1]["data"]
            assert payload["scope"] == DESIGNER_SCOPE
