from pydantic_settings import BaseSettings
from typing import Optional, Dict

class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_KEY: str = "sk-m365-copilot-secret-key"
    LOG_LEVEL: str = "INFO"
    RATE_LIMIT_RPM: int = 120
    MAX_CONCURRENT_WS: int = 5
    WS_TIMEOUT_SEC: float = 300.0
    BROWSER_TIMEOUT_SEC: float = 300.0
    SEMAPHORE_TIMEOUT_SEC: float = 30.0

    DISPLAY: str = ":99"
    NOVNC_ENABLE: bool = True
    VNC_PASSWORD: Optional[str] = None

    CAMOUFOX_HEADLESS: bool = False
    CAMOUFOX_AUTO_HEADLESS: bool = True
    CAMOUFOX_USER_DATA_DIR: str = "/app/data/camoufox_profile"

    IMAGE_DOWNLOAD_DIR: str = "generated_images"

    TOKEN_PREFETCH_MARGIN: int = 600
    LOG_TOKEN_CLAIMS: bool = False

    TOOL_CALLING_ENGINE: str = "auto"

    # Model Tone mapping config
    MODEL_TONE_MAP: Dict[str, str] = {
        "m365-copilot": "magic",
        "m365-quick": "Gpt_Quick",
        "m365-think-deeper": "Reasoning",
        "claude-sonnet": "Claude_Sonnet"
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
