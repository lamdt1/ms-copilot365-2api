import time
from fastapi import APIRouter, Depends
from app.auth import verify_api_key
from app.core.model_registry import model_registry

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/v1/models")
async def list_models():
    """
    Returns an OpenAI-compatible model list filtered to the models that the
    currently authenticated M365 Copilot account is licensed to use.

    Before login (no WS URL intercepted yet) the full MODEL_TONE_MAP is
    returned as a fallback.  After browser login, the list narrows to the
    account's actual license tier (Starter / Standard / Premium / etc.).
    """
    return {
        "object": "list",
        "data": model_registry.get_models()
    }
