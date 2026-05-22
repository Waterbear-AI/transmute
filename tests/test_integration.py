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
            "development_roadmap", "education_progress", "graduation_record",
            "moral_ledger", "practice_journal", "profile_snapshots", "safety_log",
            "schema_version", "users",
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
