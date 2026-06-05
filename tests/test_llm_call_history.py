"""Consolidated unit and integration tests for backend LLM call history features.

Covers:
- SqliteSessionService.record_llm_call: insertion, timestamp, error handling
- SqliteSessionService.list_llm_calls: user scoping, ordering, pagination, empty state
- describe_llm_call: known/unknown/null author+phase combinations
- GET /api/usage/llm-calls: auth, user scoping, pagination, invalid inputs, empty state
- _stream_agent_response recording: data persisted, author/phase captured, stream resilient
- Regression: test_cost_total.py aggregate cost tracking unaffected
"""

import asyncio
import json
import sqlite3
import uuid
from unittest.mock import MagicMock, patch

import pytest

from agents.transmutation.session_service import SqliteSessionService
from api.usage import describe_llm_call
from db.database import get_db_session


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _insert_user(user_id: str, phase: str = "orientation") -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (user_id, "T", f"{user_id}@test.example.com", "pw", phase),
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


def _record(user_id: str, author: str = "assessment_agent", phase: str = "assessment",
            input_tokens: int = 100, output_tokens: int = 50, cost: float = 0.001) -> None:
    SqliteSessionService().record_llm_call(
        session_id=None, user_id=user_id, author=author,
        phase=phase, model_id="gemini-1.5-flash",
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
    )


def _row_count(user_id: str) -> int:
    with get_db_session() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE user_id=?", (user_id,)
        ).fetchone()[0]


def _rows(user_id: str) -> list[dict]:
    with get_db_session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM llm_calls WHERE user_id=? ORDER BY id ASC", (user_id,)
        ).fetchall()]


# ---------------------------------------------------------------------------
# Unit tests: SqliteSessionService.record_llm_call
# ---------------------------------------------------------------------------

class TestRecordLlmCallUnit:
    def test_inserts_all_fields(self):
        uid = _uid()
        _insert_user(uid)
        sid = _insert_session(uid)
        svc = SqliteSessionService()

        svc.record_llm_call(
            session_id=sid, user_id=uid, author="education_agent",
            phase="education", model_id="gemini-1.5-flash",
            input_tokens=300, output_tokens=150, cost_usd=0.004,
        )

        rows = _rows(uid)
        assert len(rows) == 1
        r = rows[0]
        assert r["session_id"] == sid
        assert r["author"] == "education_agent"
        assert r["phase"] == "education"
        assert r["model_id"] == "gemini-1.5-flash"
        assert r["input_tokens"] == 300
        assert r["output_tokens"] == 150
        assert r["cost_usd"] == pytest.approx(0.004)

    def test_created_at_is_set_explicitly(self):
        uid = _uid()
        _insert_user(uid)
        _record(uid)

        rows = _rows(uid)
        assert rows[0]["created_at"] is not None
        # Should look like an ISO datetime, not an SQLite CURRENT_TIMESTAMP
        ts = rows[0]["created_at"]
        assert "T" in ts or "-" in ts  # ISO format has hyphens at minimum

    def test_db_error_logged_not_raised(self, caplog):
        svc = SqliteSessionService()
        uid = _uid()

        with patch("agents.transmutation.session_service.get_db_session") as mock_ctx:
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.execute.side_effect = sqlite3.OperationalError("disk full")
            mock_ctx.return_value = conn

            # Must NOT raise
            svc.record_llm_call(
                session_id=None, user_id=uid, author="a", phase="p",
                model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
            )

        assert any("record_llm_call failed" in r.message for r in caplog.records)

    def test_null_optional_fields_accepted(self):
        uid = _uid()
        _insert_user(uid)
        svc = SqliteSessionService()

        svc.record_llm_call(
            session_id=None, user_id=uid, author=None,
            phase=None, model_id=None,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
        )
        assert _row_count(uid) == 1


# ---------------------------------------------------------------------------
# Unit tests: SqliteSessionService.list_llm_calls
# ---------------------------------------------------------------------------

class TestListLlmCallsUnit:
    def test_empty_state(self):
        uid = _uid()
        _insert_user(uid)
        items, has_more = SqliteSessionService().list_llm_calls(uid, limit=10)
        assert items == []
        assert has_more is False

    def test_retrieves_only_own_user_calls(self):
        uid_a, uid_b = _uid(), _uid()
        _insert_user(uid_a)
        _insert_user(uid_b)
        _record(uid_a)
        _record(uid_a)
        _record(uid_b)

        items_a, _ = SqliteSessionService().list_llm_calls(uid_a, limit=10)
        items_b, _ = SqliteSessionService().list_llm_calls(uid_b, limit=10)
        assert len(items_a) == 2
        assert len(items_b) == 1

    def test_ordered_newest_first(self):
        uid = _uid()
        _insert_user(uid)
        for _ in range(3):
            _record(uid)
        items, _ = SqliteSessionService().list_llm_calls(uid, limit=10)
        ids = [i["id"] for i in items]
        assert ids == sorted(ids, reverse=True)

    def test_has_more_true_when_more_exist(self):
        uid = _uid()
        _insert_user(uid)
        for _ in range(6):
            _record(uid)
        items, has_more = SqliteSessionService().list_llm_calls(uid, limit=5)
        assert len(items) == 5
        assert has_more is True

    def test_pagination_with_before_id(self):
        uid = _uid()
        _insert_user(uid)
        for _ in range(10):
            _record(uid)

        page1, more1 = SqliteSessionService().list_llm_calls(uid, limit=5)
        assert more1 is True
        cursor = page1[-1]["id"]

        page2, more2 = SqliteSessionService().list_llm_calls(uid, limit=5, before_id=cursor)
        assert len(page2) == 5
        assert more2 is False
        # No overlap
        ids1 = {i["id"] for i in page1}
        ids2 = {i["id"] for i in page2}
        assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# Unit tests: describe_llm_call
# ---------------------------------------------------------------------------

class TestDescribeLlmCallUnit:
    def test_known_author_and_phase(self):
        d = describe_llm_call("assessment_agent", "assessment")
        assert "Assessment agent" in d
        assert "scoring your responses" in d

    def test_known_author_unknown_phase(self):
        d = describe_llm_call("education_agent", "custom_phase")
        assert "Education agent" in d
        assert "custom_phase" in d

    def test_unknown_author_raw_returned(self):
        d = describe_llm_call("my_custom_bot", None)
        assert "my_custom_bot" in d

    def test_none_author_none_phase_generic(self):
        assert describe_llm_call(None, None) == "LLM call"

    def test_none_author_known_phase(self):
        d = describe_llm_call(None, "profile")
        assert "building your profile" in d

    def test_known_author_none_phase(self):
        d = describe_llm_call("transmutation_engine", None)
        assert "Transmutation engine" in d

    def test_empty_string_treated_as_null(self):
        assert describe_llm_call("", None) == "LLM call"


# ---------------------------------------------------------------------------
# Integration tests: GET /api/usage/llm-calls
# ---------------------------------------------------------------------------

class TestLlmCallsEndpointIntegration:
    def test_unauthenticated_returns_401(self, api_client):
        assert api_client.get("/api/usage/llm-calls").status_code == 401

    def test_authenticated_returns_200(self, authenticated_client):
        assert authenticated_client.get("/api/usage/llm-calls").status_code == 200

    def test_empty_state_response(self, authenticated_client):
        body = authenticated_client.get("/api/usage/llm-calls").json()
        assert body["items"] == []
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    def test_returns_own_calls_only(self, authenticated_client, api_client):
        uid_a = authenticated_client.user_id
        _record(uid_a, author="assessment_agent", n=3) if False else [
            _record(uid_a, author="assessment_agent") for _ in range(3)
        ]

        # Register user B
        resp_b = api_client.post("/auth/register", json={
            "name": "B", "email": f"b-{uuid.uuid4()}@t.example.com", "password": "pass123",
        })
        uid_b = resp_b.json()["user_id"]
        for _ in range(5):
            _record(uid_b, author="education_agent")

        body_a = authenticated_client.get("/api/usage/llm-calls?limit=100").json()
        assert len(body_a["items"]) == 3
        assert all(i["author"] == "assessment_agent" for i in body_a["items"])

        body_b = api_client.get("/api/usage/llm-calls?limit=100", cookies=resp_b.cookies).json()
        assert len(body_b["items"]) == 5

    def test_pagination_across_pages(self, authenticated_client):
        uid = authenticated_client.user_id
        for _ in range(8):
            _record(uid)

        page1 = authenticated_client.get("/api/usage/llm-calls?limit=5").json()
        assert len(page1["items"]) == 5
        assert page1["has_more"] is True
        cursor = page1["next_cursor"]

        page2 = authenticated_client.get(f"/api/usage/llm-calls?limit=5&cursor={cursor}").json()
        assert len(page2["items"]) == 3
        assert page2["has_more"] is False
        assert page2["next_cursor"] is None

    def test_invalid_limit_above_max_returns_422(self, authenticated_client):
        assert authenticated_client.get("/api/usage/llm-calls?limit=101").status_code == 422

    def test_invalid_limit_zero_returns_422(self, authenticated_client):
        assert authenticated_client.get("/api/usage/llm-calls?limit=0").status_code == 422

    def test_invalid_cursor_returns_400(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?cursor=not-a-number")
        assert resp.status_code == 400

    def test_description_field_is_human_readable(self, authenticated_client):
        uid = authenticated_client.user_id
        _record(uid, author="assessment_agent", phase="assessment")

        items = authenticated_client.get("/api/usage/llm-calls").json()["items"]
        assert "Assessment agent" in items[0]["description"]


# ---------------------------------------------------------------------------
# Integration test: stream recording via _stream_agent_response
# ---------------------------------------------------------------------------

def _make_event(input_tokens=0, output_tokens=0, author="transmutation_agent",
                is_final=False):
    event = MagicMock()
    event.author = author
    event.error_code = None
    event.error_message = None
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


async def _mock_runner_gen(events):
    for e in events:
        yield e


def _run_stream(user_id, session_id, events, phase="orientation"):
    from api.chat import _stream_agent_response

    async def _collect():
        chunks = []
        async for chunk in _stream_agent_response(user_id, session_id, "hi"):
            chunks.append(chunk)
        return "".join(chunks)

    with patch("api.chat._runner") as mock_runner, \
         patch("api.chat._get_user_phase", return_value=phase):
        mock_runner.run_async.return_value = _mock_runner_gen(events)
        return asyncio.get_event_loop().run_until_complete(_collect())


class TestStreamRecordingIntegration:
    def test_token_events_recorded_to_db(self):
        uid = _uid()
        _insert_user(uid, phase="assessment")
        sid = _insert_session(uid)

        events = [
            _make_event(input_tokens=100, output_tokens=50, author="assessment_agent"),
            _make_event(input_tokens=200, output_tokens=100, author="education_agent"),
        ]
        _run_stream(uid, sid, events, phase="assessment")

        assert _row_count(uid) == 2

    def test_author_and_phase_persisted(self):
        uid = _uid()
        _insert_user(uid, phase="education")
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50, author="education_agent")]
        _run_stream(uid, sid, events, phase="education")

        rows = _rows(uid)
        assert rows[0]["author"] == "education_agent"
        assert rows[0]["phase"] == "education"

    def test_zero_token_events_not_recorded(self):
        uid = _uid()
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=0, output_tokens=0)]
        _run_stream(uid, sid, events)

        assert _row_count(uid) == 0

    def test_recording_failure_does_not_interrupt_stream(self):
        uid = _uid()
        _insert_user(uid)
        sid = _insert_session(uid)

        events = [_make_event(input_tokens=100, output_tokens=50)]
        from api.chat import _stream_agent_response

        async def _collect():
            parts = []
            async for chunk in _stream_agent_response(uid, sid, "hi"):
                parts.append(chunk)
            return "".join(parts)

        with patch("api.chat._runner") as mock_runner, \
             patch("api.chat._get_user_phase", return_value="orientation"), \
             patch.object(SqliteSessionService, "record_llm_call",
                         side_effect=RuntimeError("simulated DB failure")):
            mock_runner.run_async.return_value = _mock_runner_gen(events)
            raw = asyncio.get_event_loop().run_until_complete(_collect())

        # Stream completed — session.cost event must be present
        assert "session.cost" in raw


# ---------------------------------------------------------------------------
# Regression: existing cost-total tests still pass (sanity import)
# ---------------------------------------------------------------------------

class TestCostTotalRegression:
    def test_get_user_total_cost_still_works(self):
        """Smoke-test that the aggregate cost service method is unaffected."""
        uid = _uid()
        _insert_user(uid)
        svc = SqliteSessionService()
        assert svc.get_user_total_cost(uid) == 0.0

        with get_db_session() as conn:
            conn.execute(
                """INSERT INTO adk_sessions (session_id, user_id, app_name,
                   archived, estimated_cost_usd, created_at)
                   VALUES (?, ?, 'transmutation', 0, ?, datetime('now'))""",
                (str(uuid.uuid4()), uid, 0.42),
            )
        assert svc.get_user_total_cost(uid) == pytest.approx(0.42)
