import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_unauthenticated(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["token_valid"] is True


@pytest.mark.asyncio
async def test_models_list_requires_auth(client: AsyncClient):
    # No header
    resp = await client.get("/v1/models")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_list_authenticated(client: AsyncClient):
    resp = await client.get(
        "/v1/models",
        headers={"Authorization": "Bearer sk-m365-copilot-secret-key"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert len(data["data"]) >= 4
    assert data["data"][0]["id"] == "m365-copilot"
