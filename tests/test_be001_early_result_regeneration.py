"""Tests for BE-001: early_result regeneration on response save.

Covers:
- Unit: _maybe_regenerate_early_result returns None when early_result is NULL
- Unit: _maybe_regenerate_early_result recomputes + persists when early_result exists
- Unit: _is_transmute_relevant classifies transmute_core vs awareness questions
- Integration: POST /api/assessment/responses scenario edit regenerates early_result
- Integration: POST /api/assessment/responses transmute_core Likert edit regenerates early_result
- Integration: POST /api/assessment/responses awareness Likert edit does NOT regenerate
- Integration: POST /api/assessment/responses/batch regenerates once for TC-Likert batch
- Integration: edit while phase != assessment -> 403 (existing gate still enforced)
- Integration: editing a load-bearing answer changes the returned archetype
"""

import json
import uuid

from db.database import get_db_session
from agents.transmutation.tools import evaluate_transmute_core_complete
from agents.transmutation.question_bank import get_question_bank
from api.assessment import _maybe_regenerate_early_result, _is_transmute_relevant


# ── Helpers ────────────────────────────────────────────────────────────────────


def _tc_ids() -> list[str]:
    qb = get_question_bank()
    return [q["id"] for q in qb.get_questions_by_tier("transmute_core")]


def _scenario_ids() -> list[str]:
    qb = get_question_bank()
    return [s["id"] for s in qb.get_all_scenarios()]


def _awareness_question_id() -> str:
    qb = get_question_bank()
    for q in qb.get_all_questions():
        if q.get("tier") != "transmute_core" and q.get("dimension") != "Transmutation Capacity":
            return q["id"]
    raise AssertionError("No awareness question found in question bank")


def _create_user(phase: str) -> str:
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Test User", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _create_assessment_state(user_id: str, responses: dict, scenario_responses: dict) -> None:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO assessment_state
               (id, user_id, responses, scenario_responses, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (sid, user_id, json.dumps(responses), json.dumps(scenario_responses)),
        )


def _register_and_auth(api_client) -> tuple:
    resp = api_client.post("/auth/register", json={
        "name": "BE001 Test User",
        "email": f"{uuid.uuid4()}@test.com",
        "password": "testpass123",
    })
    assert resp.status_code == 200
    return resp.cookies, resp.json()["user_id"]


def _set_user_phase(user_id: str, phase: str):
    with get_db_session() as conn:
        conn.execute(
            "UPDATE users SET current_phase = ? WHERE id = ?",
            (phase, user_id),
        )


def _complete_tier1(user_id: str, score: int = 4, choice: str = "a"):
    """Answer enough TC Likert + scenarios and run Tier-1 completion so
    early_result exists in assessment_state for the given user."""
    tc_responses = {qid: {"score": score} for qid in _tc_ids()}
    scenario_responses = {sid: {"choice": choice} for sid in _scenario_ids()[:6]}
    _create_assessment_state(user_id, tc_responses, scenario_responses)
    result = evaluate_transmute_core_complete(user_id)
    assert result["complete"] is True
    return tc_responses, scenario_responses


# ── Unit: _is_transmute_relevant ────────────────────────────────────────────────


class TestIsTransmuteRelevant:
    def test_transmute_core_tier_is_relevant(self):
        qb = get_question_bank()
        tc_q = qb.get_question_by_id(_tc_ids()[0])
        assert _is_transmute_relevant(tc_q) is True

    def test_awareness_question_is_not_relevant(self):
        qb = get_question_bank()
        aw_q = qb.get_question_by_id(_awareness_question_id())
        assert _is_transmute_relevant(aw_q) is False

    def test_dimension_fallback_when_tier_absent(self):
        assert _is_transmute_relevant({"dimension": "Transmutation Capacity"}) is True

    def test_neither_tier_nor_dimension_is_not_relevant(self):
        assert _is_transmute_relevant({"tier": "awareness_core", "dimension": "Something Else"}) is False


# ── Unit: _maybe_regenerate_early_result ────────────────────────────────────────


class TestMaybeRegenerateEarlyResultUnit:
    def test_returns_none_when_early_result_is_null(self):
        uid = _create_user("assessment")
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()[:6]}
        _create_assessment_state(uid, tc_responses, scenario_responses)

        with get_db_session() as conn:
            result = _maybe_regenerate_early_result(conn, uid, tc_responses, scenario_responses)

        assert result is None

    def test_recomputes_and_persists_when_early_result_exists(self):
        uid = _create_user("assessment")
        tc_responses, scenario_responses = _complete_tier1(uid)

        # Change a scenario choice and recompute.
        first_scenario = _scenario_ids()[0]
        scenario_responses = dict(scenario_responses)
        scenario_responses[first_scenario] = {"choice": "b"}

        with get_db_session() as conn:
            result = _maybe_regenerate_early_result(conn, uid, tc_responses, scenario_responses)

        assert result is not None
        assert result["event_type"] == "assessment.transmute_result"
        for key in ("archetype", "x", "y", "confidence", "confidence_reason", "computed_at"):
            assert key in result

        # Persisted in the DB too.
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT early_result FROM assessment_state WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (uid,),
            ).fetchone()
        persisted = json.loads(row["early_result"])
        assert persisted == result

    def test_recompute_with_different_inputs_can_change_archetype_fields(self):
        uid = _create_user("assessment")
        tc_responses, scenario_responses = _complete_tier1(uid, score=2, choice="a")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT early_result FROM assessment_state WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (uid,),
            ).fetchone()
        original = json.loads(row["early_result"])

        # Flip all TC scores to the opposite end of the scale.
        flipped_responses = {qid: {"score": 5} for qid in tc_responses}

        with get_db_session() as conn:
            result = _maybe_regenerate_early_result(conn, uid, flipped_responses, scenario_responses)

        assert result is not None
        # x/y should differ given the score flip (score is a direct scoring input).
        assert (result["x"], result["y"]) != (original["x"], original["y"])


# ── Integration: POST /api/assessment/responses ────────────────────────────────


class TestSaveResponseScenarioRegenerate:
    def test_scenario_edit_regenerates_early_result(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid)

        sid = _scenario_ids()[0]
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": sid, "type": "scenario", "choice_key": "b"},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["saved"] is True
        assert body["early_result"] is not None
        assert body["early_result"]["event_type"] == "assessment.transmute_result"

        # DB early_result reflects the same value.
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT early_result FROM assessment_state WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (uid,),
            ).fetchone()
        assert json.loads(row["early_result"]) == body["early_result"]

    def test_scenario_edit_before_tier1_complete_returns_null_early_result(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")

        sid = _scenario_ids()[0]
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": sid, "type": "scenario", "choice_key": "a"},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["early_result"] is None


class TestSaveResponseLikertRegenerate:
    def test_transmute_core_likert_edit_regenerates_early_result(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid)

        tc_qid = _tc_ids()[0]
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": tc_qid, "score": 1},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["early_result"] is not None
        assert body["early_result"]["event_type"] == "assessment.transmute_result"

    def test_awareness_likert_edit_does_not_regenerate(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid)

        aw_qid = _awareness_question_id()
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": aw_qid, "score": 3},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["saved"] is True
        assert body["early_result"] is None

    def test_edit_while_phase_not_assessment_returns_403(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid)
        _set_user_phase(uid, "development")

        tc_qid = _tc_ids()[0]
        resp = api_client.post(
            "/api/assessment/responses",
            json={"question_id": tc_qid, "score": 5},
            cookies=cookies,
        )
        assert resp.status_code == 403

    def test_editing_load_bearing_answer_changes_returned_archetype_fields(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid, score=2)

        state_before = api_client.get("/api/assessment/state", cookies=cookies).json()
        early_before = state_before["early_result"]

        for qid in _tc_ids():
            resp = api_client.post(
                "/api/assessment/responses",
                json={"question_id": qid, "score": 5},
                cookies=cookies,
            )
            assert resp.status_code == 200

        assert resp.json()["early_result"] is not None
        early_after = resp.json()["early_result"]
        assert (early_after["x"], early_after["y"]) != (early_before["x"], early_before["y"])


class TestSaveResponsesBatchRegenerate:
    def test_batch_with_transmute_core_item_regenerates_once(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid)

        tc_qid = _tc_ids()[0]
        resp = api_client.post(
            "/api/assessment/responses/batch",
            json={"responses": [{"question_id": tc_qid, "score": 2}]},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["saved"] == 1
        assert body["early_result"] is not None
        assert body["early_result"]["event_type"] == "assessment.transmute_result"

    def test_batch_with_only_awareness_items_does_not_regenerate(self, api_client):
        cookies, uid = _register_and_auth(api_client)
        _set_user_phase(uid, "assessment")
        _complete_tier1(uid)

        aw_qid = _awareness_question_id()
        resp = api_client.post(
            "/api/assessment/responses/batch",
            json={"responses": [{"question_id": aw_qid, "score": 3}]},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["early_result"] is None
