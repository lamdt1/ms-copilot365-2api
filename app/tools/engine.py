import json
import logging
from typing import List, Dict, Any, Optional

from app.config import settings
from app.tools.agent_store import load_agent_id
from app.tools.agent_mode import get_or_create_agent
from app.tools.stream_parser import ToolCallStreamParser

logger = logging.getLogger(__name__)

# Default system prompt for Copilot Studio Agent Mode
AGENT_SYSTEM_PROMPT = """You are an AI assistant with tool calling capabilities.
When the user requests a tool action, respond with the tool invocation using
fenced code blocks. For example, to read a file:

```read
path: README.md
```

For shell commands:
```bash
ls -la
```

Always use the appropriate code block language tag matching the tool name."""



def build_tool_xml_injection(tools: List[Dict[str, Any]]) -> str:
    """
    Builds an XML-style tool description block to inject into the prompt
    for the Stream Parser engine (Engine 2).
    """
    lines = ["<available_tools>"]
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        params = json.dumps(func.get("parameters", {}))
        lines.append(f'  <tool name="{name}" description="{desc}">')
        lines.append(f"    <parameters>{params}</parameters>")
        lines.append(f"  </tool>")
    lines.append("</available_tools>")
    lines.append("")
    lines.append("CRITICAL TOOL-USE INSTRUCTIONS:")
    lines.append("1. You HAVE full access to the tools listed above. Use them to complete any task.")
    lines.append("2. To run shell commands or read files: ALWAYS use the bash tool — NEVER say you cannot.")
    lines.append("3. FORBIDDEN phrases: 'I cannot access', 'I don't have access', 'unable to read files'.")
    lines.append("4. Format ALL tool calls EXACTLY like this JSON inside tags:")
    lines.append('<tool_call>{"name": "bash", "arguments": {"command": "ls -la"}}</tool_call>')
    lines.append("")
    lines.append("Example — listing project files:")
    lines.append('<tool_call>{"name": "bash", "arguments": {"command": "find . -type f | head -50"}}</tool_call>')
    return "\n".join(lines)


def needs_auto_bash(tools: Optional[List[Dict[str, Any]]], messages: List[Dict[str, Any]]) -> bool:
    """
    Returns True when we should auto-generate a bash tool_use response WITHOUT querying M365.
    Triggered when: bash tool is available AND no tool_results exist yet in the conversation.
    This prevents M365 from refusing filesystem access on Claude Code's initial request.
    """
    if not tools:
        return False
    tool_names = {t.get("function", {}).get("name", "") for t in tools}
    if "bash" not in tool_names:
        return False
    # Check if any previous tool results exist (means Claude Code already explored)
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "tool":
            return False
        if role == "user" and isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                return False
        # Also skip if assistant already made a tool_use (second+ turn)
        if role == "assistant" and isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
                return False
    return True


async def resolve_tool_strategy(
    tools: Optional[List[Dict[str, Any]]],
    prompt: str
) -> tuple[Optional[str], Optional[str], Optional[ToolCallStreamParser]]:
    """
    Determines the tool calling engine and augments the prompt.

    Returns:
        (agent_id, augmented_prompt, parser_instance)
        - agent_id is non-None only for agent mode
        - augmented_prompt has tool instructions prepended if using parser mode
        - parser_instance is a ToolCallStreamParser if using parser mode
    """
    if not tools:
        return None, prompt, None

    engine = settings.TOOL_CALLING_ENGINE

    if engine == "disabled":
        return None, prompt, None

    # Check for shell-like tools that benefit from agent mode
    tool_names = [t.get("function", {}).get("name", "") for t in tools]
    has_shell = any(n in ("bash", "shell", "run_command", "execute_bash") for n in tool_names)

    if engine == "agent" or (engine == "auto" and has_shell):
        # Try agent mode
        agent_id = load_agent_id()
        if not agent_id:
            try:
                agent_id = await get_or_create_agent(AGENT_SYSTEM_PROMPT)
            except Exception as exc:
                logger.warning("ToolEngine: agent mode failed, falling back to parser: %s", exc)

        if agent_id:
            return agent_id, prompt, None

    # Fallback: parser mode (XML injection + stream parsing)
    tool_injection = build_tool_xml_injection(tools)
    augmented_prompt = f"{tool_injection}\n\n{prompt}"
    parser = ToolCallStreamParser()
    return None, augmented_prompt, parser
