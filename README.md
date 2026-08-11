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
- **Model**: `m365-copilot` (or `m365-think-deeper`, `m365-quick`, `claude-sonnet`)
- **API Key**: `sk-m365-copilot-secret-key` (set via `API_KEY` in environment)

---

## Configuration Variables (`.env`)

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
