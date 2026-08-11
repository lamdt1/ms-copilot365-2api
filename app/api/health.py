import time
from fastapi import APIRouter
from app.core.token_store import token_store
from app.config import settings

router = APIRouter()

START_TIME = time.time()


@router.get("/healthz")
async def health_check():
    """
    Standard health check endpoint.
    Exempt from API key authorization check.
    """
    # Simply inspect local state
    token_valid = token_store.is_valid
    rem = token_store.seconds_remaining

    # Check VNC active (process matching)
    vnc_active = False
    try:
        import subprocess
        vnc_active = b"x11vnc" in subprocess.check_output(["pgrep", "x11vnc"])
    except Exception:
        pass

    # Check browser running
    camoufox_running = False
    try:
        import subprocess
        camoufox_running = b"firefox" in subprocess.check_output(["pgrep", "firefox"])
    except Exception:
        pass

    return {
        "status": "ok",
        "token_valid": token_valid,
        "token_seconds_remaining": rem,
        "token_expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(token_store.exp or 0)) if token_store.exp else None,
        "camoufox_running": camoufox_running,
        "camoufox_mode": "headless" if settings.CAMOUFOX_HEADLESS else "headful",
        "vnc_active": vnc_active,
        "uptime_seconds": int(time.time() - START_TIME),
        "version": "1.0.0"
    }
