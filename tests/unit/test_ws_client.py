import pytest
from unittest.mock import AsyncMock, MagicMock
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from websockets.frames import Close
from app.substrate.ws_client import SubstrateWSClient, RECORD_SEPARATOR


@pytest.mark.asyncio
async def test_ws_client_clean_close():
    mock_ws = AsyncMock()
    close_frame = Close(1000, "OK")
    mock_ws.recv.side_effect = [
        f"{{}}{RECORD_SEPARATOR}",  # Handshake response
        ConnectionClosedOK(close_frame, close_frame, True)
    ]

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_ws

    client = SubstrateWSClient("oid", "tid", "token", "session", "conv", ws_factory=mock_factory)
    events = []
    async for ev_type, payload in client.stream_chat("hello"):
        events.append((ev_type, payload))

    # Should not produce error event for clean 1000 OK close
    error_events = [e for e in events if e[0] == "error"]
    assert len(error_events) == 0


@pytest.mark.asyncio
async def test_ws_client_error_close():
    mock_ws = AsyncMock()
    close_frame = Close(1006, "abnormal closure")
    mock_ws.recv.side_effect = [
        f"{{}}{RECORD_SEPARATOR}",
        ConnectionClosedError(close_frame, close_frame, True)
    ]

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_ws

    client = SubstrateWSClient("oid", "tid", "token", "session", "conv", ws_factory=mock_factory)
    events = []
    async for ev_type, payload in client.stream_chat("hello"):
        events.append((ev_type, payload))

    error_events = [e for e in events if e[0] == "error"]
    assert len(error_events) == 1
    assert "1006" in error_events[0][1]["message"]
