import time
from fastapi import APIRouter, Depends
from app.auth import verify_api_key
from app.config import settings

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/v1/models")
async def list_models():
    """
    Exposes OpenAI-compatible models layout.
    """
    models = [
        {
            "id": "m365-copilot",
            "object": "model",
            "created": 1719360000,
            "owned_by": "microsoft",
            "description": "Auto-routing mode (magic tone)"
        },
        {
            "id": "m365-quick",
            "object": "model",
            "created": 1719360000,
            "owned_by": "microsoft",
            "description": "Fast response mode (Chat/Gpt_Quick tone, TTFT ~1-3s)"
        },
        {
            "id": "m365-think-deeper",
            "object": "model",
            "created": 1719360000,
            "owned_by": "microsoft",
            "description": "Deep reasoning mode (Reasoning tone, TTFT ~10-30s)"
        },
        {
            "id": "claude-sonnet",
            "object": "model",
            "created": 1719360000,
            "owned_by": "microsoft",
            "description": "Claude Sonnet 4.5 via M365 Copilot integration"
        }
    ]

    return {
        "object": "list",
        "data": models
    }
