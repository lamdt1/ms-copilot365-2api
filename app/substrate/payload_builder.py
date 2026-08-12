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
        "source": "officeweb",
        "product": "Office",
        "agentHost": "Bizchat.FullScreen",
        "licenseType": "Starter",
        "isEdu": "false",
        "agent": "web",
        "scenario": "OfficeWebIncludedCopilot",
    }

    encoded = urllib.parse.urlencode(query_params)
    return f"{base}?{encoded}"


DEFAULT_OPTIONS_SETS = [
    "search_result_progress_messages_with_search_queries",
    "update_textdoc_response_after_streaming",
    "deepleo_networking_timeout_10minutes_canmore",
    "cwc_flux_image",
    "cwc_code_interpreter",
    "cwc_code_interpreter_amsfix",
    "cwcfluxgptv",
    "flux_v3_gptv_enable_upload_multi_image_in_turn_wo_ch",
    "gptvnorm2048",
    "cwc_code_interpreter_citation_fix",
    "code_interpreter_interactive_charts",
    "cwc_code_interpreter_interactive_charts_inline_image",
    "code_interpreter_matplotlib_patching",
    "cwc_fileupload_odb",
    "update_memory_plugin",
    "add_custom_instructions",
    "cwc_flux_v3",
    "flux_v3_progress_messages",
    "enable_batch_token_processing",
    "enable_gg_gpt",
    "async_client_interaction",
    "enable_inferred_memory_read",
    "cwc_table_context",
    "flux_v3_references",
    "flux_v3_references_entities",
    "flux_v3_references_ci",
    "add_filestore_filetype",
    "cwc_code_interpreter_citation_sourceannotations",
    "cdxcwc_code_interpreter_hallucinated_url_filter",
    "flux_v3_image_gen_enable_dimensions",
    "flux_v3_image_gen_enable_non_watermarked_storage",
    "flux_v3_image_gen_enable_icon_dimensions",
    "flux_v3_image_gen_enable_system_text_with_params",
    "flux_v3_image_gen_enable_designer_dimensions_meta_prompting_in_system_prompts",
    "flux_v3_image_gen_enable_story",
    "rich_responses"
]

DEFAULT_ALLOWED_MESSAGE_TYPES = [
    "Chat",
    "Suggestion",
    "InternalSearchQuery",
    "Disengaged",
    "InternalLoaderMessage",
    "Progress",
    "GeneratedCode",
    "RenderCardRequest",
    "AdsQuery",
    "SemanticSerp",
    "GenerateContentQuery",
    "GenerateGraphicArt",
    "SearchQuery",
    "ConfirmationCard",
    "AuthError",
    "DeveloperLogs",
    "TriggerPlugin",
    "HintInvocation",
    "MemoryUpdate",
    "EndOfRequest",
    "TriggerConfirmation",
    "ResumeInvokeAction",
    "ResumeUserInputRequest",
    "TriggerUserInputRequest",
    "EscapeHatch",
    "TriggerPluginAuth",
    "ResumePluginAuth",
    "SideBySide",
    "ReferencesListComplete",
    "SwitchResponding"
]


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
    session_nodash = conversation_id.replace("-", "")

    arguments = {
        "source": "officeweb",
        "clientCorrelationId": session_nodash,
        "sessionId": conversation_id,
        "optionsSets": DEFAULT_OPTIONS_SETS,
        "allowedMessageTypes": DEFAULT_ALLOWED_MESSAGE_TYPES,
        "streamingMode": "ConciseWithPadding",
        "isStartOfSession": is_start,
        "conversationId": conversation_id,
        "message": {
            "text": prompt,
            "messageType": "Chat",
            "author": "user"
        },
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
