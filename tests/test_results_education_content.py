"""API integration tests for the education learning-journal /api/results contract (BE-002).

Verifies:
  - GET /api/results/{user_id} surfaces education.content grouped by
    dimension -> category after present_education_content captures.
  - education.exists is True when ONLY education_content exists (no
    education_progress row yet) -- regression guard for the "hidden until
    first quiz" bug this feature fixes.
  - education.content is empty/absent when nothing has been captured.
  - GET /api/results/{user_id} requires authentication (401).
  - A user cannot read another user's education content via the endpoint.
"""

from agents.transmutation.tools import present_education_content


DIM = "Emotional Awareness & Regulation"
CAT = "what_this_means"


class TestEducationContentInResults:
    def test_content_populated_after_capture(self, authenticated_client):
        uid = authenticated_client.user_id
        present_education_content(uid, DIM, CAT, "Captured teaching text.")

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["education"]["exists"] is True
        assert data["education"]["content"][DIM][CAT] == "Captured teaching text."

    def test_exists_true_with_content_but_no_progress_row(self, authenticated_client):
        """Regression guard: content alone (no education_progress row) must
        still surface exists=True so the tab appears before the first quiz."""
        uid = authenticated_client.user_id
        present_education_content(uid, DIM, CAT, "Teaching text with no quiz yet.")

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["education"]["exists"] is True
        assert data["education"]["progress"] == {}
        assert data["education"]["content"][DIM][CAT] == "Teaching text with no quiz yet."

    def test_no_captures_returns_empty_or_absent_content(self, authenticated_client):
        uid = authenticated_client.user_id
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["education"]["exists"] is False
        assert data["education"]["content"] in (None, {})

    def test_multiple_categories_grouped_correctly(self, authenticated_client):
        uid = authenticated_client.user_id
        present_education_content(uid, DIM, "what_this_means", "Text A")
        present_education_content(uid, DIM, "your_score", "Text B")

        resp = authenticated_client.get(f"/api/results/{uid}")
        data = resp.json()
        content = data["education"]["content"]
        assert content[DIM]["what_this_means"] == "Text A"
        assert content[DIM]["your_score"] == "Text B"


class TestEducationContentAuth:
    def test_unauthenticated_returns_401(self, api_client):
        """api_client is a fresh TestClient with no session cookie."""
        resp = api_client.post("/auth/register", json={
            "name": "Unauth Target User",
            "email": "unauth-target-edu-content@example.com",
            "password": "testpass123",
        })
        assert resp.status_code == 200
        uid = resp.json()["user_id"]

        # A second, cookie-less client (no registration on it) is unauthenticated.
        from fastapi.testclient import TestClient
        from main import app
        anon_client = TestClient(app)
        resp = anon_client.get(f"/api/results/{uid}")
        assert resp.status_code == 401

    def test_cannot_read_another_users_content(self, api_client, authenticated_client):
        """A second authenticated user must not read the first user's results."""
        uid1 = authenticated_client.user_id
        present_education_content(uid1, DIM, CAT, "User 1's private teaching text.")

        resp2 = api_client.post("/auth/register", json={
            "name": "Second User",
            "email": "second-user-edu-content@example.com",
            "password": "testpass123",
        })
        assert resp2.status_code == 200
        cookies2 = resp2.cookies

        resp = api_client.get(f"/api/results/{uid1}", cookies=cookies2)
        assert resp.status_code == 403
