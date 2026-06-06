import json
import logging
import sqlite3
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


def _slim_events_for_storage(events: list[dict]) -> list[dict]:
    """Reduce token footprint of stored events.

    Large tool responses (question batches, assessment state, profile data)
    bloat the session history. Since these are already persisted in the DB
    and rendered to the frontend via SSE, we replace them with compact
    summaries in the conversation history the LLM sees on subsequent turns.
    """
    slimmed = []
    for event in events:
        content = event.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            slimmed.append(event)
            continue

        new_parts = []
        changed = False
        for part in parts:
            fr = part.get("function_response")
            if fr and isinstance(fr.get("response"), dict):
                response = fr["response"]
                slim = _slim_tool_response(fr.get("name", ""), response)
                if slim is not response:
                    part = {**part, "function_response": {**fr, "response": slim}}
                    changed = True
            new_parts.append(part)

        if changed:
            event = {**event, "content": {**content, "parts": new_parts}}
        slimmed.append(event)

    return slimmed


def _slim_tool_response(tool_name: str, response: dict) -> dict:
    """Replace verbose tool responses with agent-friendly summaries."""
    event_type = response.get("event_type", "")

    # Question batches: strip full question objects, keep metadata + question_ids
    # question_ids MUST be retained so /history can re-hydrate LikertCard widgets.
    if event_type == "assessment.question_batch":
        return {
            "event_type": event_type,
            "batch_id": response.get("batch_id", ""),
            "dimension": response.get("dimension", ""),
            "sub_dimension": response.get("sub_dimension", ""),
            "count": response.get("count", 0),
            "question_ids": response.get("question_ids", []),
            "summary": f"Presented {response.get('count', 0)} questions to user. Waiting for responses.",
        }

    # Profile snapshot: strip spider chart binary and large score objects
    if event_type == "profile.snapshot":
        return {
            "event_type": event_type,
            "saved": response.get("saved"),
            "snapshot_id": response.get("snapshot_id"),
            "quadrant": response.get("quadrant"),
            "summary": "Profile snapshot saved with scores, quadrant placement, and spider chart.",
        }

    # Assessment state: already slimmed in tool, but guard against old events
    if tool_name == "get_assessment_state" and "responses" in response:
        progress = response.get("progress", {})
        return {
            "exists": response.get("exists"),
            "current_phase": response.get("current_phase"),
            "progress": progress,
        }

    # Profile generation: strip large score details
    if tool_name == "generate_profile_snapshot" and "scores" in response:
        return {
            "scores_summary": {dim: round(v.get("score", v.get("weighted_avg", 0)), 2)
                               if isinstance(v, dict) else round(v, 2)
                               for dim, v in response.get("scores", {}).items()},
            "quadrant": response.get("quadrant"),
            "has_spider_chart": response.get("has_spider_chart"),
            "insufficient_dimensions": response.get("insufficient_dimensions"),
        }

    # Scenario: strip after presentation
    if event_type == "assessment.scenario":
        return {
            "event_type": event_type,
            "scenario_id": response.get("scenario_id"),
            "summary": "Scenario presented to user.",
        }

    # Default: pass through if small enough, otherwise truncate
    response_str = json.dumps(response)
    if len(response_str) > 2000:
        logger.debug("Truncating large tool response from %s (%d chars)", tool_name, len(response_str))
        # Keep only top-level keys with scalar values
        return {k: v for k, v in response.items()
                if isinstance(v, (str, int, float, bool, type(None))) and len(str(v)) < 200}

    return response


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
        archive_prior: bool = True,
        title: Optional[str] = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())

        with get_db_session() as conn:
            if archive_prior:
                # Archive all prior active sessions for this user before creating a new one.
                conn.execute(
                    "UPDATE adk_sessions SET archived = TRUE WHERE user_id = ? AND archived = FALSE",
                    (user_id,),
                )

            conn.execute(
                """INSERT INTO adk_sessions
                   (session_id, user_id, app_name, session_state, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    user_id,
                    app_name,
                    json.dumps(state or {}),
                    title,
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

        # Restore conversation history
        events = []
        events_json = row["events_json"] if "events_json" in row.keys() else None
        if events_json:
            try:
                events_data = json.loads(events_json) if isinstance(events_json, str) else events_json
                events = [Event.model_validate(e) for e in events_data]
            except Exception as e:
                logger.warning("Failed to restore events for session %s: %s", session_id, e)

        session = Session(
            id=row["session_id"],
            app_name=row["app_name"] or app_name,
            user_id=row["user_id"],
            state=state or {},
            events=events,
        )
        return session

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

        # Persist updated state and slimmed events to SQLite
        events_data = [e.model_dump(mode="json", exclude_none=True) for e in session.events]
        slimmed = _slim_events_for_storage(events_data)
        with get_db_session() as conn:
            conn.execute(
                """UPDATE adk_sessions
                   SET session_state = ?, events_json = ?, updated_at = ?
                   WHERE session_id = ?""",
                (
                    json.dumps(session.state),
                    json.dumps(slimmed),
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
    ) -> tuple[int, int, float]:
        """Accumulate this turn's usage onto the session row and return the
        new cumulative totals as (input_tokens, output_tokens, cost_usd).

        Returning the totals lets callers emit a session-cumulative number to
        clients without a second SELECT race — the read is on the same
        connection as the write.
        """
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
            row = conn.execute(
                """SELECT total_input_tokens, total_output_tokens, estimated_cost_usd
                   FROM adk_sessions WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
        if not row:
            return (input_tokens, output_tokens, cost_usd)
        return (
            int(row["total_input_tokens"] or 0),
            int(row["total_output_tokens"] or 0),
            float(row["estimated_cost_usd"] or 0.0),
        )

    def record_llm_call(
        self,
        session_id: str | None,
        user_id: str,
        author: str | None,
        phase: str | None,
        model_id: str | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """Insert one row into llm_calls for auditing a single LLM turn.

        Best-effort: errors are logged but never re-raised so a recording
        failure cannot interrupt the main chat stream.
        """
        try:
            with get_db_session() as conn:
                conn.execute(
                    """INSERT INTO llm_calls
                       (session_id, user_id, author, phase, model_id,
                        input_tokens, output_tokens, cost_usd, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        user_id,
                        author,
                        phase,
                        model_id,
                        input_tokens,
                        output_tokens,
                        cost_usd,
                        datetime.utcnow().isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            logger.error(
                "record_llm_call failed for user_id=%s session_id=%s: %s",
                user_id,
                session_id,
                exc,
            )

    def list_llm_calls(
        self,
        user_id: str,
        limit: int,
        before_id: int | None = None,
    ) -> tuple[list[dict], bool]:
        """Return up to *limit* LLM call records for *user_id*, newest first.

        Uses keyset pagination: pass the last row's *id* as *before_id* to
        retrieve the next page.  Returns a tuple of (items, has_more).
        """
        # Clamp to sane bounds (1..100) — enforced server-side regardless of
        # what the caller passes (backend-business-logic-protection R5).
        effective_limit = max(1, min(limit, 100))

        if before_id is not None:
            rows = self._query_llm_calls_before(user_id, effective_limit + 1, before_id)
        else:
            rows = self._query_llm_calls(user_id, effective_limit + 1)

        has_more = len(rows) > effective_limit
        items = rows[:effective_limit]

        return (
            [dict(row) for row in items],
            has_more,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _query_llm_calls(self, user_id: str, fetch_limit: int) -> list:
        with get_db_session() as conn:
            return conn.execute(
                """SELECT id, session_id, author, phase, model_id,
                          input_tokens, output_tokens, cost_usd, created_at
                   FROM llm_calls
                   WHERE user_id = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (user_id, fetch_limit),
            ).fetchall()

    def _query_llm_calls_before(
        self, user_id: str, fetch_limit: int, before_id: int
    ) -> list:
        with get_db_session() as conn:
            return conn.execute(
                """SELECT id, session_id, author, phase, model_id,
                          input_tokens, output_tokens, cost_usd, created_at
                   FROM llm_calls
                   WHERE user_id = ? AND id < ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (user_id, before_id, fetch_limit),
            ).fetchall()

    def get_user_total_cost(self, user_id: str) -> float:
        """Return the user's lifetime accumulated estimated LLM cost (USD)
        across ALL of their sessions, archived included.

        A single SUM — never a per-session loop. Computed separately from the
        archived-filtered session list so archiving a session does not
        undercount the lifetime total.
        """
        with get_db_session() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(estimated_cost_usd), 0.0) AS total
                   FROM adk_sessions WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
        return float(row["total"] or 0.0) if row else 0.0
