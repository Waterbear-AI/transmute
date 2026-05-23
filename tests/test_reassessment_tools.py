"""Integration tests for reassessment selection tools.

Validates:
- get_dimension_staleness: correct cycle + staleness from DB
- select_reassessment_targets: roadmap extraction, flagging, sentinel selection, fallbacks
- select_sentinel_questions: extremity-based question prioritization
"""

import json
import uuid

import pytest

from db.database import get_db_session
from agents.transmutation.tools import (
    get_dimension_staleness,
    select_reassessment_targets,
    select_sentinel_questions,
)
from agents.transmutation.question_bank import get_question_bank


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_user(user_id: str = None, phase: str = "reassessment", cycle: int = 0) -> str:
    uid = user_id or str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase, reassessment_cycle) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, "Test", f"{uid}@test.com", "hash", phase, cycle),
        )
    return uid


def _create_snapshot(user_id: str, scores: dict) -> str:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"archetype": "absorber"}), "2026-01-01T00:00:00"),
        )
    return sid


def _seed_das(user_id: str, dimension: str, last_assessed_cycle: int, kind: str = "targeted",
              last_score: float = 3.0, flagged: bool = False) -> None:
    """Seed a dimension_assessment_state row."""
    row_id = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO dimension_assessment_state
               (id, user_id, dimension, last_assessed_cycle, last_assessment_kind, last_score, flagged_for_full_reassessment, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row_id, user_id, dimension, last_assessed_cycle, kind, last_score, 1 if flagged else 0, "2026-01-01T00:00:00"),
        )


def _create_roadmap(user_id: str, roadmap: dict) -> str:
    rid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
            (rid, user_id, json.dumps(roadmap), "2026-01-01T00:00:00"),
        )
    return rid


def _create_assessment_state(user_id: str, responses: dict) -> None:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO assessment_state (id, user_id, responses, scenario_responses, current_phase, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sid, user_id, json.dumps(responses), "{}", "reassessment", "2026-01-01T00:00:00"),
        )


# ---------------------------------------------------------------------------
# TestGetDimensionStaleness
# ---------------------------------------------------------------------------

class TestGetDimensionStaleness:
    def test_baseline_user_all_staleness_zero(self):
        """After baseline (cycle 0, all dims assessed at 0), staleness is 0 for all."""
        qb = get_question_bank()
        all_dims = qb.get_dimensions()
        uid = _create_user(cycle=0)

        # Seed all dims at cycle 0
        for dim in all_dims:
            _seed_das(uid, dim, last_assessed_cycle=0, kind="baseline")

        result = get_dimension_staleness(uid)
        assert result["current_cycle"] == 0
        for dim in all_dims:
            assert result["staleness"][dim] == 0
            assert result["last_assessed_cycle"][dim] == 0

    def test_never_assessed_dim_staleness_equals_current_cycle(self):
        """Dims never in dimension_assessment_state have staleness = current_cycle."""
        uid = _create_user(cycle=3)
        # No DAS rows at all
        result = get_dimension_staleness(uid)
        assert result["current_cycle"] == 3
        for dim in result["staleness"]:
            assert result["staleness"][dim] == 3

    def test_partial_assessment_staleness(self):
        """Dims assessed at cycle 1 with current cycle 3 → staleness 2."""
        qb = get_question_bank()
        all_dims = qb.get_dimensions()
        dim_a = all_dims[0]
        uid = _create_user(cycle=3)

        # Only one dim assessed at cycle 1
        _seed_das(uid, dim_a, last_assessed_cycle=1)

        result = get_dimension_staleness(uid)
        assert result["staleness"][dim_a] == 2  # 3 - 1 = 2
        # All other dims never assessed → staleness = 3
        for dim in all_dims:
            if dim != dim_a:
                assert result["staleness"][dim] == 3

    def test_user_not_found_returns_error(self):
        """Non-existent user returns error dict."""
        result = get_dimension_staleness("nonexistent-user-id")
        assert "error" in result

    def test_returns_all_13_dims(self):
        """Result always contains all 13 dimensions."""
        qb = get_question_bank()
        all_dims = qb.get_dimensions()
        uid = _create_user(cycle=0)
        result = get_dimension_staleness(uid)
        assert set(result["staleness"].keys()) == set(all_dims)


# ---------------------------------------------------------------------------
# TestSelectReassessmentTargets
# ---------------------------------------------------------------------------

class TestSelectReassessmentTargets:

    def test_roadmap_dimension_key_extraction(self):
        """Dims at 'dimension' key in roadmap → targeted."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _create_roadmap(uid, {"dimension": dim})
        # Seed all dims in DAS at cycle 1 so staleness=0
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)

        result = select_reassessment_targets(uid)
        assert dim in result["targeted_dimensions"]

    def test_roadmap_dimensions_list_key(self):
        """Dims at 'dimensions' list key in roadmap → targeted."""
        qb = get_question_bank()
        dims = qb.get_dimensions()[:2]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _create_roadmap(uid, {"dimensions": dims})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)

        result = select_reassessment_targets(uid)
        for dim in dims:
            assert dim in result["targeted_dimensions"]

    def test_roadmap_steps_target_key(self):
        """Dims at 'steps[].target' in roadmap → targeted."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[1]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _create_roadmap(uid, {"steps": [{"target": dim}]})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)

        result = select_reassessment_targets(uid)
        assert dim in result["targeted_dimensions"]

    def test_prior_flagged_dim_added_to_targeted(self):
        """Dim with flagged_for_full_reassessment=1 in DAS → targeted."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[2]
        uid = _create_user(cycle=2)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _seed_das(uid, dim, last_assessed_cycle=1, flagged=True)

        result = select_reassessment_targets(uid)
        assert dim in result["targeted_dimensions"]

    def test_no_roadmap_fallback_uses_flagged_only(self):
        """No roadmap → targeted = only flagged dims (possibly empty)."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _seed_das(uid, dim, last_assessed_cycle=0, flagged=True)
        # No roadmap created

        result = select_reassessment_targets(uid)
        assert dim in result["targeted_dimensions"]
        assert "targeted_dimensions" in result
        assert "sentinel_dimensions" in result
        assert "carried_dimensions" in result

    def test_no_roadmap_no_flags_all_carried_or_sentinel(self):
        """No roadmap, no flags → targeted empty; dims split between sentinel and carried."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})

        result = select_reassessment_targets(uid)
        assert result["targeted_dimensions"] == []
        total = len(result["sentinel_dimensions"]) + len(result["carried_dimensions"])
        assert total == len(qb.get_dimensions())

    def test_targeted_excluded_from_sentinel(self):
        """Targeted dims never appear in sentinel_dimensions."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _create_roadmap(uid, {"dimension": dim})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)

        result = select_reassessment_targets(uid)
        assert dim not in result["sentinel_dimensions"]
        assert dim not in result["carried_dimensions"]

    def test_partition_covers_all_dims(self):
        """targeted + sentinel + carried == all 13 dims (no overlap, no gaps)."""
        qb = get_question_bank()
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        dim = qb.get_dimensions()[0]
        _create_roadmap(uid, {"dimension": dim})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)

        result = select_reassessment_targets(uid)
        all_selected = set(result["targeted_dimensions"]) | set(result["sentinel_dimensions"]) | set(result["carried_dimensions"])
        assert all_selected == set(qb.get_dimensions())
        # No overlap
        targeted_set = set(result["targeted_dimensions"])
        sentinel_set = set(result["sentinel_dimensions"])
        carried_set = set(result["carried_dimensions"])
        assert targeted_set & sentinel_set == set()
        assert targeted_set & carried_set == set()
        assert sentinel_set & carried_set == set()

    def test_non_dimension_strings_in_roadmap_ignored(self):
        """Non-dimension strings in roadmap dimension fields are silently ignored."""
        qb = get_question_bank()
        uid = _create_user(cycle=1)
        _create_snapshot(uid, {d: {"score": 3.0, "sub_dimensions": {}} for d in qb.get_dimensions()})
        _create_roadmap(uid, {"dimension": "NotARealDimension"})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)

        result = select_reassessment_targets(uid)
        assert "NotARealDimension" not in result["targeted_dimensions"]


# ---------------------------------------------------------------------------
# TestSelectSentinelQuestions
# ---------------------------------------------------------------------------

class TestSelectSentinelQuestions:

    def _get_real_dim(self):
        qb = get_question_bank()
        return qb.get_dimensions()[0]

    def test_returns_question_ids_only(self):
        """Returns list of question IDs (strings), not full question objects."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user()

        result = select_sentinel_questions(uid, [dim], n=3)
        assert "question_ids" in result
        assert "by_dimension" in result
        assert isinstance(result["question_ids"], list)
        for qid in result["question_ids"]:
            assert isinstance(qid, str)

    def test_n_questions_per_dim(self):
        """Selects at most n questions per dimension."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user()

        result = select_sentinel_questions(uid, [dim], n=3)
        assert len(result["by_dimension"][dim]) <= 3

    def test_extreme_responses_prioritized(self):
        """Questions with scores closest to 1 or 5 come first."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        questions = qb.get_questions_by_dimension(dim)
        if len(questions) < 3:
            pytest.skip("Not enough questions in this dimension")

        uid = _create_user()

        # Seed responses: q0=5 (extreme), q1=3 (neutral), q2=1 (extreme)
        responses = {
            questions[0]["id"]: {"score": 5},
            questions[1]["id"]: {"score": 3},
            questions[2]["id"]: {"score": 1},
        }
        _create_assessment_state(uid, responses)

        result = select_sentinel_questions(uid, [dim], n=2)
        selected = result["by_dimension"][dim]
        # questions[0] (score=5, extremity=2) and questions[2] (score=1, extremity=2)
        # should be prioritized over questions[1] (score=3, extremity=0)
        assert questions[1]["id"] not in selected or len(selected) >= 3

    def test_no_prior_responses_still_returns_questions(self):
        """With no prior responses, falls back to returning any n questions."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user()
        # No assessment state created

        result = select_sentinel_questions(uid, [dim], n=5)
        assert len(result["by_dimension"][dim]) >= 1

    def test_empty_dimensions_returns_empty(self):
        """Empty dimensions list → empty result."""
        uid = _create_user()
        result = select_sentinel_questions(uid, [], n=5)
        assert result["question_ids"] == []
        assert result["by_dimension"] == {}

    def test_multiple_dimensions(self):
        """Questions returned for each dimension in the list."""
        qb = get_question_bank()
        dims = qb.get_dimensions()[:2]
        uid = _create_user()

        result = select_sentinel_questions(uid, dims, n=3)
        for dim in dims:
            assert dim in result["by_dimension"]
            assert len(result["by_dimension"][dim]) >= 1

    def test_question_ids_are_valid(self):
        """All returned question IDs exist in the question bank."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user()

        result = select_sentinel_questions(uid, [dim], n=5)
        for qid in result["question_ids"]:
            assert qb.get_question_by_id(qid) is not None
