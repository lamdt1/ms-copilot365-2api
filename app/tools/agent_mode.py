import logging
from typing import Optional

import httpx

from app.core.token_store import token_store
from app.tools.agent_store import load_agent_id, save_agent_id

logger = logging.getLogger(__name__)

BAP_BASE = "https://api.bap.microsoft.com"
PP_BASE = "https://api.powerplatform.com"


async def get_or_create_agent(system_instructions: str) -> Optional[str]:
    """
    Checks for cached agent, or creates a new Copilot Studio bot
    via PowerPlatform APIs and returns the threadLevelGptId string.
    """
    cached = load_agent_id()
    if cached:
        logger.debug("agent_mode: Using cached agent_id = %s", cached)
        return cached

    access_token = token_store.access_token
    if not access_token:
        logger.error("agent_mode: No access_token available for PowerPlatform API calls")
        return None

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Discover default environment
            env_url = f"{BAP_BASE}/providers/Microsoft.BusinessAppPlatform/environments?$select=properties.displayName&api-version=2024-05-01"
            resp = await client.get(env_url, headers=headers)
            if resp.status_code != 200:
                logger.error("agent_mode: Failed to list environments: %d %s", resp.status_code, resp.text)
                return None

            envs = resp.json().get("value", [])
            if not envs:
                logger.error("agent_mode: No PowerPlatform environments found")
                return None

            env_id = envs[0].get("name")
            logger.info("agent_mode: Using environment %s", env_id)

            # 2. Create minimal bot
            bot_url = f"{PP_BASE}/powervirtualagents/environments/{env_id}/bots/minimalBots?api-version=2022-03-01"
            bot_payload = {
                "displayName": "M365 Copilot Proxy Agent",
                "systemInstructions": system_instructions,
                "type": "minimalBot"
            }
            resp = await client.post(bot_url, headers=headers, json=bot_payload)
            if resp.status_code not in (200, 201):
                logger.error("agent_mode: Failed to create bot: %d %s", resp.status_code, resp.text)
                return None

            bot_data = resp.json()
            bot_id = bot_data.get("botId")
            schema_name = bot_data.get("schemaName")
            logger.info("agent_mode: Created bot %s (schema: %s)", bot_id, schema_name)

            # 3. Publish bot
            publish_url = f"{PP_BASE}/powervirtualagents/environments/{env_id}/bots/{bot_id}/publish?api-version=2022-03-01"
            resp = await client.post(publish_url, headers=headers)
            if resp.status_code not in (200, 201, 202):
                logger.error("agent_mode: Failed to publish bot: %d %s", resp.status_code, resp.text)
                return None

            pub_data = resp.json()
            title_id = pub_data.get("titleId")
            agent_id = f"T_{title_id}.{bot_id}.gpt.default"
            logger.info("agent_mode: Published agent_id = %s", agent_id)

            save_agent_id(agent_id)
            return agent_id

    except Exception as exc:
        logger.error("agent_mode: Exception during agent creation: %s", exc)
        return None
