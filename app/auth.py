from fastapi import Security, HTTPException, status, Query
from fastapi.security.api_key import APIKeyHeader
from app.config import settings

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)

def verify_api_key(
    api_key_header: str = Security(API_KEY_HEADER),
    api_key_query: str = Query(None, alias="api_key")
) -> str:
    # 1. Split valid keys if comma-separated
    valid_keys = [k.strip() for k in settings.API_KEY.split(",")]

    token = None
    # 2. Extract Bearer token from header
    if api_key_header and api_key_header.startswith("Bearer "):
        parts = api_key_header.split(" ", 1)
        token = parts[1].strip() if len(parts) > 1 else None
    elif api_key_header:
        token = api_key_header.strip()

    # 3. Fallback to query parameter
    if not token and api_key_query:
        token = api_key_query.strip()

    if not token or token not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid API key",
                    "type": "authentication_error",
                    "code": 401
                }
            }
        )
    return token
