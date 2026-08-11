import pytest
from httpx import AsyncClient
from app.config import settings


@pytest.mark.asyncio
async def test_images_generations_unauthenticated(client: AsyncClient):
    resp = await client.post(
        "/v1/images/generations",
        json={"prompt": "a cute cat"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_images_generations_missing_prompt(client: AsyncClient):
    api_key = settings.API_KEY.split(",")[0]
    resp = await client.post(
        "/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}"},
        json={}
    )
    assert resp.status_code == 422
