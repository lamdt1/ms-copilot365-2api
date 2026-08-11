import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AGENT_FILE = Path(os.getenv("CAMOUFOX_USER_DATA_DIR", "/app/data")).parent / "agent-id.json"


def load_agent_id() -> Optional[str]:
    if AGENT_FILE.exists():
        try:
            data = json.loads(AGENT_FILE.read_text())
            return data.get("agent_id")
        except Exception as exc:
            logger.warning("agent_store: failed to load %s: %s", AGENT_FILE, exc)
    return None


def save_agent_id(agent_id: str) -> None:
    try:
        AGENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        AGENT_FILE.write_text(json.dumps({"agent_id": agent_id}))
        logger.info("agent_store: cached agent_id = %s", agent_id)
    except Exception as exc:
        logger.warning("agent_store: failed to save %s: %s", AGENT_FILE, exc)
