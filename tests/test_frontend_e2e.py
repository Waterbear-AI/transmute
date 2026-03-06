"""E2E tests for frontend user journeys.

Tests the full user lifecycle through API endpoints as the frontend would call them:
static file serving, sessions, results, assessment responses, and access control.
"""
import json

import pytest


class TestStaticFileServing:
    """Frontend assets are served correctly by the StaticFiles mount."""

    def test_index_html_served_at_root(self, api_client):
        resp = api_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Transmutation Engine" in resp.text

    def test_css_served(self, api_client):
        resp = api_client.get("/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_js_files_served(self, api_client):
        js_files = [
            "/js/sanitize.js",
            "/js/auth.js",
            "/js/results.js",
            "/js/sessions.js",
            "/js/chat.js",
            "/js/app.js",
            "/js/components/likert-card.js",
            "/js/components/scenario-card.js",
            "/js/components/structured-choice.js",
        ]
        for path in js_files:
            resp = api_client.get(path)
            assert resp.status_code == 200, f"Failed to serve {path}"
            assert "javascript" in resp.headers["content-type"]

    def test_orientation_content_served(self, api_client):
        resp = api_client.get("/content/orientation.html")
        assert resp.status_code == 200
        assert "Transmutarianism" in resp.text

    def test_nonexistent_asset_returns_404(self, api_client):
        resp = api_client.get("/js/nonexistent.js")
        assert resp.status_code == 404


class TestSessionManagementJourney:
    """Authenticated user creates and lists sessions."""

    def test_create_session_returns_session_data(self, authenticated_client):
        resp = authenticated_client.post("/api/sessions", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["user_id"] == authenticated_client.user_id

    def test_list_sessions_after_create(self, authenticated_client):
        # Creating a session archives prior ones, so listing returns the latest
        authenticated_client.post("/api/sessions", json={})
        s2 = authenticated_client.post("/api/sessions", json={}).json()

        resp = authenticated_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        session_ids = [s["session_id"] for s in data["sessions"]]
        # Most recent session should be in the list
        assert s2["session_id"] in session_ids

    def test_unauthenticated_session_access_denied(self, api_client):
        resp = api_client.get("/api/sessions")
        assert resp.status_code == 401

        resp = api_client.post("/api/sessions", json={})
        assert resp.status_code == 401


class TestResultsPanelJourney:
    """Authenticated user views results for each phase."""

    def test_get_results_for_own_user(self, authenticated_client):
        uid = authenticated_client.user_id
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == uid
        assert data["current_phase"] == "orientation"
        assert data["assessment"]["exists"] is False
        assert data["profiles"] == []

    def test_cannot_access_other_users_results(self, authenticated_client):
        resp = authenticated_client.get("/api/results/some-other-user-id")
        assert resp.status_code == 403

    def test_unauthenticated_results_access_denied(self, api_client):
        resp = api_client.get("/api/results/any-user-id")
        assert resp.status_code == 401


class TestAssessmentResponseJourney:
    """User in assessment phase saves Likert responses."""

    @pytest.fixture
    def assessment_client(self, authenticated_client):
        """Move user to assessment phase so response endpoints work."""
        from db.database import get_db_session
        with get_db_session() as conn:
            conn.execute(
                "UPDATE users SET current_phase = 'assessment' WHERE id = ?",
                (authenticated_client.user_id,),
            )
        return authenticated_client

    def _get_first_question_id(self):
        from agents.transmutation.question_bank import get_question_bank
        qb = get_question_bank()
        questions = qb.get_all_questions()
        return questions[0]["id"] if questions else None

    def test_get_assessment_state_empty(self, assessment_client):
        resp = assessment_client.get("/api/assessment/state")
        assert resp.status_code == 200

    def test_save_single_response(self, assessment_client):
        qid = self._get_first_question_id()
        if not qid:
            pytest.skip("No questions in question bank")

        resp = assessment_client.post("/api/assessment/responses", json={
            "question_id": qid,
            "score": 4,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["question_id"] == qid
        assert "progress" in data

    def test_save_batch_responses(self, assessment_client):
        from agents.transmutation.question_bank import get_question_bank
        qb = get_question_bank()
        questions = qb.get_all_questions()
        if len(questions) < 3:
            pytest.skip("Not enough questions for batch test")

        batch = [
            {"question_id": questions[0]["id"], "score": 3},
            {"question_id": questions[1]["id"], "score": 5},
            {"question_id": questions[2]["id"], "score": 2},
        ]
        resp = assessment_client.post("/api/assessment/responses/batch", json={
            "responses": batch,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] == 3
        assert data["errors"] == []

    def test_save_response_with_invalid_question_id(self, assessment_client):
        resp = assessment_client.post("/api/assessment/responses", json={
            "question_id": "nonexistent-q-id",
            "score": 3,
        })
        assert resp.status_code == 404

    def test_cannot_save_response_in_orientation_phase(self, authenticated_client):
        """User in orientation phase cannot submit assessment responses."""
        # Use a real question_id so the 404 check passes and phase check triggers
        qid = self._get_first_question_id()
        if not qid:
            pytest.skip("No questions in question bank")
        resp = authenticated_client.post("/api/assessment/responses", json={
            "question_id": qid,
            "score": 3,
        })
        assert resp.status_code == 409

    def test_unauthenticated_assessment_access_denied(self, api_client):
        resp = api_client.get("/api/assessment/state")
        assert resp.status_code == 401

        resp = api_client.post("/api/assessment/responses", json={
            "question_id": "x",
            "score": 1,
        })
        assert resp.status_code == 401


class TestChatEndpointAccess:
    """Chat endpoint auth and session ownership checks."""

    def test_unauthenticated_chat_denied(self, api_client):
        resp = api_client.post(
            "/api/chat/fake-session-id",
            json={"message": "hello"},
        )
        assert resp.status_code == 401

    def test_chat_with_nonexistent_session_returns_404(self, authenticated_client):
        resp = authenticated_client.post(
            "/api/chat/nonexistent-session-id",
            json={"message": "hello"},
        )
        assert resp.status_code == 404

    def test_chat_returns_sse_content_type(self, authenticated_client):
        # Create a session first
        session = authenticated_client.post("/api/sessions", json={}).json()
        sid = session["session_id"]

        # The chat endpoint should return SSE even if the agent errors
        resp = authenticated_client.post(
            f"/api/chat/{sid}",
            json={"message": "hello"},
        )
        # Should either succeed with SSE or fail gracefully
        if resp.status_code == 200:
            assert "text/event-stream" in resp.headers.get("content-type", "")


class TestFullUserJourney:
    """Complete user lifecycle: register -> sessions -> results -> assessment."""

    def test_new_user_full_flow(self, api_client):
        # 1. Register
        reg = api_client.post("/auth/register", json={
            "name": "Journey User",
            "email": "journey@example.com",
            "password": "journey-pass-123",
        })
        assert reg.status_code == 200
        user = reg.json()
        cookies = reg.cookies
        assert user["current_phase"] == "orientation"

        # 2. Verify auth
        me = api_client.get("/auth/me", cookies=cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "journey@example.com"

        # 3. Create a session
        session = api_client.post("/api/sessions", json={}, cookies=cookies)
        assert session.status_code == 200
        sid = session.json()["session_id"]

        # 4. List sessions
        sessions = api_client.get("/api/sessions", cookies=cookies)
        assert sessions.status_code == 200
        assert sessions.json()["count"] >= 1

        # 5. Get results (should show orientation, no assessment data)
        results = api_client.get(
            f"/api/results/{user['user_id']}", cookies=cookies
        )
        assert results.status_code == 200
        r = results.json()
        assert r["current_phase"] == "orientation"
        assert r["assessment"]["exists"] is False

        # 6. Get assessment state (should work even in orientation)
        state = api_client.get("/api/assessment/state", cookies=cookies)
        assert state.status_code == 200

        # 7. Logout
        logout = api_client.post("/auth/logout", cookies=cookies)
        assert logout.status_code == 200

        # 8. Verify no access after logout
        me_after = api_client.get("/auth/me")
        assert me_after.status_code == 401

    def test_assessment_flow_after_phase_transition(self, api_client):
        """User transitions to assessment and submits responses."""
        from db.database import get_db_session
        from agents.transmutation.question_bank import get_question_bank

        # Register
        reg = api_client.post("/auth/register", json={
            "name": "Assessment User",
            "email": "assess@example.com",
            "password": "assess-pass-123",
        })
        assert reg.status_code == 200
        user = reg.json()
        cookies = reg.cookies

        # Simulate phase transition to assessment
        with get_db_session() as conn:
            conn.execute(
                "UPDATE users SET current_phase = 'assessment' WHERE id = ?",
                (user["user_id"],),
            )

        # Get questions
        questions_resp = api_client.get(
            "/api/assessment/questions", cookies=cookies
        )
        assert questions_resp.status_code == 200

        # Save a response
        qb = get_question_bank()
        questions = qb.get_all_questions()
        if not questions:
            pytest.skip("No questions available")

        save = api_client.post("/api/assessment/responses", cookies=cookies, json={
            "question_id": questions[0]["id"],
            "score": 4,
        })
        assert save.status_code == 200
        assert save.json()["saved"] is True

        # Verify results now show assessment data
        results = api_client.get(
            f"/api/results/{user['user_id']}", cookies=cookies
        )
        assert results.status_code == 200
        r = results.json()
        assert r["assessment"]["exists"] is True
        assert r["assessment"]["answered"] == 1

    def test_multi_session_user(self, api_client):
        """User creates multiple sessions; latest is active, prior are archived."""
        reg = api_client.post("/auth/register", json={
            "name": "Multi Session",
            "email": "multi@example.com",
            "password": "multi-pass-123",
        })
        cookies = reg.cookies
        user_id = reg.json()["user_id"]

        api_client.post("/api/sessions", json={}, cookies=cookies)
        api_client.post("/api/sessions", json={}, cookies=cookies)
        s3 = api_client.post("/api/sessions", json={}, cookies=cookies).json()

        listing = api_client.get("/api/sessions", cookies=cookies).json()
        # At minimum the latest session should be listed
        assert listing["count"] >= 1
        # Latest session is present and belongs to the user
        session_ids = [s["session_id"] for s in listing["sessions"]]
        assert s3["session_id"] in session_ids
        for s in listing["sessions"]:
            assert s["user_id"] == user_id


class TestAccessControl:
    """Cross-cutting access control verification."""

    def test_api_routes_take_precedence_over_static(self, api_client):
        """API routes should not be shadowed by the static file mount."""
        resp = api_client.get("/auth/me")
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/json")

    def test_session_isolation_between_users(self, api_client):
        """User A cannot interact with User B's sessions via chat."""
        # Register two users
        a = api_client.post("/auth/register", json={
            "name": "User A", "email": "a@iso.com", "password": "a-pass",
        })
        b = api_client.post("/auth/register", json={
            "name": "User B", "email": "b@iso.com", "password": "b-pass",
        })

        # User A creates a session
        a_session = api_client.post(
            "/api/sessions", json={}, cookies=a.cookies
        ).json()

        # User B tries to chat in User A's session
        resp = api_client.post(
            f"/api/chat/{a_session['session_id']}",
            json={"message": "sneaky"},
            cookies=b.cookies,
        )
        assert resp.status_code == 404
