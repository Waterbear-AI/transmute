"""Tests for RESPONSE_SAVE_PHASES constant and updated phase guards.

Covers:
- RESPONSE_SAVE_PHASES constant contents
- save_assessment_response accepting assessment, reassessment, check_in
- save_assessment_response rejecting other phases
- _validate_assessment_phase accepting assessment, reassessment, check_in
- _validate_assessment_phase raising 403 for invalid phases
- save_scenario_response remains assessment-only
- Integration: API endpoint accepts reassessment and check_in phases
"""

import json
import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException

from db.database import get_db_session
from agents.transmutation.tools import (
    RESPONSE_SAVE_PHASES,
    save_assessment_response,
    save_scenario_response,
)
from api.assessment import _validate_assessment_phase


# ── Helpers ────────────────────────────────────────────────────────────────────


def _create_user(phase: str) -> str:
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Test User", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _get_first_question_id() -> str:
    from agents.transmutation.question_bank import get_question_bank
    qb = get_question_bank()
    questions = qb.get_all_questions()
    return questions[0]["id"]


def _get_first_scenario() -> dict:
    from agents.transmutation.question_bank import get_question_bank
    qb = get_question_bank()
    scenarios = qb.get_all_scenarios()
    return scenarios[0]


# ── RESPONSE_SAVE_PHASES constant ─────────────────────────────────────────────


class TestResponseSavePhasesConstant:
    def test_contains_assessment(self):
        assert "assessment" in RESPONSE_SAVE_PHASES

    def test_contains_reassessment(self):
        assert "reassessment" in RESPONSE_SAVE_PHASES

    def test_contains_check_in(self):
        assert "check_in" in RESPONSE_SAVE_PHASES

    def test_does_not_contain_development(self):
        assert "development" not in RESPONSE_SAVE_PHASES

    def test_does_not_contain_graduation(self):
        assert "graduation" not in RESPONSE_SAVE_PHASES

    def test_does_not_contain_graduated(self):
        assert "graduated" not in RESPONSE_SAVE_PHASES

    def test_does_not_contain_orientation(self):
        assert "orientation" not in RESPONSE_SAVE_PHASES

    def test_does_not_contain_education(self):
        assert "education" not in RESPONSE_SAVE_PHASES

    def test_exactly_three_phases(self):
        assert len(RESPONSE_SAVE_PHASES) == 3


# ── save_assessment_response phase guard ──────────────────────────────────────


class TestSaveAssessmentResponsePhaseGuard:
    """Unit tests for save_assessment_response phase validation."""

    def test_accepts_assessment_phase(self):
        uid = _create_user("assessment")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=4)
        assert result.get("saved") is True
        assert "error" not in result

    def test_accepts_reassessment_phase(self):
        uid = _create_user("reassessment")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=3)
        assert result.get("saved") is True, f"Expected saved=True, got: {result}"
        assert "error" not in result

    def test_accepts_check_in_phase(self):
        uid = _create_user("check_in")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=5)
        assert result.get("saved") is True, f"Expected saved=True, got: {result}"
        assert "error" not in result

    def test_rejects_development_phase(self):
        uid = _create_user("development")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=3)
        assert "error" in result
        assert "development" in result["error"]

    def test_rejects_orientation_phase(self):
        uid = _create_user("orientation")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=2)
        assert "error" in result
        assert "orientation" in result["error"]

    def test_rejects_education_phase(self):
        uid = _create_user("education")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=2)
        assert "error" in result
        assert "education" in result["error"]

    def test_rejects_graduation_phase(self):
        uid = _create_user("graduation")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=1)
        assert "error" in result

    def test_rejects_graduated_phase(self):
        uid = _create_user("graduated")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=1)
        assert "error" in result

    def test_returns_error_for_unknown_user(self):
        qid = _get_first_question_id()
        result = save_assessment_response("nonexistent-user-id", qid, score=3)
        assert "error" in result

    def test_na_response_accepted_in_reassessment(self):
        uid = _create_user("reassessment")
        qid = _get_first_question_id()
        result = save_assessment_response(uid, qid, score=None, skipped_reason="not_applicable")
        assert result.get("saved") is True, f"Expected saved=True, got: {result}"


# ── save_scenario_response remains assessment-only ────────────────────────────


class TestSaveScenarioResponseRemainsAssessmentOnly:
    """Verify scenario responses are still locked to assessment phase only."""

    def _get_scenario_choice(self) -> tuple[str, str]:
        scenario = _get_first_scenario()
        choice_key = scenario["choices"][0]["key"]
        return scenario["id"], choice_key

    def test_accepts_assessment_phase(self):
        uid = _create_user("assessment")
        sid, choice = self._get_scenario_choice()
        result = save_scenario_response(uid, sid, choice)
        assert result.get("saved") is True

    def test_rejects_reassessment_phase(self):
        uid = _create_user("reassessment")
        sid, choice = self._get_scenario_choice()
        result = save_scenario_response(uid, sid, choice)
        assert "error" in result
        assert "reassessment" in result["error"]

    def test_rejects_check_in_phase(self):
        uid = _create_user("check_in")
        sid, choice = self._get_scenario_choice()
        result = save_scenario_response(uid, sid, choice)
        assert "error" in result
        assert "check_in" in result["error"]

    def test_rejects_development_phase(self):
        uid = _create_user("development")
        sid, choice = self._get_scenario_choice()
        result = save_scenario_response(uid, sid, choice)
        assert "error" in result


# ── _validate_assessment_phase ─────────────────────────────────────────────────


class TestValidateAssessmentPhase:
    """Unit tests for _validate_assessment_phase in api/assessment.py."""

    def _conn_for_user(self, phase: str):
        """Create a user and return (conn, user_id) using a live DB session."""
        uid = _create_user(phase)
        return uid

    def test_no_exception_for_assessment(self):
        uid = _create_user("assessment")
        with get_db_session() as conn:
            # Must not raise
            _validate_assessment_phase(conn, uid)

    def test_no_exception_for_reassessment(self):
        uid = _create_user("reassessment")
        with get_db_session() as conn:
            _validate_assessment_phase(conn, uid)

    def test_no_exception_for_check_in(self):
        uid = _create_user("check_in")
        with get_db_session() as conn:
            _validate_assessment_phase(conn, uid)

    def test_raises_403_for_development(self):
        uid = _create_user("development")
        with get_db_session() as conn:
            with pytest.raises(HTTPException) as exc_info:
                _validate_assessment_phase(conn, uid)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Forbidden"

    def test_raises_403_for_orientation(self):
        uid = _create_user("orientation")
        with get_db_session() as conn:
            with pytest.raises(HTTPException) as exc_info:
                _validate_assessment_phase(conn, uid)
        assert exc_info.value.status_code == 403

    def test_raises_403_for_education(self):
        uid = _create_user("education")
        with get_db_session() as conn:
            with pytest.raises(HTTPException) as exc_info:
                _validate_assessment_phase(conn, uid)
        assert exc_info.value.status_code == 403

    def test_raises_403_for_graduation(self):
        uid = _create_user("graduation")
        with get_db_session() as conn:
            with pytest.raises(HTTPException) as exc_info:
                _validate_assessment_phase(conn, uid)
        assert exc_info.value.status_code == 403

    def test_raises_403_for_graduated(self):
        uid = _create_user("graduated")
        with get_db_session() as conn:
            with pytest.raises(HTTPException) as exc_info:
                _validate_assessment_phase(conn, uid)
        assert exc_info.value.status_code == 403

    def test_raises_404_for_unknown_user(self):
        with get_db_session() as conn:
            with pytest.raises(HTTPException) as exc_info:
                _validate_assessment_phase(conn, "nonexistent-user")
        assert exc_info.value.status_code == 404


# ── Integration: API endpoint accepts new phases ───────────────────────────────


class TestAssessmentAPIIntegration:
    """Integration tests for the POST /api/assessment/responses endpoint."""

    def _register_and_auth(self, api_client) -> tuple:
        """Register a user and return (cookies, user_id)."""
        resp = api_client.post("/auth/register", json={
            "name": "Phase Test User",
            "email": f"{uuid.uuid4()}@test.com",
            "password": "testpass123",
        })
        assert resp.status_code == 200
        return resp.cookies, resp.json()["user_id"]

    def _set_user_phase(self, user_id: str, phase: str):
        with get_db_session() as conn:
            conn.execute(
                "UPDATE users SET current_phase = ? WHERE id = ?",
                (phase, user_id),
            )

    def test_api_accepts_response_in_assessment_phase(self, api_client):
        cookies, user_id = self._register_and_auth(api_client)
        self._set_user_phase(user_id, "assessment")
        qid = _get_first_question_id()
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": qid, "score": 4},
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["saved"] is True

    def test_api_accepts_response_in_reassessment_phase(self, api_client):
        cookies, user_id = self._register_and_auth(api_client)
        self._set_user_phase(user_id, "reassessment")
        qid = _get_first_question_id()
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": qid, "score": 3},
            cookies=cookies,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["saved"] is True

    def test_api_accepts_response_in_check_in_phase(self, api_client):
        cookies, user_id = self._register_and_auth(api_client)
        self._set_user_phase(user_id, "check_in")
        qid = _get_first_question_id()
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": qid, "score": 5},
            cookies=cookies,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["saved"] is True

    def test_api_returns_403_in_development_phase(self, api_client):
        cookies, user_id = self._register_and_auth(api_client)
        self._set_user_phase(user_id, "development")
        qid = _get_first_question_id()
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": qid, "score": 3},
            cookies=cookies,
        )
        assert resp.status_code == 403

    def test_api_returns_403_in_orientation_phase(self, api_client):
        cookies, user_id = self._register_and_auth(api_client)
        # New users start in orientation
        qid = _get_first_question_id()
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": qid, "score": 2},
            cookies=cookies,
        )
        assert resp.status_code == 403

    def test_api_batch_accepts_responses_in_reassessment_phase(self, api_client):
        cookies, user_id = self._register_and_auth(api_client)
        self._set_user_phase(user_id, "reassessment")
        qid = _get_first_question_id()
        resp = api_client.post(
            "/api/assessment/responses/batch",
            json={"responses": [{"question_id": qid, "score": 4}]},
            cookies=cookies,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["saved"] == 1
