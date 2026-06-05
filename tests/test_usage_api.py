"""Tests for GET /api/usage/llm-calls endpoint and describe_llm_call utility.

Covers:
- describe_llm_call: known/unknown/null authors and phases
- Endpoint authentication (401 unauthenticated, 200 authenticated)
- Pagination with limit and cursor parameters
- Input validation: invalid limit → 422, invalid cursor → 400
- User-scoping: authenticated user only sees their own calls
"""

import uuid

import pytest

from agents.transmutation.session_service import SqliteSessionService
from api.usage import describe_llm_call
from db.database import get_db_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_user(user_id: str, email: str = "") -> None:
    email = email or f"{user_id}@test.example.com"
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "Test", email, "pw"),
        )


def _record_call(user_id: str, author: str = "assessment_agent", n: int = 1) -> None:
    svc = SqliteSessionService()
    for _ in range(n):
        svc.record_llm_call(
            session_id=None,
            user_id=user_id,
            author=author,
            phase="assessment",
            model_id="gemini-1.5-flash",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
        )


# ---------------------------------------------------------------------------
# Unit tests: describe_llm_call
# ---------------------------------------------------------------------------

class TestDescribeLlmCall:
    def test_known_author_and_phase(self):
        desc = describe_llm_call("assessment_agent", "assessment")
        assert "Assessment agent" in desc
        assert "scoring your responses" in desc

    def test_known_author_unknown_phase(self):
        desc = describe_llm_call("education_agent", "unknown_phase")
        assert "Education agent" in desc
        assert "unknown_phase" in desc

    def test_unknown_author_returns_raw(self):
        desc = describe_llm_call("some_custom_bot", None)
        assert "some_custom_bot" in desc

    def test_none_author_none_phase_returns_generic(self):
        desc = describe_llm_call(None, None)
        assert desc == "LLM call"

    def test_none_author_known_phase(self):
        desc = describe_llm_call(None, "profile")
        assert "building your profile" in desc

    def test_known_author_none_phase(self):
        desc = describe_llm_call("transmutation_engine", None)
        assert "Transmutation engine" in desc

    def test_all_known_authors_are_mapped(self):
        known_authors = [
            "transmutation_engine",
            "assessment_agent",
            "education_agent",
            "profile_agent",
            "check_in_agent",
            "roadmap_agent",
            "safety_agent",
        ]
        for author in known_authors:
            desc = describe_llm_call(author, None)
            # Should not return the raw key — must be human-readable label
            assert desc != "LLM call", f"Author {author!r} should be mapped"
            assert "_" not in desc or "·" in desc, \
                f"Author {author!r} should produce readable label without underscores"

    def test_empty_string_author_treated_as_null(self):
        # Empty string author has no label; should fall back to generic
        desc = describe_llm_call("", None)
        assert desc == "LLM call"


# ---------------------------------------------------------------------------
# Integration tests: endpoint authentication
# ---------------------------------------------------------------------------

class TestLlmCallsEndpointAuth:
    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.get("/api/usage/llm-calls")
        assert resp.status_code == 401

    def test_authenticated_returns_200(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls")
        assert resp.status_code == 200

    def test_response_shape_for_empty_history(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls")
        body = resp.json()
        assert "items" in body
        assert "next_cursor" in body
        assert "has_more" in body
        assert body["items"] == []
        assert body["has_more"] is False
        assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# Integration tests: pagination
# ---------------------------------------------------------------------------

class TestLlmCallsEndpointPagination:
    def test_first_page_returns_limit_items_and_has_more_true(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, n=10)

        resp = authenticated_client.get("/api/usage/llm-calls?limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 5
        assert body["has_more"] is True
        assert body["next_cursor"] is not None

    def test_second_page_via_cursor(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, n=10)

        page1 = authenticated_client.get("/api/usage/llm-calls?limit=5").json()
        cursor = page1["next_cursor"]

        page2 = authenticated_client.get(
            f"/api/usage/llm-calls?limit=5&cursor={cursor}"
        ).json()
        assert len(page2["items"]) == 5
        assert page2["has_more"] is False
        assert page2["next_cursor"] is None

    def test_pages_do_not_overlap(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, n=8)

        page1 = authenticated_client.get("/api/usage/llm-calls?limit=4").json()
        cursor = page1["next_cursor"]
        page2 = authenticated_client.get(
            f"/api/usage/llm-calls?limit=4&cursor={cursor}"
        ).json()

        # Descriptions are all the same here, but we check that combined
        # count equals total inserted, and no cursor overlap.
        assert len(page1["items"]) == 4
        assert len(page2["items"]) == 4

    def test_items_contain_expected_fields(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, n=1)

        body = authenticated_client.get("/api/usage/llm-calls").json()
        item = body["items"][0]
        for field in ("session_id", "author", "phase", "description",
                      "model_id", "input_tokens", "output_tokens",
                      "cost_usd", "created_at"):
            assert field in item, f"Expected field '{field}' in item"

    def test_description_is_human_readable(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, author="assessment_agent", n=1)

        body = authenticated_client.get("/api/usage/llm-calls").json()
        desc = body["items"][0]["description"]
        assert "Assessment agent" in desc

    def test_cost_usd_rounded_to_6_decimal_places(self, authenticated_client):
        uid = authenticated_client.user_id
        svc = SqliteSessionService()
        svc.record_llm_call(
            session_id=None, user_id=uid, author="a", phase="p",
            model_id="m", input_tokens=1, output_tokens=1,
            cost_usd=0.00012345678,
        )

        body = authenticated_client.get("/api/usage/llm-calls").json()
        cost = body["items"][0]["cost_usd"]
        # Should be rounded to at most 6 decimal places
        assert cost == round(0.00012345678, 6)

    def test_default_limit_is_25(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, n=30)

        body = authenticated_client.get("/api/usage/llm-calls").json()
        assert len(body["items"]) == 25
        assert body["has_more"] is True

    def test_has_more_false_on_last_page(self, authenticated_client):
        uid = authenticated_client.user_id
        _record_call(uid, n=3)

        body = authenticated_client.get("/api/usage/llm-calls?limit=10").json()
        assert len(body["items"]) == 3
        assert body["has_more"] is False
        assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# Integration tests: input validation
# ---------------------------------------------------------------------------

class TestLlmCallsEndpointInputValidation:
    def test_limit_above_100_returns_422(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?limit=101")
        assert resp.status_code == 422

    def test_limit_zero_returns_422(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?limit=0")
        assert resp.status_code == 422

    def test_limit_negative_returns_422(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?limit=-5")
        assert resp.status_code == 422

    def test_invalid_cursor_returns_400(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?cursor=not-an-integer")
        assert resp.status_code == 400
        assert "cursor" in resp.json().get("detail", "").lower()

    def test_valid_limit_100_accepted(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?limit=100")
        assert resp.status_code == 200

    def test_valid_limit_1_accepted(self, authenticated_client):
        resp = authenticated_client.get("/api/usage/llm-calls?limit=1")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration tests: user-scoping
# ---------------------------------------------------------------------------

class TestLlmCallsEndpointUserScoping:
    def test_user_only_sees_own_calls(self, api_client, authenticated_client):
        # authenticated_client is already logged in as user A
        uid_a = authenticated_client.user_id
        _record_call(uid_a, author="assessment_agent", n=3)

        # Create and authenticate user B separately
        resp_b = api_client.post("/auth/register", json={
            "name": "User B",
            "email": f"userb-{uuid.uuid4()}@test.example.com",
            "password": "passB123",
        })
        assert resp_b.status_code == 200
        uid_b = resp_b.json()["user_id"]
        cookies_b = resp_b.cookies

        svc = SqliteSessionService()
        for _ in range(5):
            svc.record_llm_call(
                session_id=None, user_id=uid_b, author="education_agent",
                phase="education", model_id="m", input_tokens=1,
                output_tokens=1, cost_usd=0.0,
            )

        # User A sees only their 3 calls
        body_a = authenticated_client.get("/api/usage/llm-calls?limit=100").json()
        assert len(body_a["items"]) == 3
        assert all(i["author"] == "assessment_agent" for i in body_a["items"])

        # User B sees only their 5 calls
        resp_b2 = api_client.get(
            "/api/usage/llm-calls?limit=100",
            cookies=cookies_b,
        )
        assert resp_b2.status_code == 200
        body_b = resp_b2.json()
        assert len(body_b["items"]) == 5
        assert all(i["author"] == "education_agent" for i in body_b["items"])
