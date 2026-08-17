import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.token_store import token_store
from app.core.token_refresh import token_refresher
from app.core.rate_limiter import RateLimitMiddleware
from app.api import health, models, token_status, sessions, chat, messages, responses, images

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    logger.info("Application starting up...")
    os.makedirs(settings.IMAGE_DOWNLOAD_DIR, exist_ok=True)
    await token_refresher.start()

    # Expose place to run Camoufox initialization inside Docker
    # (Phase 4 coordinates with this)
    try:
        from app.browser.camoufox_manager import camoufox_manager
        # Initialize background browser manager
        app.state.camoufox = camoufox_manager
        await camoufox_manager.start()
        # Connect nudge refresh hook
        token_refresher.register_nudge_callback(camoufox_manager.nudge_refresh)
    except ImportError:
        logger.warning("CamoufoxManager not yet fully initialized, skipping manager start.")
    except Exception as exc:
        logger.error("Failed to start CamoufoxManager background process: %s", exc)

    yield

    # Shutdown tasks
    logger.info("Application shutting down...")
    await token_refresher.stop()

    try:
        if hasattr(app.state, "camoufox"):
            await app.state.camoufox.stop()
    except Exception as exc:
        logger.error("Error during Camoufox shutdown: %s", exc)


app = FastAPI(
    title="M365 Copilot Compatible REST API Proxy",
    description="OpenAI and Anthropic compatible API proxy wrapping Microsoft 365 Copilot.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS headers setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enforce rate limits
app.add_middleware(RateLimitMiddleware)

# Serve downloaded images as static files
app.mount("/images", StaticFiles(directory=settings.IMAGE_DOWNLOAD_DIR), name="images")

# Registers routers
app.include_router(health.router)
app.include_router(models.router)
app.include_router(token_status.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(messages.router)
app.include_router(responses.router)
app.include_router(images.router)
