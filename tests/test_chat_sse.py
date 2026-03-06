"""Integration tests for chat pipeline, SSE streaming, session, and assessment APIs.

These tests validate the API infrastructure, request/response models,
authentication, and SSE event formatting. Agent LLM calls are not tested
here (they require API keys) — those are covered by E2E tests.
"""

import json
import os

import pytest


# --- SSE Parsing Utility ---

def parse_sse_events(raw_text: str) -> list[dict]:
    """Parse raw SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = []

    for line in raw_text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_event:
            data_str = "\n".join(current_data)
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = data_str
            events.append({"event": current_event, "data": data})
            current_event = None
            current_data = []

    return events


# --- Session API Tests ---

class TestSessionAPI:
    def test_create_session(self, authenticated_client):
        resp = authenticated_client.post("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["user_id"] == authenticated_client.user_id

    def test_list_sessions(self, authenticated_client):
        # Create a session first
        authenticated_client.post("/api/sessions")
        resp = authenticated_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert len(data["sessions"]) >= 1

    def test_session_requires_auth(self, api_client):
        resp = api_client.post("/api/sessions")
        assert resp.status_code == 401

    def test_create_session_archives_previous(self, authenticated_client):
        # Create two sessions
        resp1 = authenticated_client.post("/api/sessions")
        session1_id = resp1.json()["session_id"]
        resp2 = authenticated_client.post("/api/sessions")
        session2_id = resp2.json()["session_id"]

        # List should only show the latest (non-archived)
        list_resp = authenticated_client.get("/api/sessions")
        session_ids = [s["session_id"] for s in list_resp.json()["sessions"]]
        assert session2_id in session_ids
        assert session1_id not in session_ids


# --- Assessment API Tests ---

class TestAssessmentAPI:
    def test_get_questions(self, authenticated_client):
        resp = authenticated_client.get("/api/assessment/questions")
        assert resp.status_code == 200
        data = resp.json()
        assert "questions" in data
        assert "scenarios" in data
        assert len(data["questions"]) == 40
        assert len(data["scenarios"]) == 5

    def test_get_state_empty(self, authenticated_client):
        resp = authenticated_client.get("/api/assessment/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False

    def test_save_response_requires_assessment_phase(self, authenticated_client):
        """User starts in orientation phase, can't save responses yet."""
        resp = authenticated_client.post("/api/assessment/responses", json={
            "question_id": "ea_rec_01",
            "score": 4,
        })
        # User is in orientation phase, should fail
        assert resp.status_code == 409

    def test_save_response_in_assessment_phase(self, authenticated_client):
        """Advance user to assessment phase, then save a response."""
        import sqlite3
        # Manually advance the user to assessment phase for this test
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE users SET current_phase = 'assessment' WHERE id = ?",
            (authenticated_client.user_id,),
        )
        conn.commit()
        conn.close()

        resp = authenticated_client.post("/api/assessment/responses", json={
            "question_id": "ea_rec_01",
            "score": 4,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["question_id"] == "ea_rec_01"
        assert "progress" in data

    def test_save_na_response(self, authenticated_client):
        """N/A responses should be accepted."""
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE users SET current_phase = 'assessment' WHERE id = ?",
            (authenticated_client.user_id,),
        )
        conn.commit()
        conn.close()

        resp = authenticated_client.post("/api/assessment/responses", json={
            "question_id": "ea_rec_01",
            "score": None,
            "skipped_reason": "not_applicable",
        })
        assert resp.status_code == 200
        assert resp.json()["saved"] is True

    def test_batch_responses(self, authenticated_client):
        """Batch response saving."""
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE users SET current_phase = 'assessment' WHERE id = ?",
            (authenticated_client.user_id,),
        )
        conn.commit()
        conn.close()

        resp = authenticated_client.post("/api/assessment/responses/batch", json={
            "responses": [
                {"question_id": "ea_rec_01", "score": 4},
                {"question_id": "ea_rec_02", "score": 3},
                {"question_id": "ea_rec_03", "score": 2},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] == 3
        assert len(data["errors"]) == 0

    def test_invalid_question_id(self, authenticated_client):
        """Invalid question ID should return 404."""
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.execute(
            "UPDATE users SET current_phase = 'assessment' WHERE id = ?",
            (authenticated_client.user_id,),
        )
        conn.commit()
        conn.close()

        resp = authenticated_client.post("/api/assessment/responses", json={
            "question_id": "nonexistent_q",
            "score": 3,
        })
        assert resp.status_code == 404


# --- Results API Tests ---

class TestResultsAPI:
    def test_get_own_results(self, authenticated_client):
        resp = authenticated_client.get(f"/api/results/{authenticated_client.user_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == authenticated_client.user_id
        assert data["assessment"]["exists"] is False
        assert data["profiles"] == []

    def test_cannot_access_other_user_results(self, authenticated_client):
        resp = authenticated_client.get("/api/results/some-other-user-id")
        assert resp.status_code == 403


# --- Chat API Tests ---

class TestChatAPI:
    def test_chat_requires_auth(self, api_client):
        resp = api_client.post("/api/chat/fake-session", json={"message": "hello"})
        assert resp.status_code == 401

    def test_chat_invalid_session(self, authenticated_client):
        resp = authenticated_client.post(
            "/api/chat/nonexistent-session",
            json={"message": "hello"},
        )
        assert resp.status_code == 404


# --- SSE Parser Tests ---

class TestSSEParser:
    def test_parse_single_event(self):
        raw = "event: agent.message.complete\ndata: {\"text\": \"hello\"}\n\n"
        events = parse_sse_events(raw)
        assert len(events) == 1
        assert events[0]["event"] == "agent.message.complete"
        assert events[0]["data"]["text"] == "hello"

    def test_parse_multiple_events(self):
        raw = (
            "event: agent.message.chunk\ndata: {\"text\": \"hi\"}\n\n"
            "event: session.cost\ndata: {\"input_tokens\": 100}\n\n"
        )
        events = parse_sse_events(raw)
        assert len(events) == 2
        assert events[0]["event"] == "agent.message.chunk"
        assert events[1]["event"] == "session.cost"
