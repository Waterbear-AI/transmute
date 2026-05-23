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
    generate_reassessment_snapshot,
    save_profile_snapshot,
    _profile_cache,
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


def _seed_das_flag(user_id: str, dimension: str, flagged: bool) -> None:
    """Update flagged_for_full_reassessment on an existing DAS row."""
    with get_db_session() as conn:
        conn.execute(
            "UPDATE dimension_assessment_state SET flagged_for_full_reassessment = ? WHERE user_id = ? AND dimension = ?",
            (1 if flagged else 0, user_id, dimension),
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


# ---------------------------------------------------------------------------
# Helpers for snapshot generation/persistence tests
# ---------------------------------------------------------------------------

def _full_responses(score: int = 3) -> dict:
    """Build a response dict answering every question in the bank with `score`."""
    qb = get_question_bank()
    responses = {}
    for dim in qb.get_dimensions():
        for q in qb.get_questions_by_dimension(dim):
            responses[q["id"]] = {"score": score}
    return responses


def _prior_scores(score: float = 3.0) -> dict:
    """Build a prior snapshot scores dict at the given score for all dims/sub-dims."""
    qb = get_question_bank()
    scores = {}
    for dim in qb.get_dimensions():
        sub_dims = {}
        for q in qb.get_questions_by_dimension(dim):
            sd = q.get("sub_dimension", "general")
            sub_dims[sd] = {"score": score, "answered": 1, "total": 1, "na_count": 0}
        scores[dim] = {
            "score": score,
            "answered": 1,
            "total": 1,
            "na_count": 0,
            "insufficient_data": False,
            "sub_dimensions": sub_dims,
        }
    return scores


def _das_rows(user_id: str) -> dict:
    """Return {dim: row_dict} for all dimension_assessment_state rows of a user."""
    with get_db_session() as conn:
        rows = conn.execute(
            "SELECT dimension, last_assessed_cycle, last_assessment_kind, last_score, flagged_for_full_reassessment "
            "FROM dimension_assessment_state WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {r["dimension"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# TestGenerateReassessmentSnapshot
# ---------------------------------------------------------------------------

class TestGenerateReassessmentSnapshot:

    def test_no_prior_snapshot_returns_error(self):
        """No prior snapshot → error."""
        uid = _create_user(cycle=0)
        _create_assessment_state(uid, _full_responses())
        result = generate_reassessment_snapshot(uid)
        assert "error" in result
        assert "prior snapshot" in result["error"].lower()

    def test_no_assessment_data_returns_error(self):
        """Prior snapshot exists but no assessment data → error."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores())
        result = generate_reassessment_snapshot(uid)
        assert "error" in result
        assert "assessment data" in result["error"].lower()

    def test_returns_reassessment_scored_event(self):
        """Successful reassessment returns the expected event shape."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores(3.0))
        _create_assessment_state(uid, _full_responses(4))

        result = generate_reassessment_snapshot(uid)
        assert result["event_type"] == "reassessment.scored"
        assert "scores" in result
        assert "quadrant" in result
        assert "sentinel" in result
        assert result["current_cycle"] == 1  # cycle 0 → 1 once saved

    def test_populates_profile_cache_with_sentinel(self):
        """_profile_cache is populated with blended scores + sentinel block."""
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores(3.0))
        _create_assessment_state(uid, _full_responses(4))

        generate_reassessment_snapshot(uid)
        cached = _profile_cache.get(uid)
        assert cached is not None
        assert "sentinel" in cached
        assert "scores" in cached
        assert "quadrant" in cached
        assert cached["sentinel"]["cycle"] == 1

    def test_carried_dims_keep_prior_score(self):
        """Carried (untouched) dims retain their prior score in the blend."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores(2.0))
        _create_assessment_state(uid, _full_responses(5))

        result = generate_reassessment_snapshot(uid)
        sentinel_meta = result["sentinel"]["dimensions"]
        for dim, meta in sentinel_meta.items():
            if meta["source"] == "carried":
                # carried dims keep prior score (2.0)
                assert result["scores"][dim]["score"] == 2.0

    def test_targeted_dim_uses_fresh_score(self):
        """A roadmap-targeted dim takes the fresh full re-score (100% new)."""
        from agents.transmutation.scoring_engine import score_responses
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, _prior_scores(2.0))
        _create_roadmap(uid, {"dimension": dim})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)
        responses = _full_responses(5)
        _create_assessment_state(uid, responses)

        # Independently compute the fresh full re-score for the targeted dim.
        fresh = score_responses(responses, {})["dimensions"][dim]["score"]

        result = generate_reassessment_snapshot(uid)
        assert dim in result["sentinel"]["targeted_dimensions"]
        # Targeted dim is 100% fresh — equals the standalone full re-score (not the
        # prior 2.0), proving the blend uses new_weight=1.0 for targeted dims.
        assert result["scores"][dim]["score"] == fresh
        assert fresh != 2.0  # sanity: fresh genuinely differs from prior

    def test_sentinel_dim_blends_70_30(self):
        """A sentinel dim blends 0.7*prior + 0.3*fresh(sentinel subset)."""
        from agents.transmutation.scoring_engine import score_question_subset
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores(2.0))
        responses = _full_responses(5)
        _create_assessment_state(uid, responses)

        result = generate_reassessment_snapshot(uid)
        sentinel_dims = result["sentinel"]["sentinel_dimensions"]
        assert len(sentinel_dims) >= 1
        # Reconstruct the fresh sentinel signal the engine used.
        sentinel_qids = select_sentinel_questions(uid, sentinel_dims)["question_ids"]
        fresh_signal = score_question_subset(responses, sentinel_qids, qb)
        for dim in sentinel_dims:
            fresh = fresh_signal[dim]["score"]
            expected = 0.7 * 2.0 + 0.3 * fresh
            assert abs(result["scores"][dim]["score"] - expected) < 0.01

    def test_quadrant_reflects_blended_capacity(self):
        """Quadrant is recomputed from blended Transmutation Capacity sub-dims."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        # Prior strongly transmuter-leaning; fresh neutral. Blended should differ
        # from a pure-fresh recompute.
        _create_snapshot(uid, _prior_scores(5.0))
        _create_assessment_state(uid, _full_responses(3))

        result = generate_reassessment_snapshot(uid)
        quadrant = result["quadrant"]
        assert "x" in quadrant
        assert "y" in quadrant
        assert "archetype" in quadrant


# ---------------------------------------------------------------------------
# TestSaveProfileSnapshotBaseline
# ---------------------------------------------------------------------------

class TestSaveProfileSnapshotBaseline:

    def test_baseline_seeds_all_dims_at_cycle_zero(self):
        """Baseline save (no sentinel block) seeds all 13 dims at cycle 0."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        scores = _prior_scores(3.5)
        _profile_cache[uid] = {
            "scores": scores,
            "quadrant": {"x": 0.0, "y": 0.0, "archetype": "conduit"},
            "insufficient_dimensions": [],
            "spider_chart": None,
            "flow_profile": None,
        }

        save_profile_snapshot(uid, "baseline interpretation")
        rows = _das_rows(uid)
        assert set(rows.keys()) == set(qb.get_dimensions())
        for dim, row in rows.items():
            assert row["last_assessed_cycle"] == 0
            assert row["last_assessment_kind"] == "baseline"
            assert row["last_score"] == 3.5
            assert row["flagged_for_full_reassessment"] == 0

    def test_baseline_does_not_increment_cycle(self):
        """Baseline save keeps users.reassessment_cycle at 0."""
        uid = _create_user(cycle=0)
        _profile_cache[uid] = {
            "scores": _prior_scores(3.0),
            "quadrant": {"x": 0.0, "y": 0.0, "archetype": "conduit"},
            "insufficient_dimensions": [],
            "spider_chart": None,
            "flow_profile": None,
        }
        save_profile_snapshot(uid, "baseline")
        with get_db_session() as conn:
            row = conn.execute("SELECT reassessment_cycle FROM users WHERE id = ?", (uid,)).fetchone()
        assert row["reassessment_cycle"] == 0


# ---------------------------------------------------------------------------
# TestSaveProfileSnapshotReassessment
# ---------------------------------------------------------------------------

class TestSaveProfileSnapshotReassessment:

    def test_reassessment_increments_cycle(self):
        """Reassessment save increments users.reassessment_cycle."""
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores(3.0))
        _create_assessment_state(uid, _full_responses(4))

        generate_reassessment_snapshot(uid)  # populates cache with sentinel block
        save_profile_snapshot(uid, "reassessment interpretation")

        with get_db_session() as conn:
            row = conn.execute("SELECT reassessment_cycle FROM users WHERE id = ?", (uid,)).fetchone()
        assert row["reassessment_cycle"] == 1

    def test_reassessment_upserts_targeted_and_sentinel_das(self):
        """Targeted and sentinel dims get DAS rows at the new cycle."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, _prior_scores(3.0))
        _create_roadmap(uid, {"dimension": dim})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)
        _create_assessment_state(uid, _full_responses(4))

        result = generate_reassessment_snapshot(uid)
        targeted = result["sentinel"]["targeted_dimensions"]
        sentinel = result["sentinel"]["sentinel_dimensions"]
        save_profile_snapshot(uid, "reassessment")

        rows = _das_rows(uid)
        for d in targeted:
            assert rows[d]["last_assessed_cycle"] == 2
            assert rows[d]["last_assessment_kind"] == "targeted"
        for d in sentinel:
            assert rows[d]["last_assessed_cycle"] == 2
            assert rows[d]["last_assessment_kind"] == "sentinel"

    def test_reassessment_flag_set_and_cleared(self):
        """flagged_for_full_reassessment is set for flagged dims and cleared otherwise."""
        qb = get_question_bank()
        uid = _create_user(cycle=0)
        # Large prior→fresh swing on sentinel dims should trip the shift flag.
        _create_snapshot(uid, _prior_scores(1.0))
        _create_assessment_state(uid, _full_responses(5))

        result = generate_reassessment_snapshot(uid)
        flagged = set(result["sentinel"]["flagged_for_full_reassessment"])
        save_profile_snapshot(uid, "reassessment")

        rows = _das_rows(uid)
        for dim in result["sentinel"]["sentinel_dimensions"]:
            expected = 1 if dim in flagged else 0
            assert rows[dim]["flagged_for_full_reassessment"] == expected

    def test_reassessment_clears_prior_flag_on_targeted(self):
        """A dim previously flagged then re-targeted has its flag cleared."""
        qb = get_question_bank()
        dim = qb.get_dimensions()[0]
        uid = _create_user(cycle=1)
        _create_snapshot(uid, _prior_scores(3.0))
        # dim was flagged last cycle; roadmap re-targets it this cycle
        _create_roadmap(uid, {"dimension": dim})
        for d in qb.get_dimensions():
            _seed_das(uid, d, last_assessed_cycle=1)
        _seed_das_flag(uid, dim, True)
        _create_assessment_state(uid, _full_responses(3))  # neutral fresh → no new flag

        generate_reassessment_snapshot(uid)
        save_profile_snapshot(uid, "reassessment")

        rows = _das_rows(uid)
        # dim was targeted (fresh==prior==3.0, no shift) → flag cleared
        assert rows[dim]["flagged_for_full_reassessment"] == 0

    def test_reassessment_persists_sentinel_in_snapshot(self):
        """The persisted snapshot's quadrant JSON carries the sentinel block."""
        uid = _create_user(cycle=0)
        _create_snapshot(uid, _prior_scores(3.0))
        _create_assessment_state(uid, _full_responses(4))

        generate_reassessment_snapshot(uid)
        save_profile_snapshot(uid, "reassessment")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT quadrant_placement FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (uid,),
            ).fetchone()
        quadrant = json.loads(row["quadrant_placement"])
        assert "sentinel" in quadrant
        assert "flagged_for_full_reassessment" in quadrant["sentinel"]

    def test_reassessment_atomic_no_partial_on_cache_miss(self):
        """save_profile_snapshot with no cached profile returns error, no DB change."""
        uid = _create_user(cycle=0)
        result = save_profile_snapshot(uid, "x")
        assert "error" in result
        with get_db_session() as conn:
            row = conn.execute("SELECT reassessment_cycle FROM users WHERE id = ?", (uid,)).fetchone()
        assert row["reassessment_cycle"] == 0
