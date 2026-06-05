"""Integration tests for LLM call recording wired into _stream_agent_response.

Uses unittest.mock to simulate ADK runner events with usage_metadata, then
asserts the llm_calls table is populated correctly.

Covers:
- record_llm_call invoked per event with non-zero tokens
- zero-token events are NOT recorded
- cost_usd is calculated via _estimate_cost per event
- author and phase are captured correctly
- recording failure does NOT interrupt the SSE stream
"""

import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.transmutation.session_service import SqliteSessionService
from db.database import get_db_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_user(user_id: str, phase: str = "orientation") -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (user_id, "T", f"{user_id}@t.example.com", "pw", phase),
        )


def _insert_session(user_id: str) -> str:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO adk_sessions (session_id, user_id, app_name, created_at)
               VALUES (?, ?, 'transmutation', datetime('now'))""",
            (sid, user_id),
        )
    return sid


def _llm_call_count(user_id: str) -> int:
    with get_db_session() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE user_id=?", (user_id,)
        ).fetchone()[0]


def _llm_call_rows(user_id: str) -> list[dict]:
    with get_db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM llm_calls WHERE user_id=? ORDER BY id ASC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def _make_event(
    input_tokens: int = 0,
    output_tokens: int = 0,
    author: str = "transmutation_agent",
    is_final: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> MagicMock:
    """Build a minimal mock ADK event."""
    event = MagicMock()
    event.author = author
    event.error_code = error_code
    event.error_message = error_message
    event.content = None
    event.partial = False
    event.is_final_response.return_value = is_final

    if input_tokens or output_tokens:
        event.usage_metadata = MagicMock()
        event.usage_metadata.prompt_token_count = input_tokens
        event.usage_metadata.candidates_token_count = output_tokens
    else:
        event.usage_metadata = None

    return event


async def _mock_runner(events: list) -> AsyncGenerator:
    """Async generator that yields the given events."""
    for event in events:
        yield event


def _run_stream(user_id: str, session_id: str, events: list, phase: str = "orientation"):
    """Execute _stream_agent_response with a mocked runner and return all SSE text."""
    from api.chat import _stream_agent_response

    async def _collect():
        chunks = []
        async for chunk in _stream_agent_response(user_id, session_id, "hello"):
            chunks.append(chunk)
        return "".join(chunks)

    import asyncio

    with patch("api.chat._runner") as mock_runner, \
         patch("api.chat._get_user_phase", return_value=phase):
        mock_runner.run_async.return_value = _mock_runner(events)
        return asyncio.get_event_loop().run_until_complete(_collect())


# ---------------------------------------------------------------------------
# Tests: recording triggered correctly
# ---------------------------------------------------------------------------

class TestLlmCallRecordingTriggered:
    def test_single_event_with_tokens_creates_one_row(self):
        uid = str(uuid.uuid4())
        _insert_user(uid, phase="assessment")
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50)]
        _run_stream(uid, sid, events, phase="assessment")

        assert _llm_call_count(uid) == 1

    def test_three_events_with_tokens_creates_three_rows(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [
            _make_event(input_tokens=100, output_tokens=50, author="agent_a"),
            _make_event(input_tokens=200, output_tokens=80, author="agent_b"),
            _make_event(input_tokens=50, output_tokens=25, author="agent_c"),
        ]
        _run_stream(uid, sid, events)

        assert _llm_call_count(uid) == 3

    def test_zero_token_event_is_not_recorded(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        # One zero-token event only
        events = [_make_event(input_tokens=0, output_tokens=0)]
        _run_stream(uid, sid, events)

        assert _llm_call_count(uid) == 0

    def test_mix_of_zero_and_nonzero_events(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [
            _make_event(input_tokens=0, output_tokens=0),  # skip
            _make_event(input_tokens=100, output_tokens=50),  # record
            _make_event(input_tokens=0, output_tokens=0),  # skip
            _make_event(input_tokens=200, output_tokens=100),  # record
        ]
        _run_stream(uid, sid, events)

        assert _llm_call_count(uid) == 2


# ---------------------------------------------------------------------------
# Tests: author and phase captured correctly
# ---------------------------------------------------------------------------

class TestLlmCallAuthorAndPhase:
    def test_author_from_event_is_stored(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50, author="education_agent")]
        _run_stream(uid, sid, events, phase="education")

        rows = _llm_call_rows(uid)
        assert rows[0]["author"] == "education_agent"

    def test_phase_from_user_row_is_stored(self):
        uid = str(uuid.uuid4())
        _insert_user(uid, phase="assessment")
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50)]
        _run_stream(uid, sid, events, phase="assessment")

        rows = _llm_call_rows(uid)
        assert rows[0]["phase"] == "assessment"

    def test_multiple_events_all_get_same_phase(self):
        """Phase is read once per turn and reused — not queried per event."""
        uid = str(uuid.uuid4())
        _insert_user(uid, phase="education")
        sid = _insert_session(uid)

        events = [
            _make_event(input_tokens=50, output_tokens=25, author="agent_a"),
            _make_event(input_tokens=60, output_tokens=30, author="agent_b"),
        ]
        _run_stream(uid, sid, events, phase="education")

        rows = _llm_call_rows(uid)
        assert all(r["phase"] == "education" for r in rows)


# ---------------------------------------------------------------------------
# Tests: cost calculation accuracy
# ---------------------------------------------------------------------------

class TestLlmCallCostCalculation:
    def test_cost_usd_matches_estimate_cost(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        input_tokens = 1000
        output_tokens = 500
        events = [_make_event(input_tokens=input_tokens, output_tokens=output_tokens)]
        _run_stream(uid, sid, events)

        from api.chat import _estimate_cost
        expected_cost = _estimate_cost(input_tokens, output_tokens)

        rows = _llm_call_rows(uid)
        assert rows[0]["cost_usd"] == pytest.approx(expected_cost, rel=1e-6)

    def test_each_event_gets_its_own_cost(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [
            _make_event(input_tokens=1000, output_tokens=500),
            _make_event(input_tokens=2000, output_tokens=1000),
        ]
        _run_stream(uid, sid, events)

        from api.chat import _estimate_cost
        rows = _llm_call_rows(uid)
        assert rows[0]["cost_usd"] == pytest.approx(_estimate_cost(1000, 500), rel=1e-6)
        assert rows[1]["cost_usd"] == pytest.approx(_estimate_cost(2000, 1000), rel=1e-6)

    def test_input_and_output_tokens_stored_correctly(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=123, output_tokens=456)]
        _run_stream(uid, sid, events)

        rows = _llm_call_rows(uid)
        assert rows[0]["input_tokens"] == 123
        assert rows[0]["output_tokens"] == 456


# ---------------------------------------------------------------------------
# Tests: stream resilience (recording failure does not break stream)
# ---------------------------------------------------------------------------

class TestStreamResilience:
    def test_record_failure_does_not_break_sse_stream(self):
        """Even if record_llm_call raises internally (before being caught by
        the service's own try/except), the SSE stream must complete and yield
        a session.cost event."""
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50)]

        from api.chat import _stream_agent_response
        import asyncio

        async def _collect():
            chunks = []
            async for chunk in _stream_agent_response(uid, sid, "hello"):
                chunks.append(chunk)
            return "".join(chunks)

        # Patch record_llm_call to raise (the service's except block would
        # normally catch sqlite3.Error; here we force an outer-level failure
        # to confirm the stream survives regardless).
        with patch("api.chat._runner") as mock_runner, \
             patch("api.chat._get_user_phase", return_value="orientation"), \
             patch.object(SqliteSessionService, "record_llm_call",
                         side_effect=RuntimeError("injected failure")):
            mock_runner.run_async.return_value = _mock_runner(events)
            raw = asyncio.get_event_loop().run_until_complete(_collect())

        # Stream must have completed with a session.cost event
        assert "session.cost" in raw

    def test_stream_produces_session_cost_event(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50, is_final=True)]
        raw = _run_stream(uid, sid, events)

        assert "session.cost" in raw
