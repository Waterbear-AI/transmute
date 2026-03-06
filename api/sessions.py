import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth import get_current_user_id
from agents.transmutation.session_service import SqliteSessionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Shared session service instance
_session_service = SqliteSessionService()

APP_NAME = "transmutation"


class CreateSessionRequest(BaseModel):
    app_name: str = Field(default=APP_NAME)


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    app_name: Optional[str] = None
    archived: bool = False
    created_at: Optional[str] = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    count: int


@router.post("", response_model=SessionResponse)
async def create_session(
    body: CreateSessionRequest = CreateSessionRequest(),
    user_id: str = Depends(get_current_user_id),
):
    """Create a new session, archiving any prior active sessions."""
    session = await _session_service.create_session(
        app_name=body.app_name,
        user_id=user_id,
    )
    return SessionResponse(
        session_id=session.id,
        user_id=session.user_id,
        app_name=body.app_name,
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user_id: str = Depends(get_current_user_id),
):
    """List all non-archived sessions for the current user."""
    result = await _session_service.list_sessions(
        app_name=APP_NAME,
        user_id=user_id,
    )
    sessions = [
        SessionResponse(
            session_id=s.id,
            user_id=s.user_id,
            app_name=APP_NAME,
        )
        for s in result.sessions
    ]
    return SessionListResponse(sessions=sessions, count=len(sessions))
