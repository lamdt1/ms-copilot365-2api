# Software Requirements Specification (SRS)
## Dự Án: M365 Copilot OpenAI Compatible API (Camoufox + FastAPI + Docker Engine)

**Mã tài liệu:** `SRS-M365-COPILOT-API-001`  
**Phiên bản:** `2.0.0 (Comprehensive Specification)`  
**Ngày cập nhật:** `2026-08-11`  
**Trạng thái:** `Approved (Production-Ready Specification)`  
**Tác giả:** AI Assistant & Core Development Team  

> [!IMPORTANT]
> Tài liệu SRS này đã được tái thiết kế chuyên biệt cho mô hình **Docker Container First**. Hệ thống cung cấp cơ chế đăng nhập trực quan thông qua **Web-based noVNC (`http://localhost:6080`)** giúp người dùng dễ dàng thao tác đăng nhập / MFA lần đầu ngay trong container mà không cần cài đặt môi trường Python hay Firefox cục bộ.

---

## Mục Lục

- [1. Tổng Quan Hệ Thống](#1-tổng-quan-hệ-thống-system-overview--scope)
- [2. Trải Nghiệm Người Dùng & Quy Trình Đăng Nhập](#2-trải-nghiệm-người-dùng--quy-trình-đăng-nhập-docker-user-experience--login-workflow)
- [3. Giao Thức SignalR WebSocket](#3-giao-thức-signalr-websocket-substrate-chathub-protocol)
- [4. Danh Sách REST Endpoints](#4-danh-sách-rest-endpoints-openai-extended-specification)
- [5. Định Dạng Yêu Cầu & Phản Hồi API](#5-định-dạng-yêu-cầu--phản-hồi-api-request--response-schemas)
- [6. Các Tính Năng Mở Rộng](#6-các-tính-năng-mở-rộng-từ-tham-chiếu-extended-features)
- [7. Cơ Chế Xử Lý Lỗi & Khả Năng Phục Hồi](#7-cơ-chế-xử-lý-lỗi--khả-năng-phục-hồi-error-handling--resilience)
- [8. Đóng Gói Docker & Cấu Hình Chi Tiết](#8-đóng-gói-docker--cấu-hình-chi-tiết-docker-specification)
- [9. Biến Môi Trường & Cấu Hình](#9-biến-môi-trường--cấu-hình-environment-variables--configuration)
- [10. Yêu Cầu Phi Chức Năng](#10-yêu-cầu-phi-chức-năng-non-functional-requirements)
- [11. Kế Hoạch Kiểm Thử](#11-kế-hoạch-kiểm-thử-container-docker-verification-plan)
- [12. Bảng Thuật Ngữ](#12-bảng-thuật-ngữ-glossary)
- [13. Lịch Sử Phiên Bản](#13-lịch-sử-phiên-bản-revision-history)

---

## 1. Tổng Quan Hệ Thống (System Overview & Scope)

### 1.1. Mục Tiêu Dự Án

Dự án cung cấp giải pháp **Docker Container Trọn Gói (All-in-One)** giúp biến giao diện Web Microsoft 365 Copilot thành máy chủ **OpenAI Compatible REST API**:

1. **Docker Container Engine**: Đóng gói sẵn Python 3.11, Camoufox Anti-detect Engine, Xvfb virtual frame buffer, và noVNC Web UI Server.
2. **Built-in noVNC Login UI (`:6080`)**: Người dùng truy cập trình duyệt web mở giao diện noVNC để đăng nhập M365 / nhập mã MFA trực quan trong container.
3. **Camoufox Engine (Headless Background)**: Tự động trích xuất `access_token` Sydney WebSocket (`wss://substrate.office.com`) và duy trì phiên làm việc ngầm.
4. **FastAPI Engine (`:8000`)**: Cung cấp REST API chuẩn OpenAI (`/v1/chat/completions`), Anthropic Messages (`/v1/messages`), và OpenAI Responses (`/v1/responses`).
5. **Substrate SignalR Protocol Handler**: Kết nối thời gian thực tới máy chủ Microsoft Sydney qua giao thức SignalR WebSocket (Record Separator `0x1E`).

### 1.2. Đặt Vấn Đề & Lý Do Cần Proxy

**Microsoft 365 Copilot (BizChat/Office Web Copilot)** không cung cấp OpenAPI REST API chuẩn cho nhà phát triển. Thay vào đó, giao diện web chính thức (`m365.cloud.microsoft`) giao tiếp với máy chủ backend Sydney thông qua giao thức **SignalR over WebSocket** (`wss://substrate.office.com/m365Copilot/Chathub`).

Các ứng dụng lập trình AI hiện đại (như **Cline**, **OpenClaw**, **Claude Code**, **Continue**, **KiloCode**) đều yêu cầu giao diện REST chuẩn **OpenAI Chat Completions** (`/v1/chat/completions`) hoặc **Anthropic Messages** (`/v1/messages`).

Proxy đóng vai trò **cầu nối giao thức**, chuyển đổi giữa REST HTTP và SignalR WebSocket:

```mermaid
flowchart LR
    A["AI Client (Cline / OpenClaw / Claude Code / Continue)"] -- "HTTP REST (OpenAI JSON / SSE)" --> B["FastAPI Proxy Server (Docker Container)"]
    B -- "SignalR over WebSocket (Record Separator 0x1E)" --> C["Microsoft Substrate Chathub (wss://substrate.office.com)"]
```

---

### 1.3. Mô Hình Kiến Trúc Docker Container (Docker Container Architecture)

```mermaid
flowchart TD
    subgraph HostOS ["Host Machine (Máy người dùng / Server)"]
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

## 2. Trải Nghiệm Người Dùng & Quy Trình Đăng Nhập Docker (User Experience & Login Workflow)

### 2.1. Quy Trình 3 Bước Dành Cho Người Dùng (3-Step Quickstart)

```mermaid
sequenceDiagram
    autonumber
    actor User as Người dùng
    participant Host as Docker Host
    participant Container as Docker Container (copilot-api)
    participant noVNC as noVNC Web GUI (:6080)
    participant M365 as m365.cloud.microsoft

    User->>Host: 1. Khởi chạy: docker compose up -d
    Host->>Container: Khởi tạo Xvfb, noVNC Server và FastAPI Proxy
    User->>noVNC: 2. Mở trình duyệt: http://localhost:6080
    noVNC->>Container: Hiển thị màn hình Firefox/Camoufox
    User->>M365: Thao tác đăng nhập Email, Password & Mã OTP (MFA)
    Container->>Container: Tự động bắt Sydney access_token & lưu Profile vào ./data
    User->>Host: 3. Tận hưởng API tại http://localhost:8000/v1
```

1. **Khởi chạy container**:
   ```bash
   docker compose up -d
   ```
2. **Đăng nhập 1 lần duy nhất qua Web UI**:
   - Người dùng truy cập `http://localhost:6080` trên trình duyệt bất kỳ.
   - Giao diện Firefox của Camoufox xuất hiện ngay trên trình duyệt web. Người dùng điền Email, Mật khẩu và xác thực 2 bước (MFA/Authenticator App).
   - Sau khi hoàn tất đăng nhập, hệ thống tự động trích xuất Token và lưu trạng thái vào thư mục `./data/camoufox_profile`.
3. **Sử dụng API**:
   - Cấu hình các ứng dụng AI (Cline, OpenClaw, Continue) trỏ Base URL tới `http://localhost:8000/v1` với API Key đã thiết lập.

---

### 2.2. Động Cơ Lấy & Gia Hạn Token Trong Container (Token Extraction & Auto-Refresh)

1. **Camoufox Network Interception**:
   - Lắng nghe các kết nối WebSocket được mở bởi trang web M365 Copilot. Khi trang kết nối tới `wss://substrate.office.com/m365Copilot/Chathub`, Camoufox trích xuất `access_token` từ URL query string.
2. **Chế độ Chạy Ẩn Tối Ưu Tài Nguyên (Headless Dynamic Switch)**:
   - Sau khi kết thúc giai đoạn đăng nhập ban đầu, trình duyệt Camoufox tự động chuyển sang chế độ **Headless**, giảm mức tiêu thụ RAM xuống dưới **600MB**.
3. **Cơ chế Refresh Token Kép (Hybrid Token Refresh)**:
   - **Luồng chính**: Khi `refresh_token` khả dụng, FastAPI Server gọi trực tiếp Entra ID OAuth2 Endpoint (`POST /oauth2/v2.0/token`) để mint Access Token mới (thời hạn ~85 phút) mà không cần bật trình duyệt.
   - **Luồng dự phòng**: Khi cần thiết, Camoufox thực hiện thao tác "Nudge" ngầm (gõ space + backspace trên trang web) để ép web kích hoạt kết nối WebSocket mới và bắt JWT mới.

### 2.3. Vòng Đời Token (Token Lifecycle)

```mermaid
stateDiagram-v2
    [*] --> NoToken: Container khởi động lần đầu
    NoToken --> WaitLogin: Mở noVNC, chờ User đăng nhập
    WaitLogin --> TokenCaptured: Camoufox bắt được access_token từ WebSocket URL
    TokenCaptured --> TokenValid: JWT decoded thành công (oid, tid, exp)
    TokenValid --> TokenExpiring: exp - now < PREFETCH_MARGIN (600s)
    TokenExpiring --> RefreshViaEntraID: refresh_token khả dụng?
    TokenExpiring --> RefreshViaNudge: Không có refresh_token
    RefreshViaEntraID --> TokenValid: Entra ID trả về access_token mới + refresh_token mới
    RefreshViaEntraID --> RefreshViaNudge: Entra ID lỗi (token revoked)
    RefreshViaNudge --> TokenValid: Camoufox Nudge thành công
    RefreshViaNudge --> WaitLogin: Nudge thất bại → Yêu cầu User đăng nhập lại
    TokenValid --> [*]: Container shutdown (token persist vào ./data)
```

**Chi tiết Payload gọi Entra ID Token Endpoint:**
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
> `client_id` sử dụng Application ID gốc của Microsoft Copilot Web App (`c0ab8ce9-e9a0-42e7-b064-33d422df41f1`). Scope `sydney/FullAccess` cho phép truy cập đầy đủ tới Substrate Chathub.

---

## 3. Giao Thức SignalR WebSocket (Substrate Chathub Protocol)

### 3.1. URL Kết Nối WebSocket

```text
wss://substrate.office.com/m365Copilot/Chathub/{oid}@{tid}?access_token={JWT}&X-SessionId={SessionID}&ConversationId={ConvID}&variants=...
```

| Tham số | Nguồn gốc | Mô tả |
|---|---|---|
| `{oid}` | JWT Claim `oid` | Object ID của tài khoản Microsoft 365 |
| `{tid}` | JWT Claim `tid` | Tenant ID của tổ chức |
| `access_token` | Token Store | JWT access token (truyền qua Query String, **không qua Authorization Header**) |
| `X-SessionId` | UUIDv4 sinh bởi proxy | Session ID để nhóm các tin nhắn trong 1 phiên |
| `ConversationId` | UUIDv4 sinh bởi proxy | Conversation ID cho cuộc hội thoại |
| `variants` | Cấu hình cố định | Feature flags phía server Microsoft |

### 3.2. Quy Trình Bắt Tay & Đóng Khung Tin Nhắn (Handshake & Framing)

Giao thức SignalR sử dụng ký tự đặc biệt **`0x1E`** (Record Separator - ASCII 30, `\x1e`) để phân cách các khung dữ liệu JSON.

```mermaid
sequenceDiagram
    autonumber
    participant Proxy as FastAPI Proxy
    participant WS as Substrate WebSocket

    Proxy->>WS: {"protocol":"json","version":1}⟨0x1E⟩
    WS-->>Proxy: {}⟨0x1E⟩ (Handshake OK)
    
    Note over Proxy,WS: Gửi 2 frames đồng thời trong 1 lần ws.send()
    
    Proxy->>WS: Frame 1 - Chat Invocation (type:4, target:"chat")⟨0x1E⟩Frame 2 - Metrics (type:1, target:"Metrics")⟨0x1E⟩
    
    loop Streaming Response
        WS-->>Proxy: {"type":1,"target":"update","arguments":[...]}⟨0x1E⟩
    end
    
    WS-->>Proxy: {"type":3,"result":{...}}⟨0x1E⟩ (Completion)
```

> [!WARNING]
> **Metrics Frame là bắt buộc.** Nếu thiếu khung Metrics (type:1, target:"Metrics") gửi kèm Chat Invocation, máy chủ Substrate sẽ treo và không phản hồi.

### 3.3. Cấu Trúc Chat Invocation Frame (Frame 1)

```json
{
  "type": 4,
  "target": "chat",
  "arguments": [{
    "source": "chat",
    "optionsSets": ["enterprise_flux", ...],
    "allowedMessageTypes": ["Chat", "Progress", "ActionRequest", "ConfirmationCard", ...],
    "isStartOfSession": true,
    "conversationId": "00000000-0000-0000-0000-000000000000",
    "message": {
      "text": "<nội dung prompt sau khi translate>",
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

### 3.4. Cấu Trúc Metrics Frame (Frame 2)

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

### 3.5. Các Loại Tin Nhắn Phản Hồi (Response Message Types)

| SignalR `type` | `target` / Nội dung | Ý nghĩa | Cách xử lý |
|---|---|---|---|
| `1` | `target: "update"` | Streaming update (delta text) | Trích xuất `writeAtCursor` → SSE `content` delta |
| `1` | `messageType: "Progress"` | Trạng thái suy luận (Thinking) | Stream ra `reasoning_content` delta |
| `1` | `messageType: "GraphicArt"` | Ảnh sinh bởi Designer | Tải ảnh qua Designer App Service → embed vào response |
| `1` | `messageType: "ConfirmationCard"` | Xác nhận hành động | Log và bỏ qua (không forward cho client) |
| `1` | `messageType: "Disengaged"` | Bộ lọc an toàn kích hoạt | Trả lỗi cho client, ghi `dea_score` |
| `3` | Completion result | Kết thúc cuộc hội thoại | Gửi SSE `[DONE]`, đóng kết nối |
| `6` | Ping | Server heartbeat | Phản hồi `{"type":6}` |

---

## 4. Danh Sách REST Endpoints (OpenAI Extended Specification)

Máy chủ FastAPI trong container cung cấp bộ API chuẩn mở rộng:

| Method | Endpoint | Chuẩn tương thích | Mô tả chi tiết |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | **OpenAI Chat Completions** | Streaming (SSE `data: {...}`) & Non-streaming. Trả về `reasoning_content`, `tool_calls`, `usage`. |
| `GET` | `/v1/models` | **OpenAI Models API** | Danh sách model khả dụng (`m365-copilot`, `m365-quick`, `m365-think-deeper`, `claude-sonnet`). |
| `POST` | `/v1/messages` | **Anthropic Messages API** | Dành riêng cho các tool tương thích Claude API (Claude Code CLI, Cline Anthropic provider). |
| `POST` | `/v1/responses` | **OpenAI Responses API** | Chuẩn API phản hồi cấu trúc. |
| `GET` | `/healthz` | **System Health** | Kiểm tra độ sống container, thời gian Token còn hạn (`token_seconds_remaining`), trạng thái VNC & Camoufox. |
| `GET` | `/v1/token/status` | **Token Monitor** | Trả về thông tin chi tiết JWT Sydney Token (`valid`, `exp`, `oid`, `tid`, `user_principal_name`). |
| `GET/DELETE` | `/v1/sessions` | **Session Manager** | Quản lý và xóa bộ nhớ tạm của các cuộc hội thoại. |
| `GET` | `/v1/sessions/{session_id}` | **Session Detail** | Chi tiết 1 phiên hội thoại cụ thể (message count, creation time). |

### 4.1. Xác Thực API (Authentication)

Tất cả các endpoint (trừ `/healthz`) yêu cầu xác thực qua API Key:

```http
Authorization: Bearer sk-m365-copilot-secret-key
```

Hoặc truyền qua query parameter:
```http
GET /v1/models?api_key=sk-m365-copilot-secret-key
```

Nếu thiếu hoặc sai API Key, server trả về:
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

## 5. Định Dạng Yêu Cầu & Phản Hồi API (Request & Response Schemas)

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

#### Streaming Response với Reasoning Content (model `m365-think-deeper`)

```text
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"Let me analyze this step by step..."},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"First, I need to understand the token structure..."},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"## JWT Authentication Explained\n\n"},"finish_reason":null}]}
```

#### Streaming Response với Tool Calls

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

## 6. Các Tính Năng Mở Rộng Từ Tham Chiếu (Extended Features)

### 6.1. Ánh Xạ Model & Tone (Model Mapping)

| OpenAI Model Name | M365 Copilot `tone` | Đặc điểm | TTFT ước tính |
|---|---|---|---|
| `m365-copilot` / `auto` | `magic` | Tự động định tuyến (Auto-routing) | ~3-5s |
| `m365-quick` / `quick` | `Chat` / `Gpt_Quick` | Phản hồi siêu nhanh | ~1-3s |
| `m365-think-deeper` / `think-deeper` | `Reasoning` / `Gpt_5_6_Reasoning` | Suy luận chuyên sâu (với `reasoning_content`) | ~10-30s |
| `claude-sonnet` | `Claude_Sonnet` | Claude Sonnet 4.5 tích hợp trong M365 | ~3-8s |

### 6.2. Luồng Suy Luận (`reasoning_content`)

Khi sử dụng các mô hình suy luận (như `m365-think-deeper`), máy chủ Microsoft gửi các khung tin nhắn `Progress` báo cáo trạng thái suy nghĩ.

**Quy trình xử lý:**
1. `TurnParser` bắt các tin nhắn có `messageType == "Progress"` từ SignalR stream.
2. Nội dung suy luận được phát ra dưới dạng sự kiện `("think", chunk)`.
3. FastAPI đóng gói thành trường `reasoning_content` trong SSE delta:

```json
{"choices":[{"index":0,"delta":{"reasoning_content":"Analyzing the codebase structure..."}}]}
```

Tính năng này giúp giao diện như **Cline** hoặc **KiloCode** hiển thị thẻ "Thinking" mượt mà theo thời gian thực.

### 6.3. Động Cơ Giả Lập Tool Calling Kép (Dual Tool Calling Engine)

Do M365 Copilot không hỗ trợ native `tool_calls` qua WebSocket công khai, proxy triển khai **2 engine** có thể chuyển đổi linh hoạt:

#### Engine 1: Copilot Studio Agent Mode (Khuyến nghị — độ tuân thủ 100%)

```mermaid
sequenceDiagram
    autonumber
    participant Proxy as FastAPI Proxy
    participant BAP as BAP API (api.bap.microsoft.com)
    participant PP as PowerPlatform API
    participant WS as Substrate WebSocket

    Proxy->>BAP: GET /providers/Microsoft.BusinessAppPlatform/environments
    BAP-->>Proxy: Trả về Environment ID mặc định
    Proxy->>PP: POST /powervirtualagents/environments/{envId}/bots/minimalBots
    Note over Proxy,PP: System Instructions chứa Fenced Tool Calling config
    PP-->>Proxy: Trả về botId & schemaName
    Proxy->>PP: POST /bots/{botId}/publish
    PP-->>Proxy: Trả về titleId
    Proxy->>Proxy: Lưu Agent ID = T_{titleId}.{botId}.gpt.default
    Proxy->>WS: Chat Invocation + threadLevelGptId: { id: agentId }
```

**Cách hoạt động:**
- Proxy tạo một Bot tùy chỉnh trên Copilot Studio via PowerPlatform APIs.
- Bot được cấu hình với System Prompt chuyên dụng ép model trả về cấu trúc **Fenced Code Blocks** (như ` ```bash `, ` ```read `).
- Khi tin nhắn WebSocket gửi kèm `threadLevelGptId`, máy chủ Microsoft áp dụng System Prompt của Agent đó phía server.
- **Shell Routing**: M365 Copilot được huấn luyện rất mạnh để trả về khối mã ` ```bash ```. Proxy khai thác hành vi này: khi danh sách tool chứa `bash`, `shell`, `run_command`, proxy ép model trả về khối code và tự động route thành tool call.

**Ví dụ chuyển đổi Fenced Codeblock → `tool_calls`:**

Copilot trả về:
````text
I'll read the README file for you:

```read
path: README.md
```
````

Proxy chuyển đổi thành:
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

#### Engine 2: Stream Parser XML Injection (Best-effort — cho tài khoản M365 phổ thông)

```mermaid
flowchart TD
    Stream["Luồng văn bản từ WebSocket"] --> Feed["ToolCallStreamParser.feed(chunk)"]
    Feed --> StateCheck{"Đang ở trong hay ngoài thẻ tool_call?"}
    
    StateCheck -- "Out State" --> EmitContent["Phát ra SSE delta content (normal text)"]
    StateCheck -- "In State" --> ReadName["Đọc trường 'name' qua Regex"]
    
    ReadName --> EmitName["Phát ra SSE delta tool_calls với tên Function"]
    EmitName --> BufferArgs["Gom phần còn lại của JSON vào Buffer"]
    
    BufferArgs --> CloseTag{"Gặp thẻ đóng?"}
    CloseTag -- Có --> ParseJSON["Parse JSON arguments an toàn"]
    ParseJSON --> EmitArgs["Phát ra SSE delta tool_calls chứa arguments"]
    CloseTag -- Không --> BufferArgs
```

**Cách hoạt động:**
- Proxy tiêm mô tả danh sách tool dưới dạng XML `<tool_call>{"name": ..., "arguments": ...}</tool_call>` vào prompt người dùng.
- `ToolCallStreamParser` phân tích luồng văn bản realtime:
  - Tên tool được gửi về Client **ngay khi đọc xong** (giúp Client chuẩn bị UI).
  - Chuỗi `arguments` chỉ được emit **sau khi đóng thẻ** `</tool_call>` và validate qua `json.loads()` (tránh JSON chưa hoàn chỉnh).

### 6.4. Chuyển Đổi Tin Nhắn (Message Translation & Conversation Folding)

Proxy hỗ trợ 3 chuẩn API bằng cách "phẳng hóa" (flatten) tin nhắn hệ thống và lịch sử trò chuyện:

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

**Nén lịch sử trò chuyện (`fold_conversation`):**

Vì proxy khởi tạo một hội thoại mới với Copilot ở từng request (hoặc để kiểm soát giới hạn quota), hàm `fold_conversation()` gộp toàn bộ tin nhắn quá khứ, kết quả tool `<tool_response>` và câu hỏi hiện tại thành một khối duy nhất:

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

### 6.5. Quản Lý Phiên Nâng Cao (Session Memory)

Proxy hỗ trợ **2 chế độ quản lý hội thoại**:

| Chế độ | Kích hoạt bằng | Hành vi |
|---|---|---|
| **Stateless (Mặc định)** | Không gửi header đặc biệt | Mỗi request tạo `ConversationId` & `SessionId` mới (UUIDv4). Mỗi câu hỏi là phiên độc lập. |
| **Persistent Session** | Header `X-M365-Session-Id: my-session` hoặc model `m365-copilot:persist` | Giữ `conversation_id` và `client_session_id`. Lượt đầu: `isStartOfSession=true`. Các lượt sau: `isStartOfSession=false`. |

**Ưu điểm Persistent Session**: Duy trì trí nhớ hội thoại phía Copilot server mà không cần gửi lại toàn bộ lịch sử trò chuyện (giảm token consumption và TTFT).

### 6.6. Theo Dõi Usage & Score Disengaged

Trả về thông tin mở rộng trong đối tượng `usage` của response:

| Trường | Kiểu | Mô tả |
|---|---|---|
| `x_m365_conversation_messages` | `int` | Số tin đã dùng trong phiên (giới hạn **600 tin/cuộc hội thoại**) |
| `x_m365_dea_score` | `float` | Điểm số đánh giá nguy cơ kích hoạt bộ lọc ngắt kết nối `Disengaged` (0.0 = an toàn, 1.0 = nguy hiểm) |

### 6.7. Image Generation (Tạo ảnh)

Khi người dùng yêu cầu tạo ảnh (ví dụ: *"vẽ hình con mèo"*), Copilot trả về khung `GraphicArt` chứa URL ảnh từ Designer App Service. Proxy tự động:
1. Mint token `designerappservice` ngầm.
2. Tải dữ liệu ảnh dạng binary Buffer/Base64.
3. Nhúng thẳng vào câu trả lời dưới dạng Markdown image hoặc Base64 data URI.

---

## 7. Cơ Chế Xử Lý Lỗi & Khả Năng Phục Hồi (Error Handling & Resilience)

### 7.1. Bảng Mã Lỗi (Error Codes)

| HTTP Code | Error Type | Nguyên nhân | Hành xử Client |
|---|---|---|---|
| `401` | `authentication_error` | API Key sai hoặc thiếu | Kiểm tra lại `Authorization` header |
| `403` | `token_expired` | Sydney JWT hết hạn, auto-refresh thất bại | Đợi hệ thống refresh hoặc đăng nhập lại qua noVNC |
| `429` | `rate_limit_exceeded` | Vượt quá giới hạn tốc độ | Retry sau `Retry-After` header (giây) |
| `500` | `internal_server_error` | Lỗi nội bộ proxy | Retry với exponential backoff |
| `502` | `substrate_connection_error` | Không kết nối được WebSocket tới Substrate | Kiểm tra trạng thái token qua `/v1/token/status` |
| `503` | `service_unavailable` | Token chưa sẵn sàng / Container đang khởi tạo | Retry sau 5-10 giây |
| `504` | `substrate_timeout` | Substrate WebSocket timeout (>120s) | Retry với timeout dài hơn hoặc model khác |

### 7.2. Cấu Trúc Error Response Chuẩn

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

### 7.3. Chiến Lược Retry & Recovery Tự Động

```mermaid
flowchart TD
    Request["Client Request"] --> CheckToken{"Token hợp lệ?"}
    CheckToken -- Có --> ConnectWS["Kết nối Substrate WebSocket"]
    CheckToken -- Không --> AutoRefresh["Kích hoạt Auto-Refresh"]
    AutoRefresh --> RefreshOK{"Refresh thành công?"}
    RefreshOK -- Có --> ConnectWS
    RefreshOK -- Không --> Return403["Trả HTTP 403 + retry_after"]
    
    ConnectWS --> WSError{"Lỗi WebSocket?"}
    WSError -- Không --> StreamResponse["Stream Response → Client"]
    WSError -- Timeout --> Retry["Retry kết nối (max 3 lần)"]
    WSError -- Disengaged --> NewConv["Tạo ConversationId mới, retry"]
    Retry --> RetryOK{"Retry thành công?"}
    RetryOK -- Có --> StreamResponse
    RetryOK -- Không --> Return502["Trả HTTP 502"]
    NewConv --> ConnectWS
```

### 7.4. Xử Lý Bộ Lọc Disengaged

Khi `Disengaged` được kích hoạt (Copilot ngắt hội thoại vì nội dung bị đánh giá vi phạm chính sách):
1. Proxy ghi lại `dea_score` hiện tại vào log.
2. Tự động tạo `ConversationId` mới và retry request.
3. Nếu liên tục bị Disengaged (>3 lần), trả lỗi cho client kèm hướng dẫn.

### 7.5. Rate Limiting

| Tài nguyên | Giới hạn | Hành vi khi vượt |
|---|---|---|
| API Requests | Cấu hình qua `RATE_LIMIT_RPM` (mặc định: 60 req/min) | HTTP 429 + `Retry-After` header |
| Concurrent WebSocket Connections | Cấu hình qua `MAX_CONCURRENT_WS` (mặc định: 5) | Xếp hàng (queue) hoặc HTTP 429 |
| Conversation Messages | 600 tin/cuộc hội thoại (giới hạn Microsoft) | Tự động tạo Conversation mới |

---

## 8. Đóng Gói Docker & Cấu Hình Chi Tiết (Docker Specification)

### 8.1. Cấu Trúc Dockerfile (`Dockerfile`)

```dockerfile
# Sử dụng Python 3.11 Bookworm làm base image
FROM python:3.11-slim-bookworm

# Cài đặt các thư viện hệ thống cần thiết cho Firefox, Camoufox, Xvfb & noVNC
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

# Thiết lập thư mục làm việc
WORKDIR /app

# Copy dependency definition & cài đặt Python package
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cài đặt Camoufox browser binary & dependencies
RUN python -m camoufox fetch

# Copy toàn bộ mã nguồn ứng dụng
COPY . .

# Tạo thư mục dữ liệu dùng chung (Volume)
RUN mkdir -p /app/data

# Khai báo các port: 8000 (FastAPI), 6080 (noVNC)
EXPOSE 8000 6080

# Script khởi chạy entrypoint (Xvfb + Fluxbox + x11vnc + noVNC + FastAPI)
ENTRYPOINT ["/app/docker-entrypoint.sh"]
```

---

### 8.2. File Orchestration Docker Compose (`docker-compose.yml`)

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

### 8.3. Quy Trình Quản Lý Volume Khả Dụng (`./data`)

Mọi thông tin nhạy cảm và trạng thái phiên được tự động ghi vào volume mount `./data`:

| File / Directory | Mục đích | Tính nhạy cảm |
|---|---|---|
| `./data/.env` | Lưu biến môi trường, API keys, refresh token | 🔴 Cao |
| `./data/camoufox_profile/` | Session Cookies, LocalStorage của Firefox/Camoufox (giữ nguyên phiên đăng nhập khi `docker compose restart`) | 🔴 Cao |
| `./data/tokens.json` | Lưu trữ `access_token` và `refresh_token` đã mã hóa | 🔴 Cao |
| `./data/msal-cache.json` | Cache MSAL OAuth credentials | 🔴 Cao |
| `./data/agent-id.json` | Lưu Copilot Studio Agent ID (nếu dùng Agent Mode) | 🟡 Trung bình |
| `./data/logs/` | Application logs (rotated) | 🟢 Thấp |

> [!CAUTION]
> Thư mục `./data` chứa toàn bộ credentials và session data. **KHÔNG** commit thư mục này vào git. Đảm bảo `./data` được thêm vào `.gitignore`.

---

## 9. Biến Môi Trường & Cấu Hình (Environment Variables & Configuration)

### 9.1. Biến Bắt Buộc

| Biến | Giá trị mặc định | Mô tả |
|---|---|---|
| `HOST` | `0.0.0.0` | Địa chỉ IP lắng nghe của FastAPI server |
| `PORT` | `8000` | Cổng HTTP cho FastAPI server |
| `API_KEY` | *(bắt buộc thiết lập)* | API Key để xác thực client. Hỗ trợ nhiều key phân cách bởi dấu phẩy. |
| `DISPLAY` | `:99` | Virtual display cho Xvfb |

### 9.2. Biến Cấu Hình Camoufox & VNC

| Biến | Giá trị mặc định | Mô tả |
|---|---|---|
| `NOVNC_ENABLE` | `true` | Bật/tắt noVNC Web UI Server |
| `VNC_PASSWORD` | *(trống = không mật khẩu)* | Mật khẩu truy cập noVNC (khuyến nghị thiết lập trong production) |
| `CAMOUFOX_HEADLESS` | `false` | `false` = Headful (cho giai đoạn đăng nhập), `true` = Headless (sau đăng nhập) |
| `CAMOUFOX_USER_DATA_DIR` | `/app/data/camoufox_profile` | Thư mục lưu Profile Firefox/Camoufox |
| `CAMOUFOX_AUTO_HEADLESS` | `true` | Tự động chuyển sang Headless sau khi đăng nhập thành công |

### 9.3. Biến Cấu Hình Token & Authentication

| Biến | Giá trị mặc định | Mô tả |
|---|---|---|
| `TOKEN_PREFETCH_MARGIN` | `600` | Số giây trước khi token hết hạn để bắt đầu refresh (mặc định 10 phút) |
| `M365_REFRESH_TOKEN` | *(auto-captured)* | Refresh Token dùng cho OAuth2 rotation. Tự động cập nhật sau mỗi lần refresh. |
| `M365_TENANT_ID` | *(auto-detected)* | Tenant ID từ JWT claims. Có thể override nếu cần. |

### 9.4. Biến Cấu Hình Tool Calling & Features

| Biến | Giá trị mặc định | Mô tả |
|---|---|---|
| `TOOL_CALLING_ENGINE` | `auto` | Engine Tool Calling: `auto` (tự chọn), `agent` (Copilot Studio), `parser` (XML Stream Parser), `disabled` |
| `RATE_LIMIT_RPM` | `60` | Số request tối đa mỗi phút |
| `MAX_CONCURRENT_WS` | `5` | Số kết nối WebSocket đồng thời tối đa |
| `LOG_LEVEL` | `INFO` | Mức log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_TOKEN_CLAIMS` | `false` | Nếu `true`, log JWT claims (chỉ dùng khi debug, **KHÔNG bật trong production**) |

---

## 10. Yêu Cầu Phi Chức Năng (Non-Functional Requirements)

### 10.1. Hiệu Năng (Performance)

| Chỉ số | Yêu cầu | Ghi chú |
|---|---|---|
| RAM (Headless mode) | 450MB – 650MB | Sau khi chuyển sang Headless |
| RAM (Headful / Login mode) | 800MB – 1.2GB | Trong giai đoạn đăng nhập qua noVNC |
| CPU idle | < 2% | Khi không có request đang xử lý |
| TTFT (Time To First Token) | < 5s (m365-quick), < 30s (m365-think-deeper) | Phụ thuộc vào model và tải server Microsoft |
| Cold start time | < 5 giây | Khi container restart với Profile có sẵn |
| Concurrent requests | Tối thiểu 5 request đồng thời | Mỗi request sử dụng 1 WebSocket connection |

### 10.2. Khả Năng Sẵn Sàng (Availability)

- Khi container khởi động lại, hệ thống nạp lại Profile cũ từ volume `./data` và sẵn sàng phục vụ API trong dưới **5 giây**.
- Token auto-refresh chạy ngầm, đảm bảo service availability > **99.5%** (trừ trường hợp Microsoft revoke token).
- Healthcheck endpoint (`/healthz`) hỗ trợ Docker orchestrator (Kubernetes, Docker Swarm) giám sát và tự động restart.

### 10.3. An Toàn Bảo Mật (Security)

| Yêu cầu | Mô tả |
|---|---|
| API Key Authentication | Tất cả endpoint (trừ `/healthz`) yêu cầu `Authorization: Bearer <key>` |
| VNC Password | noVNC hỗ trợ cấu hình mật khẩu truy cập qua biến `VNC_PASSWORD` |
| Token Masking | **Không** xuất log JWT đầy đủ ra `docker logs`. Chỉ log 8 ký tự đầu + `...` |
| Sensitive Volume | Thư mục `./data` chứa credentials, **PHẢI** thêm vào `.gitignore` |
| No External Network (Optional) | Container chỉ cần kết nối outbound tới `*.microsoft.com` và `*.office.com` |

### 10.4. Khả Năng Giám Sát (Observability)

| Tính năng | Mô tả |
|---|---|
| Structured Logging | JSON-formatted logs với `request_id`, `session_id`, `duration_ms` |
| Health Endpoint | `/healthz` trả về trạng thái token, VNC, Camoufox, uptime |
| Token Status | `/v1/token/status` hiển thị chi tiết JWT claims và thời hạn |
| Usage Tracking | `x_m365_conversation_messages` và `x_m365_dea_score` trong response |

---

## 11. Kế Hoạch Kiểm Thử Container (Docker Verification Plan)

### 11.1. Kiểm Thử Chức Năng (Functional Tests)

| # | Test Case | Bước kiểm tra | Kết quả mong đợi |
|---|---|---|---|
| F1 | noVNC Web UI | Truy cập `http://localhost:6080`, thao tác trên Firefox GUI | Hiển thị giao diện Firefox, có thể đăng nhập M365 |
| F2 | Health Check | `curl http://localhost:8000/healthz` | HTTP 200 OK, `token_valid: true` |
| F3 | Models List | `curl -H "Authorization: Bearer $KEY" http://localhost:8000/v1/models` | Danh sách 4+ models |
| F4 | Chat Stream | `POST /v1/chat/completions` với `stream: true` | Nhận luồng SSE `data: {...}` liên tục, kết thúc `[DONE]` |
| F5 | Chat Non-stream | `POST /v1/chat/completions` với `stream: false` | JSON response đầy đủ với `choices`, `usage` |
| F6 | Anthropic Messages | `POST /v1/messages` | SSE events: `message_start`, `content_block_delta`, `message_stop` |
| F7 | Tool Calling | `POST /v1/chat/completions` kèm `tools` array | Response chứa `tool_calls` với `function.name` và `arguments` hợp lệ |
| F8 | Reasoning Content | `POST /v1/chat/completions` với `model: m365-think-deeper` | SSE delta chứa `reasoning_content` |
| F9 | Session Persistence | 2 request liên tiếp với `X-M365-Session-Id: test` | Request thứ 2 nhận ngữ cảnh từ request 1 |
| F10 | Auth Rejection | Request không có `Authorization` header | HTTP 401, error `authentication_error` |

### 11.2. Kiểm Thử Phi Chức Năng (Non-Functional Tests)

| # | Test Case | Bước kiểm tra | Kết quả mong đợi |
|---|---|---|---|
| N1 | Container Restart | `docker compose restart` | Phiên đăng nhập không bị mất, API hoạt động lại < 5s |
| N2 | Token Auto-Refresh | Đợi token gần hết hạn (~75 phút) | Token được refresh tự động, không gián đoạn API |
| N3 | Rate Limiting | Gửi > 60 request trong 1 phút | Request thừa nhận HTTP 429 + `Retry-After` |
| N4 | Memory Usage | Monitor RAM sau 1 giờ chạy Headless | Ổn định < 650MB, không memory leak |
| N5 | Concurrent Requests | Gửi 5 request song song | Tất cả 5 request đều nhận response thành công |
| N6 | Disengaged Recovery | Trigger Disengaged filter | Proxy tự retry với ConversationId mới |

### 11.3. Ví Dụ Lệnh Kiểm Thử

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

## 12. Bảng Thuật Ngữ (Glossary)

| Thuật ngữ | Định nghĩa |
|---|---|
| **Substrate Chathub** | Máy chủ backend Sydney của Microsoft 365 Copilot, giao tiếp qua SignalR WebSocket tại `wss://substrate.office.com/m365Copilot/Chathub` |
| **SignalR** | Giao thức real-time communication của Microsoft, sử dụng Record Separator `0x1E` để phân cách các khung JSON |
| **Camoufox** | Trình duyệt Firefox anti-detect, tối ưu hóa để tránh bị phát hiện là bot automation |
| **noVNC** | Web-based VNC client cho phép truy cập giao diện Desktop qua trình duyệt web |
| **Entra ID** | Dịch vụ xác thực và quản lý danh tính của Microsoft (trước đây là Azure AD) |
| **MSAL** | Microsoft Authentication Library — thư viện xác thực chính thức của Microsoft |
| **JWT** | JSON Web Token — chuẩn mã hóa thông tin xác thực |
| **SSE** | Server-Sent Events — giao thức HTTP streaming một chiều từ server tới client |
| **TTFT** | Time To First Token — thời gian từ khi gửi request đến khi nhận token đầu tiên |
| **Tone** | Tham số định tuyến model trong M365 Copilot (ví dụ: `magic`, `Chat`, `Reasoning`) |
| **Nudge** | Kỹ thuật gõ space + backspace vào ô chat web để ép Copilot tạo kết nối WebSocket mới |
| **DEA Score** | Disengaged Assessment Score — điểm số đánh giá nguy cơ Copilot ngắt kết nối do vi phạm chính sách |
| **Fenced Codeblock** | Khối mã markdown (` ```lang `) được sử dụng để emulate tool calling |
| **Shell Routing** | Kỹ thuật khai thác hành vi phản xạ trả khối ` ```bash ` của Copilot để route thành tool call |
| **Copilot Studio** | Nền tảng của Microsoft cho phép tạo Bot AI tùy chỉnh qua PowerPlatform APIs |
| **Record Separator** | Ký tự ASCII 30 (`0x1E`, `\x1e`) dùng trong giao thức SignalR để phân cách các frame |
| **Token Rotation** | Cơ chế xoay vòng refresh token — mỗi lần refresh, Entra ID cấp refresh token mới thay thế cũ |
| **Prefetch Margin** | Khoảng thời gian (giây) trước khi token hết hạn để bắt đầu refresh sớm |

---

## 13. Lịch Sử Phiên Bản (Revision History)

| Phiên bản | Ngày | Tác giả | Thay đổi chính |
|---|---|---|---|
| `1.0.0` | 2026-08-11 | AI Assistant | Phiên bản SRS ban đầu từ grill-me interview |
| `1.1.0` | 2026-08-11 | AI Assistant | Tối ưu hóa cho Docker Container Architecture, thêm noVNC login flow |
| `2.0.0` | 2026-08-11 | AI Assistant | **Major Update**: Thêm 6 sections mới (SignalR Protocol, Error Handling, Env Config, API Schemas, Glossary, Revision History). Mở rộng chi tiết Token Lifecycle, Dual Tool Calling Engine, Message Translation & Folding, Security Requirements. Bổ sung API request/response examples, error codes table, test cases matrix, và curl commands. |
