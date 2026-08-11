import time
from fastapi import APIRouter, Depends
from app.auth import verify_api_key
from app.core.token_store import token_store

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/v1/token/status")
async def get_token_status():
    """
    Returns diagnostic details on the captured Sydney JWT.
    """
    exp_iso = None
    if token_store.exp:
        exp_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(token_store.exp))

    refreshed_iso = None
    if token_store.last_refreshed:
        refreshed_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(token_store.last_refreshed))

    return {
        "valid": token_store.is_valid,
        "expires_at": exp_iso,
        "seconds_remaining": token_store.seconds_remaining,
        "claims": {
            "oid": token_store.oid,
            "tid": token_store.tid,
            "user_principal_name": token_store.upn
        },
        "refresh_token_available": token_store.refresh_token is not None,
        "last_refreshed_at": refreshed_iso
    }
