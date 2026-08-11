import pytest
import asyncio
from typing import AsyncGenerator
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.config import settings
from app.main import app as fastapi_app
from app.core.token_store import token_store


@pytest.fixture(autouse=True)
def mock_token_store_valid():
    """
    Ensure token store is marked as valid for general unit & api tests.
    """
    token_store.set_tokens(
        access_token="eyJhbGciOiJSUzI1NiIsImtpZCI6IjEifQ.eyJvaWQiOiJmYWtlLW9pZCIsInRpZCI6ImZha2UtdGlkIiwiZXhwIjoyNTI0NjA4MDAwLCJ1cG4iOiJ0ZXN0QHVwbi5jb20ifQ.signature",
        refresh_token="fake-refresh-token"
    )
    yield
    token_store._access_token = None
    token_store._refresh_token = None
    token_store._claims = {}


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class MockWebSocket:
    def __init__(self, responses: list):
        self.responses = responses
        self.sent = []
        self.closed = False

    async def send(self, data: str):
        self.sent.append(data)

    async def recv(self) -> str:
        if not self.responses:
            # Block or close connection simulation
            await asyncio.sleep(0.5)
            raise ConnectionAbortedError("Mock WebSocket closed")
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.closed = True
