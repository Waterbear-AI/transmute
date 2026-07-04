"""Tests for BE-004: tier-aware agent tools and orchestration.

Verifies:
- present_transmute_core_batch: TC items first, then scenarios one at a time,
  then a done status once Tier 1 is exhausted
- evaluate_transmute_core_complete: insufficient-data path touches nothing;
  sufficient path computes+persists early_result and advances the tier
- get_next_adaptive_batch: awareness_core -> awareness_deepdive -> complete
  progression, delegating item selection to adaptive_engine
- get_assessment_state surfaces assessment_tier/flagged_dimensions/
  deep_dive_dimensions/early_result
- The new _check_assessment_completion_gate requires assessment_tier ==
  'complete' (replacing the old per-dimension >=60% check) and rejects an
  illegal advance_phase('profile') attempt server-side
- The 3 new tools are wired into the assessment sub-agent's tool list
"""

import json
import uuid

from db.database import get_db_session
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.tools import (
    advance_phase,
    evaluate_transmute_core_complete,
    get_assessment_state,
    get_next_adaptive_batch,
    present_transmute_core_batch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_user(phase: str = "assessment") -> str:
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Test", f"{uid}@test.example.com", "hash", phase),
        )
    return uid


def _create_assessment_state(
    user_id: str,
    responses: dict | None = None,
    scenario_responses: dict | None = None,
    assessment_tier: str = "transmute_core",
) -> str:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO assessment_state
               (id, user_id, responses, scenario_responses, assessment_tier, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (
                sid, user_id,
                json.dumps(responses or {}),
                json.dumps(scenario_responses or {}),
                assessment_tier,
            ),
        )
    return sid


def _get_state_row(user_id: str):
    with get_db_session() as conn:
        return conn.execute(
            "SELECT * FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()


def _tc_ids() -> list[str]:
    qb = get_question_bank()
    return [q["id"] for q in qb.get_questions_by_tier("transmute_core")]


def _scenario_ids() -> list[str]:
    qb = get_question_bank()
    return [s["id"] for s in qb.get_all_scenarios()]


# ---------------------------------------------------------------------------
# present_transmute_core_batch
# ---------------------------------------------------------------------------


class TestPresentTransmuteCoreBatch:
    def test_presents_tc_items_first_when_nothing_answered(self):
        uid = _create_user()
        _create_assessment_state(uid)

        result = present_transmute_core_batch(uid)
        assert result["event_type"] == "assessment.question_batch"
        assert set(result["question_ids"]) == set(_tc_ids())

    def test_presents_a_scenario_once_all_tc_items_answered(self):
        uid = _create_user()
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        _create_assessment_state(uid, responses=tc_responses)

        result = present_transmute_core_batch(uid)
        assert result["event_type"] == "assessment.scenario"
        assert result["scenario_id"] in _scenario_ids()

    def test_reports_done_once_tc_and_scenarios_all_answered(self):
        uid = _create_user()
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()}
        _create_assessment_state(uid, responses=tc_responses, scenario_responses=scenario_responses)

        result = present_transmute_core_batch(uid)
        assert result["status"] == "success"
        assert result["done"] is True

    def test_no_assessment_state_yet_presents_tc_items(self):
        """A brand-new user (no assessment_state row) should still get the
        first TC batch, not an error."""
        uid = _create_user()
        result = present_transmute_core_batch(uid)
        assert result["event_type"] == "assessment.question_batch"
        assert set(result["question_ids"]) == set(_tc_ids())


# ---------------------------------------------------------------------------
# evaluate_transmute_core_complete
# ---------------------------------------------------------------------------


class TestEvaluateTransmuteCoreComplete:
    def test_insufficient_data_returns_complete_false_and_touches_nothing(self):
        uid = _create_user()
        # Only 1 TC item answered (< MIN_ITEMS_PER_DIM=2), no scenarios.
        one_tc = {_tc_ids()[0]: {"score": 4}}
        _create_assessment_state(uid, responses=one_tc)

        result = evaluate_transmute_core_complete(uid)
        assert result["status"] == "success"
        assert result["complete"] is False

        row = _get_state_row(uid)
        assert row["assessment_tier"] == "transmute_core"
        assert row["early_result"] is None

    def test_sufficient_data_computes_and_persists_early_result(self):
        uid = _create_user()
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_ids = _scenario_ids()[:6]
        scenario_responses = {sid: {"choice": "a"} for sid in scenario_ids}
        _create_assessment_state(uid, responses=tc_responses, scenario_responses=scenario_responses)

        result = evaluate_transmute_core_complete(uid)
        assert result["complete"] is True
        assert result["event_type"] == "assessment.transmute_result"
        for key in ("archetype", "x", "y", "confidence", "confidence_reason"):
            assert key in result

    def test_sufficient_data_advances_tier_to_awareness_core(self):
        uid = _create_user()
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()[:6]}
        _create_assessment_state(uid, responses=tc_responses, scenario_responses=scenario_responses)

        evaluate_transmute_core_complete(uid)

        row = _get_state_row(uid)
        assert row["assessment_tier"] == "awareness_core"

    def test_sufficient_data_persists_early_result_json(self):
        uid = _create_user()
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()[:6]}
        _create_assessment_state(uid, responses=tc_responses, scenario_responses=scenario_responses)

        evaluate_transmute_core_complete(uid)

        row = _get_state_row(uid)
        assert row["early_result"] is not None
        persisted = json.loads(row["early_result"])
        assert persisted["event_type"] == "assessment.transmute_result"
        assert "computed_at" in persisted

    def test_no_assessment_state_returns_error_status(self):
        uid = _create_user()
        result = evaluate_transmute_core_complete(uid)
        assert result["status"] == "error"

    def test_exactly_at_minimum_thresholds_is_sufficient(self):
        """MIN_ITEMS_PER_DIM=2 TC items + MIN_SCENARIOS=3 scenarios exactly."""
        uid = _create_user()
        tc_responses = {qid: {"score": 4} for qid in _tc_ids()[:2]}
        scenario_responses = {sid: {"choice": "a"} for sid in _scenario_ids()[:3]}
        _create_assessment_state(uid, responses=tc_responses, scenario_responses=scenario_responses)

        result = evaluate_transmute_core_complete(uid)
        assert result["complete"] is True


# ---------------------------------------------------------------------------
# get_next_adaptive_batch
# ---------------------------------------------------------------------------


class TestGetNextAdaptiveBatch:
    def test_awareness_core_returns_unanswered_tier2_items(self):
        uid = _create_user()
        _create_assessment_state(uid, assessment_tier="awareness_core")

        result = get_next_adaptive_batch(uid)
        assert result["status"] == "success"
        assert result["tier"] == "awareness_core"
        assert result["done"] is False
        qb = get_question_bank()
        core_ids = {q["id"] for q in qb.get_questions_by_tier("awareness_core")}
        assert set(result["items"]) == core_ids

    def test_advances_to_deepdive_once_awareness_core_exhausted(self):
        uid = _create_user()
        qb = get_question_bank()
        core_responses = {q["id"]: {"score": 3} for q in qb.get_questions_by_tier("awareness_core")}
        _create_assessment_state(uid, responses=core_responses, assessment_tier="awareness_core")

        result = get_next_adaptive_batch(uid)
        assert result["tier"] == "awareness_deepdive"
        assert result["done"] is False
        # Screener-first: every item returned should be a screener item.
        for qid in result["items"]:
            q = qb.get_question_by_id(qid)
            assert q["is_screener"] is True

        row = _get_state_row(uid)
        assert row["assessment_tier"] == "awareness_deepdive"

    def test_reports_complete_once_deepdive_exhausted(self):
        uid = _create_user()
        qb = get_question_bank()
        all_answerable = {
            q["id"]: {"score": 3}
            for q in qb.get_questions_by_tier("awareness_core") + qb.get_questions_by_tier("awareness_deepdive")
        }
        _create_assessment_state(uid, responses=all_answerable, assessment_tier="awareness_deepdive")

        result = get_next_adaptive_batch(uid)
        assert result["tier"] == "complete"
        assert result["done"] is True
        assert result["items"] == []

        row = _get_state_row(uid)
        assert row["assessment_tier"] == "complete"

    def test_invalid_tier_for_this_tool_returns_error(self):
        """transmute_core/complete are not valid tiers for this tool."""
        uid = _create_user()
        _create_assessment_state(uid, assessment_tier="transmute_core")
        result = get_next_adaptive_batch(uid)
        assert result["status"] == "error"

    def test_no_assessment_state_returns_error(self):
        uid = _create_user()
        result = get_next_adaptive_batch(uid)
        assert result["status"] == "error"

    def test_tier_is_never_accepted_from_caller(self):
        """get_next_adaptive_batch only accepts user_id -- there is no tier
        parameter a caller could use to jump tiers."""
        import inspect
        sig = inspect.signature(get_next_adaptive_batch)
        assert list(sig.parameters.keys()) == ["user_id"]


# ---------------------------------------------------------------------------
# get_assessment_state tier/early_result surfacing
# ---------------------------------------------------------------------------


class TestGetAssessmentStateTierFields:
    def test_no_state_defaults_to_transmute_core(self):
        uid = _create_user()
        result = get_assessment_state(uid)
        assert result["assessment_tier"] == "transmute_core"
        assert result["early_result"] is None

    def test_reflects_persisted_tier_and_early_result(self):
        uid = _create_user()
        sid = _create_assessment_state(uid, assessment_tier="awareness_core")
        early_result = {"event_type": "assessment.transmute_result", "archetype": "transmuter"}
        with get_db_session() as conn:
            conn.execute(
                "UPDATE assessment_state SET early_result = ? WHERE id = ?",
                (json.dumps(early_result), sid),
            )

        result = get_assessment_state(uid)
        assert result["assessment_tier"] == "awareness_core"
        assert result["early_result"] == early_result


# ---------------------------------------------------------------------------
# New per-tier completion gate (advance_phase('profile'))
# ---------------------------------------------------------------------------


class TestPerTierCompletionGate:
    def test_advance_to_profile_rejected_when_tier_not_complete(self):
        uid = _create_user()
        _create_assessment_state(uid, assessment_tier="awareness_core")

        result = advance_phase(uid, "profile", reason="test")
        assert "error" in result
        assert result["assessment_tier"] == "awareness_core"

        # Server-side rejection means the phase must NOT have changed.
        with get_db_session() as conn:
            row = conn.execute("SELECT current_phase FROM users WHERE id = ?", (uid,)).fetchone()
        assert row["current_phase"] == "assessment"

    def test_advance_to_profile_succeeds_when_tier_complete(self):
        uid = _create_user()
        _create_assessment_state(uid, assessment_tier="complete")

        result = advance_phase(uid, "profile", reason="test")
        assert "error" not in result
        assert result["new_phase"] == "profile"

    def test_no_assessment_state_at_all_rejected(self):
        uid = _create_user()
        result = advance_phase(uid, "profile", reason="test")
        assert "error" in result


# ---------------------------------------------------------------------------
# Sub-agent tool-list integration
# ---------------------------------------------------------------------------


class TestAssessmentSubAgentToolIntegration:
    def test_new_tools_in_assessment_agent_tool_list(self):
        from agents.transmutation.sub_agents.assessment import create_assessment_agent

        agent = create_assessment_agent(model="mock/scripted")
        tool_names = {getattr(t, "__name__", str(t)) for t in agent.tools}
        for expected in (
            "present_transmute_core_batch",
            "evaluate_transmute_core_complete",
            "get_next_adaptive_batch",
        ):
            assert expected in tool_names, f"{expected} missing from assessment agent tools"
