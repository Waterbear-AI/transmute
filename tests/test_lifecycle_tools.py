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
    generate_comparison_snapshot,
    evaluate_graduation_readiness,
    generate_graduation_artifacts,
    save_graduation_record,
    save_check_in_log,
    get_graduation_record,
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
    """Helper: insert a profile snapshot and return snapshot_id."""
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"quadrant": quadrant}), ts),
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
            progress = {
                "dim1": {
                    "cat1": {"understanding_score": 80},
                    "cat2": {"understanding_score": 50},
                },
                "dim2": {
                    "cat1": {"understanding_score": 90},
                },
            }
            conn.execute(
                "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
                (uid, json.dumps(progress)),
            )

        result = get_education_progress(uid)
        assert result["exists"] is True
        # 2 of 3 categories >= 70 (80, 90)
        assert result["summary"]["completed_categories"] == 2
        assert result["summary"]["total_categories"] == 3


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


# ── TEST-003: Reassessment Agent sentinel and graduation logic ──


class TestEvaluateGraduationReadiness:
    def _setup_snapshots(self, user_id, scores_list, quadrants):
        """Create snapshots newest-first."""
        for i, (scores, quadrant) in enumerate(zip(scores_list, quadrants)):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_snapshot(user_id, scores, quadrant, ts)

    def test_insufficient_snapshots(self):
        uid = _create_user(phase="reassessment")
        _create_snapshot(uid, {"dim1": {"score": 50}}, "absorber")
        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is False
        assert result["indicators_met"] == 0

    def test_pattern_stability_met(self):
        uid = _create_user(phase="reassessment")
        # 3 snapshots with <5% delta across all cycles
        scores = {"dim1": {"score": 50}, "dim2": {"score": 70}}
        self._setup_snapshots(uid, [scores, scores, scores], ["absorber", "absorber", "absorber"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["pattern_stability"]["met"] is True

    def test_pattern_stability_not_met(self):
        uid = _create_user(phase="reassessment")
        s1 = {"dim1": {"score": 50}}
        s2 = {"dim1": {"score": 60}}  # 10% delta
        s3 = {"dim1": {"score": 70}}
        self._setup_snapshots(uid, [s3, s2, s1], ["absorber", "absorber", "absorber"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["pattern_stability"]["met"] is False

    def test_quadrant_consolidation_met(self):
        uid = _create_user(phase="reassessment")
        scores = {"dim1": {"score": 50}}
        self._setup_snapshots(uid, [scores, scores, scores], ["transmuter", "transmuter", "transmuter"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["quadrant_consolidation"]["met"] is True

    def test_quadrant_consolidation_not_met(self):
        uid = _create_user(phase="reassessment")
        scores = {"dim1": {"score": 50}}
        self._setup_snapshots(uid, [scores, scores, scores], ["transmuter", "absorber", "transmuter"])

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["quadrant_consolidation"]["met"] is False

    def test_two_of_three_triggers_graduation(self):
        uid = _create_user(phase="reassessment")
        # Pattern stability + quadrant consolidation both met
        scores = {"dim1": {"score": 50}}
        self._setup_snapshots(uid, [scores, scores, scores], ["transmuter", "transmuter", "transmuter"])

        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is True
        assert result["indicators_met"] >= 2


class TestGenerateComparisonSnapshot:
    def test_computes_deltas(self):
        uid = _create_user(phase="reassessment")
        prev_id = _create_snapshot(uid, {"dim1": {"score": 40}}, "absorber",
                                   (datetime.utcnow() - timedelta(days=30)).isoformat())
        _create_snapshot(uid, {"dim1": {"score": 55}}, "transmuter")

        result = generate_comparison_snapshot(uid, prev_id)
        assert result["deltas"]["dim1"]["delta"] == 15
        assert result["deltas"]["dim1"]["direction"] == "up"
        assert result["quadrant_shift"]["shifted"] is True

    def test_missing_snapshot_returns_error(self):
        uid = _create_user()
        result = generate_comparison_snapshot(uid, "nonexistent-id")
        assert "error" in result


# ── TEST-004: Graduation Agent artifacts and record ──


class TestGenerateGraduationArtifacts:
    def test_produces_growth_trajectory(self):
        uid = _create_user(phase="graduation")
        _create_snapshot(uid, {"dim1": {"score": 30}}, "absorber",
                         (datetime.utcnow() - timedelta(days=90)).isoformat())
        _create_snapshot(uid, {"dim1": {"score": 65}}, "transmuter")
        # Need 3 snapshots for evaluate_graduation_readiness
        _create_snapshot(uid, {"dim1": {"score": 64}}, "transmuter",
                         (datetime.utcnow() - timedelta(days=1)).isoformat())

        result = generate_graduation_artifacts(uid)
        assert "growth_trajectory" in result
        assert result["growth_trajectory"]["dim1"]["change"] == pytest.approx(35, abs=1)

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
