"""API integration tests for BE-005: AssessmentProgressResponse tier fields.

Verifies:
- GET /api/assessment/state surfaces assessment_tier/flagged_dimensions/
  deep_dive_dimensions/early_result for a fresh (no assessment_state row) user
- The same fields reflect real persisted state after Tier-1 completion
  (assessment_tier='awareness_core', early_result populated)
- GET /api/assessment/state returns 401 for an unauthenticated request
- Two different authenticated users each see only their own state -- this
  endpoint takes no user_id path/query parameter (it is always scoped to the
  caller's session cookie via get_current_user_id), so there is no
  cross-user access vector to attempt in the first place; the meaningful
  security property to verify is that per-user isolation actually holds
  end-to-end through the real DB and response model.
"""

import json
import uuid

from db.database import get_db_session
from agents.transmutation.tools import evaluate_transmute_core_complete
from agents.transmutation.question_bank import get_question_bank


def _tc_ids() -> list[str]:
    qb = get_question_bank()
    return [q["id"] for q in qb.get_questions_by_tier("transmute_core")]


def _scenario_ids() -> list[str]:
    qb = get_question_bank()
    return [s["id"] for s in qb.get_all_scenarios()]


def _create_assessment_state(user_id: str, responses: dict, scenario_responses: dict) -> None:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO assessment_state
               (id, user_id, responses, scenario_responses, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (sid, user_id, json.dumps(responses), json.dumps(scenario_responses)),
        )


class TestAssessmentProgressResponseFreshState:
    """A user with no assessment_state row yet."""

    def test_returns_200_with_default_tier_fields(self, authenticated_client):
        resp = authenticated_client.get("/api/assessment/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False
        assert data["assessment_tier"] == "transmute_core"
        assert data["flagged_dimensions"] is None
        assert data["deep_dive_dimensions"] is None
        assert data["early_result"] is None


class TestAssessmentProgressResponseAfterTier1Completion:
    """A user who has just completed Tier 1 via evaluate_transmute_core_complete."""

    def test_reflects_awareness_core_tier_and_early_result(self, authenticated_client):
        uid = authenticated_client.user_id
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()[:6]}
        _create_assessment_state(uid, tc_responses, scenario_responses)

        eval_result = evaluate_transmute_core_complete(uid)
        assert eval_result["complete"] is True

        resp = authenticated_client.get("/api/assessment/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        assert data["assessment_tier"] == "awareness_core"
        assert data["early_result"] is not None
        assert data["early_result"]["event_type"] == "assessment.transmute_result"
        for key in ("archetype", "x", "y", "confidence", "confidence_reason", "computed_at"):
            assert key in data["early_result"]


class TestAssessmentProgressResponseSecurity:
    """Auth enforcement and per-user isolation."""

    def test_unauthenticated_request_returns_401(self, api_client):
        """api_client is a fresh TestClient with no session cookie."""
        resp = api_client.get("/api/assessment/state")
        assert resp.status_code == 401

    def test_user_id_is_a_fastapi_dependency_not_a_client_supplied_value(self):
        """GET /api/assessment/state's only "user_id" parameter resolves via
        Depends(get_current_user_id) (the session cookie) -- it is not a
        path or query parameter a client could set to another user's id.
        This is the structural reason a 404 cross-user test doesn't apply to
        this specific route (unlike /api/results/{user_id}, which DOES take
        a path parameter and enforces ownership at the service layer)."""
        import inspect

        from fastapi.params import Depends as DependsMarker

        from api.assessment import get_state

        sig = inspect.signature(get_state)
        user_id_param = sig.parameters["user_id"]
        assert isinstance(user_id_param.default, DependsMarker)

    def test_two_authenticated_users_see_only_their_own_state(self, api_client):
        """Register two independent users and confirm each one's
        /api/assessment/state reflects ONLY their own assessment_state row --
        the real security property this endpoint must uphold, verified
        end-to-end through the actual DB and response model."""
        resp_a = api_client.post("/auth/register", json={
            "name": "User A", "email": f"{uuid.uuid4()}@test.example.com", "password": "testpass123",
        })
        assert resp_a.status_code == 200
        cookies_a = resp_a.cookies
        uid_a = resp_a.json()["user_id"]

        resp_b = api_client.post("/auth/register", json={
            "name": "User B", "email": f"{uuid.uuid4()}@test.example.com", "password": "testpass123",
        })
        assert resp_b.status_code == 200
        cookies_b = resp_b.cookies

        # Only user A completes Tier 1.
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()[:6]}
        _create_assessment_state(uid_a, tc_responses, scenario_responses)
        evaluate_transmute_core_complete(uid_a)

        state_a = api_client.get("/api/assessment/state", cookies=cookies_a).json()
        state_b = api_client.get("/api/assessment/state", cookies=cookies_b).json()

        assert state_a["assessment_tier"] == "awareness_core"
        assert state_a["early_result"] is not None

        # User B's state must be completely unaffected by user A's progress.
        assert state_b["exists"] is False
        assert state_b["assessment_tier"] == "transmute_core"
        assert state_b["early_result"] is None
