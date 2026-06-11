"""Integration tests for the /api/results reassessment field.

Covers the verification scenarios from spec B13.2:
  1. Latest snapshot is a reassessment (sentinel-marked) + ≥2 snapshots →
     reassessment.available=true, latest_comparison populated.
  2. Only one (baseline) snapshot → reassessment.available=false.
  3. Latest snapshot is a baseline (no sentinel, no kind) → available=false.
  4. Latest snapshot is a check-in (kind="check_in") → available=false.
  5. generate_comparison_snapshot returns an error → available=false (no 500), logged.
  6. 403 guard preserved for non-owner access.
  7. Reassessment field shape matches frontend contract (all required keys present).
  8. cycle populated from sentinel block when present.
  9. Post-graduation user with check-ins AND earlier reassessment, latest is check-in →
     available=false, check_ins.count > 0.
  10. Re-entry user (latest is reassessment) → available=true, reassessment wins.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from agents.transmutation.tools import SNAPSHOT_KIND_CHECK_IN
from db.database import get_db_session


# ── Seeding helpers ──────────────────────────────────────────────────────────


def _create_user(phase: str = "reassessment") -> str:
    """Create a bare user row and return user_id."""
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase, reassessment_cycle) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, "Test User", f"{uid}@test.com", "hash", phase, 1),
        )
    return uid


def _insert_snapshot(
    user_id: str,
    scores: dict,
    quadrant_placement: dict,
    created_at: str,
) -> str:
    """Insert a profile_snapshots row and return its id."""
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                sid,
                user_id,
                json.dumps(scores),
                json.dumps(quadrant_placement),
                created_at,
            ),
        )
    return sid


# Sentinel-marked quadrant_placement for a reassessment snapshot
def _reassessment_qp(cycle: int = 1) -> dict:
    return {
        "quadrant": "transmuter",
        "archetype": "transmuter",
        "sentinel": {"cycle": cycle, "targeted_dimensions": [], "sentinel_dimensions": []},
    }


# Baseline quadrant_placement (no sentinel, no kind)
def _baseline_qp() -> dict:
    return {"quadrant": "transmuter", "archetype": "transmuter"}


# Check-in quadrant_placement
def _checkin_qp() -> dict:
    return {"quadrant": "transmuter", "archetype": "transmuter", "kind": SNAPSHOT_KIND_CHECK_IN}


_SCORES_A = {"dim1": {"score": 3.0}, "dim2": {"score": 3.0}}
_SCORES_B = {"dim1": {"score": 4.0}, "dim2": {"score": 3.5}}

_T_BASELINE = (datetime.utcnow() - timedelta(days=10)).isoformat()
_T_LATEST = datetime.utcnow().isoformat()


# ── Scenario 1: Reassessment available ──────────────────────────────────────


class TestReassessmentAvailable:
    def test_returns_available_true_with_comparison(self, authenticated_client):
        """Latest snapshot is sentinel-marked, prior exists → available=true."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()

        ra = data.get("reassessment")
        assert ra is not None
        assert ra["available"] is True
        assert ra["kind"] == "reassessment"
        assert ra["latest_comparison"] is not None

    def test_cycle_populated_from_sentinel_block(self, authenticated_client):
        """cycle is read from the sentinel block in quadrant_placement."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=2), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        data = resp.json()
        assert data["reassessment"]["cycle"] == 2

    def test_latest_comparison_has_required_keys(self, authenticated_client):
        """Verify the frontend contract: deltas, quadrant_shift, snapshot ids present."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        comp = resp.json()["reassessment"]["latest_comparison"]
        assert "current_snapshot_id" in comp
        assert "previous_snapshot_id" in comp
        assert "deltas" in comp
        assert "quadrant_shift" in comp
        # flow_deltas must NOT be present (excluded per spec B5.3)
        assert "flow_deltas" not in comp

    def test_deltas_non_empty_when_scores_differ(self, authenticated_client):
        """Dimension deltas are populated when scores differ between snapshots."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        deltas = resp.json()["reassessment"]["latest_comparison"]["deltas"]
        assert len(deltas) > 0
        for _dim, d in deltas.items():
            for key in ("previous", "current", "delta", "previous_normalized",
                        "current_normalized", "delta_normalized", "direction"):
                assert key in d

    def test_quadrant_shift_present(self, authenticated_client):
        """quadrant_shift always present (even when no shift)."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        qs = resp.json()["reassessment"]["latest_comparison"]["quadrant_shift"]
        assert "previous" in qs
        assert "current" in qs
        assert "shifted" in qs


# ── Scenario 2: Only one snapshot → available=false ─────────────────────────


class TestOnlyOneSnapshot:
    def test_single_snapshot_returns_available_false(self, authenticated_client):
        """With only one snapshot, no comparison is possible."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        ra = resp.json().get("reassessment")
        assert ra is not None
        assert ra["available"] is False
        assert ra["latest_comparison"] is None


# ── Scenario 3: Latest is baseline → available=false ────────────────────────


class TestLatestIsBaseline:
    def test_baseline_latest_returns_available_false(self, authenticated_client):
        """Latest snapshot has no sentinel → not a reassessment."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _baseline_qp(), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        ra = resp.json().get("reassessment")
        assert ra["available"] is False
        assert ra["latest_comparison"] is None


# ── Scenario 4: Latest is check-in → available=false ────────────────────────


class TestLatestIsCheckIn:
    def test_checkin_latest_returns_available_false(self, authenticated_client):
        """Latest snapshot has kind=check_in → check-in path, not reassessment."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _checkin_qp(), _T_LATEST)

        resp = authenticated_client.get(f"/api/results/{uid}")
        ra = resp.json().get("reassessment")
        assert ra["available"] is False
        assert ra["latest_comparison"] is None


# ── Scenario 5: generate_comparison_snapshot errors → available=false ────────


class TestComparisonToolError:
    def test_tool_error_returns_available_false_no_500(
        self, authenticated_client, caplog
    ):
        """generate_comparison_snapshot error → 200, available=false, error logged."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        error_resp = {"error": "corrupt prior snapshot — cannot compute deltas"}
        with patch(
            "api.results.generate_comparison_snapshot",
            return_value=error_resp,
        ):
            with caplog.at_level(logging.WARNING, logger="api.results"):
                resp = authenticated_client.get(f"/api/results/{uid}")

        assert resp.status_code == 200
        ra = resp.json().get("reassessment")
        assert ra["available"] is False
        assert ra["latest_comparison"] is None
        # Error must be logged (anti-patterns-error-swallowing)
        assert any("generate_comparison_snapshot error" in r.message for r in caplog.records)

    def test_tool_exception_returns_available_false_no_500(
        self, authenticated_client, caplog
    ):
        """generate_comparison_snapshot exception → 200, available=false, logged."""
        uid = authenticated_client.user_id

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), _T_BASELINE)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), _T_LATEST)

        with patch(
            "api.results.generate_comparison_snapshot",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with caplog.at_level(logging.WARNING, logger="api.results"):
                resp = authenticated_client.get(f"/api/results/{uid}")

        assert resp.status_code == 200
        ra = resp.json().get("reassessment")
        assert ra["available"] is False
        assert any("reassessment comparison snapshot derivation failed" in r.message
                   for r in caplog.records)


# ── Scenario 6: 403 guard preserved ─────────────────────────────────────────


class TestAuthorizationPreserved:
    def test_other_users_results_returns_403(self, authenticated_client):
        """The existing same-user 403 guard is unchanged."""
        other_uid = str(uuid.uuid4())
        resp = authenticated_client.get(f"/api/results/{other_uid}")
        assert resp.status_code == 403


# ── Scenario 7: reassessment field always present in response ────────────────


class TestReassessmentFieldAlwaysPresent:
    def test_field_present_with_no_snapshots(self, authenticated_client):
        """reassessment field is always present in the response (may be null or object)."""
        uid = authenticated_client.user_id
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        # Field may be null or a ReassessmentResponse object — must be in the payload
        assert "reassessment" in data


# ── Scenario 9: Post-graduation user — latest is check-in → check_ins wins ──


class TestCheckInWinsOverOlderReassessment:
    def test_checkin_latest_shows_checkins_not_reassessment(
        self, authenticated_client
    ):
        """Post-graduation: latest is check-in → available=false, check_ins.count > 0."""
        uid = authenticated_client.user_id

        t0 = (datetime.utcnow() - timedelta(days=20)).isoformat()
        t1 = (datetime.utcnow() - timedelta(days=10)).isoformat()
        t2 = datetime.utcnow().isoformat()

        baseline_id = _insert_snapshot(uid, _SCORES_A, _baseline_qp(), t0)
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=1), t1)
        # Latest is a check-in
        checkin_snap_id = _insert_snapshot(uid, _SCORES_A, _checkin_qp(), t2)

        # Seed a check_in_log row with real snapshot IDs so FK constraints pass
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO check_in_log "
                "(id, user_id, snapshot_id, graduation_snapshot_id, regression_detected, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), uid, checkin_snap_id, baseline_id, False, t2),
            )

        resp = authenticated_client.get(f"/api/results/{uid}")
        data = resp.json()
        assert data["reassessment"]["available"] is False
        assert data["check_ins"]["count"] > 0


# ── Scenario 10: Re-entry user — latest is reassessment → reassessment wins ─


class TestReassessmentWinsOnReentry:
    def test_reentry_reassessment_available_true(self, authenticated_client):
        """Re-entry user: latest is reassessment → available=true."""
        uid = authenticated_client.user_id

        t0 = (datetime.utcnow() - timedelta(days=20)).isoformat()
        t1 = (datetime.utcnow() - timedelta(days=10)).isoformat()
        t2 = datetime.utcnow().isoformat()

        _insert_snapshot(uid, _SCORES_A, _baseline_qp(), t0)
        _insert_snapshot(uid, _SCORES_A, _checkin_qp(), t1)
        # Latest is a reassessment
        _insert_snapshot(uid, _SCORES_B, _reassessment_qp(cycle=2), t2)

        resp = authenticated_client.get(f"/api/results/{uid}")
        data = resp.json()
        assert data["reassessment"]["available"] is True
        assert data["reassessment"]["latest_comparison"] is not None
