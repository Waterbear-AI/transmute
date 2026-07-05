import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.auth import get_current_user_id
from agents.transmutation.session_service import SqliteSessionService
from agents.transmutation.question_bank import get_question_bank
from db.database import get_db_session
from rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Shared session service instance
_session_service = SqliteSessionService()

APP_NAME = "transmutation"


class CreateSessionRequest(BaseModel):
    app_name: str = Field(default=APP_NAME)
    archive_prior: bool = Field(
        default=False,
        description="Archive all prior active sessions before creating the new one.",
    )
    title: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional user-chosen label for this session tab.",
    )


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    app_name: Optional[str] = None
    archived: bool = False
    created_at: Optional[str] = None
    message_count: int = 0
    title: Optional[str] = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    count: int
    user_total_cost_usd: float = 0.0  # lifetime accumulated LLM cost across all sessions


class RenameSessionRequest(BaseModel):
    title: str = Field(description="New title for the session tab (1-80 characters).")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 1:
            raise ValueError("title must be at least 1 character after trimming whitespace")
        if len(stripped) > 80:
            raise ValueError("title must not exceed 80 characters after trimming whitespace")
        return stripped


# --- History endpoint models ---

class HistoryMessage(BaseModel):
    """A single reconstructed conversation message."""
    role: str  # "user" | "agent" | "widget"
    text: Optional[str] = None
    event_type: Optional[str] = None
    data: Optional[dict[str, Any]] = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]
    answered_responses: dict[str, Any] = Field(default_factory=dict)
    scenario_responses: dict[str, Any] = Field(default_factory=dict)


# --- Widget re-hydration helpers ---

def _rehydrate_question_batch(response: dict) -> dict:
    """Re-hydrate a slimmed assessment.question_batch response with full question objects.

    The slimmed version stored by BE-002 retains question_ids so we can look up
    the full text, scale_type, and scale_labels from the question bank.
    """
    question_ids = response.get("question_ids", [])
    if not question_ids:
        return response

    qb = get_question_bank()
    scale_types = qb.scale_types
    questions = []
    missing = []
    for qid in question_ids:
        q = qb.get_question_by_id(qid)
        if q:
            eq = dict(q)
            st = scale_types.get(q.get("scale_type", ""), {})
            eq["scale_labels"] = st.get(
                "labels",
                ["Strongly Disagree", "Disagree", "Neutral", "Agree", "Strongly Agree"],
            )
            questions.append(eq)
        else:
            missing.append(qid)

    if missing:
        logger.warning("Re-hydration: question IDs not found in bank: %s", missing)

    hydrated = dict(response)
    hydrated["questions"] = questions
    return hydrated


def _rehydrate_scenario(response: dict) -> dict:
    """Re-hydrate a slimmed assessment.scenario response with full narrative and choices."""
    scenario_id = response.get("scenario_id")
    if not scenario_id:
        return response

    qb = get_question_bank()
    scenario = qb.get_scenario_by_id(scenario_id)
    if not scenario:
        logger.warning("Re-hydration: scenario_id %s not found in question bank", scenario_id)
        return response

    hydrated = dict(response)
    hydrated["narrative"] = scenario.get("narrative", "")
    hydrated["choices"] = scenario.get("choices", [])
    return hydrated


def _rehydrate_widget_response(response: dict) -> dict:
    """Dispatch re-hydration based on event_type."""
    event_type = response.get("event_type", "")
    if event_type == "assessment.question_batch":
        return _rehydrate_question_batch(response)
    if event_type == "assessment.scenario":
        return _rehydrate_scenario(response)
    return response


@router.post("", response_model=SessionResponse)
async def create_session(
    body: CreateSessionRequest = CreateSessionRequest(),
    user_id: str = Depends(get_current_user_id),
):
    """Create a new session, optionally archiving prior active sessions."""
    # Strip and sanitize title: remove leading/trailing whitespace; treat blank as None.
    raw_title = body.title
    sanitized_title = raw_title.strip() if raw_title else None
    if not sanitized_title:
        sanitized_title = None

    session = await _session_service.create_session(
        app_name=body.app_name,
        user_id=user_id,
        state={"user_id": user_id},
        archive_prior=body.archive_prior,
        title=sanitized_title,
    )
    return SessionResponse(
        session_id=session.id,
        user_id=session.user_id,
        app_name=body.app_name,
        created_at=datetime.utcnow().isoformat(),
        title=sanitized_title,
    )


@router.patch("/{session_id}", response_model=SessionResponse)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Rename a session tab owned by the current user.

    Returns 404 if the session does not exist or is not owned by the caller —
    this prevents enumeration of session IDs belonging to other users.
    """
    updated = _session_service.rename_session(
        user_id=user_id,
        session_id=session_id,
        title=body.title,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")

    with get_db_session() as conn:
        row = conn.execute(
            """SELECT session_id, user_id, app_name, archived, created_at, title
               FROM adk_sessions WHERE session_id = ? AND user_id = ?""",
            (session_id, user_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse(
        session_id=row["session_id"],
        user_id=row["user_id"],
        app_name=row["app_name"] or APP_NAME,
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        title=row["title"],
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user_id: str = Depends(get_current_user_id),
):
    """List all non-archived sessions for the current user with metadata."""
    with get_db_session() as conn:
        rows = conn.execute(
            """SELECT session_id, user_id, app_name, archived, created_at, events_json, title
               FROM adk_sessions
               WHERE user_id = ? AND archived = FALSE
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()

    sessions = []
    for row in rows:
        # Count user messages from events
        msg_count = 0
        events_json = row["events_json"]
        if events_json:
            try:
                events = json.loads(events_json) if isinstance(events_json, str) else events_json
                msg_count = sum(1 for e in events if e.get("content", {}).get("role") == "user")
            except Exception:
                pass

        sessions.append(SessionResponse(
            session_id=row["session_id"],
            user_id=row["user_id"],
            app_name=row["app_name"] or APP_NAME,
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            message_count=msg_count,
            title=row["title"],
        ))

    # Lifetime accumulated cost across all the user's sessions (best-effort).
    try:
        user_total_cost = _session_service.get_user_total_cost(user_id)
    except Exception:
        logger.warning("Failed to compute user total cost for %s", user_id, exc_info=True)
        user_total_cost = 0.0

    return SessionListResponse(
        sessions=sessions,
        count=len(sessions),
        user_total_cost_usd=round(user_total_cost, 6),
    )


@router.get("/{session_id}/history", response_model=HistoryResponse)
async def get_session_history(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Get conversation history for a session with re-hydrated widget data.

    Returns:
        200: Session history with messages and answered_responses.
        401: Not authenticated.
        404: Session not found or not owned by the caller.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT events_json FROM adk_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    messages: list[HistoryMessage] = []
    events_json = row["events_json"]
    if events_json:
        try:
            events = json.loads(events_json) if isinstance(events_json, str) else events_json
            for event in events:
                content = event.get("content", {})
                parts = content.get("parts", [])
                role = content.get("role")
                if not role or not parts:
                    continue

                for part in parts:
                    # Text messages
                    if part.get("text"):
                        text = part["text"]
                        msg_role = "user" if role == "user" else "agent"
                        # Filter out automated batch_complete JSON messages
                        # and the auto-greet seed (server-internal, never shown).
                        if msg_role == "user":
                            if text.startswith("[session_start]"):
                                continue
                            try:
                                parsed = json.loads(text)
                                if isinstance(parsed, dict) and parsed.get("type") == "batch_complete":
                                    continue
                            except (json.JSONDecodeError, TypeError):
                                pass
                        messages.append(HistoryMessage(role=msg_role, text=text))

                    # Function responses — re-hydrate and emit as widget messages
                    elif part.get("function_response"):
                        response = part["function_response"].get("response", {})
                        if isinstance(response, dict) and response.get("event_type"):
                            # Re-hydrate slimmed widget payloads from question bank
                            hydrated = _rehydrate_widget_response(response)
                            messages.append(HistoryMessage(
                                role="widget",
                                event_type=hydrated["event_type"],
                                data=hydrated,
                            ))
        except Exception as e:
            logger.warning("Failed to parse events for session %s: %s", session_id, e)
            # Graceful degradation: return empty messages on corrupt events_json

    # Fetch answered responses (Likert) and scenario responses so widgets can
    # show completed state and let ScenarioCard prefill a prior choice on replay.
    answered_responses: dict[str, Any] = {}
    scenario_responses: dict[str, Any] = {}
    with get_db_session() as conn:
        state_row = conn.execute(
            "SELECT responses, scenario_responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if state_row and state_row["responses"]:
            try:
                answered_responses = (
                    json.loads(state_row["responses"])
                    if isinstance(state_row["responses"], str)
                    else state_row["responses"]
                )
            except Exception as e:
                logger.warning(
                    "Failed to parse assessment_state.responses for user %s: %s",
                    user_id, e,
                )
        if state_row and state_row["scenario_responses"]:
            try:
                scenario_responses = (
                    json.loads(state_row["scenario_responses"])
                    if isinstance(state_row["scenario_responses"], str)
                    else state_row["scenario_responses"]
                )
            except Exception as e:
                logger.warning(
                    "Failed to parse assessment_state.scenario_responses for user %s: %s",
                    user_id, e,
                )

    return HistoryResponse(
        session_id=session_id,
        messages=messages,
        answered_responses=answered_responses,
        scenario_responses=scenario_responses,
    )


@router.post("/reset", response_model=SessionResponse)
@limiter.limit("20/hour")
async def reset_session(
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """Full reset: wipe all progress and start fresh from orientation.

    Deletes all user-scoped domain data (assessment_state, profile_snapshots,
    education_progress, development_roadmap, practice_journal, graduation_record,
    check_in_log, moral_ledger), archives sessions, resets current_phase to
    'orientation', and creates a fresh session. safety_log is retained.
    """
    with get_db_session() as conn:
        # Reset user phase to orientation
        conn.execute(
            "UPDATE users SET current_phase = 'orientation' WHERE id = ?",
            (user_id,),
        )
        # Archive all existing sessions
        conn.execute(
            "UPDATE adk_sessions SET archived = TRUE WHERE user_id = ?",
            (user_id,),
        )
        # Clear assessment state
        conn.execute(
            "DELETE FROM assessment_state WHERE user_id = ?",
            (user_id,),
        )
        # Clear profile snapshots
        conn.execute(
            "DELETE FROM profile_snapshots WHERE user_id = ?",
            (user_id,),
        )
        # Clear education progress
        conn.execute(
            "DELETE FROM education_progress WHERE user_id = ?",
            (user_id,),
        )
        # Clear development roadmap
        conn.execute(
            "DELETE FROM development_roadmap WHERE user_id = ?",
            (user_id,),
        )
        # Clear practice journal
        conn.execute(
            "DELETE FROM practice_journal WHERE user_id = ?",
            (user_id,),
        )
        # Clear graduation/check-in records
        conn.execute(
            "DELETE FROM graduation_record WHERE user_id = ?",
            (user_id,),
        )
        conn.execute(
            "DELETE FROM check_in_log WHERE user_id = ?",
            (user_id,),
        )
        # Clear moral ledger (R11: must be wiped on reset)
        conn.execute(
            "DELETE FROM moral_ledger WHERE user_id = ?",
            (user_id,),
        )
        # NOTE: safety_log is intentionally NOT deleted — it is an audit trail

    logger.info("Full reset for user %s", user_id)

    # Create a fresh session
    session = await _session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        state={"user_id": user_id},
    )
    return SessionResponse(
        session_id=session.id,
        user_id=session.user_id,
        app_name=APP_NAME,
        created_at=datetime.utcnow().isoformat(),
    )
