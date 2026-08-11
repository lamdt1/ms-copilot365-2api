from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import verify_api_key
from app.core.session_manager import session_manager

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/v1/sessions/{session_id}")
async def get_session_details(session_id: str):
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    return sess


@router.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    success = session_manager.delete_session(session_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    return {"status": "deleted"}


@router.delete("/v1/sessions")
async def clear_all_sessions():
    session_manager.clear_all()
    return {"status": "all_cleared"}
