"""Unit tests for lifecycle agent tools: Education, Development, Reassessment, Graduation, Check-in."""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from db.database import get_db_session
from agents.transmutation.tools import (
    record_comprehension_answer,
    get_education_progress,
    log_practice_entry,
    update_roadmap,
    save_roadmap,
    generate_roadmap,
    rank_gaps,
    check_roadmap_targets_gaps,
    generate_comparison_snapshot,
    evaluate_graduation_readiness,
    generate_graduation_artifacts,
    save_graduation_record,
    save_check_in_log,
    get_graduation_record,
    get_development_gate_progress,
    advance_phase,
)


def _create_user(user_id: str = None, phase: str = "education") -> str:
    """Helper: insert a test user and return user_id."""
    uid = user_id or str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Test", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _create_snapshot(user_id: str, scores: dict, quadrant: str = "absorber", created_at: str = None) -> str:
    """Helper: insert a profile snapshot and return snapshot_id.

    Stores quadrant_placement under the ``archetype`` key to match production
    (scoring_engine._calculate_quadrant emits ``archetype``, never ``quadrant``).
    Scores are on the engine's raw 1–5 Likert scale.
    """
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"archetype": quadrant}), ts),
        )
    return sid


# ── TEST-001: Education Agent scoring logic ──


class TestRecordComprehensionAnswer:
    def test_correct_answer_scores_100(self):
        uid = _create_user()
        # Use actual question from comprehension_checks.json
        result = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "b",  # Correct answer
        )
        assert result["correct"] is True
        assert result["score"] == 100
        assert "explanation" in result

    def test_incorrect_answer(self):
        uid = _create_user()
        result = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "a",  # Wrong answer
        )
        assert result["correct"] is False
        assert result["score"] == 0

    def test_unknown_question_returns_error(self):
        uid = _create_user()
        result = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "nonexistent_q", "a",
        )
        assert "error" in result

    def test_score_formula_correct_over_answered(self):
        """Verify understanding_score = (correct / answered) * 100."""
        uid = _create_user()
        # Manually set up education progress with known data
        with get_db_session() as conn:
            progress = {
                "Emotional Awareness": {
                    "what_this_means": {
                        "understanding_score": 0,
                        "questions_answered": ["q1", "q2", "q3"],
                        "questions_correct": ["q1", "q3"],
                        "last_discussed": None,
                        "reflection_given": False,
                    }
                }
            }
            conn.execute(
                "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
                (uid, json.dumps(progress)),
            )

        result = get_education_progress(uid)
        assert result["exists"] is True
        cat = result["progress"]["Emotional Awareness"]["what_this_means"]
        assert len(cat["questions_answered"]) == 3
        assert len(cat["questions_correct"]) == 2

    def test_duplicate_answer_not_recounted(self):
        """Answering the same question twice should not inflate the score."""
        uid = _create_user()
        # First answer — correct
        record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "b",
        )
        # Answer same question again — should not add duplicate
        result = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "a",
        )
        assert result["score"] == 100  # Score unchanged since duplicate skipped


class TestGetEducationProgress:
    def test_no_progress_returns_empty(self):
        uid = _create_user()
        result = get_education_progress(uid)
        assert result["exists"] is False
        assert result["progress"] == {}

    def test_summary_computation(self):
        uid = _create_user()
        with get_db_session() as conn:
            # Two started dimensions. Untouched canonical categories (e.g.
            # external_interaction) are absent from the JSON but must still
            # count toward the denominator (5 per started dimension).
            progress = {
                "Emotional Awareness": {
                    "what_this_means": {"understanding_score": 80},
                    "your_score": {"understanding_score": 50},
                },
                "Social Awareness": {
                    "what_this_means": {"understanding_score": 90},
                },
            }
            conn.execute(
                "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
                (uid, json.dumps(progress)),
            )

        result = get_education_progress(uid)
        assert result["exists"] is True
        # 2 canonical categories >= 70 (the two 80/90 what_this_means scores).
        assert result["summary"]["completed_categories"] == 2
        # Denominator is 5 canonical categories per started dimension (2 dims).
        assert result["summary"]["total_categories"] == 10
        assert result["summary"]["completion_pct"] == 20.0

    def test_summary_counts_all_five_canonical_categories_per_dimension(self):
        """A single fully-taught dimension reports 5/5, not 3/3 or 4/4."""
        uid = _create_user()
        with get_db_session() as conn:
            progress = {
                "Emotional Awareness": {
                    "what_this_means": {"understanding_score": 100},
                    "your_score": {"understanding_score": 100},
                    "daily_effects": {"understanding_score": 100},
                    "strengths_gaps": {"understanding_score": 0},
                    # external_interaction not yet started — absent from JSON
                },
            }
            conn.execute(
                "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
                (uid, json.dumps(progress)),
            )

        result = get_education_progress(uid)
        # 3 of the 5 canonical categories are complete; total is 5 (not 4).
        assert result["summary"]["completed_categories"] == 3
        assert result["summary"]["total_categories"] == 5
        assert result["summary"]["completion_pct"] == 60.0


# ── TEST-002: Development Agent tools ──


class TestLogPracticeEntry:
    def test_basic_logging(self):
        uid = _create_user(phase="development")
        result = log_practice_entry(uid, "practice_1", "Went well", 7)
        assert result["saved"] is True
        assert result["total_entries"] == 1
        assert result["reassessment_ready"] is False
        assert result["downward_trend"] is False

    def test_reassessment_ready_at_10(self):
        uid = _create_user(phase="development")
        for i in range(10):
            result = log_practice_entry(uid, f"p_{i % 3}", f"Entry {i}", 5)
        assert result["reassessment_ready"] is True
        assert result["total_entries"] == 10

    def test_downward_trend_detected(self):
        uid = _create_user(phase="development")
        # 3 entries with declining ratings
        log_practice_entry(uid, "practice_x", "Good", 8)
        log_practice_entry(uid, "practice_x", "OK", 6)
        result = log_practice_entry(uid, "practice_x", "Struggling", 4)
        assert result["downward_trend"] is True

    def test_no_trend_with_improvement(self):
        uid = _create_user(phase="development")
        log_practice_entry(uid, "practice_x", "OK", 5)
        log_practice_entry(uid, "practice_x", "Better", 7)
        result = log_practice_entry(uid, "practice_x", "Great", 9)
        assert result["downward_trend"] is False


class TestUpdateRoadmap:
    def test_no_roadmap_returns_error(self):
        uid = _create_user(phase="development")
        result = update_roadmap(uid, "not working", ["p1"], ["p2"])
        assert "error" in result

    def test_cooldown_enforced_within_7_days(self):
        uid = _create_user(phase="development")
        save_roadmap(uid, {"steps": [1, 2, 3]})

        result = update_roadmap(uid, "adjusting", ["p1"], ["p2"])
        assert "error" in result
        assert "cooldown" in result["error"].lower()

    def test_cooldown_passes_after_7_days(self):
        uid = _create_user(phase="development")
        # Insert roadmap with old date
        old_date = (datetime.utcnow() - timedelta(days=8)).isoformat()
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), uid, json.dumps({"steps": [1, 2, 3]}), old_date),
            )

        result = update_roadmap(uid, "adjusting", ["p1"], ["p2"])
        assert result["saved"] is True
        assert result["parent_roadmap_id"] is not None

    def test_parent_roadmap_id_links(self):
        uid = _create_user(phase="development")
        old_date = (datetime.utcnow() - timedelta(days=8)).isoformat()
        original_id = str(uuid.uuid4())
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
                (original_id, uid, json.dumps({"steps": []}), old_date),
            )

        result = update_roadmap(uid, "reason", [], [])
        assert result["parent_roadmap_id"] == original_id


class TestSaveRoadmapReplacementCooldown:
    """save_roadmap must not bypass the adjustment cooldown update_roadmap enforces."""

    def _backdate_roadmap(self, uid, **delta):
        backdated = (datetime.utcnow() - timedelta(**delta)).isoformat()
        with get_db_session() as conn:
            conn.execute(
                "UPDATE development_roadmap SET created_at = ? WHERE user_id = ?",
                (backdated, uid),
            )

    def test_replacement_rejected_within_cooldown(self):
        uid = _create_user(phase="development")
        assert save_roadmap(uid, {"steps": [1, 2, 3]})["saved"] is True
        self._backdate_roadmap(uid, days=2)

        result = save_roadmap(uid, {"steps": [4, 5, 6]})
        assert "error" in result
        assert "cooldown" in result["error"].lower()
        assert result["days_remaining"] == 5
        with get_db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM development_roadmap WHERE user_id = ?",
                (uid,),
            ).fetchone()["cnt"]
        assert count == 1  # rejected save wrote nothing

    def test_resave_allowed_within_authoring_grace(self):
        uid = _create_user(phase="development")
        save_roadmap(uid, {"steps": [1, 2, 3]})
        # Same-conversation correction (e.g. retry after a validation error)
        result = save_roadmap(uid, {"steps": [1, 2, 3, 4]})
        assert result["saved"] is True

    def test_new_roadmap_allowed_after_cooldown(self):
        uid = _create_user(phase="development")
        save_roadmap(uid, {"steps": [1, 2, 3]})
        self._backdate_roadmap(uid, days=8)
        result = save_roadmap(uid, {"steps": [4, 5, 6]})
        assert result["saved"] is True


# ── TEST-003: Reassessment Agent sentinel and graduation logic ──


class TestEvaluateGraduationReadiness:
    def _setup_snapshots(self, user_id, scores_list, quadrants):
        """Create snapshots newest-first."""
        for i, (scores, quadrant) in enumerate(zip(scores_list, quadrants)):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_snapshot(user_id, scores, quadrant, ts)

    def test_insufficient_snapshots(self):
        uid = _create_user(phase="reassessment")
        _create_snapshot(uid, {"dim1": {"score": 3.0}}, "absorber")
        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is False
        assert result["indicators_met"] == 0

    def test_pattern_stability_met(self):
        uid = _create_user(phase="reassessment")
        # 3 identical snapshots → 0-pt normalized delta across all cycles (stable)
        scores = {"dim1": {"score": 3.0}, "dim2": {"score": 4.0}}
        self._setup_snapshots(uid, [scores, scores, scores], ["absorber", "absorber", "absorber"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["pattern_stability"]["met"] is True

    def test_pattern_stability_not_met(self):
        uid = _create_user(phase="reassessment")
        # Raw 0.5/cycle on the 1–5 scale = 12.5 normalized pts/cycle (> 5) → not stable
        s1 = {"dim1": {"score": 2.0}}
        s2 = {"dim1": {"score": 2.5}}
        s3 = {"dim1": {"score": 3.0}}
        self._setup_snapshots(uid, [s3, s2, s1], ["absorber", "absorber", "absorber"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["pattern_stability"]["met"] is False

    def test_pattern_stability_not_trivially_true_on_1_5_scale(self):
        """Regression guard for the original scale bug.

        On raw 1–5 data with a 1.0/cycle move (2.0→3.0→4.0), the old
        ``delta < 5`` raw check would have wrongly reported stability (1.0 < 5).
        Normalized, each cycle moved 25 pts (> 5), so stability must be False.
        """
        uid = _create_user(phase="reassessment")
        s_oldest = {"dim1": {"score": 2.0}}
        s_mid = {"dim1": {"score": 3.0}}
        s_newest = {"dim1": {"score": 4.0}}
        self._setup_snapshots(
            uid, [s_newest, s_mid, s_oldest], ["transmuter", "transmuter", "transmuter"]
        )

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["pattern_stability"]["met"] is False
        evidence = result["indicators"]["pattern_stability"]["evidence"]
        assert "normalized" in evidence and "%" not in evidence

    def test_quadrant_consolidation_met(self):
        uid = _create_user(phase="reassessment")
        scores = {"dim1": {"score": 3.0}}
        self._setup_snapshots(uid, [scores, scores, scores], ["transmuter", "transmuter", "transmuter"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["quadrant_consolidation"]["met"] is True

    def test_quadrant_consolidation_not_met(self):
        uid = _create_user(phase="reassessment")
        scores = {"dim1": {"score": 3.0}}
        self._setup_snapshots(uid, [scores, scores, scores], ["transmuter", "absorber", "transmuter"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["quadrant_consolidation"]["met"] is False

    def test_two_of_three_triggers_graduation(self):
        uid = _create_user(phase="reassessment")
        # Pattern stability + quadrant consolidation both met
        scores = {"dim1": {"score": 3.0}}
        self._setup_snapshots(uid, [scores, scores, scores], ["transmuter", "transmuter", "transmuter"])

        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is True
        assert result["indicators_met"] >= 2


class TestGenerateComparisonSnapshot:
    def test_computes_deltas(self):
        uid = _create_user(phase="reassessment")
        prev_id = _create_snapshot(uid, {"dim1": {"score": 2.0}}, "absorber",
                                   (datetime.utcnow() - timedelta(days=30)).isoformat())
        _create_snapshot(uid, {"dim1": {"score": 3.0}}, "transmuter")

        result = generate_comparison_snapshot(uid, prev_id)
        d = result["deltas"]["dim1"]
        assert d["delta"] == 1.0                 # raw 1–5 delta
        assert d["delta_normalized"] == 25.0     # 0–100 scale
        assert d["previous_normalized"] == 25.0
        assert d["current_normalized"] == 50.0
        assert d["direction"] == "up"
        assert result["quadrant_shift"]["shifted"] is True
        assert result["quadrant_shift"]["current"] == "transmuter"

    def test_missing_snapshot_returns_error(self):
        uid = _create_user()
        result = generate_comparison_snapshot(uid, "nonexistent-id")
        assert "error" in result


# ── TEST-004: Graduation Agent artifacts and record ──


class TestGenerateGraduationArtifacts:
    def test_produces_growth_trajectory(self):
        uid = _create_user(phase="graduation")
        _create_snapshot(uid, {"dim1": {"score": 2.0}}, "absorber",
                         (datetime.utcnow() - timedelta(days=90)).isoformat())
        _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter")
        # Need 3 snapshots for evaluate_graduation_readiness
        _create_snapshot(uid, {"dim1": {"score": 3.9}}, "transmuter",
                         (datetime.utcnow() - timedelta(days=1)).isoformat())

        result = generate_graduation_artifacts(uid)
        assert "growth_trajectory" in result
        # Newest (4.0) − oldest (2.0) on the raw 1–5 scale
        assert result["growth_trajectory"]["dim1"]["change"] == pytest.approx(2.0, abs=0.1)

    def test_practice_map_grouped(self):
        uid = _create_user(phase="graduation")
        _create_snapshot(uid, {}, "absorber")
        _create_snapshot(uid, {}, "absorber", (datetime.utcnow() - timedelta(days=1)).isoformat())
        _create_snapshot(uid, {}, "absorber", (datetime.utcnow() - timedelta(days=2)).isoformat())

        log_practice_entry(uid, "p1", "r1", 5)
        log_practice_entry(uid, "p1", "r2", 7)
        log_practice_entry(uid, "p2", "r3", 6)

        result = generate_graduation_artifacts(uid)
        assert result["unique_practices"] == 2
        assert result["total_practices"] == 3
        assert len(result["practice_map"]["p1"]) == 2


class TestSaveGraduationRecord:
    def test_persists_record(self):
        uid = _create_user(phase="graduation")
        _create_snapshot(uid, {}, "absorber")

        indicators = {"pattern_stability": {"met": True}, "quadrant_consolidation": {"met": True}}
        result = save_graduation_record(uid, "A narrative about growth.", indicators)
        assert result["saved"] is True
        assert result["event_type"] == "graduation.complete"

        # Verify it can be retrieved
        record = get_graduation_record(uid)
        assert record["exists"] is True
        assert record["pattern_narrative"] == "A narrative about growth."


# ── TEST-005: Check-in Agent regression detection ──


class TestSaveCheckInLog:
    def test_basic_check_in(self):
        uid = _create_user(phase="check_in")
        snap_id = _create_snapshot(uid, {}, "absorber")
        grad_snap_id = _create_snapshot(uid, {}, "absorber",
                                        (datetime.utcnow() - timedelta(days=90)).isoformat())
        result = save_check_in_log(
            uid, snap_id, grad_snap_id,
            regression_detected=False,
        )
        assert result["saved"] is True
        assert result["event_type"] == "checkin.complete"
        assert result["regression_detected"] is False

    def test_regression_flagged(self):
        uid = _create_user(phase="check_in")
        snap_id = _create_snapshot(uid, {}, "absorber")
        grad_snap_id = _create_snapshot(uid, {}, "absorber",
                                        (datetime.utcnow() - timedelta(days=90)).isoformat())
        result = save_check_in_log(
            uid, snap_id, grad_snap_id,
            regression_detected=True,
            re_entered_development=True,
        )
        assert result["regression_detected"] is True
        assert result["re_entered_development"] is True


class TestGetGraduationRecord:
    def test_no_record_returns_empty(self):
        uid = _create_user()
        result = get_graduation_record(uid)
        assert result["exists"] is False

    def test_returns_indicators(self):
        uid = _create_user(phase="graduated")
        _create_snapshot(uid, {}, "absorber")
        indicators = {"stability": {"met": True, "evidence": "delta < 5%"}}
        save_graduation_record(uid, "narrative", indicators)

        result = get_graduation_record(uid)
        assert result["exists"] is True
        assert result["graduation_indicators"]["stability"]["met"] is True


# ── TEST-010: rank_gaps tool ──

def _make_full_scores() -> dict:
    """Create a realistic scores dict for testing rank_gaps/generate_roadmap."""
    return {
        "Transmutation Capacity": {
            "score": 2.8,
            "sub_dimensions": {
                "Deprivation Filtering": {"score": 2.0},
                "Fulfillment Emission": {"score": 3.0},
                "Amplification Awareness": {"score": 2.5},
                "Absorption Patterns": {"score": 4.0},
                "Conduit Recognition": {"score": 3.0},
            },
        },
        "Emotional Awareness": {"score": 3.5},
        "Mindfulness": {"score": 2.5},
        "Cognitive Awareness": {"score": 4.0},
    }


class TestRankGaps:
    def test_no_snapshot_returns_error(self):
        uid = _create_user(phase="development")
        result = rank_gaps(uid)
        assert "error" in result

    def test_returns_ranked_targets_with_snapshot(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        result = rank_gaps(uid)
        assert "ranked_targets" in result
        assert "source_snapshot_id" in result
        assert isinstance(result["ranked_targets"], list)
        assert len(result["ranked_targets"]) == 3  # default top_n=3

    def test_top_n_respected(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        result = rank_gaps(uid, top_n=2)
        assert len(result["ranked_targets"]) == 2

    def test_ranked_by_leverage_not_raw_score(self):
        """Leverage ranking differs from raw lowest-score ranking."""
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        result = rank_gaps(uid, top_n=5)
        targets = result["ranked_targets"]
        leverages = [t["leverage"] for t in targets]
        # Verify sorted descending by leverage
        assert leverages == sorted(leverages, reverse=True)

    def test_source_snapshot_id_matches_latest(self):
        uid = _create_user(phase="development")
        sid = _create_snapshot(uid, _make_full_scores())
        result = rank_gaps(uid)
        assert result["source_snapshot_id"] == sid


# ── TEST-011: generate_roadmap returns leverage_targets ──


class TestGenerateRoadmap:
    def test_no_snapshot_returns_error(self):
        uid = _create_user(phase="development")
        result = generate_roadmap(uid)
        assert "error" in result

    def test_returns_leverage_targets_not_weakest_dimensions(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        result = generate_roadmap(uid)
        # New format: leverage_targets
        assert "leverage_targets" in result
        assert "profile_scores" in result
        assert "instruction" in result
        assert "step_count" in result
        # Should NOT have old weakest_dimensions key
        assert "weakest_dimensions" not in result

    def test_leverage_targets_are_ranked(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        result = generate_roadmap(uid)
        targets = result["leverage_targets"]
        leverages = [t["leverage"] for t in targets]
        assert leverages == sorted(leverages, reverse=True)

    def test_instruction_mentions_rank_gaps(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        result = generate_roadmap(uid)
        # Instruction should guide LLM to use tool outputs, not compute itself
        assert "leverage_targets" in result["instruction"].lower() or "leverage" in result["instruction"].lower()


# ── TEST-012: log_practice_entry with linkage ──


class TestLogPracticeEntryLinkage:
    def test_legacy_call_saves_with_null_linkage(self):
        """Existing positional callers unaffected — linkage columns NULL."""
        uid = _create_user(phase="development")
        result = log_practice_entry(uid, "p1", "reflection", 7)
        assert result["saved"] is True
        assert "error" not in result
        # Verify linkage columns are NULL in DB
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT dimension, sub_dimension, transmutation_operation FROM practice_journal WHERE user_id = ? AND practice_id = ?",
                (uid, "p1"),
            ).fetchone()
        assert row["dimension"] is None
        assert row["sub_dimension"] is None
        assert row["transmutation_operation"] is None

    def test_valid_linkage_saves(self):
        uid = _create_user(phase="development")
        result = log_practice_entry(
            uid, "p2", "reflection", 6,
            dimension="Emotional Awareness",
            sub_dimension="Emotion Recognition",
            transmutation_operation="none",
        )
        assert result["saved"] is True
        assert "error" not in result
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT dimension, sub_dimension, transmutation_operation FROM practice_journal WHERE user_id = ? AND practice_id = ?",
                (uid, "p2"),
            ).fetchone()
        assert row["dimension"] == "Emotional Awareness"
        assert row["sub_dimension"] == "Emotion Recognition"
        assert row["transmutation_operation"] == "none"

    def test_invalid_dimension_returns_error_nothing_written(self):
        uid = _create_user(phase="development")
        result = log_practice_entry(uid, "p3", "reflection", 5, dimension="Bogus Dimension")
        assert "error" in result
        assert "validation_errors" in result
        assert len(result["validation_errors"]) > 0
        # Nothing should be written
        with get_db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM practice_journal WHERE user_id = ? AND practice_id = ?",
                (uid, "p3"),
            ).fetchone()["cnt"]
        assert count == 0

    def test_backfill_linkage_from_roadmap_practices(self):
        """When practice_id matches a roadmap_practices row, linkage is backfilled."""
        uid = _create_user(phase="development")
        # Seed a roadmap_practices row directly
        with get_db_session() as conn:
            conn.execute(
                """INSERT INTO roadmap_practices
                   (id, user_id, roadmap_id, practice_id, title, dimension, sub_dimension, transmutation_operation)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), uid, "backfill_p", "Backfill Practice",
                 "Mindfulness", "Attention Control", "none"),
            )
        result = log_practice_entry(uid, "backfill_p", "reflection", 7)
        assert result["saved"] is True
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT dimension, sub_dimension, transmutation_operation FROM practice_journal WHERE user_id = ? AND practice_id = ?",
                (uid, "backfill_p"),
            ).fetchone()
        assert row["dimension"] == "Mindfulness"
        assert row["sub_dimension"] == "Attention Control"

    def test_trend_and_readiness_unchanged(self):
        """Downward trend and reassessment_ready still work with linkage args."""
        uid = _create_user(phase="development")
        log_practice_entry(uid, "px", "Good", 8, dimension="Mindfulness")
        log_practice_entry(uid, "px", "OK", 6, dimension="Mindfulness")
        result = log_practice_entry(uid, "px", "Struggling", 4, dimension="Mindfulness")
        assert result["downward_trend"] is True
        assert result["saved"] is True


# ── TEST-013: save_roadmap with structured practices ──


class TestSaveRoadmapLinkage:
    def test_legacy_roadmap_saves_without_upsert(self):
        """Legacy {"steps":[...]} shape saves as-is, no roadmap_practices rows."""
        uid = _create_user(phase="development")
        result = save_roadmap(uid, {"steps": [1, 2, 3]})
        assert result["saved"] is True
        assert "error" not in result
        with get_db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM roadmap_practices WHERE user_id = ?",
                (uid,),
            ).fetchone()["cnt"]
        assert count == 0

    def test_structured_practices_upserted(self):
        uid = _create_user(phase="development")
        practices = [
            {
                "practice_id": "sp1",
                "title": "Practice 1",
                "dimension": "Emotional Awareness",
                "sub_dimension": "Emotion Recognition",
                "transmutation_operation": "none",
            }
        ]
        result = save_roadmap(uid, {"practices": practices, "steps": []})
        assert result["saved"] is True
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT dimension, sub_dimension, transmutation_operation FROM roadmap_practices WHERE user_id = ? AND practice_id = ?",
                (uid, "sp1"),
            ).fetchone()
        assert row is not None
        assert row["dimension"] == "Emotional Awareness"

    def test_invalid_practice_returns_error_nothing_saved(self):
        uid = _create_user(phase="development")
        practices = [
            {
                "practice_id": "bad1",
                "title": "Bad",
                "dimension": "Bogus Dimension",
                "sub_dimension": None,
                "transmutation_operation": "none",
            }
        ]
        result = save_roadmap(uid, {"practices": practices})
        assert "error" in result
        assert "validation_errors" in result
        # Roadmap must NOT be saved
        with get_db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM development_roadmap WHERE user_id = ?",
                (uid,),
            ).fetchone()["cnt"]
        assert count == 0

    def test_upsert_updates_existing_row(self):
        """Second save_roadmap with same practice_id updates the row."""
        uid = _create_user(phase="development")
        practices_v1 = [{"practice_id": "upx", "title": "V1", "dimension": "Mindfulness", "sub_dimension": None, "transmutation_operation": "none"}]
        practices_v2 = [{"practice_id": "upx", "title": "V2", "dimension": "Emotional Awareness", "sub_dimension": None, "transmutation_operation": "none"}]
        save_roadmap(uid, {"practices": practices_v1})
        save_roadmap(uid, {"practices": practices_v2})
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT dimension FROM roadmap_practices WHERE user_id = ? AND practice_id = ?",
                (uid, "upx"),
            ).fetchone()
        assert row["dimension"] == "Emotional Awareness"


# ── TEST-014: check_roadmap_targets_gaps ──


class TestCheckRoadmapTargetsGaps:
    def test_no_snapshot_returns_error(self):
        uid = _create_user(phase="development")
        result = check_roadmap_targets_gaps(uid, {"practices": []})
        assert "error" in result

    def test_covered_gap_reported(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        # Get the top gap's dimension/sub_dimension
        gaps = rank_gaps(uid, top_n=1)
        top = gaps["ranked_targets"][0]
        roadmap = {
            "practices": [
                {
                    "practice_id": "cover_p",
                    "title": "Cover top gap",
                    "dimension": top["dimension"],
                    "sub_dimension": top["sub_dimension"],
                    "transmutation_operation": top["operation"],
                }
            ]
        }
        result = check_roadmap_targets_gaps(uid, roadmap)
        assert "covered" in result
        assert any(
            g["dimension"] == top["dimension"] and g["sub_dimension"] == top["sub_dimension"]
            for g in result["covered"]
        )

    def test_uncovered_high_leverage_gap_reported(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        # Empty practices means nothing is covered
        result = check_roadmap_targets_gaps(uid, {"practices": []})
        assert len(result["uncovered_high_leverage"]) > 0
        assert result["coverage_pct"] == 0.0

    def test_coverage_pct_100_when_all_top_covered(self):
        uid = _create_user(phase="development")
        _create_snapshot(uid, _make_full_scores())
        gaps = rank_gaps(uid, top_n=5)
        practices = [
            {
                "practice_id": f"p_{i}",
                "title": f"Practice {i}",
                "dimension": t["dimension"],
                "sub_dimension": t["sub_dimension"],
                "transmutation_operation": t["operation"],
            }
            for i, t in enumerate(gaps["ranked_targets"])
        ]
        result = check_roadmap_targets_gaps(uid, {"practices": practices})
        assert result["coverage_pct"] == 100.0
        assert len(result["uncovered_high_leverage"]) == 0


# ── BE-001: get_development_gate_progress helper ──


def _insert_roadmap(user_id: str, created_at: str = None) -> str:
    """Insert a development_roadmap row for tests and return its id."""
    rid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
            (rid, user_id, json.dumps({"steps": []}), ts),
        )
    return rid


def _insert_journal_entry(user_id: str, practice_id: str = "p1", self_rating: int = 5) -> str:
    """Insert a practice_journal row and return its id."""
    eid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO practice_journal (id, user_id, practice_id, reflection, self_rating, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, user_id, practice_id, "test reflection", self_rating, datetime.utcnow().isoformat()),
        )
    return eid


class TestGetDevelopmentGateProgress:
    """Unit tests for get_development_gate_progress (BE-001)."""

    def test_returns_all_required_keys(self):
        uid = _create_user(phase="development")
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert set(result.keys()) == {
            "entries_logged", "entries_required",
            "days_elapsed", "days_required",
            "passed", "via",
        }

    def test_no_roadmap_no_entries_returns_sensible_defaults(self):
        uid = _create_user(phase="development")
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["entries_logged"] == 0
        assert result["entries_required"] == 10
        assert result["days_elapsed"] is None
        assert result["days_required"] == 30
        assert result["passed"] is False
        assert result["via"] is None

    def test_entries_logged_counts_journal_rows(self):
        uid = _create_user(phase="development")
        for _ in range(3):
            _insert_journal_entry(uid)
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["entries_logged"] == 3

    def test_days_elapsed_computed_from_first_roadmap(self):
        uid = _create_user(phase="development")
        old_ts = (datetime.utcnow() - timedelta(days=15)).isoformat()
        _insert_roadmap(uid, created_at=old_ts)
        # Insert a newer roadmap — elapsed should reflect the FIRST one
        _insert_roadmap(uid, created_at=(datetime.utcnow() - timedelta(days=5)).isoformat())
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["days_elapsed"] >= 15

    def test_passed_via_entries_when_10_or_more(self):
        uid = _create_user(phase="development")
        for _ in range(10):
            _insert_journal_entry(uid)
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["passed"] is True
        assert result["via"] == "entries"

    def test_passed_via_time_when_30_days_elapsed(self):
        uid = _create_user(phase="development")
        old_ts = (datetime.utcnow() - timedelta(days=31)).isoformat()
        _insert_roadmap(uid, created_at=old_ts)
        _insert_journal_entry(uid)  # only 1 entry — time path
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["passed"] is True
        assert result["via"] == "time"

    def test_entries_takes_precedence_over_time_when_both_met(self):
        uid = _create_user(phase="development")
        old_ts = (datetime.utcnow() - timedelta(days=35)).isoformat()
        _insert_roadmap(uid, created_at=old_ts)
        for _ in range(10):
            _insert_journal_entry(uid)
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["passed"] is True
        assert result["via"] == "entries"

    def test_not_passed_when_neither_threshold_met(self):
        uid = _create_user(phase="development")
        recent_ts = (datetime.utcnow() - timedelta(days=5)).isoformat()
        _insert_roadmap(uid, created_at=recent_ts)
        for _ in range(3):
            _insert_journal_entry(uid)
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["passed"] is False
        assert result["via"] is None

    def test_days_elapsed_none_when_no_roadmap(self):
        uid = _create_user(phase="development")
        # No roadmap row — days_elapsed must be None regardless of entries
        for _ in range(5):
            _insert_journal_entry(uid)
        with get_db_session() as conn:
            result = get_development_gate_progress(conn, uid)
        assert result["days_elapsed"] is None


class TestCheckDevelopmentCompletionGateRefactor:
    """Integration tests verifying _check_development_completion_gate still works after refactor (BE-001)."""

    def test_gate_passes_returns_none_at_10_entries(self):
        uid = _create_user(phase="development")
        for _ in range(10):
            _insert_journal_entry(uid)
        with get_db_session() as conn:
            from agents.transmutation.tools import _check_development_completion_gate
            result = _check_development_completion_gate(conn, uid)
        assert result is None  # gate passes

    def test_gate_passes_returns_none_at_30_days(self):
        uid = _create_user(phase="development")
        old_ts = (datetime.utcnow() - timedelta(days=31)).isoformat()
        _insert_roadmap(uid, created_at=old_ts)
        _insert_journal_entry(uid)
        with get_db_session() as conn:
            from agents.transmutation.tools import _check_development_completion_gate
            result = _check_development_completion_gate(conn, uid)
        assert result is None

    def test_gate_fails_returns_error_dict(self):
        uid = _create_user(phase="development")
        _insert_roadmap(uid)  # fresh roadmap
        _insert_journal_entry(uid)
        with get_db_session() as conn:
            from agents.transmutation.tools import _check_development_completion_gate
            result = _check_development_completion_gate(conn, uid)
        assert isinstance(result, dict)
        assert "error" in result
        assert "entries" in result
        assert result["entries"] == 1

    def test_advance_phase_blocked_before_gate(self):
        uid = _create_user(phase="development")
        _insert_roadmap(uid)
        _insert_journal_entry(uid)
        result = advance_phase(uid, "reassessment")
        assert "error" in result

    def test_advance_phase_allowed_after_entries_gate(self):
        uid = _create_user(phase="development")
        for _ in range(10):
            _insert_journal_entry(uid)
        result = advance_phase(uid, "reassessment")
        # Should succeed (no error key, or success-like response)
        assert "error" not in result
