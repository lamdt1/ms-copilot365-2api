# Microsoft 365 Copilot OpenAI Compatible API Proxy

A Docker-based proxy server that wraps the official Microsoft 365 Copilot interface (which communicates using SignalR over WebSockets) and exposes it as standard, drop-in REST APIs conforming to:
1. **OpenAI Chat Completions API** (`/v1/chat/completions`)
2. **Anthropic Messages API** (`/v1/messages`)
3. **OpenAI Responses API** (`/v1/responses`)

This enables LLM client tooling (like Cline, OpenClaw, Claude Code, Continue, etc.) to use your enterprise M365 Copilot subscription as a backend reasoning model.

## Features
- **Container First**: Packages Python, Firefox (via Camoufox), virtual screen buffer (Xvfb), VNC server, and noVNC in one Docker image.
- **Web-based Login UI**: Connect to `http://localhost:6080` to complete initial login & MFA in the container.
- **Token Rotation**: Extracted credentials are auto-refreshed using Entra ID OAuth refresh flow or background browser "nudge" fallbacks.
- **Dynamic Model List**: `GET /v1/models` returns only the models your account's M365 license actually supports (Starter / Standard / Premium). The list updates automatically after login and on each token refresh.
- **Thinking / Reasoning**: Exposes Copilot reasoning frames in SSE streaming chunks (`reasoning_content` field).
- **XML & Fenced Tool Engine**: Integrates descriptions of functions into prompts and statefully parses XML/Markdown fenced blocks into standard `tool_calls` formats.

---

## 3-Step Quickstart

### 1. Launch Container
Spin up the service stack using docker compose:
```bash
docker compose up -d
```

### 2. Login via VNC
Open your web browser and navigate to `http://localhost:6080`. You will see the virtual desktop window showing a Firefox instance. Complete your Microsoft 365 login and verify any 2-Factor Authentication (MFA). 

Upon successful login, the proxy intercepts your `access_token` and `refresh_token`, saves them to `./data`, and immediately switches Firefox to headless mode to conserve RAM.

### 3. Connect Client
Configure your AI tool (Cline, Continue, etc.) to use:
- **Base URL**: `http://localhost:8000/v1`
- **Model**: query `GET /v1/models` after login to see models available for your license tier (e.g. `m365-copilot`, `m365-quick`, `m365-think-deeper`, `claude-sonnet`)
- **API Key**: `sk-m365-copilot-secret-key` (set via `API_KEY` in environment)

#### Integration Examples

**Claude Code**
Run Claude Code with a custom OpenAI compatible endpoint:
```bash
claude-code --model m365-copilot --base-url http://localhost:8000/v1 --api-key sk-m365-copilot-secret-key
```
Overwrite settings file of Claude Code at ~/.claude/settings.json
```json
{ 
 "env": { 
 "ANTHROPIC_BASE_URL": "http://localhost:8000/v1", 
 "ANTHROPIC_AUTH_TOKEN": "sk-xxx", 
  "ANTHROPIC_DEFAULT_OPUS_MODEL": "<model_name>", 
  "ANTHROPIC_DEFAULT_SONNET_MODEL": "<model_name>", 
  "ANTHROPIC_DEFAULT_HAIKU_MODEL": "<model_name>" 
 }, 
 "autoUpdatesChannel": "latest", 
 "theme": "dark", 
 "model": "<model_name>", 
 "permissions": { 
 "defaultMode": "auto" 
 } 
}
```

**Cline / Roo Code**
In the API Configuration settings:
- **API Provider**: OpenAI Compatible
- **Base URL**: `http://localhost:8000/v1`
- **API Key**: `sk-m365-copilot-secret-key`
- **Model ID**: `m365-copilot`

**Continue (VS Code / JetBrains)**
Add to your `config.json` inside the `models` array:
```json
{
  "title": "M365 Copilot",
  "provider": "openai",
  "model": "m365-copilot",
  "apiBase": "http://localhost:8000/v1",
  "apiKey": "sk-m365-copilot-secret-key"
}
```

---

## Dynamic Model List

The `/v1/models` endpoint returns models based on your account's Microsoft 365 Copilot license tier:

| License Tier | Available Models |
|---|---|
| **Starter** (free/basic) | `m365-copilot`, `m365-quick` |
| **Standard** (paid Copilot) | `m365-copilot`, `m365-quick`, `m365-think-deeper` |
| **Premium / E3 / E5** | All models including `claude-sonnet` |

Before login, all possible models are returned as a fallback. After login, the list automatically narrows to your account's actual entitlements. The list refreshes automatically on token rotation — no restart needed.

```bash
# Check which models your account supports
curl -s http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-m365-copilot-secret-key" | python -m json.tool
```

---

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | `sk-m365-copilot-secret-key` | API authorization key (comma-separated keys allowed) |
| `RATE_LIMIT_RPM` | `60` | Maximum API requests allowed per minute |
| `MAX_CONCURRENT_WS` | `5` | Semaphore limit for concurrent backend WebSocket sessions |
| `CAMOUFOX_AUTO_HEADLESS` | `true` | Transitions browser into headless mode after first login capture |
| `NOVNC_ENABLE` | `true` | Toggles the noVNC browser interface |

---

## Verification Curl Commands

### Health Check
```bash
curl -s http://localhost:8000/healthz | jq .
```

### Completions Stream
```bash
curl -N -s -H "Authorization: Bearer sk-m365-copilot-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"m365-copilot","messages":[{"role":"user","content":"Explain JWT in one sentence."}],"stream":true}' \
  http://localhost:8000/v1/chat/completions
```
