import asyncio
import json
import uuid
import urllib.parse
import websockets
from app.core.token_store import token_store
from app.substrate.payload_builder import build_ws_url, build_metrics_frame, DEFAULT_OPTIONS_SETS, DEFAULT_ALLOWED_MESSAGE_TYPES
from app.substrate.turn_parser import RECORD_SEPARATOR


async def test_matching_session():
    if not token_store.is_valid:
        print("Token invalid!")
        return

    session_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    session_nodash = session_id.replace("-", "")

    url = build_ws_url(
        oid=token_store.oid,
        tid=token_store.tid,
        access_token=token_store.access_token,
        session_id=session_id,
        conversation_id=conversation_id
    )

    arguments = {
        "source": "officeweb",
        "clientCorrelationId": session_nodash,
        "sessionId": session_id,  # MUST match X-SessionId
        "optionsSets": DEFAULT_OPTIONS_SETS,
        "allowedMessageTypes": DEFAULT_ALLOWED_MESSAGE_TYPES,
        "streamingMode": "ConciseWithPadding",
        "isStartOfSession": True,
        "conversationId": conversation_id,
        "message": {
            "text": "Xin chào! Trả lời ngắn gọn 1 câu bằng tiếng Việt.",
            "messageType": "Chat",
            "author": "user"
        },
        "clientInfo": {
            "clientName": "m365copilot",
            "clientVersion": "1.0.0"
        }
    }

    chat_frame = {
        "type": 4,
        "target": "chat",
        "arguments": [arguments]
    }

    print("Connecting with matched sessionId & clientCorrelationId...")
    async with websockets.connect(
        url,
        origin="https://m365.cloud.microsoft",
        user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEPARATOR)
        ack = await ws.recv()
        print("ACK:", repr(ack))

        payload = json.dumps(chat_frame) + RECORD_SEPARATOR
        print("Sending chat frame...")
        await ws.send(payload)

        for i in range(15):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                print(f"RECV[{i}]:", repr(msg[:300]))
                if '"type":3' in msg:
                    print("DONE FRAME RECEIVED!")
                    break
            except Exception as e:
                print("Recv end:", e)
                break

if __name__ == "__main__":
    asyncio.run(test_matching_session())
