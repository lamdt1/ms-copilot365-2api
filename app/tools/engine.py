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


def _get_tool_name(tool: Dict[str, Any]) -> str:
    """Extract tool name from either OpenAI or Anthropic format."""
    if "function" in tool:
        return tool["function"].get("name", "")
    return tool.get("name", "")  # Anthropic format


def _get_tool_func(tool: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize tool to a unified dict with name/description/parameters."""
    if "function" in tool:
        return tool["function"]
    # Anthropic format: name, description, input_schema at top level
    return {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "parameters": tool.get("input_schema", {}),
    }


def build_tool_xml_injection(tools: List[Dict[str, Any]]) -> str:
    """
    Builds an XML-style tool description block to inject into the prompt
    for the Stream Parser engine (Engine 2).
    Supports both OpenAI and Anthropic tool formats.
    """
    lines = ["<available_tools>"]
    for tool in tools:
        func = _get_tool_func(tool)
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


def get_bash_tool_name(tools: Optional[List[Dict[str, Any]]], messages: List[Dict[str, Any]]) -> Optional[str]:
    """
    Returns the exact bash tool name (e.g. 'Bash' or 'bash') if auto-bash should trigger,
    or None if it should not. Uses case-insensitive matching — Claude Code sends 'Bash' not 'bash'.
    Auto-bash triggers when: bash tool exists AND no recent tool_results in the last 6 messages.
    Only checks recent messages so old tool_use history doesn't prevent fresh file exploration.
    """
    if not tools:
        logger.debug("get_bash_tool_name: no tools → skip")
        return None
    tool_names = {_get_tool_name(t) for t in tools}
    # Case-insensitive: find the actual name as sent by Claude Code ("Bash", "bash", etc.)
    bash_name = next((n for n in tool_names if n.lower() == "bash"), None)
    if not bash_name:
        logger.debug("get_bash_tool_name: no bash-like tool found (tools=%s) → skip", list(tool_names)[:5])
        return None

    # Only check the last 6 messages — old tool_use history shouldn't prevent fresh auto-bash
    recent = messages[-6:] if len(messages) > 6 else messages
    logger.info("get_bash_tool_name: checking %d/%d messages for tool_results (bash=%s)",
                len(recent), len(messages), bash_name)

    for i, msg in enumerate(recent):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "tool":
            logger.info("get_bash_tool_name: SKIP — role=tool at recent[%d]", i)
            return None
        if role == "user" and isinstance(content, list):
            types = [b.get("type") for b in content if isinstance(b, dict)]
            if "tool_result" in types:
                logger.info("get_bash_tool_name: SKIP — tool_result in user msg recent[%d]", i)
                return None
        if role == "assistant":
            # Any prior assistant response means M365 already answered, or auto-bash
            # already triggered this turn. Don't trigger again to avoid feedback loop.
            logger.info("get_bash_tool_name: SKIP — assistant already responded at recent[%d]", i)
            return None

    logger.info("get_bash_tool_name: TRIGGER auto-bash with tool=%s (total msgs=%d)", bash_name, len(messages))
    return bash_name


# Keep backward compat alias used in messages.py
def needs_auto_bash(tools: Optional[List[Dict[str, Any]]], messages: List[Dict[str, Any]]) -> bool:
    return get_bash_tool_name(tools, messages) is not None


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
    tool_names = [_get_tool_name(t) for t in tools]
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
