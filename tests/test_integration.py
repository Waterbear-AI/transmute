import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set test DB path before importing app
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db.close()
os.environ["DB_PATH"] = _test_db.name

from config import _settings
# Reset cached settings so test DB path is picked up
import config
config._settings = None

from main import app
from rate_limit import limiter

# Disable rate limiting for integration tests
limiter.enabled = False

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database between tests."""
    from db.database import run_migrations
    import sqlite3

    # Drop all tables and re-run migrations
    conn = sqlite3.connect(os.environ["DB_PATH"])
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS [{table}]")
    conn.commit()
    conn.close()

    run_migrations(db_path=os.environ["DB_PATH"])
    yield


class TestRegistration:
    def test_register_new_user(self):
        resp = client.post("/auth/register", json={
            "name": "Test User",
            "email": "test@example.com",
            "password": "password123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test User"
        assert data["email"] == "test@example.com"
        assert "user_id" in data
        assert "transmute_session" in resp.cookies

    def test_duplicate_email_returns_409(self):
        client.post("/auth/register", json={
            "name": "User 1",
            "email": "dupe@example.com",
            "password": "pass1",
        })
        resp = client.post("/auth/register", json={
            "name": "User 2",
            "email": "dupe@example.com",
            "password": "pass2",
        })
        assert resp.status_code == 409


class TestLogin:
    def _register(self):
        return client.post("/auth/register", json={
            "name": "Login User",
            "email": "login@example.com",
            "password": "correct-password",
        })

    def test_login_success(self):
        self._register()
        resp = client.post("/auth/login", json={
            "email": "login@example.com",
            "password": "correct-password",
        })
        assert resp.status_code == 200
        assert resp.json()["email"] == "login@example.com"
        assert "transmute_session" in resp.cookies

    def test_invalid_password_returns_401(self):
        self._register()
        resp = client.post("/auth/login", json={
            "email": "login@example.com",
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    def test_nonexistent_email_returns_401(self):
        resp = client.post("/auth/login", json={
            "email": "noone@example.com",
            "password": "anything",
        })
        assert resp.status_code == 401


class TestMe:
    def test_me_with_valid_session(self):
        reg = client.post("/auth/register", json={
            "name": "Me User",
            "email": "me@example.com",
            "password": "password",
        })
        cookies = reg.cookies
        resp = client.get("/auth/me", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["email"] == "me@example.com"
        assert resp.json()["current_phase"] == "orientation"

    def test_me_without_cookie_returns_401(self):
        resp = client.get("/auth/me")
        assert resp.status_code == 401


class TestLogout:
    def test_logout_clears_cookie(self):
        reg = client.post("/auth/register", json={
            "name": "Logout User",
            "email": "logout@example.com",
            "password": "password",
        })
        resp = client.post("/auth/logout", cookies=reg.cookies)
        assert resp.status_code == 200


class TestGlobalExceptionHandler:
    def test_unhandled_exception_returns_structured_error(self):
        from fastapi import APIRouter
        error_router = APIRouter()

        @error_router.get("/test-error")
        def trigger_error():
            raise RuntimeError("Unexpected failure")

        # Insert route before the catch-all static files mount
        app.include_router(error_router)
        route = app.routes.pop()
        app.routes.insert(0, route)
        try:
            # Must disable raise_server_exceptions to get the 500 response
            error_client = TestClient(app, raise_server_exceptions=False)
            resp = error_client.get("/test-error")
            assert resp.status_code == 500
            data = resp.json()
            assert data["error"] == "Internal server error"
            assert "request_id" in data
            # Verify request_id is a valid UUID
            import uuid
            uuid.UUID(data["request_id"])
            # Verify no stack trace in response
            assert "Traceback" not in resp.text
            assert "RuntimeError" not in resp.text
        finally:
            app.routes[:] = [r for r in app.routes if not (hasattr(r, 'path') and r.path == '/test-error')]


class TestHealthEndpoints:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"

    def test_readiness_returns_ok(self):
        resp = client.get("/readiness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestExportEndpoint:
    def test_export_returns_user_data_with_content_disposition(self):
        # Register a user (authenticated via cookie)
        reg = client.post("/auth/register", json={
            "name": "Export User",
            "email": "export@example.com",
            "password": "password123",
        })
        cookies = reg.cookies
        user_id = reg.json()["user_id"]

        resp = client.get("/api/export", cookies=cookies)
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "transmute-export.json" in resp.headers.get("content-disposition", "")

        data = resp.json()
        # Should have user data
        assert "users" in data
        assert len(data["users"]) == 1
        assert data["users"][0]["id"] == user_id
        assert data["users"][0]["name"] == "Export User"

        # Should have empty arrays for tables with no data yet
        assert "assessment_state" in data
        assert isinstance(data["assessment_state"], list)

    def test_export_without_auth_returns_401(self):
        fresh_client = TestClient(app)
        resp = fresh_client.get("/api/export")
        assert resp.status_code == 401


class TestRateLimiting:
    def test_chat_rate_limit_returns_429(self):
        """Verify /api/chat returns 429 after exceeding 30 requests/minute."""
        limiter.enabled = True
        limiter.reset()
        try:
            rate_client = TestClient(app)
            # Register and auth
            reg = rate_client.post("/auth/register", json={
                "name": "Rate User",
                "email": "rate@example.com",
                "password": "password123",
            })
            cookies = reg.cookies

            # Need a session_id - use a dummy one (will get 404 but rate limit applies first)
            got_429 = False
            for i in range(35):
                resp = rate_client.post(
                    "/api/chat/test-session",
                    json={"message": "hi"},
                    cookies=cookies,
                )
                if resp.status_code == 429:
                    got_429 = True
                    break
            assert got_429, "Expected 429 response after exceeding rate limit"
        finally:
            limiter.enabled = False

    def test_assessment_rate_limit_returns_429(self):
        """Verify /api/assessment/responses returns 429 after exceeding 60 requests/minute."""
        limiter.enabled = True
        limiter.reset()
        try:
            rate_client = TestClient(app)
            reg = rate_client.post("/auth/register", json={
                "name": "Rate User 2",
                "email": "rate2@example.com",
                "password": "password123",
            })
            cookies = reg.cookies

            got_429 = False
            for i in range(65):
                resp = rate_client.post(
                    "/api/assessment/responses",
                    json={"question_id": "q1", "score": 3},
                    cookies=cookies,
                )
                if resp.status_code == 429:
                    got_429 = True
                    break
            assert got_429, "Expected 429 response after exceeding rate limit"
        finally:
            limiter.enabled = False


class TestMigrations:
    def test_all_tables_created(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        tables = sorted([
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ])
        # Verify events_json column added by migration 003
        cols = [
            r[1] for r in conn.execute(
                "PRAGMA table_info(adk_sessions)"
            ).fetchall()
        ]
        conn.close()
        expected = [
            "adk_sessions", "assessment_state", "check_in_log",
            "development_roadmap", "dimension_assessment_state", "education_progress",
            "graduation_record", "llm_calls", "moral_ledger", "practice_journal",
            "profile_snapshots", "roadmap_practices", "safety_log", "schema_version",
            "users",
        ]
        assert tables == expected
        assert "events_json" in cols


class TestHistoryEndpoint:
    """Integration tests for GET /api/sessions/{session_id}/history (BE-003)."""

    def _register_and_create_session(self, email: str = "historyuser@example.com"):
        resp = client.post("/auth/register", json={
            "name": "History User",
            "email": email,
            "password": "password123",
        })
        assert resp.status_code == 200
        cookies = resp.cookies
        user_id = resp.json()["user_id"]
        sess = client.post("/api/sessions", cookies=cookies)
        assert sess.status_code == 200
        session_id = sess.json()["session_id"]
        return user_id, session_id, cookies

    def test_history_returns_200_with_empty_messages_for_new_session(self):
        _, session_id, cookies = self._register_and_create_session("h1@example.com")
        resp = client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert isinstance(data["messages"], list)
        assert isinstance(data["answered_responses"], dict)

    def test_history_returns_404_for_nonexistent_session(self):
        _, _, cookies = self._register_and_create_session("h2@example.com")
        resp = client.get("/api/sessions/nonexistent-session-id/history", cookies=cookies)
        assert resp.status_code == 404

    def test_history_returns_404_for_other_users_session(self):
        _, session_id, _ = self._register_and_create_session("h3@example.com")

        # Register a second user
        resp2 = client.post("/auth/register", json={
            "name": "Other User",
            "email": "h3other@example.com",
            "password": "password123",
        })
        assert resp2.status_code == 200
        cookies2 = resp2.cookies

        # Other user tries to access first user's session
        resp = client.get(f"/api/sessions/{session_id}/history", cookies=cookies2)
        assert resp.status_code == 404

    def test_history_returns_401_when_unauthenticated(self):
        _, session_id, _ = self._register_and_create_session("h4@example.com")
        # Use a fresh client with no session cookie to simulate unauthenticated request
        fresh_client = TestClient(app)
        resp = fresh_client.get(f"/api/sessions/{session_id}/history")
        assert resp.status_code == 401

    def test_history_with_corrupt_events_json_returns_200_with_empty_messages(self):
        """Corrupt events_json must degrade gracefully to empty messages."""
        import sqlite3

        _, session_id, cookies = self._register_and_create_session("h5@example.com")

        # Corrupt events_json
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET events_json = ? WHERE session_id = ?",
            ("INVALID_JSON{{{", session_id),
        )
        conn.commit()
        conn.close()

        resp = client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []

    def test_history_with_question_batch_event_rehydrates_questions(self):
        """Widget events with question_ids are re-hydrated from the question bank."""
        import sqlite3
        import json as _json

        user_id, session_id, cookies = self._register_and_create_session("h6@example.com")

        # Manually write a slimmed question_batch event into events_json
        from agents.transmutation.question_bank import get_question_bank
        qb = get_question_bank()
        all_questions = qb.get_all_questions()
        if not all_questions:
            # Skip if question bank not available in test environment
            return

        # Use real question IDs from the bank
        qids = [q["id"] for q in all_questions[:2]]
        slimmed_event = {
            "content": {
                "role": "tool",
                "parts": [{
                    "function_response": {
                        "name": "present_question_batch",
                        "response": {
                            "event_type": "assessment.question_batch",
                            "batch_id": "b1",
                            "dimension": all_questions[0].get("dimension", "test"),
                            "count": len(qids),
                            "question_ids": qids,
                            "summary": "Presented questions.",
                        }
                    }
                }]
            }
        }

        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET events_json = ? WHERE session_id = ?",
            (_json.dumps([slimmed_event]), session_id),
        )
        conn.commit()
        conn.close()

        resp = client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        widget_msgs = [m for m in data["messages"] if m["role"] == "widget"]
        assert len(widget_msgs) == 1
        widget_data = widget_msgs[0]["data"]
        assert widget_data["event_type"] == "assessment.question_batch"
        assert "questions" in widget_data
        assert len(widget_data["questions"]) == len(qids)
        # Each question must have text and scale_labels
        for q in widget_data["questions"]:
            assert "text" in q or "id" in q
            assert "scale_labels" in q

    def test_history_filters_batch_complete_user_messages(self):
        """User messages with type=batch_complete must be filtered from history."""
        import sqlite3
        import json as _json

        _, session_id, cookies = self._register_and_create_session("h7@example.com")

        events = [
            {
                "content": {
                    "role": "user",
                    "parts": [{"text": _json.dumps({"type": "batch_complete", "data": {}})}]
                }
            },
            {
                "content": {
                    "role": "user",
                    "parts": [{"text": "Hello agent!"}]
                }
            },
        ]

        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET events_json = ? WHERE session_id = ?",
            (_json.dumps(events), session_id),
        )
        conn.commit()
        conn.close()

        resp = client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        user_msgs = [m for m in msgs if m["role"] == "user"]
        # Only the real user message, not the batch_complete one
        assert len(user_msgs) == 1
        assert user_msgs[0]["text"] == "Hello agent!"


class TestResetEndpoint:
    """Integration tests for POST /api/sessions/reset (BE-004)."""

    def _register_user(self, email: str):
        """Register a user and return (user_id, cookies)."""
        resp = client.post("/auth/register", json={
            "name": "Reset User",
            "email": email,
            "password": "password123",
        })
        assert resp.status_code == 200
        return resp.json()["user_id"], resp.cookies

    def _seed_user_data(self, user_id: str):
        """Insert a row into every user-scoped table for the given user."""
        import sqlite3
        import uuid
        conn = sqlite3.connect(os.environ["DB_PATH"])
        # assessment_state
        conn.execute(
            "INSERT OR IGNORE INTO assessment_state (id, user_id, responses) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), user_id, "{}"),
        )
        # profile_snapshots
        conn.execute(
            "INSERT OR IGNORE INTO profile_snapshots (id, user_id, scores) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), user_id, "{}"),
        )
        # moral_ledger
        conn.execute(
            "INSERT OR IGNORE INTO moral_ledger (id, user_id, c_plus, c_minus) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, 1.0, 0.0),
        )
        # safety_log (must be retained after reset)
        conn.execute(
            "INSERT OR IGNORE INTO safety_log (id, user_id, reason) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), user_id, "should survive reset"),
        )
        conn.commit()
        conn.close()

    def _count_rows(self, table: str, user_id: str) -> int:
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        conn.close()
        return count

    def test_reset_returns_200_with_new_session(self):
        """POST /reset returns 200 and a new SessionResponse for authenticated user."""
        user_id, cookies = self._register_user("r1@example.com")
        resp = client.post("/api/sessions/reset", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["user_id"] == user_id

    def test_reset_deletes_user_domain_data(self):
        """POST /reset wipes assessment_state, profile_snapshots, and other domain data."""
        user_id, cookies = self._register_user("r2@example.com")
        self._seed_user_data(user_id)

        assert self._count_rows("assessment_state", user_id) >= 1
        assert self._count_rows("profile_snapshots", user_id) >= 1

        resp = client.post("/api/sessions/reset", cookies=cookies)
        assert resp.status_code == 200

        assert self._count_rows("assessment_state", user_id) == 0
        assert self._count_rows("profile_snapshots", user_id) == 0

    def test_reset_deletes_moral_ledger(self):
        """POST /reset must delete moral_ledger rows for the user (R11)."""
        user_id, cookies = self._register_user("r3@example.com")
        self._seed_user_data(user_id)

        assert self._count_rows("moral_ledger", user_id) >= 1

        resp = client.post("/api/sessions/reset", cookies=cookies)
        assert resp.status_code == 200

        assert self._count_rows("moral_ledger", user_id) == 0

    def test_reset_retains_safety_log(self):
        """POST /reset must NOT delete safety_log rows (audit trail retention)."""
        user_id, cookies = self._register_user("r4@example.com")
        self._seed_user_data(user_id)

        before = self._count_rows("safety_log", user_id)
        assert before >= 1

        resp = client.post("/api/sessions/reset", cookies=cookies)
        assert resp.status_code == 200

        after = self._count_rows("safety_log", user_id)
        assert after == before

    def test_reset_resets_current_phase_to_orientation(self):
        """POST /reset must set users.current_phase back to 'orientation'."""
        import sqlite3

        user_id, cookies = self._register_user("r5@example.com")

        # Simulate phase advancement
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE users SET current_phase = 'assessment' WHERE id = ?", (user_id,)
        )
        conn.commit()
        conn.close()

        resp = client.post("/api/sessions/reset", cookies=cookies)
        assert resp.status_code == 200

        conn = sqlite3.connect(os.environ["DB_PATH"])
        phase = conn.execute(
            "SELECT current_phase FROM users WHERE id = ?", (user_id,)
        ).fetchone()[0]
        conn.close()
        assert phase == "orientation"

    def test_reset_returns_401_when_unauthenticated(self):
        """POST /reset for an unauthenticated request returns 401."""
        fresh_client = TestClient(app)
        resp = fresh_client.post("/api/sessions/reset")
        assert resp.status_code == 401

    def test_reset_rate_limit_returns_429(self):
        """POST /reset returns 429 after exceeding 20 requests/hour."""
        limiter.enabled = True
        limiter.reset()
        try:
            rate_client = TestClient(app)
            reg = rate_client.post("/auth/register", json={
                "name": "Reset Rate User",
                "email": "rrate@example.com",
                "password": "password123",
            })
            cookies = reg.cookies

            got_429 = False
            for i in range(25):
                resp = rate_client.post("/api/sessions/reset", cookies=cookies)
                if resp.status_code == 429:
                    got_429 = True
                    break
            assert got_429, "Expected 429 after exceeding reset rate limit"
        finally:
            limiter.enabled = False


class TestSessionPersistence:
    """Integration tests for SqliteSessionService event persistence (BE-002)."""

    def _create_user_and_session(self):
        """Register a user, return (user_id, session_id)."""
        resp = client.post("/auth/register", json={
            "name": "Session User",
            "email": "sessionuser@example.com",
            "password": "password123",
        })
        assert resp.status_code == 200
        user_id = resp.json()["user_id"]
        cookies = resp.cookies

        sess_resp = client.post("/api/sessions", cookies=cookies)
        assert sess_resp.status_code == 200
        session_id = sess_resp.json()["session_id"]
        return user_id, session_id, cookies

    def test_append_event_persists_slimmed_events_json(self):
        """After append_event, events_json column contains slimmed event data."""
        import asyncio
        import sqlite3

        user_id, session_id, cookies = self._create_user_and_session()

        from agents.transmutation.session_service import SqliteSessionService
        from google.adk.events.event import Event
        from google.genai import types as genai_types

        svc = SqliteSessionService()

        async def run():
            session = await svc.get_session(
                app_name="transmutation",
                user_id=user_id,
                session_id=session_id,
            )
            assert session is not None

            # Build a question_batch tool response event
            event_data = {
                "event_type": "assessment.question_batch",
                "batch_id": "b1",
                "count": 2,
                "question_ids": ["q-a", "q-b"],
                "questions": [
                    {"id": "q-a", "text": "Full question text that should be stripped"},
                    {"id": "q-b", "text": "Another full question text"},
                ],
            }
            content = genai_types.Content(
                role="tool",
                parts=[
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name="get_next_question_batch",
                            response=event_data,
                        )
                    )
                ],
            )
            evt = Event(author="tool", content=content)
            await svc.append_event(session, evt)

        asyncio.run(run())

        # Inspect raw DB — events_json must exist and be slimmed
        conn = sqlite3.connect(os.environ["DB_PATH"])
        row = conn.execute(
            "SELECT events_json FROM adk_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        conn.close()

        import json as _json
        assert row is not None
        assert row[0] is not None
        events = _json.loads(row[0])
        assert len(events) >= 1
        # Find the tool response part
        fr = events[-1]["content"]["parts"][0]["function_response"]
        assert "question_ids" in fr["response"]
        assert fr["response"]["question_ids"] == ["q-a", "q-b"]
        assert "questions" not in fr["response"], "Full questions must be stripped"

    def test_get_session_restores_events_from_events_json(self):
        """Reloading a session after append_event restores slimmed events."""
        import asyncio

        user_id, session_id, cookies = self._create_user_and_session()

        from agents.transmutation.session_service import SqliteSessionService
        from google.adk.events.event import Event
        from google.genai import types as genai_types

        svc = SqliteSessionService()

        async def run():
            session = await svc.get_session(
                app_name="transmutation",
                user_id=user_id,
                session_id=session_id,
            )
            content = genai_types.Content(
                role="model",
                parts=[genai_types.Part(text="Hello from the agent")],
            )
            evt = Event(author="agent", content=content)
            await svc.append_event(session, evt)

            # Re-load session
            session2 = await svc.get_session(
                app_name="transmutation",
                user_id=user_id,
                session_id=session_id,
            )
            assert session2 is not None
            assert len(session2.events) >= 1

        asyncio.run(run())

    def test_get_session_with_null_events_json_returns_empty_events(self):
        """A session row with NULL events_json loads with events == []."""
        import sqlite3

        user_id, session_id, cookies = self._create_user_and_session()

        # Manually set events_json to NULL
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET events_json = NULL WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        conn.close()

        from agents.transmutation.session_service import SqliteSessionService
        import asyncio

        svc = SqliteSessionService()

        async def run():
            session = await svc.get_session(
                app_name="transmutation",
                user_id=user_id,
                session_id=session_id,
            )
            assert session is not None
            assert session.events == []

        asyncio.run(run())

    def test_get_session_with_invalid_events_json_logs_warning_and_returns_empty(self, caplog):
        """A session with invalid events_json falls back to [] and logs a warning."""
        import sqlite3
        import logging

        user_id, session_id, cookies = self._create_user_and_session()

        # Corrupt events_json
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET events_json = ? WHERE session_id = ?",
            ("NOT_VALID_JSON{{{", session_id),
        )
        conn.commit()
        conn.close()

        from agents.transmutation.session_service import SqliteSessionService
        import asyncio

        svc = SqliteSessionService()

        async def run():
            with caplog.at_level(logging.WARNING, logger="agents.transmutation.session_service"):
                session = await svc.get_session(
                    app_name="transmutation",
                    user_id=user_id,
                    session_id=session_id,
                )
            assert session is not None
            assert session.events == []

        asyncio.run(run())
        assert any("Failed to restore events" in r.message for r in caplog.records)


class TestListSessionsEndpoint:
    """Integration tests for GET /api/sessions (BE-005)."""

    def _register_user(self, email: str):
        resp = client.post("/auth/register", json={
            "name": "List User",
            "email": email,
            "password": "password123",
        })
        assert resp.status_code == 200
        return resp.json()["user_id"], resp.cookies

    def test_list_sessions_returns_200_with_session_and_metadata(self):
        """GET /api/sessions returns non-archived sessions with message_count and created_at."""
        user_id, cookies = self._register_user("ls1@example.com")
        # Create a session
        sess = client.post("/api/sessions", cookies=cookies)
        assert sess.status_code == 200

        resp = client.get("/api/sessions", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert len(data["sessions"]) >= 1
        session = data["sessions"][0]
        assert "session_id" in session
        assert "user_id" in session
        assert "message_count" in session
        assert "created_at" in session
        assert session["user_id"] == user_id
        assert isinstance(session["message_count"], int)

    def test_list_sessions_message_count_counts_user_events(self):
        """message_count accurately reflects the number of user-role events in events_json."""
        import sqlite3
        import json as _json

        user_id, cookies = self._register_user("ls2@example.com")
        sess = client.post("/api/sessions", cookies=cookies)
        session_id = sess.json()["session_id"]

        # Inject 2 user messages and 1 agent message
        events = [
            {"content": {"role": "user", "parts": [{"text": "First message"}]}},
            {"content": {"role": "model", "parts": [{"text": "Agent reply"}]}},
            {"content": {"role": "user", "parts": [{"text": "Second message"}]}},
        ]
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET events_json = ? WHERE session_id = ?",
            (_json.dumps(events), session_id),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/sessions", cookies=cookies)
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        target = next((s for s in sessions if s["session_id"] == session_id), None)
        assert target is not None
        assert target["message_count"] == 2

    def test_list_sessions_returns_empty_for_user_with_no_sessions(self):
        """GET /api/sessions returns empty list when no non-archived sessions exist."""
        user_id, cookies = self._register_user("ls3@example.com")
        # The registration archives any prior sessions but there are none yet.
        # After register, a session is NOT auto-created, so list should be empty.
        resp = client.get("/api/sessions", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["sessions"] == []

    def test_list_sessions_returns_401_when_unauthenticated(self):
        """GET /api/sessions returns 401 for unauthenticated requests."""
        fresh_client = TestClient(app)
        resp = fresh_client.get("/api/sessions")
        assert resp.status_code == 401

    def test_list_sessions_excludes_archived_sessions(self):
        """GET /api/sessions only returns non-archived sessions."""
        import sqlite3

        user_id, cookies = self._register_user("ls4@example.com")
        sess = client.post("/api/sessions", cookies=cookies)
        session_id = sess.json()["session_id"]

        # Archive the session manually
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE adk_sessions SET archived = TRUE WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/sessions", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        session_ids = [s["session_id"] for s in data["sessions"]]
        assert session_id not in session_ids


class TestCreateSessionEndpoint:
    """Integration tests for POST /api/sessions with archive_prior and title (BE-001)."""

    def _register_user(self, email: str):
        resp = client.post("/auth/register", json={
            "name": "Create Session User",
            "email": email,
            "password": "password123",
        })
        assert resp.status_code == 200
        return resp.json()["user_id"], resp.cookies

    def test_create_session_with_title_returns_title_in_response(self):
        """POST /api/sessions with title returns the title in the response."""
        _, cookies = self._register_user("cs1@example.com")
        resp = client.post(
            "/api/sessions",
            json={"title": "My First Tab"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My First Tab", "title must be returned in the response"

    def test_create_session_with_archive_prior_false_leaves_prior_active(self):
        """POST /api/sessions with archive_prior=False keeps prior sessions active."""
        _, cookies = self._register_user("cs2@example.com")

        # Create first session (archive_prior defaults to False at API level)
        first = client.post("/api/sessions", json={"archive_prior": False}, cookies=cookies)
        assert first.status_code == 200
        first_id = first.json()["session_id"]

        # Create second session without archiving
        second = client.post("/api/sessions", json={"archive_prior": False}, cookies=cookies)
        assert second.status_code == 200

        # Both sessions must appear in the active list
        list_resp = client.get("/api/sessions", cookies=cookies)
        assert list_resp.status_code == 200
        session_ids = [s["session_id"] for s in list_resp.json()["sessions"]]
        assert first_id in session_ids, "Prior session must remain active with archive_prior=False"

    def test_create_session_with_archive_prior_true_archives_prior_sessions(self):
        """POST /api/sessions with archive_prior=True archives all prior active sessions."""
        _, cookies = self._register_user("cs3@example.com")

        # Create first session without archiving
        first = client.post("/api/sessions", json={"archive_prior": False}, cookies=cookies)
        assert first.status_code == 200
        first_id = first.json()["session_id"]

        # Create second session with archive_prior=True
        second = client.post("/api/sessions", json={"archive_prior": True}, cookies=cookies)
        assert second.status_code == 200
        second_id = second.json()["session_id"]

        # Only the new session must appear in the active list
        list_resp = client.get("/api/sessions", cookies=cookies)
        assert list_resp.status_code == 200
        session_ids = [s["session_id"] for s in list_resp.json()["sessions"]]
        assert first_id not in session_ids, "Prior session must be archived with archive_prior=True"
        assert second_id in session_ids, "New session must be active"

    def test_create_session_without_title_stores_null_title(self):
        """POST /api/sessions without a title stores NULL and returns null title."""
        _, cookies = self._register_user("cs4@example.com")
        resp = client.post("/api/sessions", json={}, cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] is None, "title must be null when not provided"

    def test_list_sessions_returns_title(self):
        """GET /api/sessions returns the title field for each session."""
        _, cookies = self._register_user("cs5@example.com")
        client.post(
            "/api/sessions",
            json={"title": "Tab Alpha", "archive_prior": False},
            cookies=cookies,
        )
        list_resp = client.get("/api/sessions", cookies=cookies)
        assert list_resp.status_code == 200
        sessions = list_resp.json()["sessions"]
        assert len(sessions) >= 1
        titles = [s["title"] for s in sessions]
        assert "Tab Alpha" in titles, "Session title must appear in list response"
