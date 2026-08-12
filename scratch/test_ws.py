import asyncio
import json
import websockets
from app.core.token_store import token_store
from app.substrate.payload_builder import build_chat_invocation, build_metrics_frame
from app.substrate.turn_parser import RECORD_SEPARATOR


async def test_intercepted_url():
    if not token_store.is_valid:
        print("Token invalid!")
        return

    # Use the exact session_id and conversation_id intercepted from Camoufox
    session_nodash = "03ea490ad5cc8b29ffdd77da23100257"
    session_id = "03ea490a-d5cc-8b29-ffdd-77da23100257"
    conversation_id = "fde6eae1-96b2-4b71-a5a7-f2ca29a6a3ce"

    base = f"wss://substrate.office.com/m365Copilot/Chathub/{token_store.oid}@{token_store.tid}"
    variants = "EnableMcpServerWidgets,feature.EnableMcpServerWidgets,feature.EnableImageGenInsufficientTokensThrottled,feature.EnableImageGenSystemCapacityThrottled,feature.EnableLuForChatCIQ,feature.enableChatCIQPlugin,EnableRequestPlugins,feature.EnableSensitivityLabels,EnableUnsupportedUrlDetector,feature.IsCustomEngineCopilotEnabled,feature.bizchatfluxv3,feature.enablechatpages,feature.enableCodeCanvas,feature.turnOnDARecommendation,feature.IsStreamingModeInChatRequestEnabled,IncludeSourceAttributionsConcise,SkipPublishEmptyMessage,feature.EnableDeduplicatingSourceAttributions,feature.IsCitationsReferencesOutputEnabled,feature.enableDeltaStreamingForReferences,feature.enableIncludeReferencesInDeltaResponse,feature.enablereferencesforagents,feature.EnableCodeInterpreterConversion,agt_module_attr_enableReferencesForCodeInterpreter,agt_module_enableCodeInterpreterHallucinatedUrlFilter,Enable3PActionProgressMessages,feature.enableClientWebRtc,feature.EnableMeetingRecapOfSeriesMeetingWithCiq,feature.EnableReferencesListCompleteSignal,feature.StorageMessageSplitDisabled,SingletonEnvOn,cdxenablefccinmainline,EnableComposeWidget,feature.EnableResearcherTodoListObserver,feature.EnableResearcherTodoObserverSlim,feature.EnableResearchSteering,feature.EnableResearcherTodoSummarizerPacing,-agt_researcheragent_enableMemoryRead,feature.cwcallowedos,feature.EnableMergingPureDeltas,feature.disabledisallowedmsgs,feature.enableCitationsForSynthesisData,feature.EnableConversationShareApis,feature.EnableConversationShareApisForMsa,feature.enableGenerateGraphicArtOptionsSet,cdximagen,feature.EnableUpdatedUXForConfirmationDialog,feature.EnableContentApiandDocTypeHtmlInRichAnswers,cdxgrounding_api_v2_rich_web_answers_reference_bottom_force,cdxenablerenderforisocomp,feature.EnableClientFileURLSupportForOfficeWebPaidCopilot,feature.EnableDesignEditorImageGrounding,feature.EnableDesignerEditor,feature.EnableSkipRehydrationForSpeCIdImages,feature.EnablePersonalization,rich_responses,feature.EnableBase64DataInMessageAnnotations,feature.EnableSkipEmittingMessageOnFlush,feature.EnableRemoveEmptySourceAttributions,feature.EnableRemoveStreamingMode,feature.OfficeWebToHelix,feature.OfficeDesktopToHelix,feature.M365TeamsHubToHelix,feature.OwaHubToHelix,feature.MonarchHubToHelix,feature.Win32OutlookHubToHelix,feature.MacOutlookHubToHelix,Agt_bizchat_enableGpt5ForHelix"

    url = (
        f"{base}?"
        f"chatsessionid={session_nodash}&"
        f"XRoutingParameterSessionKey={session_nodash}&"
        f"clientrequestid={session_nodash}&"
        f"X-SessionId={session_id}&"
        f"ConversationId={conversation_id}&"
        f"access_token={token_store.access_token}&"
        f"variants={variants}&"
        f'source="officeweb"&'
        f"product=Office&"
        f"agentHost=Bizchat.FullScreen&"
        f"licenseType=Starter&"
        f"isEdu=false&"
        f"agent=web&"
        f"scenario=OfficeWebIncludedCopilot"
    )

    print("Connecting with intercepted SessionID:", session_id)

    async with websockets.connect(
        url,
        origin="https://m365.cloud.microsoft",
        user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEPARATOR)
        ack = await ws.recv()
        print("ACK:", repr(ack))

        chat_frame = build_chat_invocation(
            prompt="Hi, reply in 1 short sentence.",
            conversation_id=conversation_id,
            tone="magic",
            is_start=False
        )
        metrics_frame = build_metrics_frame()

        payload = json.dumps(chat_frame) + RECORD_SEPARATOR + json.dumps(metrics_frame) + RECORD_SEPARATOR
        await ws.send(payload)
        print("Sent Invocation with intercepted session. Waiting for response...")

        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                print(f"RECV[{i}]:", repr(msg[:300]))
            except Exception as e:
                print("Recv end:", e)
                break

if __name__ == "__main__":
    asyncio.run(test_intercepted_url())
