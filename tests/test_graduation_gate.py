"""Tests for BE-002: server-side graduation gate enforcement and self-assessed readiness.

Covers:
- _evaluate_graduation_readiness reads from users.self_assessed_readiness
- _check_graduation_readiness_gate returns None when ready, error dict when not
- record_self_assessed_readiness sets users.self_assessed_readiness = 1 (idempotent)
- advance_phase graduation branch enforces the gate
- advance_phase resets self_assessed_readiness = 0 on 'development' entry
- evaluate_graduation_readiness (public wrapper) remains backward-compatible
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from db.database import get_db_session
from agents.transmutation.tools import (
    _evaluate_graduation_readiness,
    _check_graduation_readiness_gate,
    evaluate_graduation_readiness,
    record_self_assessed_readiness,
    advance_phase,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _create_user(phase: str = "reassessment") -> str:
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Gate Test User", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _create_profile_snapshot(
    user_id: str,
    scores: dict,
    archetype: str = "transmuter",
    created_at: str | None = None,
) -> str:
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO profile_snapshots
               (id, user_id, scores, quadrant_placement, interpretation, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                sid,
                user_id,
                json.dumps(scores),
                json.dumps({"archetype": archetype}),
                "Test interpretation",
                ts,
            ),
        )
    return sid


def _stable_scores() -> dict:
    """Return scores that will produce near-zero deltas across 3 snapshots."""
    return {"dim1": {"score": 4.0}, "dim2": {"score": 4.5}}


def _set_self_assessed_readiness(user_id: str, value: int) -> None:
    with get_db_session() as conn:
        conn.execute(
            "UPDATE users SET self_assessed_readiness = ? WHERE id = ?",
            (value, user_id),
        )


def _get_self_assessed_readiness(user_id: str) -> int:
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT self_assessed_readiness FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row["self_assessed_readiness"]


def _create_three_stable_snapshots(user_id: str) -> None:
    scores = _stable_scores()
    for i in range(3):
        ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
        _create_profile_snapshot(user_id, scores, "transmuter", ts)


def _create_development_roadmap(user_id: str, days_ago: int = 31) -> None:
    """Create a roadmap so the development gate passes by time."""
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                user_id,
                json.dumps({"practices": []}),
                (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
            ),
        )


# ── record_self_assessed_readiness ────────────────────────────────────────────


class TestRecordSelfAssessedReadiness:
    def test_sets_readiness_to_1(self):
        uid = _create_user()
        assert _get_self_assessed_readiness(uid) == 0  # Default is 0
        result = record_self_assessed_readiness(uid)
        assert result["recorded"] is True
        assert result["event_type"] == "graduation.readiness_recorded"
        assert _get_self_assessed_readiness(uid) == 1

    def test_is_idempotent(self):
        uid = _create_user()
        record_self_assessed_readiness(uid)
        record_self_assessed_readiness(uid)
        assert _get_self_assessed_readiness(uid) == 1

    def test_returns_error_for_unknown_user(self):
        result = record_self_assessed_readiness("nonexistent-user-id")
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_default_readiness_is_zero(self):
        uid = _create_user()
        assert _get_self_assessed_readiness(uid) == 0


# ── _evaluate_graduation_readiness ────────────────────────────────────────────


class TestEvaluateGraduationReadiness:
    """Tests for the conn-accepting helper."""

    def test_reads_self_assessed_readiness_from_users_table(self):
        uid = _create_user()
        _create_three_stable_snapshots(uid)  # satisfies pattern_stability + consolidation
        _set_self_assessed_readiness(uid, 1)

        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        assert result["indicators"]["self_assessed_readiness"]["met"] is True
        assert "User indicated readiness" in result["indicators"]["self_assessed_readiness"]["evidence"]

    def test_self_assessed_readiness_false_when_zero(self):
        uid = _create_user()
        # Default is 0, no snapshots needed for this check
        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        assert result["indicators"]["self_assessed_readiness"]["met"] is False
        assert "has not indicated" in result["indicators"]["self_assessed_readiness"]["evidence"]

    def test_graduation_ready_with_two_objective_indicators(self):
        """Pattern stability + quadrant consolidation ≥ 2 indicators → ready."""
        uid = _create_user()
        _create_three_stable_snapshots(uid)

        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        assert result["graduation_ready"] is True
        assert result["indicators_met"] >= 2

    def test_graduation_ready_with_self_assessed_plus_one_objective(self):
        """Self-assessed + pattern stability → ready (2-of-3)."""
        uid = _create_user()
        # 3 stable snapshots → pattern_stability met; quadrant varies → consolidation not met
        scores_a = {"dim1": {"score": 4.0}}
        scores_b = {"dim1": {"score": 4.0}}
        scores_c = {"dim1": {"score": 4.0}}
        for i, (s, arch) in enumerate([(scores_c, "absorber"), (scores_b, "transmuter"), (scores_a, "transmuter")]):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, s, arch, ts)

        _set_self_assessed_readiness(uid, 1)

        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        # pattern_stability met (zero delta), self_assessed met → 2/3 → ready
        assert result["indicators"]["pattern_stability"]["met"] is True
        assert result["indicators"]["self_assessed_readiness"]["met"] is True
        assert result["graduation_ready"] is True

    def test_not_ready_with_fewer_than_3_snapshots(self):
        uid = _create_user()
        # Only 2 snapshots — objective indicators cannot be met
        for i in range(2):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, _stable_scores(), "transmuter", ts)

        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        assert result["indicators"]["pattern_stability"]["met"] is False
        assert result["indicators"]["quadrant_consolidation"]["met"] is False
        # self_assessed is also 0 by default → not ready
        assert result["graduation_ready"] is False

    def test_not_ready_with_shifting_scores(self):
        uid = _create_user()
        s1 = {"dim1": {"score": 2.0}}
        s2 = {"dim1": {"score": 3.0}}
        s3 = {"dim1": {"score": 4.0}}
        for i, s in enumerate([s3, s2, s1]):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, s, "absorber", ts)

        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        assert result["graduation_ready"] is False
        assert result["indicators"]["pattern_stability"]["met"] is False

    def test_returns_required_keys(self):
        uid = _create_user()
        with get_db_session() as conn:
            result = _evaluate_graduation_readiness(conn, uid)

        assert "graduation_ready" in result
        assert "indicators_met" in result
        assert "indicators_required" in result
        assert "indicators" in result
        assert result["indicators_required"] == 2


# ── _check_graduation_readiness_gate ─────────────────────────────────────────


class TestCheckGraduationReadinessGate:
    def test_returns_none_when_ready(self):
        uid = _create_user()
        _create_three_stable_snapshots(uid)

        with get_db_session() as conn:
            gate = _check_graduation_readiness_gate(conn, uid)

        assert gate is None

    def test_returns_error_dict_when_not_ready(self):
        uid = _create_user()
        # No snapshots → not ready

        with get_db_session() as conn:
            gate = _check_graduation_readiness_gate(conn, uid)

        assert gate is not None
        assert "error" in gate
        assert "indicators_met" in gate
        assert "indicators_required" in gate
        assert "indicators" in gate
        assert gate["indicators_required"] == 2

    def test_error_message_is_generic(self):
        """Gate error message must not expose numeric thresholds (security-error-handling)."""
        uid = _create_user()
        with get_db_session() as conn:
            gate = _check_graduation_readiness_gate(conn, uid)

        assert gate is not None
        # Should not expose normalized delta threshold in the top-level error key
        assert "5.0" not in gate["error"]
        assert "15.0" not in gate["error"]

    def test_returns_none_with_self_assessed_plus_stability(self):
        uid = _create_user()
        _create_three_stable_snapshots(uid)
        _set_self_assessed_readiness(uid, 1)

        with get_db_session() as conn:
            gate = _check_graduation_readiness_gate(conn, uid)

        assert gate is None


# ── evaluate_graduation_readiness (public wrapper) ───────────────────────────


class TestEvaluateGraduationReadinessPublic:
    """Verify the public tool is a backward-compatible thin wrapper."""

    def test_returns_same_shape_as_helper(self):
        uid = _create_user()
        _create_three_stable_snapshots(uid)

        result = evaluate_graduation_readiness(uid)

        assert "graduation_ready" in result
        assert "indicators_met" in result
        assert "indicators_required" in result
        assert "indicators" in result
        assert result["event_type"] == "graduation.readiness"

    def test_reads_self_assessed_readiness_from_users_not_session(self):
        """Confirm the public wrapper also reads from users table (not adk_sessions)."""
        uid = _create_user()
        _create_three_stable_snapshots(uid)
        _set_self_assessed_readiness(uid, 1)

        result = evaluate_graduation_readiness(uid)

        assert result["indicators"]["self_assessed_readiness"]["met"] is True


# ── advance_phase integration ─────────────────────────────────────────────────


class TestAdvancePhaseGraduationGate:
    """advance_phase now enforces the graduation gate server-side."""

    def test_graduation_blocked_when_criteria_not_met(self):
        uid = _create_user(phase="reassessment")
        # No snapshots → not ready

        result = advance_phase(uid, "graduation")

        assert "error" in result
        assert "graduation" in result["error"].lower() or "criteria" in result["error"].lower()

    def test_graduation_allowed_when_criteria_met(self):
        uid = _create_user(phase="reassessment")
        _create_three_stable_snapshots(uid)

        result = advance_phase(uid, "graduation")

        assert result.get("success") is True
        assert result["to"] == "graduation"

    def test_graduation_blocked_with_only_1_indicator(self):
        uid = _create_user(phase="reassessment")
        # Only self-assessed readiness set.
        # Use varying archetypes (consolidation NOT met) + large shifts (stability NOT met).
        _set_self_assessed_readiness(uid, 1)
        s_vals = [4.0, 1.0, 4.0]
        archetypes = ["transmuter", "absorber", "magnifier"]  # all different → consolidation NOT met
        for i, (v, arch) in enumerate(zip(s_vals, archetypes)):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, {"dim1": {"score": v}}, arch, ts)

        result = advance_phase(uid, "graduation")

        # Only self_assessed met (1/3) → blocked
        assert "error" in result

    def test_graduation_allowed_with_self_assessed_plus_one_objective(self):
        uid = _create_user(phase="reassessment")
        # Pattern stability met (zero delta), quadrant varies → only 2 indicators
        scores = {"dim1": {"score": 4.0}}
        archetypes = ["absorber", "transmuter", "transmuter"]  # not all same → consolidation not met
        for i, arch in enumerate(archetypes):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, scores, arch, ts)
        _set_self_assessed_readiness(uid, 1)

        result = advance_phase(uid, "graduation")

        # pattern_stability + self_assessed = 2 → ready
        assert result.get("success") is True
        assert result["to"] == "graduation"


class TestAdvancePhaseReadinessReset:
    """advance_phase resets self_assessed_readiness=0 when entering development."""

    def _create_user_with_roadmap(self, phase: str) -> str:
        uid = _create_user(phase=phase)
        _create_development_roadmap(uid, days_ago=31)
        return uid

    def test_reset_on_development_from_education(self):
        uid = _create_user(phase="education")
        # Create a profile snapshot so the education gate passes
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, interpretation, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), uid, json.dumps({}), json.dumps({}), "test", datetime.utcnow().isoformat()),
            )
            # Provide enough education progress to pass the gate
            # For simplicity we'll bypass via direct phase set
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        # Set readiness to 1 (as if from a prior cycle)
        _set_self_assessed_readiness(uid, 1)

        # Now simulate re-entry from reassessment
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'reassessment' WHERE id = ?", (uid,))

        # Create comparison snapshot so reassessment→development gate passes
        _create_profile_snapshot(uid, {"dim1": {"score": 3.0}}, "absorber")
        _create_profile_snapshot(uid, {"dim1": {"score": 3.5}}, "absorber")

        result = advance_phase(uid, "development")
        assert result.get("success") is True
        assert _get_self_assessed_readiness(uid) == 0

    def test_reset_on_development_from_check_in(self):
        uid = _create_user(phase="check_in")
        _set_self_assessed_readiness(uid, 1)

        result = advance_phase(uid, "development")

        assert result.get("success") is True
        assert _get_self_assessed_readiness(uid) == 0

    def test_no_reset_when_not_entering_development(self):
        uid = _create_user(phase="reassessment")
        _create_three_stable_snapshots(uid)
        _set_self_assessed_readiness(uid, 1)

        result = advance_phase(uid, "graduation")
        assert result.get("success") is True
        # Readiness should NOT be reset when entering graduation
        assert _get_self_assessed_readiness(uid) == 1
