from pydantic_settings import BaseSettings
from typing import Optional, Dict, List

class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_KEY: str = "sk-m365-copilot-secret-key"
    LOG_LEVEL: str = "INFO"
    RATE_LIMIT_RPM: int = 120
    MAX_CONCURRENT_WS: int = 5
    WS_TIMEOUT_SEC: float = 300.0
    BROWSER_TIMEOUT_SEC: float = 90.0
    SEMAPHORE_TIMEOUT_SEC: float = 30.0

    DISPLAY: str = ":99"
    NOVNC_ENABLE: bool = True
    VNC_PASSWORD: Optional[str] = None

    CAMOUFOX_HEADLESS: bool = False
    CAMOUFOX_AUTO_HEADLESS: bool = True
    CAMOUFOX_USER_DATA_DIR: str = "/app/data/camoufox_profile"

    IMAGE_DOWNLOAD_DIR: str = "/app/data/images"

    TOKEN_PREFETCH_MARGIN: int = 600
    LOG_TOKEN_CLAIMS: bool = False

    TOOL_CALLING_ENGINE: str = "auto"

    # Model Tone mapping config: model_id → internal tone name (superset of all possible models)
    MODEL_TONE_MAP: Dict[str, str] = {
        "m365-copilot": "magic",
        "m365-quick": "Gpt_Quick",
        "m365-think-deeper": "Reasoning",
        "claude-sonnet": "Claude_Sonnet",
        "claude-opus": "Claude_Opus"
    }

    # Model metadata: model_id → {description, owned_by} for OpenAI-compatible /v1/models response
    MODEL_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
        "m365-copilot": {
            "description": "Auto-routing mode (magic tone)",
            "owned_by": "microsoft"
        },
        "m365-quick": {
            "description": "Fast response mode (Chat/Gpt_Quick tone, TTFT ~1-3s)",
            "owned_by": "microsoft"
        },
        "m365-think-deeper": {
            "description": "Deep reasoning mode (Reasoning tone, TTFT ~10-30s)",
            "owned_by": "microsoft"
        },
        "claude-sonnet": {
            "description": "Claude Sonnet 4.5 via M365 Copilot integration",
            "owned_by": "microsoft"
        },
        "claude-opus": {
            "description": "Claude Opus via M365 Copilot integration",
            "owned_by": "microsoft"
        }
    }

    # License tier → allowed tones mapping
    # Determines which models are shown for a given M365 licenseType from the intercepted WS URL.
    # Keys must match licenseType values observed in substrate.office.com Chathub WebSocket URLs.
    # Values are sets of tone strings that map to MODEL_TONE_MAP values.
    LICENSE_TONE_MAP: Dict[str, list] = {
        "Starter": ["magic", "Gpt_Quick"],
        "Standard": ["magic", "Gpt_Quick", "Reasoning"],
        "Premium": ["magic", "Gpt_Quick", "Reasoning", "Claude_Sonnet", "Claude_Opus"],
        # Enterprise plans — treat same as Premium
        "E3": ["magic", "Gpt_Quick", "Reasoning", "Claude_Sonnet", "Claude_Opus"],
        "E5": ["magic", "Gpt_Quick", "Reasoning", "Claude_Sonnet", "Claude_Opus"],
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
