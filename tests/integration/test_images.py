"""
Integration tests for POST /v1/images/generations endpoint.

Coverage:
  - Authentication (missing / wrong key)
  - Token not ready (503)
  - Successful generation → url format
  - Successful generation → b64_json format
  - n slicing (n < number of backend-returned URLs)
  - created timestamp present
  - Quota exceeded (429)
  - Content filtered (400)
  - Capacity / service unavailable (503)
  - No image returned – generic failure (500)
  - Empty stream → 500
  - Designer token fetch failure (500)
  - All artifact fetches fail → 500
  - Partial artifact fetch failure → partial success (200)
  - Request validation: missing prompt, n=0, n=11
"""

import base64
import pytest
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

ENDPOINT = "/v1/images/generations"
AUTH_HEADER = {"Authorization": "Bearer sk-m365-copilot-lamdt-2026"}

FAKE_IMAGE_BYTES = b"\x89PNG\r\nFAKE"
FAKE_B64 = base64.b64encode(FAKE_IMAGE_BYTES).decode()
FAKE_IMAGE_URL = "https://designer.microsoft.com/fake-image.png"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestImageGenerationAuth:
    @pytest.mark.asyncio
    async def test_missing_auth_returns_401(self, client: AsyncClient):
        resp = await client.post(ENDPOINT, json={"prompt": "a cat"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_api_key_returns_401(self, client: AsyncClient):
        resp = await client.post(
            ENDPOINT,
            json={"prompt": "a cat"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Token not ready
# ---------------------------------------------------------------------------

class TestImageGenerationTokenNotReady:
    @pytest.mark.asyncio
    async def test_token_not_ready_returns_503(self, client: AsyncClient):
        from app.core.token_store import token_store

        token_store._access_token = None
        token_store._refresh_token = None
        token_store._claims = {}

        resp = await client.post(
            ENDPOINT,
            json={"prompt": "a cat"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["type"] == "service_unavailable"


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------

class TestImageGenerationSuccess:

    @staticmethod
    def _mock_stream_one_image():
        async def fake_stream(self_inner, **kwargs):
            yield "image", {"urls": [FAKE_IMAGE_URL]}
            yield "done", {}

        return patch("app.api.images.SubstrateWSClient.stream_chat", new=fake_stream)

    @pytest.mark.asyncio
    async def test_success_url_format(self, client: AsyncClient):
        """Response data item must contain 'url' with data URI when response_format=url."""
        with (
            self._mock_stream_one_image(),
            patch("app.api.images.get_designer_token", new=AsyncMock(return_value="tok")),
            patch("app.api.images.fetch_raw_image_base64", new=AsyncMock(return_value=(FAKE_B64, "image/png"))),
        ):
            resp = await client.post(
                ENDPOINT,
                json={"prompt": "a beautiful sunset", "response_format": "url"},
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert len(data["data"]) == 1
        item = data["data"][0]
        assert "url" in item
        assert item["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_success_b64_json_format(self, client: AsyncClient):
        """Response data item must contain 'b64_json' when response_format=b64_json."""
        with (
            self._mock_stream_one_image(),
            patch("app.api.images.get_designer_token", new=AsyncMock(return_value="tok")),
            patch("app.api.images.fetch_raw_image_base64", new=AsyncMock(return_value=(FAKE_B64, "image/png"))),
        ):
            resp = await client.post(
                ENDPOINT,
                json={"prompt": "mountain landscape", "response_format": "b64_json"},
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200
        data = resp.json()
        item = data["data"][0]
        assert "b64_json" in item
        assert item["b64_json"] == FAKE_B64

    @pytest.mark.asyncio
    async def test_has_created_unix_timestamp(self, client: AsyncClient):
        """Response must include a positive unix integer in 'created'."""
        with (
            self._mock_stream_one_image(),
            patch("app.api.images.get_designer_token", new=AsyncMock(return_value="tok")),
            patch("app.api.images.fetch_raw_image_base64", new=AsyncMock(return_value=(FAKE_B64, "image/png"))),
        ):
            resp = await client.post(
                ENDPOINT,
                json={"prompt": "sunrise"},
                headers=AUTH_HEADER,
            )

        data = resp.json()
        assert isinstance(data.get("created"), int)
        assert data["created"] > 0

    @pytest.mark.asyncio
    async def test_n_slicing_limits_returned_images(self, client: AsyncClient):
        """When backend returns 3 URLs but n=2, only 2 items should appear."""
        three_urls = [
            "https://designer.microsoft.com/img1.png",
            "https://designer.microsoft.com/img2.png",
            "https://designer.microsoft.com/img3.png",
        ]

        async def fake_stream_multi(self_inner, **kwargs):
            yield "image", {"urls": three_urls}
            yield "done", {}

        with (
            patch("app.api.images.SubstrateWSClient.stream_chat", new=fake_stream_multi),
            patch("app.api.images.get_designer_token", new=AsyncMock(return_value="tok")),
            patch("app.api.images.fetch_raw_image_base64", new=AsyncMock(return_value=(FAKE_B64, "image/png"))),
        ):
            resp = await client.post(
                ENDPOINT,
                json={"prompt": "collage", "n": 2},
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

class TestImageGenerationFailureClassification:

    @staticmethod
    def _patch_stream_text(text: str):
        """Patch stream_chat to yield a text event then done (0 images)."""
        async def fake_stream(self_inner, **kwargs):
            yield "text", {"text": text}
            yield "done", {}

        return patch("app.api.images.SubstrateWSClient.stream_chat", new=fake_stream)

    @pytest.mark.asyncio
    async def test_quota_exceeded_returns_429(self, client: AsyncClient):
        with self._patch_stream_text("can't generate any more images today"):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "quota_exceeded"

    @pytest.mark.asyncio
    async def test_content_filtered_returns_400(self, client: AsyncClient):
        with self._patch_stream_text("that goes against policy"):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "content_policy_violation"

    @pytest.mark.asyncio
    async def test_capacity_exceeded_returns_503(self, client: AsyncClient):
        with self._patch_stream_text("having trouble creating image, try again later"):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "capacity_exceeded"

    @pytest.mark.asyncio
    async def test_unknown_no_image_returns_500(self, client: AsyncClient):
        with self._patch_stream_text("some unrecognised backend error occurred"):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "image_generation_failed"

    @pytest.mark.asyncio
    async def test_empty_stream_returns_500(self, client: AsyncClient):
        """Backend yields done without any text/images → 500."""
        async def fake_stream_empty(self_inner, **kwargs):
            yield "done", {}

        with patch("app.api.images.SubstrateWSClient.stream_chat", new=fake_stream_empty):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Designer token / artifact fetch errors
# ---------------------------------------------------------------------------

class TestImageGenerationArtifactErrors:

    @staticmethod
    def _patch_stream_one_image():
        async def fake_stream(self_inner, **kwargs):
            yield "image", {"urls": [FAKE_IMAGE_URL]}
            yield "done", {}

        return patch("app.api.images.SubstrateWSClient.stream_chat", new=fake_stream)

    @pytest.mark.asyncio
    async def test_designer_token_failure_returns_500(self, client: AsyncClient):
        with (
            self._patch_stream_one_image(),
            patch("app.api.images.get_designer_token", side_effect=RuntimeError("MSAL error")),
        ):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)

        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "designer_token_failed"

    @pytest.mark.asyncio
    async def test_all_artifacts_fail_returns_500(self, client: AsyncClient):
        """If fetch_raw_image_base64 raises for every URL, data_list is empty → 500."""
        with (
            self._patch_stream_one_image(),
            patch("app.api.images.get_designer_token", new=AsyncMock(return_value="tok")),
            patch("app.api.images.fetch_raw_image_base64", side_effect=RuntimeError("HTTP 403")),
        ):
            resp = await client.post(ENDPOINT, json={"prompt": "test"}, headers=AUTH_HEADER)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_partial_artifact_failure_returns_remaining_image(self, client: AsyncClient):
        """If only one of two artifact fetches fails, the remaining image is still returned."""
        two_urls = [
            "https://designer.microsoft.com/ok.png",
            "https://designer.microsoft.com/fail.png",
        ]

        async def fake_stream_two(self_inner, **kwargs):
            yield "image", {"urls": two_urls}
            yield "done", {}

        call_count = 0

        async def fetch_side_effect(url, token):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("HTTP 403 on second image")
            return FAKE_B64, "image/png"

        with (
            patch("app.api.images.SubstrateWSClient.stream_chat", new=fake_stream_two),
            patch("app.api.images.get_designer_token", new=AsyncMock(return_value="tok")),
            patch("app.api.images.fetch_raw_image_base64", side_effect=fetch_side_effect),
        ):
            resp = await client.post(
                ENDPOINT,
                json={"prompt": "test", "n": 2},
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1


# ---------------------------------------------------------------------------
# Request validation (Pydantic / FastAPI)
# ---------------------------------------------------------------------------

class TestImageGenerationRequestValidation:
    @pytest.mark.asyncio
    async def test_missing_prompt_returns_422(self, client: AsyncClient):
        resp = await client.post(ENDPOINT, json={"model": "dall-e-3"}, headers=AUTH_HEADER)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_n_zero_returns_422(self, client: AsyncClient):
        resp = await client.post(ENDPOINT, json={"prompt": "test", "n": 0}, headers=AUTH_HEADER)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_n_above_max_returns_422(self, client: AsyncClient):
        resp = await client.post(ENDPOINT, json={"prompt": "test", "n": 11}, headers=AUTH_HEADER)
        assert resp.status_code == 422
