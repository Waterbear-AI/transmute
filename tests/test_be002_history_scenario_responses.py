"""Integration tests for BE-002: /api/sessions/{id}/history returns scenario_responses.

Covers:
- HistoryResponse includes scenario_responses alongside answered_responses
- A session with both Likert and scenario responses returns both, correctly
- A session with only Likert responses (no scenarios saved yet) returns an
  empty scenario_responses dict, not null/missing
- A session with no assessment_state row at all still returns 200 with an
  empty scenario_responses dict
- Existing answered_responses behavior is unaffected
- Ownership/auth are unaffected (still 404 for another user's session, 401
  unauthenticated) -- these guards are pre-existing and not touched by this
  change, verified here so a regression would be caught
"""

import json
import uuid

from fastapi.testclient import TestClient

from db.database import get_db_session
from main import app


def _register_and_create_session(api_client, email: str | None = None):
    email = email or f"{uuid.uuid4()}@test.example.com"
    resp = api_client.post("/auth/register", json={
        "name": "History Scenario User",
        "email": email,
        "password": "testpass123",
    })
    assert resp.status_code == 200
    cookies = resp.cookies
    user_id = resp.json()["user_id"]
    sess = api_client.post("/api/sessions", cookies=cookies)
    assert sess.status_code == 200
    session_id = sess.json()["session_id"]
    return user_id, session_id, cookies


def _write_assessment_state(user_id: str, responses: dict, scenario_responses: dict) -> None:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO assessment_state
               (id, user_id, responses, scenario_responses, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (sid, user_id, json.dumps(responses), json.dumps(scenario_responses)),
        )


class TestHistoryScenarioResponses:
    def test_history_returns_scenario_responses_with_likert_and_scenario_data(self, api_client):
        user_id, session_id, cookies = _register_and_create_session(api_client)

        likert_responses = {"tc_filt_01": {"score": 4, "dimension": "Transmutation Capacity"}}
        scenario_responses = {
            "sc_belong_01": {
                "choice": "a",
                "quadrant_weight": {"x": 0.2, "y": 0.1},
                "answered_at": "2026-07-05T00:00:00",
            }
        }
        _write_assessment_state(user_id, likert_responses, scenario_responses)

        resp = api_client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert data["answered_responses"] == likert_responses
        assert data["scenario_responses"] == scenario_responses

    def test_history_returns_empty_scenario_responses_when_only_likert_saved(self, api_client):
        user_id, session_id, cookies = _register_and_create_session(api_client)

        likert_responses = {"tc_filt_01": {"score": 3}}
        _write_assessment_state(user_id, likert_responses, {})

        resp = api_client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["answered_responses"] == likert_responses
        assert data["scenario_responses"] == {}

    def test_history_returns_empty_scenario_responses_for_fresh_session_no_state(self, api_client):
        _, session_id, cookies = _register_and_create_session(api_client)

        resp = api_client.get(f"/api/sessions/{session_id}/history", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["scenario_responses"], dict)
        assert data["scenario_responses"] == {}
        assert data["answered_responses"] == {}

    def test_history_returns_404_for_other_users_session(self, api_client):
        _, session_id, _ = _register_and_create_session(api_client)

        resp2 = api_client.post("/auth/register", json={
            "name": "Other User",
            "email": f"{uuid.uuid4()}@test.example.com",
            "password": "testpass123",
        })
        assert resp2.status_code == 200
        cookies2 = resp2.cookies

        resp = api_client.get(f"/api/sessions/{session_id}/history", cookies=cookies2)
        assert resp.status_code == 404

    def test_history_returns_401_when_unauthenticated(self, api_client):
        _, session_id, _ = _register_and_create_session(api_client)
        # Fresh client with no session cookie -- api_client would still carry
        # the cookie set by the register call above.
        fresh_client = TestClient(app)
        resp = fresh_client.get(f"/api/sessions/{session_id}/history")
        assert resp.status_code == 401
