import urllib.parse
import uuid
from typing import List, Optional
from app.config import settings


def build_ws_url(
    oid: str,
    tid: str,
    access_token: str,
    session_id: str,
    conversation_id: str,
    variants: Optional[List[str]] = None
) -> str:
    """
    Constructs the Sydney substrate WebSocket connection URL matching Microsoft 365 Copilot web client.
    """
    base = f"wss://substrate.office.com/m365Copilot/Chathub/{oid}@{tid}"
    session_nodash = session_id.replace("-", "")

    query_params = {
        "chatsessionid": session_nodash,
        "XRoutingParameterSessionKey": session_nodash,
        "clientrequestid": session_nodash,
        "X-SessionId": session_id,
        "ConversationId": conversation_id,
        "access_token": access_token,
        "variants": ",".join(variants or ["enterprise_flux", "deepleo", "harmony", "enlightened"]),
        "source": '"officeweb"',
        "product": "Office",
        "agentHost": "Bizchat.FullScreen",
        "licenseType": "Starter",
        "isEdu": "false",
        "agent": "web",
        "scenario": "OfficeWebIncludedCopilot",
    }

    encoded = urllib.parse.urlencode(query_params)
    return f"{base}?{encoded}"


def build_chat_invocation(
    prompt: str,
    conversation_id: str,
    tone: str = "magic",
    agent_id: Optional[str] = None,
    is_start: bool = True,
    generate_images: bool = False
) -> dict:
    """
    Builds the type 4 chat invocation frame to initiate conversation.
    """
    tone_value = settings.MODEL_TONE_MAP.get(tone, tone)

    arguments = {
        "source": "chat",
        "optionsSets": ["enterprise_flux", "deepleo", "harmony", "enlightened"],
        "allowedMessageTypes": [
            "Chat",
            "Progress",
            "ActionRequest",
            "ConfirmationCard",
            "GraphicArt",
            "Disengaged"
        ],
        "isStartOfSession": is_start,
        "conversationId": conversation_id,
        "message": {
            "text": prompt,
            "messageType": "Chat",
            "author": "user"
        },
        "tone": tone_value,
        "clientInfo": {
            "clientName": "m365copilot",
            "clientVersion": "1.0.0"
        }
    }

    # Image gen requirement
    if generate_images:
        arguments["generateImages"] = True

    # If using Copilot Studio Custom Agent
    if agent_id:
        arguments["threadLevelGptId"] = {"id": agent_id}

    return {
        "type": 4,
        "target": "chat",
        "arguments": [arguments]
    }


def build_metrics_frame() -> dict:
    """
    Builds the metric frame (type 1) required to be sent alongside the chat invocation.
    If metrics frame is omitted, substrate backend hangs.
    """
    return {
        "type": 1,
        "target": "Metrics",
        "arguments": [{
            "Timestamps": {
                "ClientPreHandshakeBufferTime": 0,
                "ClientPostHandshakeBufferTime": 150
            }
        }]
    }
