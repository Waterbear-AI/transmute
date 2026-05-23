"""Unit/integration tests for detect_check_in_regression (deterministic check-in gate).

Covers the happy paths, the per-dimension normalized-drop signal, the quadrant
downgrade signal, threshold boundaries, edge cases (missing record/baseline/
check-in snapshot, same-id baseline, undetermined archetype), and pins the
returned result-dictionary contract. DB-backed via the conftest autouse reset.
"""

import json
import uuid
from datetime import datetime, timedelta

from db.database import get_db_session
from agents.transmutation.tools import (
    detect_check_in_regression,
    CHECK_IN_REGRESSION_DROP_NORMALIZED,
)


def _create_user(phase: str = "check_in") -> str:
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Test", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _create_snapshot(user_id: str, scores: dict, archetype: str = "transmuter", days_ago: int = 0) -> str:
    """Insert a production-shaped snapshot (archetype key, 1–5 scores)."""
    sid = str(uuid.uuid4())
    ts = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"archetype": archetype}), ts),
        )
    return sid


def _create_graduation_record(user_id: str, final_snapshot_id) -> None:
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO graduation_record
               (id, user_id, final_snapshot_id, initial_snapshot_id, practice_map,
                pattern_narrative, graduation_indicators, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), user_id, final_snapshot_id, None, "{}",
                "narrative", "{}", datetime.utcnow().isoformat(),
            ),
        )


class TestDetectCheckInRegression:
    def test_no_graduation_record_not_evaluated(self):
        uid = _create_user()
        result = detect_check_in_regression(uid)
        assert result["evaluated"] is False
        assert result["regression_detected"] is False
        assert "graduation record" in result["reason"].lower()

    def test_null_baseline_not_evaluated(self):
        uid = _create_user()
        # A graduation record can carry a NULL final_snapshot_id (no snapshot at
        # graduation time). With no baseline there is nothing to compare against.
        _create_graduation_record(uid, None)
        _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["evaluated"] is False
        assert "baseline" in result["reason"].lower()

    def test_no_check_in_snapshot_since_graduation(self):
        uid = _create_user()
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        # Only the graduation snapshot exists → latest == baseline.
        result = detect_check_in_regression(uid)
        assert result["evaluated"] is False
        assert "since graduation" in result["reason"].lower()
        assert result["baseline_snapshot_id"] == baseline

    def test_stable_no_regression(self):
        uid = _create_user()
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}, "dim2": {"score": 3.5}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 4.0}, "dim2": {"score": 3.5}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["evaluated"] is True
        assert result["regression_detected"] is False
        assert result["regressed_dimensions"] == []
        assert result["quadrant"]["downgraded"] is False

    def test_improvement_no_regression(self):
        uid = _create_user()
        baseline = _create_snapshot(uid, {"dim1": {"score": 3.0}}, "conduit", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 4.5}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["evaluated"] is True
        assert result["regression_detected"] is False

    def test_dimension_drop_detected(self):
        uid = _create_user()
        # 4.0 → 2.5 = 37.5 normalized pts drop (> 15) → regression.
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 2.5}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["regression_detected"] is True
        assert result["evaluated"] is True
        assert len(result["regressed_dimensions"]) == 1
        dim = result["regressed_dimensions"][0]
        assert dim["dimension"] == "dim1"
        assert dim["baseline_normalized"] == 75.0
        assert dim["current_normalized"] == 37.5
        assert dim["drop_normalized"] == 37.5

    def test_drop_exactly_at_threshold_not_regression(self):
        uid = _create_user()
        # 4.0 → 3.4 = exactly 15.0 normalized pts; threshold is strict (>), so NOT regression.
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 3.4}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["regression_detected"] is False
        assert result["regressed_dimensions"] == []

    def test_quadrant_downgrade_detected(self):
        uid = _create_user()
        # Scores unchanged; archetype transmuter (rank 3) → absorber (rank 1) is a downgrade.
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 4.0}}, "absorber", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["regression_detected"] is True
        assert result["regressed_dimensions"] == []  # purely a quadrant signal
        assert result["quadrant"] == {"baseline": "transmuter", "current": "absorber", "downgraded": True}

    def test_quadrant_lateral_move_not_downgrade(self):
        uid = _create_user()
        # absorber (rank 1) → magnifier (rank 1): lateral, not a downgrade. Scores stable.
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "absorber", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 4.0}}, "magnifier", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["regression_detected"] is False
        assert result["quadrant"]["downgraded"] is False

    def test_undetermined_archetype_skips_quadrant_signal(self):
        uid = _create_user()
        # Baseline archetype has no rank → quadrant signal skipped; stable scores → no regression.
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "undetermined", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["evaluated"] is True
        assert result["regression_detected"] is False
        assert result["quadrant"]["downgraded"] is False

    def test_dimension_intersection_only(self):
        uid = _create_user()
        # A dim present only on one side must be skipped, never defaulted (which would
        # normalize 0 → -25 and manufacture a phantom drop).
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}, "only_baseline": {"score": 4.0}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        _create_snapshot(uid, {"dim1": {"score": 4.0}, "only_current": {"score": 1.0}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert result["regression_detected"] is False
        assert result["regressed_dimensions"] == []

    def test_result_contract_shape(self):
        uid = _create_user()
        baseline = _create_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter", days_ago=90)
        _create_graduation_record(uid, baseline)
        check_in = _create_snapshot(uid, {"dim1": {"score": 2.5}}, "transmuter", days_ago=0)
        result = detect_check_in_regression(uid)
        assert set(result.keys()) == {
            "regression_detected", "evaluated", "reason", "threshold_normalized",
            "regressed_dimensions", "quadrant", "baseline_snapshot_id", "check_in_snapshot_id",
        }
        assert result["threshold_normalized"] == CHECK_IN_REGRESSION_DROP_NORMALIZED
        assert result["baseline_snapshot_id"] == baseline
        assert result["check_in_snapshot_id"] == check_in
        assert set(result["quadrant"].keys()) == {"baseline", "current", "downgraded"}
        assert set(result["regressed_dimensions"][0].keys()) == {
            "dimension", "baseline_normalized", "current_normalized", "drop_normalized",
        }
