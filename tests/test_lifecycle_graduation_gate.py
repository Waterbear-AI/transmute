"""Integration tests for TEST-001: lifecycle lifecycle graduation gate, widened save paths, and
record_self_assessed_readiness across reassessment → graduation → check-in transitions.

These tests exercise the full tool chain as the agents call it — using tools.advance_phase
rather than direct DB writes — so gate enforcement is actually exercised.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from db.database import get_db_session
from agents.transmutation.tools import (
    advance_phase,
    evaluate_graduation_readiness,
    record_self_assessed_readiness,
    save_assessment_response,
    generate_graduation_artifacts,
    save_graduation_record,
    get_graduation_record,
)
from agents.transmutation.question_bank import get_question_bank


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user(phase: str = "reassessment") -> str:
    """Insert a test user in the given phase and return its user_id."""
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase)"
            " VALUES (?, ?, ?, ?, ?)",
            (uid, "Gate User", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _insert_snapshot(user_id: str, scores: dict, archetype: str = "transmuter",
                     created_at: str | None = None) -> str:
    """Insert a profile snapshot directly and return its snapshot_id."""
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots"
            " (id, user_id, scores, quadrant_placement, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"archetype": archetype}), ts),
        )
    return sid


def _stable_scores(value: float = 4.0) -> dict:
    return {"dim1": {"score": value}, "dim2": {"score": value + 0.5}}


def _seed_3_stable_snapshots(user_id: str, archetype: str = "transmuter") -> None:
    """Seed the minimum 3 snapshots with stable scores (pattern_stability + quadrant_consolidation met)."""
    for i in range(3):
        ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
        _insert_snapshot(user_id, _stable_scores(), archetype, ts)


def _get_first_question_id() -> str | None:
    qb = get_question_bank()
    questions = qb.get_all_questions()
    return questions[0]["id"] if questions else None


# ── Gate enforcement via advance_phase ────────────────────────────────────────


class TestGraduationGateEnforcement:
    """advance_phase('graduation') enforces the 2-of-3 convergence indicator gate."""

    def test_gate_passes_with_2_of_3_indicators_met(self):
        """advance_phase('graduation') succeeds when 2/3 indicators are met (no self-assessed)."""
        uid = _create_user(phase="reassessment")
        # 3 stable snapshots with same archetype → pattern_stability + quadrant_consolidation met (2/3)
        _seed_3_stable_snapshots(uid)

        # self_assessed_readiness is NOT set — but 2 other indicators are met
        result = advance_phase(uid, "graduation")
        assert "error" not in result, (
            f"advance_phase('graduation') must pass when 2/3 indicators are met. Got: {result}"
        )

    def test_gate_passes_after_record_self_assessed_readiness(self):
        """advance_phase('graduation') also succeeds after recording self-assessed readiness (redundant indicator)."""
        uid = _create_user(phase="reassessment")
        _seed_3_stable_snapshots(uid)

        # Record self-assessed readiness (3rd indicator — already 2/3 met without it)
        sar_result = record_self_assessed_readiness(uid)
        assert "error" not in sar_result

        # All 3 indicators met — gate should pass
        result = advance_phase(uid, "graduation")
        assert "error" not in result, (
            f"advance_phase('graduation') should succeed with 3/3 indicators. Got: {result}"
        )

    def test_gate_blocked_with_only_1_snapshot(self):
        """Gate requires at least 3 snapshots — 1 is not enough for any indicator."""
        uid = _create_user(phase="reassessment")
        # Insert only 1 snapshot — pattern_stability requires >=3, quadrant_consolidation requires >=3
        _insert_snapshot(uid, _stable_scores(), "transmuter")

        # Record self-assessed readiness so that one indicator is met
        record_self_assessed_readiness(uid)

        result = advance_phase(uid, "graduation")
        assert "error" in result, (
            f"Gate should block graduation with only 1 snapshot (< 2 indicators met). Got: {result}"
        )

    def test_gate_blocked_with_only_self_assessed_and_unstable_scores(self):
        """Only 1 indicator (self_assessed) → gate blocks — need at least 2."""
        uid = _create_user(phase="reassessment")
        # Varied archetypes → quadrant_consolidation NOT met
        # Shifting scores → pattern_stability NOT met
        # Only self_assessed will be met (1/3)
        archetypes = ["transmuter", "absorber", "magnifier"]
        for i, (arch, score) in enumerate(zip(archetypes, [2.0, 3.0, 4.0])):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _insert_snapshot(uid, {"dim1": {"score": score}}, arch, ts)

        record_self_assessed_readiness(uid)

        result = advance_phase(uid, "graduation")
        assert "error" in result, (
            f"Gate should block graduation when only self_assessed is met (1/3 < 2). Got: {result}"
        )

    def test_self_assessed_readiness_is_idempotent(self):
        """Calling record_self_assessed_readiness twice is safe."""
        uid = _create_user(phase="reassessment")
        r1 = record_self_assessed_readiness(uid)
        r2 = record_self_assessed_readiness(uid)
        assert "error" not in r1
        assert "error" not in r2
        # DB value should remain 1
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT self_assessed_readiness FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["self_assessed_readiness"] == 1

    def test_self_assessed_readiness_reset_on_development_entry(self):
        """advance_phase('development') resets self_assessed_readiness to 0."""
        uid = _create_user(phase="reassessment")
        record_self_assessed_readiness(uid)

        # Confirm it was set
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT self_assessed_readiness FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["self_assessed_readiness"] == 1

        # Retreat from reassessment to development requires >= 2 snapshots (comparison gate)
        _insert_snapshot(uid, _stable_scores(), "transmuter",
                         (datetime.utcnow() - timedelta(days=2)).isoformat())
        _insert_snapshot(uid, _stable_scores(4.2), "transmuter")

        result = advance_phase(uid, "development")
        assert "error" not in result, f"advance_phase('development') from reassessment failed: {result}"

        # self_assessed_readiness must be reset
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT self_assessed_readiness FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["self_assessed_readiness"] == 0, (
            "self_assessed_readiness must be reset to 0 when entering development"
        )


# ── Widened save paths (RESPONSE_SAVE_PHASES) ─────────────────────────────────


class TestWidenedSavePathsInLifecycle:
    """save_assessment_response accepts assessment, reassessment, and check_in phases."""

    def _setup_user_in_phase(self, phase: str) -> tuple[str, str]:
        uid = _create_user(phase=phase)
        qid = _get_first_question_id()
        return uid, qid

    def test_save_response_in_assessment_phase(self):
        uid, qid = self._setup_user_in_phase("assessment")
        if not qid:
            pytest.skip("No questions in bank")
        result = save_assessment_response(uid, qid, 4)
        assert "error" not in result

    def test_save_response_in_reassessment_phase(self):
        uid, qid = self._setup_user_in_phase("reassessment")
        if not qid:
            pytest.skip("No questions in bank")
        result = save_assessment_response(uid, qid, 3)
        assert "error" not in result, (
            f"save_assessment_response must accept 'reassessment' phase. Got: {result}"
        )

    def test_save_response_in_check_in_phase(self):
        uid, qid = self._setup_user_in_phase("check_in")
        if not qid:
            pytest.skip("No questions in bank")
        result = save_assessment_response(uid, qid, 5)
        assert "error" not in result, (
            f"save_assessment_response must accept 'check_in' phase. Got: {result}"
        )

    def test_save_response_blocked_in_development_phase(self):
        uid, qid = self._setup_user_in_phase("development")
        if not qid:
            pytest.skip("No questions in bank")
        result = save_assessment_response(uid, qid, 3)
        assert "error" in result, (
            "save_assessment_response must be blocked in 'development' phase"
        )

    def test_save_response_blocked_in_graduation_phase(self):
        uid, qid = self._setup_user_in_phase("graduation")
        if not qid:
            pytest.skip("No questions in bank")
        result = save_assessment_response(uid, qid, 3)
        assert "error" in result, (
            "save_assessment_response must be blocked in 'graduation' phase"
        )

    def test_save_response_blocked_in_orientation_phase(self):
        uid, qid = self._setup_user_in_phase("orientation")
        if not qid:
            pytest.skip("No questions in bank")
        result = save_assessment_response(uid, qid, 3)
        assert "error" in result


# ── Integrated reassessment → graduation flow ─────────────────────────────────


class TestReassessmentToGraduationFlow:
    """Full tool-chain flow as the reassessment agent will execute it."""

    def test_full_agent_driven_graduation_path(self):
        """Record responses → evaluate readiness → record self-assessed → advance → verify phase."""
        uid = _create_user(phase="reassessment")
        _seed_3_stable_snapshots(uid)

        qid = _get_first_question_id()
        if qid:
            save_assessment_response(uid, qid, 4)

        # Evaluate readiness (2 of 3 indicators met at minimum)
        readiness = evaluate_graduation_readiness(uid)
        # With self_assessed_readiness=0, the indicator is not met
        # but we test the flow anyway — user says yes, agent records it
        assert "graduation_ready" in readiness

        # Agent records self-asserted readiness
        record_self_assessed_readiness(uid)

        # Advance to graduation via the gate
        result = advance_phase(uid, "graduation")
        assert "error" not in result, f"Expected graduation to succeed. Got: {result}"

        # Verify phase changed
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT current_phase FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["current_phase"] == "graduation"

    def test_failed_gate_triggers_development_retreat(self):
        """When the gate rejects graduation, agent must call advance_phase('development')."""
        uid = _create_user(phase="reassessment")
        # Use varied archetypes + shifting scores so only self_assessed passes (1/3 < 2)
        archetypes = ["transmuter", "absorber", "magnifier"]
        for i, arch in enumerate(archetypes):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _insert_snapshot(uid, {"dim1": {"score": float(i + 2)}}, arch, ts)

        record_self_assessed_readiness(uid)
        grad_result = advance_phase(uid, "graduation")
        assert "error" in grad_result  # Gate rejects (only 1/3 indicators met)

        # Retreat to development — comparison gate requires >= 2 snapshots (already seeded 3)
        dev_result = advance_phase(uid, "development")
        assert "error" not in dev_result, f"Development retreat failed: {dev_result}"

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT current_phase, self_assessed_readiness FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["current_phase"] == "development"
        # self_assessed_readiness reset should have occurred on development entry
        assert row["self_assessed_readiness"] == 0


# ── Graduation artifacts and record (phase-independent) ───────────────────────


class TestGraduationArtifactsAndRecord:
    """generate_graduation_artifacts and save_graduation_record work after gate passes."""

    def test_artifacts_available_after_gate_passes(self):
        uid = _create_user(phase="reassessment")
        _seed_3_stable_snapshots(uid)
        record_self_assessed_readiness(uid)
        advance_phase(uid, "graduation")

        artifacts = generate_graduation_artifacts(uid)
        assert "error" not in artifacts
        assert "growth_trajectory" in artifacts

    def test_save_graduation_record_persists(self):
        uid = _create_user(phase="graduation")
        _seed_3_stable_snapshots(uid)

        indicators = {
            "pattern_stability": {"met": True, "evidence": "stable across 3 cycles"},
            "quadrant_consolidation": {"met": True, "evidence": "transmuter x3"},
            "self_assessed_readiness": {"met": True, "evidence": "user confirmed"},
        }
        result = save_graduation_record(uid, "Completed the journey.", indicators)
        assert result["saved"] is True
        assert result["event_type"] == "graduation.complete"

        record = get_graduation_record(uid)
        assert record["exists"] is True
        assert record["pattern_narrative"] == "Completed the journey."
        assert record["graduation_indicators"]["self_assessed_readiness"]["met"] is True


# ── Check-in phase allows Likert saves ────────────────────────────────────────


class TestCheckInLikertSaves:
    """Likert responses must be saveable during check_in (BE-001 widened guard)."""

    def test_check_in_user_can_save_responses(self):
        """A user in check_in phase can save assessment responses."""
        uid = _create_user(phase="check_in")
        qid = _get_first_question_id()
        if not qid:
            pytest.skip("No questions in bank")

        # Save multiple responses
        for score in [3, 4, 5]:
            result = save_assessment_response(uid, qid, score)
            assert "error" not in result, (
                f"check_in user should be able to save responses. Got: {result}"
            )

    def test_check_in_responses_accumulate_correctly(self):
        """Multiple saves in check_in phase accumulate in assessment_state.responses."""
        uid = _create_user(phase="check_in")
        qb = get_question_bank()
        questions = qb.get_all_questions()
        if len(questions) < 2:
            pytest.skip("Not enough questions")

        q1, q2 = questions[0]["id"], questions[1]["id"]
        r1 = save_assessment_response(uid, q1, 3)
        r2 = save_assessment_response(uid, q2, 5)

        assert "error" not in r1
        assert "error" not in r2

        # Responses are stored in assessment_state.responses (JSON blob)
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT responses FROM assessment_state WHERE user_id = ?", (uid,)
            ).fetchone()
        assert row is not None, "assessment_state row must exist after saves"
        stored = json.loads(row["responses"])
        assert q1 in stored or q2 in stored, (
            f"At least one question_id must appear in stored responses. Got keys: {list(stored.keys())}"
        )


# ── evaluate_graduation_readiness with self_assessed_readiness ─────────────────


class TestEvaluateGraduationReadinessWithSelfAssessed:
    """evaluate_graduation_readiness reads self_assessed_readiness from users table."""

    def test_self_assessed_indicator_false_by_default(self):
        uid = _create_user(phase="reassessment")
        _seed_3_stable_snapshots(uid)

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["self_assessed_readiness"]["met"] is False

    def test_self_assessed_indicator_true_after_record(self):
        uid = _create_user(phase="reassessment")
        _seed_3_stable_snapshots(uid)
        record_self_assessed_readiness(uid)

        result = evaluate_graduation_readiness(uid)
        assert result["indicators"]["self_assessed_readiness"]["met"] is True

    def test_graduation_ready_with_all_three_indicators(self):
        """graduation_ready = True when all 3 indicators are met (3/3)."""
        uid = _create_user(phase="reassessment")
        _seed_3_stable_snapshots(uid)
        record_self_assessed_readiness(uid)

        result = evaluate_graduation_readiness(uid)
        # With 3 stable snapshots (same archetype) + self_assessed, all 3 should be met
        assert result["indicators_met"] == 3
        assert result["graduation_ready"] is True

    def test_graduation_not_ready_with_only_self_assessed_met(self):
        """graduation_ready = False when only self_assessed is met (< 2 indicators)."""
        uid = _create_user(phase="reassessment")
        # Varied archetypes → quadrant_consolidation NOT met
        # Shifting scores (1.0/cycle on 1–5 scale > threshold) → pattern_stability NOT met
        # Only self_assessed_readiness will be met
        archetypes = ["transmuter", "absorber", "magnifier"]
        for i, arch in enumerate(archetypes):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _insert_snapshot(uid, {"dim1": {"score": float(i + 2)}}, arch, ts)

        record_self_assessed_readiness(uid)  # sets 1/3
        result = evaluate_graduation_readiness(uid)

        assert result["indicators"]["self_assessed_readiness"]["met"] is True
        # With only 1/3 indicators met, graduation_ready must be False
        assert result["indicators_met"] < 2
        assert result["graduation_ready"] is False
