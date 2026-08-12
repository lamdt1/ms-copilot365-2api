import asyncio
import logging
from app.browser.camoufox_manager import camoufox_manager

logging.basicConfig(level=logging.INFO)


async def send_chat_in_browser():
    # Find active browser page in camoufox_manager
    # Note: camoufox_manager is singleton in app process, but we can access it if we run in app context
    pass
