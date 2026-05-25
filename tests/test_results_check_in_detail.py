"""API contract tests for /api/results check-in regression detail fields.

Covers all 9 scenarios from spec B13.2:
  1. No auth → 401
  2. Other user's uid → 403
  3. No check-in row → null detail fields
  4. Check-in + regression detected → evaluated=true, regression_detected=true
  5. Check-in + no regression → evaluated=true, regression_detected=false
  6. Graduation baseline missing → evaluated=false, reason non-empty
  7. Graduation snapshot + later check-in → latest_comparison.deltas populated
  8. Backward-compat: latest_regression boolean still present
  9. Engine raises → 200, latest_regression_detail=null, logger.warning emitted
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from db.database import get_db_session


# ── Module-local seeding helpers ────────────────────────────────────────────


def _create_user(phase: str = "check_in") -> str:
    """Create a bare user row and return user_id."""
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
            (uid, "Test User", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _create_profile_snapshot(
    user_id: str,
    scores: dict,
    archetype: str = "transmuter",
    created_at: str = None,
) -> str:
    """Insert a profile_snapshots row and return its id."""
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                sid,
                user_id,
                json.dumps(scores),
                json.dumps({"archetype": archetype}),
                ts,
            ),
        )
    return sid


def _seed_graduation(user_id: str, final_snapshot_id: str) -> None:
    """Insert a graduation_record row pointing to the given snapshot as baseline."""
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO graduation_record "
            "(id, user_id, final_snapshot_id, pattern_narrative, graduation_indicators, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                user_id,
                final_snapshot_id,
                "Narrative",
                json.dumps({"stability": {"met": True}}),
                datetime.utcnow().isoformat(),
            ),
        )


def _seed_check_in(
    user_id: str,
    snapshot_id: str,
    graduation_snapshot_id: str,
    regression_detected: bool,
) -> None:
    """Insert a check_in_log row."""
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO check_in_log "
            "(id, user_id, snapshot_id, graduation_snapshot_id, regression_detected, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                user_id,
                snapshot_id,
                graduation_snapshot_id,
                regression_detected,
                datetime.utcnow().isoformat(),
            ),
        )


# Scores that normalize to a high value (4.0 → 75.0 on 0-100)
_HIGH_SCORES = {"dim1": {"score": 4.0}, "dim2": {"score": 4.0}}

# Scores that normalize to a low value (1.5 → 12.5 on 0-100), causing a
# 75.0 − 12.5 = 62.5 pt drop → well above the 15.0 threshold → regression
_LOW_SCORES = {"dim1": {"score": 1.5}, "dim2": {"score": 1.5}}

# Scores close to HIGH but within threshold (3.5 → 62.5, drop = 75−62.5 = 12.5 < 15)
_SIMILAR_SCORES = {"dim1": {"score": 3.5}, "dim2": {"score": 3.5}}


# ── Scenario 1: No auth → 401 ─────────────────────────────────────────────


class TestNoAuth:
    def test_unauthenticated_returns_401(self, api_client):
        """GET /api/results/{uid} without a session cookie returns 401."""
        resp = api_client.get("/api/results/some-user-id")
        assert resp.status_code == 401


# ── Scenario 2: Other user's uid → 403 ───────────────────────────────────


class TestAuthorizationDenied:
    def test_other_users_results_returns_403(self, authenticated_client):
        """GET /api/results/{other_uid} as a different user returns 403."""
        other_uid = str(uuid.uuid4())
        resp = authenticated_client.get(f"/api/results/{other_uid}")
        assert resp.status_code == 403


# ── Scenario 3: No check-in row → null detail fields ─────────────────────


class TestNoCheckIn:
    def test_no_checkin_returns_null_detail(self, authenticated_client):
        """When no check_in_log rows exist, both new fields are null."""
        uid = authenticated_client.user_id
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        ci = data.get("check_ins") or {}
        assert ci.get("latest_regression_detail") is None
        assert ci.get("latest_comparison") is None


# ── Scenario 4: Regression detected ──────────────────────────────────────


class TestRegressionDetected:
    def test_regression_detail_populated_when_regression_detected(
        self, authenticated_client
    ):
        """Regression detail is fully populated when engine detects regression."""
        uid = authenticated_client.user_id

        # Baseline snapshot (transmuter, high scores)
        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter", baseline_ts)
        _seed_graduation(uid, baseline_id)

        # Later check-in snapshot (extractor archetype = rank 0 < rank 3, low scores)
        checkin_id = _create_profile_snapshot(uid, _LOW_SCORES, "extractor")
        _seed_check_in(uid, checkin_id, baseline_id, regression_detected=True)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        ci = data["check_ins"]

        detail = ci["latest_regression_detail"]
        assert detail is not None
        assert detail["evaluated"] is True
        assert detail["regression_detected"] is True
        assert len(detail["regressed_dimensions"]) > 0
        assert detail["threshold_normalized"] == 15.0
        # Quadrant downgraded: transmuter (rank 3) → extractor (rank 0)
        assert detail["quadrant"]["downgraded"] is True
        assert detail["quadrant"]["baseline"] == "transmuter"
        assert detail["quadrant"]["current"] == "extractor"
        # Each regressed dimension has the required fields
        for rd in detail["regressed_dimensions"]:
            assert "dimension" in rd
            assert "baseline_normalized" in rd
            assert "current_normalized" in rd
            assert "drop_normalized" in rd
            assert rd["drop_normalized"] > 15.0

        # Backward-compat: latest_regression bool still present
        assert ci["latest_regression"] is True


# ── Scenario 5: No regression ─────────────────────────────────────────────


class TestNoRegression:
    def test_regression_detail_clean_when_no_regression(self, authenticated_client):
        """evaluated=true, regression_detected=false when scores stay within threshold."""
        uid = authenticated_client.user_id

        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter", baseline_ts)
        _seed_graduation(uid, baseline_id)

        # Similar scores — drop < 15 pts
        checkin_id = _create_profile_snapshot(uid, _SIMILAR_SCORES, "transmuter")
        _seed_check_in(uid, checkin_id, baseline_id, regression_detected=False)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        ci = data["check_ins"]

        detail = ci["latest_regression_detail"]
        assert detail is not None
        assert detail["evaluated"] is True
        assert detail["regression_detected"] is False
        assert detail["regressed_dimensions"] == []
        assert detail["quadrant"]["downgraded"] is False
        assert detail["threshold_normalized"] == 15.0

        # Backward-compat
        assert ci["latest_regression"] is False


# ── Scenario 6: Graduation baseline missing → evaluated=false ─────────────


class TestGraduationBaselineMissing:
    def test_no_graduation_record_returns_unevaluated(self, authenticated_client):
        """When no graduation_record exists, evaluated=false with non-empty reason."""
        uid = authenticated_client.user_id

        # Create a snapshot and check-in but NO graduation record
        snap_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter")
        _seed_check_in(uid, snap_id, snap_id, regression_detected=False)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        ci = data["check_ins"]

        detail = ci["latest_regression_detail"]
        assert detail is not None
        assert detail["evaluated"] is False
        assert detail["reason"] != ""
        # When not evaluated, comparison should also be null (no graduation baseline)
        assert ci["latest_comparison"] is None

    def test_graduation_with_no_final_snapshot_id_returns_unevaluated(
        self, authenticated_client
    ):
        """graduation_record with final_snapshot_id=NULL yields evaluated=false."""
        uid = authenticated_client.user_id

        snap_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter")
        # Insert graduation record with NULL final_snapshot_id
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO graduation_record "
                "(id, user_id, final_snapshot_id, pattern_narrative, graduation_indicators, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    uid,
                    "Narrative",
                    json.dumps({}),
                    datetime.utcnow().isoformat(),
                ),
            )
        _seed_check_in(uid, snap_id, snap_id, regression_detected=False)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        detail = data["check_ins"]["latest_regression_detail"]
        assert detail is not None
        assert detail["evaluated"] is False
        assert detail["reason"] != ""


# ── Scenario 7: Comparison deltas populated ───────────────────────────────


class TestComparisonDeltasPopulated:
    def test_latest_comparison_contains_per_dimension_deltas(
        self, authenticated_client
    ):
        """latest_comparison.deltas has entries with required sub-fields."""
        uid = authenticated_client.user_id

        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter", baseline_ts)
        _seed_graduation(uid, baseline_id)

        checkin_id = _create_profile_snapshot(uid, _SIMILAR_SCORES, "transmuter")
        _seed_check_in(uid, checkin_id, baseline_id, regression_detected=False)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        ci = data["check_ins"]

        comp = ci["latest_comparison"]
        assert comp is not None
        assert "deltas" in comp
        assert len(comp["deltas"]) > 0
        assert "quadrant_shift" in comp

        # Verify delta shape for each dimension
        for dim_name, delta in comp["deltas"].items():
            assert "previous" in delta
            assert "current" in delta
            assert "delta" in delta
            assert "previous_normalized" in delta
            assert "current_normalized" in delta
            assert "delta_normalized" in delta
            assert delta["direction"] in ("up", "down", "stable")

        # Quadrant shift shape
        qs = comp["quadrant_shift"]
        assert "previous" in qs
        assert "current" in qs
        assert "shifted" in qs


# ── Scenario 8: Backward-compat: latest_regression bool ──────────────────


class TestBackwardCompatibility:
    def test_latest_regression_boolean_still_present_and_accurate(
        self, authenticated_client
    ):
        """latest_regression bool is present and matches the persisted value."""
        uid = authenticated_client.user_id

        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter", baseline_ts)
        _seed_graduation(uid, baseline_id)

        checkin_id = _create_profile_snapshot(uid, _LOW_SCORES, "extractor")
        _seed_check_in(uid, checkin_id, baseline_id, regression_detected=True)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        ci = resp.json()["check_ins"]

        # Boolean field exists and matches the persisted regression_detected value
        assert "latest_regression" in ci
        assert ci["latest_regression"] is True

    def test_latest_regression_false_for_no_regression(self, authenticated_client):
        """latest_regression=False is preserved when no regression was logged."""
        uid = authenticated_client.user_id

        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter", baseline_ts)
        _seed_graduation(uid, baseline_id)

        checkin_id = _create_profile_snapshot(uid, _SIMILAR_SCORES, "transmuter")
        _seed_check_in(uid, checkin_id, baseline_id, regression_detected=False)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        ci = resp.json()["check_ins"]
        assert ci["latest_regression"] is False


# ── Scenario 9: Engine raises → graceful degradation ─────────────────────


class TestEngineFallback:
    def test_detect_check_in_regression_exception_returns_200_with_null_detail(
        self, authenticated_client, monkeypatch, caplog
    ):
        """When detect_check_in_regression raises, API still returns 200 with null detail."""
        uid = authenticated_client.user_id

        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_id = _create_profile_snapshot(uid, _HIGH_SCORES, "transmuter", baseline_ts)
        _seed_graduation(uid, baseline_id)

        checkin_id = _create_profile_snapshot(uid, _SIMILAR_SCORES, "transmuter")
        _seed_check_in(uid, checkin_id, baseline_id, regression_detected=False)

        # Monkeypatch the engine function as imported in api.results
        def _always_raise(user_id):
            raise RuntimeError("simulated engine failure")

        monkeypatch.setattr("api.results.detect_check_in_regression", _always_raise)

        import logging
        with caplog.at_level(logging.WARNING, logger="api.results"):
            resp = authenticated_client.get(f"/api/results/{uid}")

        assert resp.status_code == 200
        data = resp.json()
        ci = data["check_ins"]

        # Field is null on failure — no 500
        assert ci["latest_regression_detail"] is None
        # Warning was logged
        assert any("recompute failed" in r.message or "recompute" in r.message.lower()
                   for r in caplog.records if r.levelno >= logging.WARNING)
