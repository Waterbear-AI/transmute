import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from google.adk.events.event import Event
from google.adk.sessions import Session
from google.adk.sessions.base_session_service import (
    BaseSessionService,
    GetSessionConfig,
    ListSessionsResponse,
)

from db.database import get_db_session

logger = logging.getLogger(__name__)


class SqliteSessionService(BaseSessionService):
    """ADK session service backed by SQLite adk_sessions table.

    Archives prior sessions on creation, persists session state as JSON,
    and tracks token usage / estimated cost per session.
    """

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())

        with get_db_session() as conn:
            # Archive prior sessions for this user
            conn.execute(
                "UPDATE adk_sessions SET archived = TRUE WHERE user_id = ? AND archived = FALSE",
                (user_id,),
            )

            conn.execute(
                """INSERT INTO adk_sessions
                   (session_id, user_id, app_name, session_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    user_id,
                    app_name,
                    json.dumps(state or {}),
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )

        return Session(
            id=sid,
            app_name=app_name,
            user_id=user_id,
            state=state or {},
        )

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT * FROM adk_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()

        if not row:
            return None

        state = row["session_state"]
        if isinstance(state, str):
            state = json.loads(state) if state else {}

        return Session(
            id=row["session_id"],
            app_name=row["app_name"] or app_name,
            user_id=row["user_id"],
            state=state or {},
        )

    async def list_sessions(
        self, *, app_name: str, user_id: str
    ) -> ListSessionsResponse:
        with get_db_session() as conn:
            rows = conn.execute(
                "SELECT * FROM adk_sessions WHERE user_id = ? AND archived = FALSE ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()

        sessions = []
        for row in rows:
            state = row["session_state"]
            if isinstance(state, str):
                state = json.loads(state) if state else {}

            sessions.append(
                Session(
                    id=row["session_id"],
                    app_name=row["app_name"] or app_name,
                    user_id=row["user_id"],
                    state=state or {},
                )
            )

        return ListSessionsResponse(sessions=sessions)

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        with get_db_session() as conn:
            conn.execute(
                "DELETE FROM adk_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            )

    async def append_event(
        self, session: Session, event: Event
    ) -> Event:
        # Let base class handle state merging into the in-memory session
        event = await super().append_event(session=session, event=event)
        session.last_update_time = event.timestamp

        # Persist updated state to SQLite
        with get_db_session() as conn:
            conn.execute(
                """UPDATE adk_sessions
                   SET session_state = ?, updated_at = ?
                   WHERE session_id = ?""",
                (
                    json.dumps(session.state),
                    datetime.utcnow().isoformat(),
                    session.id,
                ),
            )

        return event

    def update_token_usage(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """Update cumulative token usage for a session."""
        with get_db_session() as conn:
            conn.execute(
                """UPDATE adk_sessions
                   SET total_input_tokens = total_input_tokens + ?,
                       total_output_tokens = total_output_tokens + ?,
                       estimated_cost_usd = estimated_cost_usd + ?,
                       updated_at = ?
                   WHERE session_id = ?""",
                (input_tokens, output_tokens, cost_usd, datetime.utcnow().isoformat(), session_id),
            )
