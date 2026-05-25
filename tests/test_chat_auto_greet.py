"""Contract tests for the auto-greet endpoint and history-render seed filter.

These tests validate the API contract for POST /api/chat/{session_id}/start
(auth, ownership, route wiring) and the seed-prefix filter in
api/sessions.py::get_session_history. Like test_chat_sse.py, they do NOT
exercise the actual LLM — that requires Bedrock credentials and is covered
by manual smoke tests.
"""

import json
import os
import uuid

from api.chat import AGENT_SESSION_START_SEED
from db.database import get_db_session


class TestAutoGreetEndpoint:
    def test_start_requires_auth(self, api_client):
        resp = api_client.post("/api/chat/some-session-id/start")
        assert resp.status_code == 401

    def test_start_invalid_session(self, authenticated_client):
        resp = authenticated_client.post("/api/chat/nonexistent-session/start")
        assert resp.status_code == 404

    def test_start_other_users_session(self, authenticated_client, api_client):
        """A session belonging to another user must 404 (not 403) — matches
        the existing /api/chat/{sid} convention of hiding cross-user existence."""
        # Create a session for the authenticated user (so we know it exists)
        create_resp = authenticated_client.post("/api/sessions")
        assert create_resp.status_code == 200
        sid = create_resp.json()["session_id"]

        # Now register a SECOND user with a fresh client and try to start that session
        other_resp = api_client.post(
            "/auth/register",
            json={
                "name": "Other User",
                "email": "other@example.com",
                "password": "otherpass123",
            },
        )
        assert other_resp.status_code == 200
        other_cookies = other_resp.cookies

        resp = api_client.post(
            f"/api/chat/{sid}/start",
            cookies=other_cookies,
        )
        assert resp.status_code == 404

    def test_start_route_does_not_collide_with_chat_route(self, authenticated_client):
        """Sanity check: POST /api/chat/<sid>/start and POST /api/chat/<sid>
        are distinct routes. The /start path must not be eaten by the
        path-parameter route."""
        # 404 (because the session doesn't exist) is the correct response —
        # NOT 405 (method not allowed) or 422 (unprocessable entity, which
        # would happen if it routed to the body-requiring /api/chat/{sid}).
        resp = authenticated_client.post("/api/chat/nonexistent-session/start")
        assert resp.status_code == 404


class TestAutoGreetSeedConstant:
    def test_seed_constant_has_recognizable_prefix(self):
        """The history filter in api/sessions.py matches by prefix, so the
        constant MUST start with the literal '[session_start]' marker."""
        assert AGENT_SESSION_START_SEED.startswith("[session_start]")

    def test_seed_constant_is_non_empty(self):
        assert len(AGENT_SESSION_START_SEED) > len("[session_start]")


class TestHistorySeedFilter:
    """The seed string is persisted in adk_sessions.events_json by ADK when
    the auto-greet runs. The /api/sessions/{id}/history filter MUST hide it
    so resuming users don't see a bracketed system note in their chat panel.
    """

    def _seed_session_with_events(self, user_id: str, session_id: str, events: list) -> None:
        """Insert an adk_sessions row directly. ADK's session_service is async
        and would require an event loop; this is simpler for a unit test."""
        with get_db_session() as conn:
            conn.execute(
                """INSERT INTO adk_sessions
                   (session_id, user_id, app_name, archived, created_at, events_json)
                   VALUES (?, ?, 'transmutation', 0, datetime('now'), ?)""",
                (session_id, user_id, json.dumps(events)),
            )

    def test_history_filters_seed_message(self, authenticated_client):
        sid = str(uuid.uuid4())
        self._seed_session_with_events(
            user_id=authenticated_client.user_id,
            session_id=sid,
            events=[
                {
                    "content": {
                        "role": "user",
                        "parts": [{"text": AGENT_SESSION_START_SEED}],
                    }
                },
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Hello! Welcome to the Transmutation Engine."}],
                    }
                },
            ],
        )

        resp = authenticated_client.get(f"/api/sessions/{sid}/history")
        assert resp.status_code == 200
        messages = resp.json()["messages"]

        # The seed (role=user) is filtered out; the agent's greeting (role=model) is kept.
        texts = [m["text"] for m in messages if m.get("text")]
        assert AGENT_SESSION_START_SEED not in texts
        assert not any(t.startswith("[session_start]") for t in texts)
        assert "Hello! Welcome to the Transmutation Engine." in texts

    def test_history_filters_any_session_start_prefix(self, authenticated_client):
        """Filter matches by prefix — robust to future seed-string evolution."""
        sid = str(uuid.uuid4())
        self._seed_session_with_events(
            user_id=authenticated_client.user_id,
            session_id=sid,
            events=[
                {
                    "content": {
                        "role": "user",
                        "parts": [{"text": "[session_start] some other variant"}],
                    }
                },
                {
                    "content": {
                        "role": "user",
                        "parts": [{"text": "real user message"}],
                    }
                },
            ],
        )

        resp = authenticated_client.get(f"/api/sessions/{sid}/history")
        assert resp.status_code == 200
        texts = [m["text"] for m in resp.json()["messages"] if m.get("text")]

        assert not any(t.startswith("[session_start]") for t in texts)
        assert "real user message" in texts

    def test_history_keeps_agent_messages_starting_with_brackets(self, authenticated_client):
        """The filter is gated on role=='user'. An agent message that
        coincidentally starts with '[session_start]' (unlikely) is NOT
        filtered."""
        sid = str(uuid.uuid4())
        self._seed_session_with_events(
            user_id=authenticated_client.user_id,
            session_id=sid,
            events=[
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "[session_start] agent echoed it"}],
                    }
                },
            ],
        )

        resp = authenticated_client.get(f"/api/sessions/{sid}/history")
        assert resp.status_code == 200
        texts = [m["text"] for m in resp.json()["messages"] if m.get("text")]
        assert "[session_start] agent echoed it" in texts
