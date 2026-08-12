import asyncio
import json
import uuid
import urllib.parse
import websockets
from app.core.token_store import token_store
from app.substrate.payload_builder import build_ws_url, build_metrics_frame
from app.substrate.turn_parser import RECORD_SEPARATOR


async def test_comb(label, options_sets, msg_types, tone_val, source_val, is_start_val, send_metrics):
    session_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())

    url = build_ws_url(
        oid=token_store.oid,
        tid=token_store.tid,
        access_token=token_store.access_token,
        session_id=session_id,
        conversation_id=conversation_id
    )

    arguments = {
        "source": source_val,
        "optionsSets": options_sets,
        "allowedMessageTypes": msg_types,
        "isStartOfSession": is_start_val,
        "conversationId": conversation_id,
        "message": {
            "text": "Hello, write 1 sentence.",
            "messageType": "Chat",
            "author": "user"
        },
        "tone": tone_val,
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

    payload = json.dumps(chat_frame) + RECORD_SEPARATOR
    if send_metrics:
        payload += json.dumps(build_metrics_frame()) + RECORD_SEPARATOR

    print(f"\n--- Testing [{label}] ---")
    try:
        async with websockets.connect(
            url,
            origin="https://m365.cloud.microsoft",
            user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
        ) as ws:
            await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEPARATOR)
            ack = await ws.recv()

            await ws.send(payload)

            for i in range(5):
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print(f"[{label}] RECV[{i}]:", repr(msg[:300]))
                if '"type":7' in msg or '"type":3' in msg:
                    break
    except Exception as e:
        print(f"[{label}] EXC:", e)


async def main():
    if not token_store.is_valid:
        print("Token invalid!")
        return

    # Standard types
    std_types = ["Chat", "Progress", "ActionRequest", "ConfirmationCard", "GraphicArt", "Disengaged"]
    ext_types = std_types + ["InternalSearchQuery", "InternalSearchResult", "RenderCardRequest", "RichAnswer"]

    # Combination 1: enterprise_flux single
    await test_comb("1: enterprise_flux single", ["enterprise_flux"], std_types, "magic", "chat", True, True)

    # Combination 2: nlu_direct_response_filter
    await test_comb("2: nlu_direct_response_filter", ["nlu_direct_response_filter", "deepleo", "disable_user_ack"], std_types, "magic", "chat", True, True)

    # Combination 3: no metrics frame
    await test_comb("3: no metrics frame", ["enterprise_flux"], std_types, "magic", "chat", True, False)

    # Combination 4: tone Creative
    await test_comb("4: tone Creative", ["enterprise_flux"], std_types, "Creative", "chat", True, True)

    # Combination 5: extended message types
    await test_comb("5: ext message types", ["enterprise_flux", "bizchatfluxv3"], ext_types, "magic", "chat", True, True)

    # Combination 6: source officeweb
    await test_comb("6: source officeweb", ["enterprise_flux"], std_types, "magic", "officeweb", True, True)

if __name__ == "__main__":
    asyncio.run(main())
