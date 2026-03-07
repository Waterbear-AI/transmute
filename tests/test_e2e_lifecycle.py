"""E2E tests for the full Transmutation Engine lifecycle.

Tests complete user journeys through all phases:
Education → Development → Reassessment → Graduation → Check-in.
Each class simulates a realistic user flow end-to-end.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from db.database import get_db_session
from agents.transmutation.tools import (
    record_comprehension_answer,
    get_education_progress,
    log_practice_entry,
    save_roadmap,
    update_roadmap,
    get_development_roadmap,
    generate_comparison_snapshot,
    evaluate_graduation_readiness,
    generate_graduation_artifacts,
    save_graduation_record,
    get_graduation_record,
    save_check_in_log,
    advance_phase,
    get_user_profile,
)


def _create_user(phase: str = "orientation") -> str:
    """Create a test user and return user_id."""
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "E2E User", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _create_profile_snapshot(user_id: str, scores: dict, quadrant: str = "absorber", created_at: str = None) -> str:
    """Create a profile snapshot and return its ID."""
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"quadrant": quadrant}), ts),
        )
    return sid


def _seed_education_progress(user_id: str, progress: dict):
    """Seed education_progress table directly."""
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
            (user_id, json.dumps(progress)),
        )


# ── Education Journey ──────────────────────────────


class TestEducationJourney:
    """Complete education flow: answer comprehension checks, track progress, verify gate."""

    def test_full_education_flow(self):
        """User answers comprehension checks across dimensions, scores accumulate correctly."""
        uid = _create_user(phase="education")

        # Answer first question correctly
        r1 = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "b",
        )
        assert r1["correct"] is True
        assert r1["score"] == 100

        # Check progress shows the answer
        progress = get_education_progress(uid)
        assert progress["exists"] is True
        ea_cat1 = progress["progress"]["Emotional Awareness"]["what_this_means"]
        assert "cc_ea_cat1_q1" in ea_cat1["questions_answered"]
        assert ea_cat1["understanding_score"] == 100

    def test_wrong_answer_reduces_score(self):
        """Wrong answer after correct answer brings score to 50%."""
        uid = _create_user(phase="education")

        # Correct first
        record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "b",
        )

        # Wrong second (different question)
        r2 = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q2", "a",  # Wrong answer
        )
        # Score = 1 correct / 2 answered * 100 = 50
        assert r2["score"] == 50

    def test_education_gate_blocks_without_progress(self):
        """Cannot advance to development without sufficient education progress."""
        uid = _create_user(phase="education")
        result = advance_phase(uid, "development")
        assert "error" in result or result.get("success") is False


# ── Development Journey ──────────────────────────────


class TestDevelopmentJourney:
    """Complete development flow: roadmap → practice → readiness check."""

    def test_roadmap_and_practice_flow(self):
        """Save roadmap, log practices, verify counts accumulate."""
        uid = _create_user(phase="development")

        # Save a roadmap
        roadmap = {"steps": [
            {"title": "Daily awareness meditation", "dimension": "Emotional Awareness"},
            {"title": "Boundary journaling", "dimension": "Physical Awareness"},
            {"title": "Values reflection", "dimension": "Spiritual Awareness"},
        ]}
        save_roadmap(uid, roadmap)

        # Verify roadmap saved
        rm = get_development_roadmap(uid)
        assert rm["exists"] is True
        assert len(rm["roadmap"]["steps"]) == 3

        # Log 3 practice entries
        for i in range(3):
            result = log_practice_entry(uid, "practice_1", f"Reflection {i}", 5 + i)
            assert result["saved"] is True

        assert result["total_entries"] == 3
        assert result["reassessment_ready"] is False

    def test_reassessment_readiness_at_10_entries(self):
        """After 10 practice entries, reassessment_ready flag is set."""
        uid = _create_user(phase="development")

        for i in range(10):
            result = log_practice_entry(uid, f"p_{i % 3}", f"Entry {i}", 5)

        assert result["reassessment_ready"] is True
        assert result["total_entries"] == 10

    def test_roadmap_cooldown_prevents_early_update(self):
        """Cannot update roadmap within 7 days of creation."""
        uid = _create_user(phase="development")
        save_roadmap(uid, {"steps": [1, 2, 3]})

        result = update_roadmap(uid, "want to change", ["p1"], ["p2"])
        assert "error" in result
        assert "cooldown" in result["error"].lower()

    def test_roadmap_update_after_cooldown(self):
        """Roadmap update succeeds after 7-day cooldown with parent link."""
        uid = _create_user(phase="development")

        # Insert roadmap with old date
        old_date = (datetime.utcnow() - timedelta(days=8)).isoformat()
        original_id = str(uuid.uuid4())
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
                (original_id, uid, json.dumps({"steps": [1, 2, 3]}), old_date),
            )

        result = update_roadmap(uid, "adjusting focus", ["p1"], ["p2"])
        assert result["saved"] is True
        assert result["parent_roadmap_id"] == original_id

    def test_downward_trend_detection(self):
        """3 entries with declining ratings triggers downward trend flag."""
        uid = _create_user(phase="development")

        log_practice_entry(uid, "practice_x", "Started well", 8)
        log_practice_entry(uid, "practice_x", "Getting harder", 6)
        result = log_practice_entry(uid, "practice_x", "Really struggling", 4)

        assert result["downward_trend"] is True


# ── Reassessment → Graduation Journey ──────────────────


class TestReassessmentToGraduationJourney:
    """Complete flow: reassessment → graduation readiness → graduation."""

    def _setup_stable_snapshots(self, uid):
        """Create 3 snapshots with stable scores (pattern stability met)."""
        scores = {"dim1": {"score": 65}, "dim2": {"score": 70}}
        for i in range(3):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, scores, "transmuter", ts)

    def test_graduation_readiness_with_stable_pattern(self):
        """Pattern stability + quadrant consolidation → graduation ready."""
        uid = _create_user(phase="reassessment")
        self._setup_stable_snapshots(uid)

        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is True
        assert result["indicators"]["pattern_stability"]["met"] is True
        assert result["indicators"]["quadrant_consolidation"]["met"] is True
        assert result["indicators_met"] >= 2

    def test_graduation_not_ready_with_shifting_scores(self):
        """Large score changes → graduation not ready."""
        uid = _create_user(phase="reassessment")
        s1 = {"dim1": {"score": 40}}
        s2 = {"dim1": {"score": 55}}
        s3 = {"dim1": {"score": 70}}

        for i, scores in enumerate([s3, s2, s1]):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, scores, "absorber", ts)

        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is False

    def test_comparison_snapshot_shows_deltas(self):
        """Comparison between two snapshots shows correct deltas."""
        uid = _create_user(phase="reassessment")
        prev_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 40}}, "absorber",
            (datetime.utcnow() - timedelta(days=30)).isoformat(),
        )
        _create_profile_snapshot(uid, {"dim1": {"score": 60}}, "transmuter")

        result = generate_comparison_snapshot(uid, prev_id)
        assert result["deltas"]["dim1"]["delta"] == 20
        assert result["deltas"]["dim1"]["direction"] == "up"
        assert result["quadrant_shift"]["shifted"] is True

    def test_full_graduation_flow(self):
        """Complete graduation: readiness check → artifacts → save record."""
        uid = _create_user(phase="graduation")
        self._setup_stable_snapshots(uid)

        # Generate artifacts
        artifacts = generate_graduation_artifacts(uid)
        assert "growth_trajectory" in artifacts

        # Save graduation record
        indicators = {
            "pattern_stability": {"met": True, "evidence": "< 5% delta for 2 cycles"},
            "quadrant_consolidation": {"met": True, "evidence": "transmuter x2"},
        }
        result = save_graduation_record(
            uid, "A narrative about personal growth.", indicators,
        )
        assert result["saved"] is True
        assert result["event_type"] == "graduation.complete"

        # Verify record persisted
        record = get_graduation_record(uid)
        assert record["exists"] is True
        assert record["pattern_narrative"] == "A narrative about personal growth."
        assert record["graduation_indicators"]["pattern_stability"]["met"] is True


# ── Check-in Journey ──────────────────────────────


class TestCheckInJourney:
    """Post-graduation check-in: full reassessment, regression detection."""

    def test_check_in_without_regression(self):
        """Normal check-in: no regression detected."""
        uid = _create_user(phase="check_in")
        snap_id = _create_profile_snapshot(uid, {"dim1": {"score": 70}}, "transmuter")
        grad_snap_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 65}}, "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )

        result = save_check_in_log(uid, snap_id, grad_snap_id, regression_detected=False)
        assert result["saved"] is True
        assert result["regression_detected"] is False
        assert result["event_type"] == "checkin.complete"

    def test_check_in_with_regression_and_reentry(self):
        """Regression detected: user offered re-entry to development."""
        uid = _create_user(phase="check_in")
        snap_id = _create_profile_snapshot(uid, {"dim1": {"score": 40}}, "absorber")
        grad_snap_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 65}}, "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )

        result = save_check_in_log(
            uid, snap_id, grad_snap_id,
            regression_detected=True,
            re_entered_development=True,
        )
        assert result["regression_detected"] is True
        assert result["re_entered_development"] is True

    def test_check_in_without_reentry(self):
        """Regression detected but user declines re-entry."""
        uid = _create_user(phase="check_in")
        snap_id = _create_profile_snapshot(uid, {"dim1": {"score": 45}}, "absorber")
        grad_snap_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 65}}, "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )

        result = save_check_in_log(
            uid, snap_id, grad_snap_id,
            regression_detected=True,
            re_entered_development=False,
        )
        assert result["regression_detected"] is True
        assert result["re_entered_development"] is False


# ── Full Lifecycle Journey ──────────────────────────────


class TestFullLifecycleJourney:
    """End-to-end test: a single user goes through the entire lifecycle."""

    def test_orientation_to_graduation(self):
        """Single user flows: education → development → graduation → check-in."""
        uid = _create_user(phase="education")

        # ── Phase 1: Education ──
        # Answer a comprehension check
        r = record_comprehension_answer(
            uid, "Emotional Awareness", "what_this_means",
            "cc_ea_cat1_q1", "b",
        )
        assert r["correct"] is True

        progress = get_education_progress(uid)
        assert progress["exists"] is True

        # ── Phase 2: Development ──
        # Advance to development (manually set phase for E2E)
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        save_roadmap(uid, {"steps": [
            {"title": "Step 1", "dimension": "Emotional Awareness"},
            {"title": "Step 2", "dimension": "Physical Awareness"},
            {"title": "Step 3", "dimension": "Spiritual Awareness"},
        ]})

        for i in range(5):
            log_practice_entry(uid, "step_1_practice", f"Day {i+1} reflection", 5 + i)

        rm = get_development_roadmap(uid)
        assert rm["exists"] is True

        # ── Phase 3: Reassessment ──
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'reassessment' WHERE id = ?", (uid,))

        # Create snapshots showing stability
        scores = {"dim1": {"score": 65}, "dim2": {"score": 70}}
        for i in range(3):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, scores, "transmuter", ts)

        readiness = evaluate_graduation_readiness(uid)
        assert readiness["graduation_ready"] is True

        # ── Phase 4: Graduation ──
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'graduation' WHERE id = ?", (uid,))

        artifacts = generate_graduation_artifacts(uid)
        assert "growth_trajectory" in artifacts

        indicators = {
            "pattern_stability": {"met": True},
            "quadrant_consolidation": {"met": True},
        }
        grad_result = save_graduation_record(uid, "My growth story.", indicators)
        assert grad_result["saved"] is True

        record = get_graduation_record(uid)
        assert record["exists"] is True
        assert record["pattern_narrative"] == "My growth story."

        # ── Phase 5: Check-in ──
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'check_in' WHERE id = ?", (uid,))

        snap_id = _create_profile_snapshot(uid, scores, "transmuter")
        grad_snap_id = _create_profile_snapshot(
            uid, scores, "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )

        checkin = save_check_in_log(uid, snap_id, grad_snap_id, regression_detected=False)
        assert checkin["saved"] is True
        assert checkin["regression_detected"] is False


# ── Results API Journey ──────────────────────────────


class TestResultsAPIJourney:
    """Verify the Results API aggregates data correctly across phases."""

    def test_results_endpoint_returns_all_phase_data(self, authenticated_client):
        uid = authenticated_client.user_id

        # Seed education progress
        _seed_education_progress(uid, {
            "Emotional Awareness": {
                "what_this_means": {"understanding_score": 80},
            }
        })

        # Seed development data
        save_roadmap(uid, {"steps": [1, 2, 3]})
        log_practice_entry(uid, "p1", "reflection", 7)

        # Seed profile snapshot
        _create_profile_snapshot(uid, {"dim1": {"score": 50}}, "absorber")

        # Fetch results
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["user_id"] == uid
        assert data["education"]["exists"] is True
        assert data["development"]["has_roadmap"] is True
        assert data["development"]["practice_count"] == 1
        assert len(data["profiles"]) >= 1
