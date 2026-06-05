"""Tests for the per-user lifetime accumulated LLM cost.

Covers SqliteSessionService.get_user_total_cost (single SUM over all of a
user's adk_sessions, archived included) and the /api/sessions response field.
"""

import uuid

from agents.transmutation.session_service import SqliteSessionService
from db.database import get_db_session


def _insert_session(user_id: str, cost: float, archived: bool = False) -> None:
    """Insert one adk_sessions row with a known estimated_cost_usd."""
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO adk_sessions
               (session_id, user_id, app_name, archived, estimated_cost_usd, created_at)
               VALUES (?, ?, 'transmutation', ?, ?, datetime('now'))""",
            (str(uuid.uuid4()), user_id, 1 if archived else 0, cost),
        )


def _insert_user(user_id: str, email: str) -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "T", email, "x"),
        )


class TestGetUserTotalCost:
    def test_sums_all_sessions_including_archived(self):
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        _insert_session(uid, 0.10)
        _insert_session(uid, 0.25)
        _insert_session(uid, 0.40, archived=True)  # archived still counts

        total = SqliteSessionService().get_user_total_cost(uid)
        assert round(total, 6) == 0.75

    def test_no_sessions_returns_zero(self):
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        assert SqliteSessionService().get_user_total_cost(uid) == 0.0

    def test_scoped_to_user(self):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        _insert_user(a, f"{a}@test.example.com")
        _insert_user(b, f"{b}@test.example.com")
        _insert_session(a, 1.00)
        _insert_session(b, 5.00)

        assert SqliteSessionService().get_user_total_cost(a) == 1.0
        assert SqliteSessionService().get_user_total_cost(b) == 5.0


class TestSessionsEndpointTotal:
    def test_list_sessions_returns_user_total_cost(self, authenticated_client):
        uid = authenticated_client.user_id
        _insert_session(uid, 0.15)
        _insert_session(uid, 0.35, archived=True)

        resp = authenticated_client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert "user_total_cost_usd" in body
        assert round(body["user_total_cost_usd"], 6) == 0.50

    def test_list_sessions_total_zero_for_new_user(self, authenticated_client):
        resp = authenticated_client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json()["user_total_cost_usd"] == 0.0

    def test_list_sessions_requires_auth(self, api_client):
        # Unauthenticated → 401 (regression guard on the existing endpoint).
        resp = api_client.get("/api/sessions")
        assert resp.status_code == 401
