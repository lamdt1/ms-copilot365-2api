# Software Requirements Specification (SRS)
## Project: M365 Copilot OpenAI Compatible API (Camoufox + FastAPI + Docker Engine)

**Document ID:** `SRS-M365-COPILOT-API-001`  
**Version:** `2.0.0 (Comprehensive Specification)`  
**Date:** `2026-08-11`  
**Status:** `Approved (Production-Ready Specification)`  
**Author:** AI Assistant & Core Development Team  

> [!IMPORTANT]
> This SRS document has been specialized for the **Docker Container First** architecture. The system provides an interactive login mechanism via **Web-based noVNC (`http://localhost:6080`)**, allowing users to perform initial login / MFA directly inside the container without needing local Python or Firefox installations.

---

## Table of Contents

- [1. System Overview & Scope](#1-system-overview--scope)
- [2. Docker User Experience & Login Workflow](#2-docker-user-experience--login-workflow)
- [3. Substrate Chathub Protocol (SignalR WebSocket)](#3-substrate-chathub-protocol-signalr-websocket)
- [4. REST Endpoints List (OpenAI Extended Specification)](#4-rest-endpoints-list-openai-extended-specification)
- [5. Request & Response Schemas](#5-request--response-schemas)
- [6. Extended Features](#6-extended-features)
- [7. Error Handling & Resilience](#7-error-handling--resilience)
- [8. Docker Specification](#8-docker-specification)
- [9. Environment Variables & Configuration](#9-environment-variables--configuration)
- [10. Non-Functional Requirements](#10-non-functional-requirements)
- [11. Container Verification Plan](#11-container-verification-plan)
- [12. Glossary](#12-glossary)
- [13. Revision History](#13-revision-history)

---

## 1. System Overview & Scope

### 1.1. Project Goals

The project provides an **All-in-One Docker Container** solution to transform the Microsoft 365 Copilot Web UI into an **OpenAI-Compatible REST API server**:

1. **Docker Container Engine**: Pre-packages Python 3.11, Camoufox Anti-detect Engine, Xvfb virtual frame buffer, and noVNC Web UI Server.
2. **Built-in noVNC Login UI (`:6080`)**: Users access any web browser to open the noVNC interface for visual M365 login / MFA entry directly within the container.
3. **Camoufox Engine (Headless Background)**: Automatically extracts the Sydney WebSocket `access_token` (`wss://substrate.office.com`) and maintains background sessions.
4. **FastAPI Engine (`:8000`)**: Provides standard OpenAI REST API (`/v1/chat/completions`), Anthropic Messages (`/v1/messages`), and OpenAI Responses (`/v1/responses`).
5. **Substrate SignalR Protocol Handler**: Real-time connection to Microsoft Sydney server via SignalR WebSocket protocol (Record Separator `0x1E`).

### 1.2. Problem Statement & Why Proxy is Needed

**Microsoft 365 Copilot (BizChat/Office Web Copilot)** does not provide a standard OpenAPI REST API for developers. Instead, the official web interface (`m365.cloud.microsoft`) communicates with the Sydney backend server via **SignalR over WebSocket** (`wss://substrate.office.com/m365Copilot/Chathub`).

Modern AI programming clients (such as **Cline**, **OpenClaw**, **Claude Code**, **Continue**, **KiloCode**) require standard REST interfaces like **OpenAI Chat Completions** (`/v1/chat/completions`) or **Anthropic Messages** (`/v1/messages`).

The proxy acts as a **protocol bridge**, converting between HTTP REST and SignalR WebSocket:

```mermaid
flowchart LR
    A["AI Client (Cline / OpenClaw / Claude Code / Continue)"] -- "HTTP REST (OpenAI JSON / SSE)" --> B["FastAPI Proxy Server (Docker Container)"]
    B -- "SignalR over WebSocket (Record Separator 0x1E)" --> C["Microsoft Substrate Chathub (wss://substrate.office.com)"]
```

---

### 1.3. Docker Container Architecture

```mermaid
flowchart TD
    subgraph HostOS ["Host Machine (User PC / Server)"]
        UserBrowser["User Browser (Web UI / Admin)"]
        AIClient["AI Client (Cline / OpenClaw / Claude Code / Continue)"]
        
        subgraph DockerContainer ["Docker Container: copilot-api"]
            subgraph DisplayLayer ["GUI & Interactive Login Layer"]
                noVNC["noVNC Server (Port 6080)"]
                X11VNC["x11vnc + Xvfb Virtual Display (:99)"]
            end

            subgraph CoreLogic ["Python Application Layer"]
                API["FastAPI App (Port 8000)"]
                Trans["Translator & Prompt Injector"]
                ToolEngine["Dual Tool Calling Engine"]
                SubstrateWS["Substrate SignalR WS Client"]
                TokenStore["Token Store & JWT Manager"]
                SessionMgr["Session Memory Manager"]
                ErrorHandler["Error Recovery & Retry Engine"]
            end

            subgraph BrowserEngine ["Camoufox Anti-Detect Browser"]
                Camoufox["Camoufox Firefox Engine (Headless/Headful)"]
                NetCapture["Network WS Token Interceptor"]
            end

            subgraph VolumeMount ["Mounted Volume: ./data"]
                ProfileDir["/app/data/camoufox_profile"]
                EnvFile["/app/data/.env"]
                TokenCache["/app/data/tokens.json"]
                MSALCache["/app/data/msal-cache.json"]
            end
        end
    end

    subgraph MSCloud ["Microsoft 365 Cloud"]
        M365Web["m365.cloud.microsoft"]
        EntraID["Entra ID OAuth2 Token Endpoint"]
        Chathub["wss://substrate.office.com/m365Copilot/Chathub"]
        PowerPlatform["PowerPlatform API (Copilot Studio)"]
    end

    UserBrowser <-->|HTTP / WebSockets (Port 6080)| noVNC
    noVNC <--> X11VNC
    X11VNC <-->|Renders Firefox GUI| Camoufox

    AIClient <-->|HTTP REST / SSE Stream (Port 8000)| API
    API --> Trans --> ToolEngine <--> SubstrateWS
    API <--> SessionMgr
    API <--> ErrorHandler
    TokenStore <--> VolumeMount

    Camoufox <-->|Auto Navigate & Nudge| M365Web
    NetCapture -->|Extract Access Token| TokenStore
    TokenStore <-->|Direct Refresh Minting| EntraID
    ToolEngine -.->|Agent Mode (Optional)| PowerPlatform
    SubstrateWS <-->|SignalR 0x1E WebSocket| Chathub
```

---

## 2. Docker User Experience & Login Workflow

### 2.1. 3-Step Quickstart

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant Host as Docker Host
    participant Container as Docker Container (copilot-api)
    participant noVNC as noVNC Web GUI (:6080)
    participant M365 as m365.cloud.microsoft

    User->>Host: 1. Launch: docker compose up -d
    Host->>Container: Initialize Xvfb, noVNC Server, and FastAPI Proxy
    User->>noVNC: 2. Open browser: http://localhost:6080
    noVNC->>Container: Render Firefox/Camoufox screen
    User->>M365: Log in with Email, Password & OTP (MFA)
    Container->>Container: Auto-capture Sydney access_token & persist Profile to ./data
    User->>Host: 3. Enjoy API at http://localhost:8000/v1
```

1. **Launch Container**:
   ```bash
   docker compose up -d
   ```
2. **One-Time Web UI Login**:
   - Access `http://localhost:6080` in any web browser.
   - The Camoufox Firefox GUI appears inside the browser window. Enter Email, Password, and 2-Factor Authentication (MFA/Authenticator App).
   - Once login completes, the system automatically captures tokens and saves state to `./data/camoufox_profile`.
3. **Use the API**:
   - Configure AI tools (Cline, OpenClaw, Continue) to point Base URL to `http://localhost:8000/v1` with your configured API Key.

---

### 2.2. Token Extraction & Auto-Refresh Engine

1. **Camoufox Network Interception**:
   - Listens to WebSocket connections initiated by the M365 Copilot web page. When connected to `wss://substrate.office.com/m365Copilot/Chathub`, Camoufox extracts the `access_token` from the URL query string.
2. **Headless Dynamic Switch (Resource Optimization)**:
   - After completing initial login, Camoufox automatically switches to **Headless** mode, lowering RAM usage below **600MB**.
3. **Hybrid Token Refresh Mechanism**:
   - **Primary Flow**: When `refresh_token` is available, FastAPI Server directly calls Entra ID OAuth2 Endpoint (`POST /oauth2/v2.0/token`) to mint new Access Tokens (~85 min lifespan) without launching the browser.
   - **Fallback Flow**: When necessary, Camoufox performs background "Nudge" operations (typing space + backspace on the web page) to force the web client to establish a fresh WebSocket connection and capture a new JWT.

### 2.3. Token Lifecycle

```mermaid
stateDiagram-v2
    [*] --> NoToken: Container initial startup
    NoToken --> WaitLogin: Open noVNC, wait for User login
    WaitLogin --> TokenCaptured: Camoufox captures access_token from WebSocket URL
    TokenCaptured --> TokenValid: JWT decoded successfully (oid, tid, exp)
    TokenValid --> TokenExpiring: exp - now < PREFETCH_MARGIN (600s)
    TokenExpiring --> RefreshViaEntraID: refresh_token available?
    TokenExpiring --> RefreshViaNudge: No refresh_token available
    RefreshViaEntraID --> TokenValid: Entra ID returns new access_token + refresh_token
    RefreshViaEntraID --> RefreshViaNudge: Entra ID fails (token revoked)
    RefreshViaNudge --> TokenValid: Camoufox Nudge succeeds
    RefreshViaNudge --> WaitLogin: Nudge fails → Prompt User to log in again
    TokenValid --> [*]: Container shutdown (tokens persisted to ./data)
```

**Payload Details for Entra ID Token Endpoint Call:**
```http
POST /{tenant_id}/oauth2/v2.0/token HTTP/1.1
Host: login.microsoftonline.com
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&scope=https://substrate.office.com/sydney/FullAccess openid profile offline_access
&refresh_token={CURRENT_REFRESH_TOKEN}
&client_id=c0ab8ce9-e9a0-42e7-b064-33d422df41f1
&SKU=msal.js.browser
&VER=5.9.0
```

> [!NOTE]
> `client_id` uses the official Application ID of Microsoft Copilot Web App (`c0ab8ce9-e9a0-42e7-b064-33d422df41f1`). The scope `sydney/FullAccess` provides full authorization to Substrate Chathub.

---

## 3. Substrate Chathub Protocol (SignalR WebSocket)

### 3.1. WebSocket Connection URL

```text
wss://substrate.office.com/m365Copilot/Chathub/{oid}@{tid}?access_token={JWT}&X-SessionId={SessionID}&ConversationId={ConvID}&variants=...
```

| Parameter | Source | Description |
|---|---|---|
| `{oid}` | JWT Claim `oid` | Object ID of the Microsoft 365 user account |
| `{tid}` | JWT Claim `tid` | Tenant ID of the organization |
| `access_token` | Token Store | JWT access token (passed via Query String, **not Authorization Header**) |
| `X-SessionId` | Proxy-generated UUIDv4 | Session ID to group messages in a session |
| `ConversationId` | Proxy-generated UUIDv4 | Conversation ID for the chat conversation |
| `variants` | Fixed configuration | Microsoft server-side feature flags |

### 3.2. Handshake & Framing

The SignalR protocol uses the special ASCII character **`0x1E`** (Record Separator - ASCII 30, `\x1e`) to demarcate JSON data frames.

```mermaid
sequenceDiagram
    autonumber
    participant Proxy as FastAPI Proxy
    participant WS as Substrate WebSocket

    Proxy->>WS: {"protocol":"json","version":1}⟨0x1E⟩
    WS-->>Proxy: {}⟨0x1E⟩ (Handshake OK)
    
    Note over Proxy,WS: Send 2 frames simultaneously in a single ws.send() call
    
    Proxy->>WS: Frame 1 - Chat Invocation (type:4, target:"chat")⟨0x1E⟩Frame 2 - Metrics (type:1, target:"Metrics")⟨0x1E⟩
    
    loop Streaming Response
        WS-->>Proxy: {"type":1,"target":"update","arguments":[...]}⟨0x1E⟩
    end
    
    WS-->>Proxy: {"type":3,"result":{...}}⟨0x1E⟩ (Completion)
```

> [!WARNING]
> **Metrics Frame is Mandatory.** If the Metrics frame (type:1, target:"Metrics") is omitted when sending Chat Invocation, the Substrate server will hang and produce no response.

### 3.3. Chat Invocation Frame Structure (Frame 1)

```json
{
  "type": 4,
  "target": "chat",
  "arguments": [{
    "source": "chat",
    "optionsSets": ["enterprise_flux"],
    "allowedMessageTypes": ["Chat", "Progress", "ActionRequest", "ConfirmationCard"],
    "isStartOfSession": true,
    "conversationId": "00000000-0000-0000-0000-000000000000",
    "message": {
      "text": "<translated prompt content>",
      "messageType": "Chat",
      "author": "user"
    },
    "tone": "magic",
    "threadLevelGptId": { "id": "T_{titleId}.{botId}.gpt.default" },
    "clientInfo": {
      "clientName": "m365copilot",
      "clientVersion": "1.0.0"
    }
  }]
}
```

### 3.4. Metrics Frame Structure (Frame 2)

```json
{
  "type": 1,
  "target": "Metrics",
  "arguments": [{
    "Timestamps": {
      "ClientPreHandshakeBufferTime": 0,
      "ClientPostHandshakeBufferTime": 150
    }
  }]
}
```

### 3.5. Response Message Types

| SignalR `type` | `target` / Content | Meaning | Handling Mechanism |
|---|---|---|---|
| `1` | `target: "update"` | Streaming update (delta text) | Extract `writeAtCursor` → SSE `content` delta |
| `1` | `messageType: "Progress"` | Reasoning state (Thinking) | Stream to `reasoning_content` delta |
| `1` | `messageType: "GraphicArt"` | Designer-generated image | Fetch image via Designer App Service → embed into response |
| `1` | `messageType: "ConfirmationCard"` | Action confirmation | Log and ignore (do not forward to client) |
| `1` | `messageType: "Disengaged"` | Safety filter triggered | Return error to client, record `dea_score` |
| `3` | Completion result | Conversation finish | Send SSE `[DONE]`, close connection |
| `6` | Ping | Server heartbeat | Respond with `{"type":6}` |

---

## 4. REST Endpoints List (OpenAI Extended Specification)

The FastAPI server inside the container provides an extended standard API suite:

| Method | Endpoint | Standard Specification | Description |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | **OpenAI Chat Completions** | Streaming (SSE `data: {...}`) & Non-streaming. Returns `reasoning_content`, `tool_calls`, `usage`. |
| `GET` | `/v1/models` | **OpenAI Models API** | List of available models (`m365-copilot`, `m365-quick`, `m365-think-deeper`, `claude-sonnet`). |
| `POST` | `/v1/messages` | **Anthropic Messages API** | Dedicated to Claude API compatible tools (Claude Code CLI, Cline Anthropic provider). |
| `POST` | `/v1/responses` | **OpenAI Responses API** | Structured response API standard. |
| `GET` | `/healthz` | **System Health** | Check container health, Token remaining time (`token_seconds_remaining`), VNC & Camoufox status. |
| `GET` | `/v1/token/status` | **Token Monitor** | Detailed information on Sydney JWT Token (`valid`, `exp`, `oid`, `tid`, `user_principal_name`). |
| `GET/DELETE` | `/v1/sessions` | **Session Manager** | Manage and clear conversation session memory. |
| `GET` | `/v1/sessions/{session_id}` | **Session Detail** | Specific session details (message count, creation timestamp). |

### 4.1. Authentication

All endpoints (except `/healthz`) require API Key authentication:

```http
Authorization: Bearer sk-m365-copilot-secret-key
```

Or via query parameter:
```http
GET /v1/models?api_key=sk-m365-copilot-secret-key
```

Missing or invalid API Key returns:
```json
{
  "error": {
    "message": "Invalid API key",
    "type": "authentication_error",
    "code": 401
  }
}
```

---

## 5. Request & Response Schemas

### 5.1. POST `/v1/chat/completions` — OpenAI Chat Completions

#### Request Body

```json
{
  "model": "m365-copilot",
  "messages": [
    {"role": "system", "content": "You are a helpful coding assistant."},
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi! How can I help you?"},
    {"role": "user", "content": "Explain how JWT authentication works."}
  ],
  "stream": true,
  "temperature": 0.7,
  "max_tokens": 4096,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "Read the contents of a file",
        "parameters": {
          "type": "object",
          "properties": {
            "path": {"type": "string", "description": "File path to read"}
          },
          "required": ["path"]
        }
      }
    }
  ]
}
```

#### Streaming Response (SSE)

```text
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1719360000,"model":"m365-copilot","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1719360000,"model":"m365-copilot","choices":[{"index":0,"delta":{"content":"JWT (JSON"},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1719360000,"model":"m365-copilot","choices":[{"index":0,"delta":{"content":" Web Token)"},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1719360000,"model":"m365-copilot","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":45,"completion_tokens":312,"total_tokens":357,"x_m365_conversation_messages":2,"x_m365_dea_score":0.12}}

data: [DONE]
```

#### Streaming Response with Reasoning Content (Model `m365-think-deeper`)

```text
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"Let me analyze this step by step..."},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"First, I need to understand the token structure..."},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"## JWT Authentication Explained\n\n"},"finish_reason":null}]}
```

#### Streaming Response with Tool Calls

```text
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_xyz789","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"README.md\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

#### Non-Streaming Response

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1719360000,
  "model": "m365-copilot",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "JWT (JSON Web Token) is an open standard..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 45,
    "completion_tokens": 312,
    "total_tokens": 357,
    "x_m365_conversation_messages": 2,
    "x_m365_dea_score": 0.12
  }
}
```

---

### 5.2. POST `/v1/messages` — Anthropic Messages API

#### Request Body

```json
{
  "model": "m365-copilot",
  "max_tokens": 4096,
  "system": "You are a helpful coding assistant.",
  "messages": [
    {"role": "user", "content": "Write a Python hello world program"}
  ],
  "stream": true
}
```

#### Streaming Response (SSE)

```text
event: message_start
data: {"type":"message_start","message":{"id":"msg_abc123","type":"message","role":"assistant","model":"m365-copilot","content":[],"usage":{"input_tokens":25,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Here's a simple"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" Python hello world:\n\n```python\nprint(\"Hello, World!\")\n```"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}

event: message_stop
data: {"type":"message_stop"}
```

---

### 5.3. GET `/v1/models` — Models List

#### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "m365-copilot",
      "object": "model",
      "created": 1719360000,
      "owned_by": "microsoft",
      "description": "Auto-routing mode (magic tone)"
    },
    {
      "id": "m365-quick",
      "object": "model",
      "created": 1719360000,
      "owned_by": "microsoft",
      "description": "Fast response mode (Chat/Gpt_Quick tone, TTFT ~1-3s)"
    },
    {
      "id": "m365-think-deeper",
      "object": "model",
      "created": 1719360000,
      "owned_by": "microsoft",
      "description": "Deep reasoning mode (Reasoning tone, TTFT ~10-30s)"
    },
    {
      "id": "claude-sonnet",
      "object": "model",
      "created": 1719360000,
      "owned_by": "microsoft",
      "description": "Claude Sonnet 4.5 via M365 Copilot integration"
    }
  ]
}
```

---

### 5.4. GET `/healthz` — Health Check

#### Response

```json
{
  "status": "ok",
  "token_valid": true,
  "token_seconds_remaining": 4200,
  "token_expires_at": "2026-08-11T14:30:00Z",
  "camoufox_running": true,
  "camoufox_mode": "headless",
  "vnc_active": true,
  "uptime_seconds": 86400,
  "version": "1.0.0"
}
```

---

### 5.5. GET `/v1/token/status` — Token Detail

#### Response

```json
{
  "valid": true,
  "expires_at": "2026-08-11T14:30:00Z",
  "seconds_remaining": 4200,
  "claims": {
    "oid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "tid": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
    "aud": "https://substrate.office.com/sydney",
    "user_principal_name": "user@domain.com"
  },
  "refresh_token_available": true,
  "last_refreshed_at": "2026-08-11T13:05:00Z"
}
```

---

## 6. Extended Features

### 6.1. Model & Tone Mapping

| OpenAI Model Name | M365 Copilot `tone` | Characteristics | Estimated TTFT |
|---|---|---|---|
| `m365-copilot` / `auto` | `magic` | Auto-routing mode | ~3-5s |
| `m365-quick` / `quick` | `Chat` / `Gpt_Quick` | Ultra-fast response | ~1-3s |
| `m365-think-deeper` / `think-deeper` | `Reasoning` / `Gpt_5_6_Reasoning` | Deep reasoning mode (with `reasoning_content`) | ~10-30s |
| `claude-sonnet` | `Claude_Sonnet` | Claude Sonnet 4.5 integrated in M365 | ~3-8s |

### 6.2. Reasoning Stream (`reasoning_content`)

When using reasoning models (such as `m365-think-deeper`), the Microsoft server emits `Progress` message frames reporting thinking status.

**Processing Workflow:**
1. `TurnParser` intercepts messages with `messageType == "Progress"` from the SignalR stream.
2. Reasoning content is emitted as `("think", chunk)` events.
3. FastAPI packages this into the `reasoning_content` field in SSE deltas:

```json
{"choices":[{"index":0,"delta":{"reasoning_content":"Analyzing the codebase structure..."}}]}
```

This feature allows UIs like **Cline** or **KiloCode** to smoothly render the "Thinking" block in real time.

### 6.3. Dual Tool Calling Engine

Since M365 Copilot does not support native `tool_calls` over public WebSockets, the proxy implements **2 engines** that can be flexibly toggled:

#### Engine 1: Copilot Studio Agent Mode (Recommended — 100% compliance)

```mermaid
sequenceDiagram
    autonumber
    participant Proxy as FastAPI Proxy
    participant BAP as BAP API (api.bap.microsoft.com)
    participant PP as PowerPlatform API
    participant WS as Substrate WebSocket

    Proxy->>BAP: GET /providers/Microsoft.BusinessAppPlatform/environments
    BAP-->>Proxy: Return default Environment ID
    Proxy->>PP: POST /powervirtualagents/environments/{envId}/bots/minimalBots
    Note over Proxy,PP: System Instructions contain Fenced Tool Calling config
    PP-->>Proxy: Return botId & schemaName
    Proxy->>PP: POST /bots/{botId}/publish
    PP-->>Proxy: Return titleId
    Proxy->>Proxy: Save Agent ID = T_{titleId}.{botId}.gpt.default
    Proxy->>WS: Chat Invocation + threadLevelGptId: { id: agentId }
```

**How it works:**
- The proxy creates a custom Bot on Copilot Studio via PowerPlatform APIs.
- The Bot is configured with a dedicated System Prompt forcing the model to output **Fenced Code Blocks** (e.g., ` ```bash `, ` ```read `).
- When the WebSocket message includes `threadLevelGptId`, Microsoft servers apply that Agent's System Prompt server-side.
- **Shell Routing**: M365 Copilot is heavily fine-tuned to return ` ```bash ``` code blocks. The proxy leverages this behavior: when the tool list contains `bash`, `shell`, or `run_command`, the proxy forces the model to return code blocks and automatically routes them as tool calls.

**Example of Fenced Codeblock → `tool_calls` conversion:**

Copilot returns:
````text
I'll read the README file for you:

```read
path: README.md
```
````

Proxy converts to:
```json
{
  "choices": [{
    "finish_reason": "tool_calls",
    "message": {
      "tool_calls": [{
        "id": "call_123456",
        "type": "function",
        "function": {
          "name": "read",
          "arguments": "{\"path\":\"README.md\"}"
        }
      }]
    }
  }]
}
```

#### Engine 2: Stream Parser XML Injection (Best-effort — for standard M365 accounts)

```mermaid
flowchart TD
    Stream["WebSocket Text Stream"] --> Feed["ToolCallStreamParser.feed(chunk)"]
    Feed --> StateCheck{"Inside or outside tool_call tag?"}
    
    StateCheck -- "Outside State" --> EmitContent["Emit SSE delta content (normal text)"]
    StateCheck -- "Inside State" --> ReadName["Read 'name' field via Regex"]
    
    ReadName --> EmitName["Emit SSE delta tool_calls with Function name"]
    EmitName --> BufferArgs["Buffer remaining JSON characters"]
    
    BufferArgs --> CloseTag{"Encounter closing tag?"}
    CloseTag -- Yes --> ParseJSON["Safely parse JSON arguments"]
    ParseJSON --> EmitArgs["Emit SSE delta tool_calls with arguments"]
    CloseTag -- No --> BufferArgs
```

**How it works:**
- The proxy injects tool definitions as XML `<tool_call>{"name": ..., "arguments": ...}</tool_call>` into the user prompt.
- `ToolCallStreamParser` analyzes the text stream in real-time:
  - Tool names are sent to the client **immediately upon extraction** (helping client prepare UI).
  - `arguments` strings are emitted **only after encountering closing tag** `</tool_call>` and validating via `json.loads()` (avoiding partial JSON errors).

### 6.4. Message Translation & Conversation Folding

The proxy supports 3 API standards by "flattening" system messages and chat history:

```mermaid
flowchart TD
    In["Input Request (OpenAI / Anthropic / Responses)"] --> Trans["translator.py"]
    Trans --> Sys["Extract System / Developer Messages → 'System instructions:\n...'"]
    Trans --> Hist["Extract Prior Messages → 'Prior conversation transcript:\n...'"]
    Trans --> Prompt["Extract Final User Message → prompt"]
    Sys & Hist --> AddCtx["additional_context (List of strings)"]
    AddCtx & Prompt --> Combined["_combine_text(): additional_context + prompt"]
    Combined --> SignalR["SignalR Payload (message.text)"]
```

**Conversation Folding (`fold_conversation`):**

Since the proxy initializes a new conversation with Copilot on each request (or to control quota limits), `fold_conversation()` consolidates past messages, tool outputs `<tool_response>`, and current queries into a single payload block:

```text
System instructions:
You are a helpful coding assistant.

Prior conversation transcript:
User: Hello
Assistant: Hi there! How can I help you?

# Conversation history (JSONL)
{"role": "user", "content": "List files in directory"}
{"role": "assistant", "content": "<tool_call>\n{\"name\": \"list_dir\", \"arguments\": {}}\n</tool_call>"}

# Current message
<tool_response tool_call_id="call_0" name="list_dir">
file1.txt
file2.py
</tool_response>

Please analyze file2.py for me.
```

### 6.5. Advanced Session Memory

The proxy supports **2 conversation management modes**:

| Mode | Triggered By | Behavior |
|---|---|---|
| **Stateless (Default)** | No special header sent | Each request generates new `ConversationId` & `SessionId` (UUIDv4). Each query is an independent session. |
| **Persistent Session** | Header `X-M365-Session-Id: my-session` or model `m365-copilot:persist` | Retains `conversation_id` and `client_session_id`. Turn 1: `isStartOfSession=true`. Subsequent turns: `isStartOfSession=false`. |

**Benefits of Persistent Session**: Preserves conversation memory on Copilot server-side without re-sending complete chat history (reducing token consumption and TTFT).

### 6.6. Usage Tracking & Disengaged Score

Returns extended information in response `usage` object:

| Field | Type | Description |
|---|---|---|
| `x_m365_conversation_messages` | `int` | Number of messages consumed in current conversation (limit: **600 msgs/conversation**) |
| `x_m365_dea_score` | `float` | Score assessing risk of triggering safety disconnect `Disengaged` (0.0 = safe, 1.0 = high risk) |

### 6.7. Image Generation

When users request image creation (e.g., *"draw a picture of a cat"*), Copilot returns a `GraphicArt` frame containing image URLs from Designer App Service. The proxy automatically:
1. Mints a `designerappservice` token in the background.
2. Downloads image data as binary Buffer / Base64.
3. Embeds directly into response content as Markdown image or Base64 data URI.

---

## 7. Error Handling & Resilience

### 7.1. Error Codes

| HTTP Code | Error Type | Root Cause | Client Action |
|---|---|---|---|
| `401` | `authentication_error` | Missing or invalid API Key | Check `Authorization` header |
| `403` | `token_expired` | Sydney JWT expired, auto-refresh failed | Wait for system auto-refresh or re-login via noVNC |
| `429` | `rate_limit_exceeded` | Exceeded rate limits | Retry after `Retry-After` header value (seconds) |
| `500` | `internal_server_error` | Proxy internal error | Retry with exponential backoff |
| `502` | `substrate_connection_error` | Failed WebSocket connection to Substrate | Check token status via `/v1/token/status` |
| `503` | `service_unavailable` | Token not ready / Container initializing | Retry after 5-10 seconds |
| `504` | `substrate_timeout` | Substrate WebSocket timeout (>120s) | Retry with longer timeout or different model |

### 7.2. Standard Error Response Schema

```json
{
  "error": {
    "message": "Token expired. Auto-refresh in progress, please retry in 10 seconds.",
    "type": "token_expired",
    "code": 403,
    "param": null,
    "retry_after": 10
  }
}
```

### 7.3. Automatic Retry & Recovery Strategy

```mermaid
flowchart TD
    Request["Client Request"] --> CheckToken{"Token valid?"}
    CheckToken -- Yes --> ConnectWS["Connect Substrate WebSocket"]
    CheckToken -- No --> AutoRefresh["Trigger Auto-Refresh"]
    AutoRefresh --> RefreshOK{"Refresh successful?"}
    RefreshOK -- Yes --> ConnectWS
    RefreshOK -- No --> Return403["Return HTTP 403 + retry_after"]
    
    ConnectWS --> WSError{"WebSocket Error?"}
    WSError -- No --> StreamResponse["Stream Response → Client"]
    WSError -- Timeout --> Retry["Retry connection (max 3 times)"]
    WSError -- Disengaged --> NewConv["Generate new ConversationId, retry"]
    Retry --> RetryOK{"Retry successful?"}
    RetryOK -- Yes --> StreamResponse
    RetryOK -- No --> Return502["Return HTTP 502"]
    NewConv --> ConnectWS
```

### 7.4. Handling Disengaged Filters

When `Disengaged` filter is triggered (Copilot terminates session due to policy flags):
1. Proxy logs current `dea_score`.
2. Automatically generates a new `ConversationId` and retries request.
3. If repeatedly Disengaged (>3 times), returns error to client with troubleshooting instructions.

### 7.5. Rate Limiting

| Resource | Limit | Behavior on Limit Exceeded |
|---|---|---|
| API Requests | Configured via `RATE_LIMIT_RPM` (default: 60 req/min) | HTTP 429 + `Retry-After` header |
| Concurrent WebSocket Connections | Configured via `MAX_CONCURRENT_WS` (default: 5) | Queue request or HTTP 429 |
| Conversation Messages | 600 msgs/conversation (Microsoft limit) | Automatically creates new Conversation |

---

## 8. Docker Specification

### 8.1. Dockerfile Structure (`Dockerfile`)

```dockerfile
# Use Python 3.11 Bookworm as base image
FROM python:3.11-slim-bookworm

# Install required system packages for Firefox, Camoufox, Xvfb & noVNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    curl \
    wget \
    bzip2 \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libx11-xcb1 \
    libasound2 \
    fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency definition & install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Camoufox browser binary & dependencies
RUN python -m camoufox fetch

# Copy application source code
COPY . .

# Create shared data volume directory
RUN mkdir -p /app/data

# Expose ports: 8000 (FastAPI), 6080 (noVNC)
EXPOSE 8000 6080

# Entrypoint script (Xvfb + Fluxbox + x11vnc + noVNC + FastAPI)
ENTRYPOINT ["/app/docker-entrypoint.sh"]
```

---

### 8.2. Docker Compose Orchestration (`docker-compose.yml`)

```yaml
version: '3.8'

services:
  copilot-api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: m365-copilot-api
    restart: unless-stopped
    ports:
      - "8000:8000"   # OpenAI Compatible API Endpoint
      - "6080:6080"   # noVNC Interactive Web Desktop UI
    environment:
      - HOST=0.0.0.0
      - PORT=8000
      - API_KEY=sk-m365-copilot-secret-key
      - DISPLAY=:99
      - NOVNC_ENABLE=true
      - CAMOUFOX_HEADLESS=false
      - CAMOUFOX_USER_DATA_DIR=/app/data/camoufox_profile
      - TOOL_CALLING_ENGINE=auto
      - RATE_LIMIT_RPM=60
      - MAX_CONCURRENT_WS=5
      - LOG_LEVEL=INFO
      - VNC_PASSWORD=
      - TOKEN_PREFETCH_MARGIN=600
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
```

---

### 8.3. Volume Management (`./data`)

All sensitive data and session states are automatically written to mounted volume `./data`:

| File / Directory | Purpose | Sensitivity |
|---|---|---|
| `./data/.env` | Environment variables, API keys, refresh tokens | 🔴 High |
| `./data/camoufox_profile/` | Firefox/Camoufox Cookies & LocalStorage (persists login state across `docker compose restart`) | 🔴 High |
| `./data/tokens.json` | Encrypted `access_token` and `refresh_token` storage | 🔴 High |
| `./data/msal-cache.json` | MSAL OAuth credentials cache | 🔴 High |
| `./data/agent-id.json` | Stores Copilot Studio Agent ID (if using Agent Mode) | 🟡 Medium |
| `./data/logs/` | Application logs (rotated) | 🟢 Low |

> [!CAUTION]
> The `./data` directory contains full credentials and session artifacts. **DO NOT** commit this directory to Git. Ensure `./data` is added to `.gitignore`.

---

## 9. Environment Variables & Configuration

### 9.1. Mandatory Variables

| Variable | Default Value | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | IP address FastAPI server listens on |
| `PORT` | `8000` | HTTP Port for FastAPI server |
| `API_KEY` | *(required)* | API Key to authenticate clients. Supports comma-separated keys. |
| `DISPLAY` | `:99` | Virtual display for Xvfb |

### 9.2. Camoufox & VNC Configuration Variables

| Variable | Default Value | Description |
|---|---|---|
| `NOVNC_ENABLE` | `true` | Enable/disable noVNC Web UI Server |
| `VNC_PASSWORD` | *(empty = no password)* | noVNC access password (recommended for production) |
| `CAMOUFOX_HEADLESS` | `false` | `false` = Headful (for login phase), `true` = Headless (post-login) |
| `CAMOUFOX_USER_DATA_DIR` | `/app/data/camoufox_profile` | Directory storing Firefox/Camoufox Profile |
| `CAMOUFOX_AUTO_HEADLESS` | `true` | Automatically switch to Headless after successful login |

### 9.3. Token & Authentication Configuration Variables

| Variable | Default Value | Description |
|---|---|---|
| `TOKEN_PREFETCH_MARGIN` | `600` | Seconds prior to token expiration to initiate refresh (default 10 mins) |
| `M365_REFRESH_TOKEN` | *(auto-captured)* | Refresh Token used for OAuth2 rotation. Auto-updated after each refresh. |
| `M365_TENANT_ID` | *(auto-detected)* | Tenant ID parsed from JWT claims. Can be overridden if needed. |

### 9.4. Tool Calling & Feature Configuration Variables

| Variable | Default Value | Description |
|---|---|---|
| `TOOL_CALLING_ENGINE` | `auto` | Tool Calling Engine: `auto` (auto-select), `agent` (Copilot Studio), `parser` (XML Stream Parser), `disabled` |
| `RATE_LIMIT_RPM` | `60` | Maximum requests per minute |
| `MAX_CONCURRENT_WS` | `5` | Maximum concurrent WebSocket connections |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_TOKEN_CLAIMS` | `false` | If `true`, logs JWT claims (debug only, **DO NOT enable in production**) |

---

## 10. Non-Functional Requirements

### 10.1. Performance

| Metric | Requirement | Notes |
|---|---|---|
| RAM (Headless mode) | 450MB – 650MB | After transitioning to Headless |
| RAM (Headful / Login mode) | 800MB – 1.2GB | During interactive noVNC login phase |
| CPU idle | < 2% | When no active requests are being processed |
| TTFT (Time To First Token) | < 5s (m365-quick), < 30s (m365-think-deeper) | Depends on model choice and Microsoft server load |
| Cold start time | < 5 seconds | Container restart with existing Profile |
| Concurrent requests | Minimum 5 concurrent requests | Each request utilizes 1 WebSocket connection |

### 10.2. Availability

- Upon container restart, system reloads existing Profile from `./data` volume and resumes API availability in under **5 seconds**.
- Background token auto-refresh maintains service availability > **99.5%** (excluding Microsoft token revocations).
- Healthcheck endpoint (`/healthz`) enables Docker orchestrators (Kubernetes, Docker Swarm) to perform health monitoring and automatic restarts.

### 10.3. Security

| Requirement | Description |
|---|---|
| API Key Authentication | All endpoints (except `/healthz`) require `Authorization: Bearer <key>` |
| VNC Password | noVNC supports password configuration via `VNC_PASSWORD` environment variable |
| Token Masking | **Never** print complete JWT to `docker logs`. Log only first 8 characters + `...` |
| Sensitive Volume | The `./data` directory contains credentials and **MUST** be added to `.gitignore` |
| Restricted Network Access (Optional) | Container requires outbound connection only to `*.microsoft.com` and `*.office.com` |

### 10.4. Observability

| Feature | Description |
|---|---|
| Structured Logging | JSON-formatted logs containing `request_id`, `session_id`, `duration_ms` |
| Health Endpoint | `/healthz` returns token validity, VNC status, Camoufox status, and uptime |
| Token Status | `/v1/token/status` displays detailed JWT claims and expiration metrics |
| Usage Tracking | `x_m365_conversation_messages` and `x_m365_dea_score` fields in API responses |

---

## 11. Container Verification Plan

### 11.1. Functional Tests

| # | Test Case | Test Steps | Expected Result |
|---|---|---|---|
| F1 | noVNC Web UI | Access `http://localhost:6080`, interact with Firefox GUI | Displays Firefox interface, allows M365 login |
| F2 | Health Check | `curl http://localhost:8000/healthz` | HTTP 200 OK, `token_valid: true` |
| F3 | Models List | `curl -H "Authorization: Bearer $KEY" http://localhost:8000/v1/models` | List of 4+ models returned |
| F4 | Chat Stream | `POST /v1/chat/completions` with `stream: true` | Continuous SSE `data: {...}` stream ending with `[DONE]` |
| F5 | Chat Non-stream | `POST /v1/chat/completions` with `stream: false` | Complete JSON response containing `choices` and `usage` |
| F6 | Anthropic Messages | `POST /v1/messages` | SSE events: `message_start`, `content_block_delta`, `message_stop` |
| F7 | Tool Calling | `POST /v1/chat/completions` with `tools` array | Response contains `tool_calls` with valid `function.name` and `arguments` |
| F8 | Reasoning Content | `POST /v1/chat/completions` with `model: m365-think-deeper` | SSE delta contains `reasoning_content` |
| F9 | Session Persistence | 2 consecutive requests with `X-M365-Session-Id: test` | Second request retains context from first request |
| F10 | Auth Rejection | Request without `Authorization` header | HTTP 401, error `authentication_error` |

### 11.2. Non-Functional Tests

| # | Test Case | Test Steps | Expected Result |
|---|---|---|---|
| N1 | Container Restart | `docker compose restart` | Login session remains intact, API resumes in < 5s |
| N2 | Token Auto-Refresh | Wait until token approaches expiry (~75 mins) | Token refreshes automatically without API interruption |
| N3 | Rate Limiting | Send > 60 requests in 1 minute | Excess requests receive HTTP 429 + `Retry-After` header |
| N4 | Memory Usage | Monitor RAM after 1 hour of Headless execution | Stable < 650MB without memory leaks |
| N5 | Concurrent Requests | Dispatch 5 parallel requests | All 5 requests receive successful responses |
| N6 | Disengaged Recovery | Trigger Disengaged filter | Proxy automatically retries with new ConversationId |

### 11.3. Verification Command Examples

```bash
# Health Check
curl -s http://localhost:8000/healthz | jq .

# Token Status
curl -s -H "Authorization: Bearer sk-m365-copilot-secret-key" \
  http://localhost:8000/v1/token/status | jq .

# Chat Streaming
curl -N -s -H "Authorization: Bearer sk-m365-copilot-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"m365-copilot","messages":[{"role":"user","content":"Hello, who are you?"}],"stream":true}' \
  http://localhost:8000/v1/chat/completions

# Chat Non-streaming
curl -s -H "Authorization: Bearer sk-m365-copilot-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"m365-copilot","messages":[{"role":"user","content":"What is 2+2?"}],"stream":false}' \
  http://localhost:8000/v1/chat/completions | jq .

# Tool Calling Test
curl -N -s -H "Authorization: Bearer sk-m365-copilot-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"m365-copilot","messages":[{"role":"user","content":"Read the file README.md"}],"stream":true,"tools":[{"type":"function","function":{"name":"read_file","description":"Read file contents","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]}' \
  http://localhost:8000/v1/chat/completions

# Persistent Session Test
curl -N -s -H "Authorization: Bearer sk-m365-copilot-secret-key" \
  -H "Content-Type: application/json" \
  -H "X-M365-Session-Id: test-session-001" \
  -d '{"model":"m365-copilot","messages":[{"role":"user","content":"My name is Alice. Remember it."}],"stream":true}' \
  http://localhost:8000/v1/chat/completions
```

---

## 12. Glossary

| Term | Definition |
|---|---|
| **Substrate Chathub** | Microsoft 365 Copilot backend Sydney server, communicating via SignalR WebSocket at `wss://substrate.office.com/m365Copilot/Chathub` |
| **SignalR** | Microsoft real-time communication protocol using Record Separator `0x1E` to demarcate JSON frames |
| **Camoufox** | Anti-detect Firefox browser engine optimized to prevent automated bot detection |
| **noVNC** | Web-based VNC client allowing browser access to the desktop GUI environment |
| **Entra ID** | Microsoft cloud identity and access management service (formerly Azure AD) |
| **MSAL** | Microsoft Authentication Library — official Microsoft authentication library |
| **JWT** | JSON Web Token — open standard for securely transmitting authentication information |
| **SSE** | Server-Sent Events — unidirectional HTTP streaming protocol from server to client |
| **TTFT** | Time To First Token — time elapsed from request dispatch to receipt of initial token |
| **Tone** | Model routing parameter in M365 Copilot (e.g., `magic`, `Chat`, `Reasoning`) |
| **Nudge** | Typing space + backspace into web chat box to force Copilot to establish a new WebSocket connection |
| **DEA Score** | Disengaged Assessment Score — metric evaluating risk of Copilot terminating session due to policy triggers |
| **Fenced Codeblock** | Markdown code block (` ```lang `) used to emulate tool calling responses |
| **Shell Routing** | Technique leveraging Copilot's reflex of returning ` ```bash ` code blocks to route as tool calls |
| **Copilot Studio** | Microsoft platform allowing creation of custom AI Bots via PowerPlatform APIs |
| **Record Separator** | ASCII 30 character (`0x1E`, `\x1e`) used in SignalR protocol to demarcate JSON frames |
| **Token Rotation** | Refresh token rotation mechanism — each refresh yields a new refresh token replacing the old one |
| **Prefetch Margin** | Time window (seconds) prior to token expiration to initiate proactive refresh |

---

## 13. Revision History

| Version | Date | Author | Main Changes |
|---|---|---|---|
| `1.0.0` | 2026-08-11 | AI Assistant | Initial SRS specification created from grill-me interview |
| `1.1.0` | 2026-08-11 | AI Assistant | Optimized for Docker Container Architecture; added noVNC interactive login workflow |
| `2.0.0` | 2026-08-11 | AI Assistant | **Major Specification Update**: Added 6 new sections (SignalR Protocol, Error Handling, Env Config, API Schemas, Glossary, Revision History). Expanded details on Token Lifecycle, Dual Tool Calling Engine, Message Translation & Folding, Security Requirements. Added complete API request/response examples, error codes table, verification matrix, and test curl commands. Updated to English specification. |
