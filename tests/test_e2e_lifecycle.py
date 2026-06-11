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
    generate_check_in_snapshot,
    generate_comparison_snapshot,
    evaluate_graduation_readiness,
    generate_graduation_artifacts,
    save_graduation_record,
    get_graduation_record,
    save_check_in_log,
    detect_check_in_regression,
    advance_phase,
    get_user_profile,
    get_dimension_staleness,
    select_reassessment_targets,
    generate_reassessment_snapshot,
    save_profile_snapshot,
)
from agents.transmutation.question_bank import get_question_bank


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
    """Create a profile snapshot and return its ID.

    Stores quadrant_placement under the ``archetype`` key (production shape) and
    expects scores on the raw 1–5 Likert scale.
    """
    sid = str(uuid.uuid4())
    ts = created_at or datetime.utcnow().isoformat()
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, json.dumps(scores), json.dumps({"archetype": quadrant}), ts),
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
        scores = {"dim1": {"score": 4.0}, "dim2": {"score": 4.5}}
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
        # 1.0/cycle on the 1–5 scale = 25 normalized pts/cycle (> 5) → not stable
        s1 = {"dim1": {"score": 2.0}}
        s2 = {"dim1": {"score": 3.0}}
        s3 = {"dim1": {"score": 4.0}}

        for i, scores in enumerate([s3, s2, s1]):
            ts = (datetime.utcnow() - timedelta(days=i)).isoformat()
            _create_profile_snapshot(uid, scores, "absorber", ts)

        result = evaluate_graduation_readiness(uid)
        assert result["graduation_ready"] is False

    def test_comparison_snapshot_shows_deltas(self):
        """Comparison between two snapshots shows correct deltas."""
        uid = _create_user(phase="reassessment")
        prev_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 2.0}}, "absorber",
            (datetime.utcnow() - timedelta(days=30)).isoformat(),
        )
        _create_profile_snapshot(uid, {"dim1": {"score": 4.0}}, "transmuter")

        result = generate_comparison_snapshot(uid, prev_id)
        assert result["deltas"]["dim1"]["delta"] == 2.0            # raw 1–5 delta
        assert result["deltas"]["dim1"]["delta_normalized"] == 50.0  # 0–100 scale
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
        snap_id = _create_profile_snapshot(uid, {"dim1": {"score": 3.5}}, "transmuter")
        grad_snap_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 4.0}}, "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )

        result = save_check_in_log(uid, snap_id, grad_snap_id, regression_detected=False)
        assert result["saved"] is True
        assert result["regression_detected"] is False
        assert result["event_type"] == "checkin.complete"

    def test_check_in_with_regression_and_reentry(self):
        """Regression detected: user offered re-entry to development."""
        uid = _create_user(phase="check_in")
        snap_id = _create_profile_snapshot(uid, {"dim1": {"score": 2.0}}, "absorber")
        grad_snap_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 4.0}}, "transmuter",
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
        snap_id = _create_profile_snapshot(uid, {"dim1": {"score": 2.5}}, "absorber")
        grad_snap_id = _create_profile_snapshot(
            uid, {"dim1": {"score": 4.0}}, "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )

        result = save_check_in_log(
            uid, snap_id, grad_snap_id,
            regression_detected=True,
            re_entered_development=False,
        )
        assert result["regression_detected"] is True
        assert result["re_entered_development"] is False

    def test_check_in_full_pipeline(self):
        """Exercise the actual generate→save→compare→detect chain.

        The other tests in this class pre-INSERT canned snapshots and only
        exercise save_check_in_log — they covered the data, not the path.
        This one runs the production-shaped flow against the temp SQLite:
        a graduated user has a real graduation snapshot + assessment_state
        responses, and we walk the four tools the check-in agent will call.

        Asserts the gap this project fixed: (1) `generate_check_in_snapshot`
        succeeds; (2) `save_profile_snapshot` returns the new check-in
        event_type; (3) `generate_comparison_snapshot` against the graduation
        baseline now sees a different latest snapshot and produces non-zero
        deltas (was previously always-zero because the latest snapshot WAS
        the graduation snapshot); (4) `detect_check_in_regression` returns
        `evaluated: true` (was previously always-false with reason "no
        check-in snapshot since graduation"); (5) the cycle counter is NOT
        bumped and dimension_assessment_state is NOT seeded — the check-in
        path must not disturb development-phase bookkeeping.
        """
        uid = _create_user(phase="check_in")

        # The graduated user starts at cycle 2 (had two reassessments during
        # development before graduating). The check-in must leave this alone.
        with get_db_session() as conn:
            conn.execute("UPDATE users SET reassessment_cycle = 2 WHERE id = ?", (uid,))

        # Baseline: the graduation snapshot, 90 days back, scores at 3.0
        # across the bank so the comparison has somewhere to move.
        grad_snap_id = _create_profile_snapshot(
            uid, _baseline_scores(3.0), "transmuter",
            (datetime.utcnow() - timedelta(days=90)).isoformat(),
        )
        with get_db_session() as conn:
            conn.execute(
                """INSERT INTO graduation_record
                   (id, user_id, final_snapshot_id, initial_snapshot_id, practice_map,
                    pattern_narrative, graduation_indicators, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), uid, grad_snap_id, None, "{}",
                    "graduation narrative", "{}", datetime.utcnow().isoformat(),
                ),
            )

        # User completes the check-in by answering every question at 5
        # (Likert max) — well above the 3.0 baseline. The comparison should
        # see a uniformly positive delta.
        _seed_assessment_state(uid, _full_responses(5))

        # DAS row-count baseline (must not change after the check-in path).
        with get_db_session() as conn:
            das_before = conn.execute(
                "SELECT COUNT(*) AS n FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]

        # ── (1) generate the check-in snapshot ─────────────────────────
        gen = generate_check_in_snapshot(uid)
        assert "error" not in gen, gen
        assert gen["event_type"] == "checkin.scored"

        # ── (2) save it — exercises the new save_profile_snapshot branch.
        saved = save_profile_snapshot(uid, "Simulated 3-month check-in.")
        assert saved["saved"] is True
        assert saved["event_type"] == "checkin.snapshot_saved"
        check_in_snap_id = saved["snapshot_id"]
        assert check_in_snap_id != grad_snap_id  # a NEW snapshot exists

        # ── (3) compare against graduation — must now see real deltas.
        comparison = generate_comparison_snapshot(uid, grad_snap_id)
        assert "error" not in comparison
        # Pick any dim from the comparison and verify the delta is non-zero
        # (every dim moved 3.0 → 5.0 on the raw 1–5 scale; 50.0 on the
        # 0–100 normalized scale).
        any_delta = next(iter(comparison["deltas"].values()))
        assert any_delta["delta"] != 0
        assert any_delta["current"] > any_delta["previous"]

        # ── (4) regression detection — must now run end-to-end.
        verdict = detect_check_in_regression(uid)
        assert verdict["evaluated"] is True, verdict
        # Strict improvement (3.0 → 5.0) → no regression flagged.
        assert verdict["regression_detected"] is False
        assert verdict["baseline_snapshot_id"] == grad_snap_id
        assert verdict["check_in_snapshot_id"] == check_in_snap_id

        # ── (5) bookkeeping invariants: cycle untouched, no DAS rows added.
        with get_db_session() as conn:
            cycle_row = conn.execute(
                "SELECT reassessment_cycle FROM users WHERE id = ?", (uid,)
            ).fetchone()
            das_after = conn.execute(
                "SELECT COUNT(*) AS n FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]
        assert cycle_row["reassessment_cycle"] == 2  # unchanged
        assert das_after == das_before  # no rows added


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
        scores = {"dim1": {"score": 4.0}, "dim2": {"score": 4.5}}
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
        _create_profile_snapshot(uid, {"dim1": {"score": 3.0}}, "absorber")

        # Fetch results
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["user_id"] == uid
        assert data["education"]["exists"] is True
        assert data["development"]["has_roadmap"] is True
        assert data["development"]["practice_count"] == 1
        assert len(data["profiles"]) >= 1

    def test_check_in_regression_detail_in_results_after_lifecycle(
        self, authenticated_client
    ):
        """Graduated-user journey: after a check-in, /api/results surfaces
        latest_regression_detail.evaluated==true and latest_comparison.deltas.

        Per spec B13.2 (lifecycle e2e criterion): asserts that after a seeded
        check-in, the API response's check_ins block carries the deterministic
        regression verdict via the new fields.
        """
        uid = authenticated_client.user_id

        # ── Step 1: Create graduation baseline snapshot (transmuter, high scores) ──
        baseline_ts = (datetime.utcnow() - timedelta(days=30)).isoformat()
        baseline_scores = {"dim1": {"score": 4.0}, "dim2": {"score": 4.5}}
        baseline_id = _create_profile_snapshot(
            uid, baseline_scores, "transmuter", baseline_ts
        )

        # Save a graduation record — final_snapshot_id will point to baseline_id
        # (it is the only/most-recent snapshot at this point).
        indicators = {
            "pattern_stability": {"met": True},
            "quadrant_consolidation": {"met": True},
        }
        grad_result = save_graduation_record(uid, "Growth story.", indicators)
        assert grad_result["saved"] is True

        # Confirm graduation baseline was set correctly
        record = get_graduation_record(uid)
        assert record["exists"] is True
        assert record["final_snapshot_id"] == baseline_id

        # ── Step 2: Create a later check-in snapshot (still transmuter, similar scores) ──
        # Scores chosen so regression is NOT triggered (all drops < 15 pts):
        #   dim1: normalize(4.0)=75 → normalize(3.5)=62.5, drop=12.5 < 15 → ok
        #   dim2: normalize(4.5)=87.5 → normalize(4.1)=77.5, drop=10.0 < 15 → ok
        checkin_scores = {"dim1": {"score": 3.5}, "dim2": {"score": 4.1}}
        checkin_id = _create_profile_snapshot(uid, checkin_scores, "transmuter")

        # Seed the check_in_log row (regression_detected=False matches the scores above)
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO check_in_log "
                "(id, user_id, snapshot_id, graduation_snapshot_id, regression_detected, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    uid,
                    checkin_id,
                    baseline_id,
                    False,
                    datetime.utcnow().isoformat(),
                ),
            )

        # ── Step 3: Fetch /api/results and assert new fields ──
        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        data = resp.json()

        ci = data["check_ins"]
        assert ci["count"] == 1

        # Spec criterion: latest_regression_detail.evaluated == true
        detail = ci["latest_regression_detail"]
        assert detail is not None, "latest_regression_detail must be populated after check-in"
        assert detail["evaluated"] is True, "evaluated must be True when baseline + check-in exist"
        assert detail["regression_detected"] is False  # scores within threshold
        assert detail["regressed_dimensions"] == []
        assert detail["quadrant"]["downgraded"] is False
        assert detail["threshold_normalized"] == 15.0

        # latest_comparison must be populated (baseline_id != checkin_id)
        comp = ci["latest_comparison"]
        assert comp is not None, "latest_comparison must be populated when snapshots differ"
        assert len(comp["deltas"]) > 0
        for dim, delta in comp["deltas"].items():
            assert "previous_normalized" in delta
            assert "current_normalized" in delta
            assert "delta_normalized" in delta
            assert delta["direction"] in ("up", "down", "stable")
        assert "quadrant_shift" in comp

        # Backward-compat: latest_regression boolean still present
        assert "latest_regression" in ci
        assert ci["latest_regression"] is False

    # ── BE-002: Extended DevelopmentResponse contract tests ──────────────────

    def test_development_practices_in_results(self, authenticated_client):
        """GET /api/results returns practices list with 3 items and expected fields."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        roadmap = {
            "practices": [
                {"practice_id": "p1", "title": "Morning check", "dimension": "Emotional Awareness",
                 "sub_dimension": "Emotion Recognition", "transmutation_operation": "filtering"},
                {"practice_id": "p2", "title": "Body scan", "dimension": "Physical Awareness",
                 "sub_dimension": None, "transmutation_operation": None},
                {"practice_id": "p3", "title": "Values sit", "dimension": "Mindfulness",
                 "sub_dimension": None, "transmutation_operation": "amplification"},
            ]
        }
        save_roadmap(uid, roadmap)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        dev = resp.json()["development"]

        assert dev["has_roadmap"] is True
        practices = dev["practices"]
        assert len(practices) == 3

        p1 = next(p for p in practices if p["practice_id"] == "p1")
        assert p1["title"] == "Morning check"
        assert p1["dimension"] == "Emotional Awareness"
        assert "entry_count" in p1
        assert "last_self_rating" in p1
        assert "last_entry_at" in p1

    def test_development_gate_in_results(self, authenticated_client):
        """/api/results gate dict matches get_development_gate_progress output."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        save_roadmap(uid, {"steps": []})
        log_practice_entry(uid, "p1", "test", 5)

        from agents.transmutation.tools import get_development_gate_progress
        with get_db_session() as conn:
            expected = get_development_gate_progress(conn, uid)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        gate = resp.json()["development"]["gate"]

        assert gate["entries_logged"] == expected["entries_logged"]
        assert gate["entries_required"] == expected["entries_required"]
        assert gate["days_elapsed"] == expected["days_elapsed"]
        assert gate["days_required"] == expected["days_required"]
        assert gate["passed"] == expected["passed"]
        assert gate["via"] == expected["via"]

    def test_development_recent_entries_capped_at_20(self, authenticated_client):
        """After 25 entries, recent_entries has 20 (newest first) and total_entries==25."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        for i in range(25):
            log_practice_entry(uid, "p1", f"entry {i}", 5)

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        dev = resp.json()["development"]

        assert dev["total_entries"] == 25
        assert len(dev["recent_entries"]) == 20
        # Newest first: created_at descending
        timestamps = [e["created_at"] for e in dev["recent_entries"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_development_gate_passed_via_entries(self, authenticated_client):
        """10+ entries → gate.passed=True, gate.via='entries'."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        for _ in range(10):
            log_practice_entry(uid, "p1", "reflection", 7)

        resp = authenticated_client.get(f"/api/results/{uid}")
        gate = resp.json()["development"]["gate"]
        assert gate["passed"] is True
        assert gate["via"] == "entries"

    def test_development_gate_passed_via_time(self, authenticated_client):
        """2 entries + 31-day-old roadmap → gate.passed=True, gate.via='time'."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))
            old_ts = (datetime.utcnow() - timedelta(days=31)).isoformat()
            conn.execute(
                "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), uid, "{}", old_ts),
            )

        log_practice_entry(uid, "p1", "entry a", 6)
        log_practice_entry(uid, "p1", "entry b", 6)

        resp = authenticated_client.get(f"/api/results/{uid}")
        gate = resp.json()["development"]["gate"]
        assert gate["passed"] is True
        assert gate["via"] == "time"
        assert gate["days_elapsed"] >= 30

    def test_development_no_roadmap_returns_empty_practices_and_null_days_elapsed(
        self, authenticated_client
    ):
        """No roadmap → has_roadmap False, practices==[], gate.days_elapsed null."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        dev = resp.json()["development"]

        assert dev["has_roadmap"] is False
        assert dev["practices"] == []
        assert dev["gate"]["days_elapsed"] is None

    def test_development_legacy_journal_null_dimension(self, authenticated_client):
        """Journal rows with NULL dimension return dimension=None without validation error."""
        uid = authenticated_client.user_id
        with get_db_session() as conn:
            conn.execute("UPDATE users SET current_phase = 'development' WHERE id = ?", (uid,))
            # Insert a legacy row without dimension (pre-migration-007 shape)
            conn.execute(
                "INSERT INTO practice_journal (id, user_id, practice_id, reflection, self_rating, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), uid, "legacy-p", "old entry", 5,
                 datetime.utcnow().isoformat()),
            )

        resp = authenticated_client.get(f"/api/results/{uid}")
        assert resp.status_code == 200
        entries = resp.json()["development"]["recent_entries"]
        assert len(entries) == 1
        assert entries[0]["dimension"] is None

    def test_development_results_enforces_same_user_403(self, authenticated_client):
        """GET /api/results/{other_uid} as authenticated user returns 403."""
        # Create a different user directly in DB
        other_uid = str(uuid.uuid4())
        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
                (other_uid, "Other", f"{other_uid}@test.com", "hash", "development"),
            )
        resp = authenticated_client.get(f"/api/results/{other_uid}")
        assert resp.status_code == 403


# ── Deterministic Sentinel Reassessment Journey ──────────────────────────────


def _full_responses(score: int = 3) -> dict:
    """Answer every question in the bank with the given Likert score."""
    qb = get_question_bank()
    return {
        q["id"]: {"score": score}
        for dim in qb.get_dimensions()
        for q in qb.get_questions_by_dimension(dim)
    }


def _baseline_scores(score: float = 3.0) -> dict:
    """Build a baseline snapshot scores dict for all dims/sub-dims."""
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


def _seed_assessment_state(user_id: str, responses: dict) -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO assessment_state (id, user_id, responses, scenario_responses, current_phase, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, json.dumps(responses), "{}", "reassessment",
             datetime.utcnow().isoformat()),
        )


def _das_map(user_id: str) -> dict:
    with get_db_session() as conn:
        rows = conn.execute(
            "SELECT dimension, last_assessed_cycle, last_assessment_kind, last_score, "
            "flagged_for_full_reassessment FROM dimension_assessment_state WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {r["dimension"]: dict(r) for r in rows}


def _reassessment_cycle(user_id: str) -> int:
    with get_db_session() as conn:
        row = conn.execute("SELECT reassessment_cycle FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["reassessment_cycle"]


class TestDeterministicReassessmentJourney:
    """End-to-end deterministic reassessment: baseline → blended snapshot → next cycle.

    Exercises the full tool chain (select_reassessment_targets →
    generate_reassessment_snapshot → save_profile_snapshot) and asserts the
    persistence invariants: cycle increment, dimension_assessment_state upserts,
    blended scores, shift flagging, and staleness progression across cycles.
    """

    def test_baseline_then_first_reassessment_cycle(self):
        qb = get_question_bank()
        uid = _create_user(phase="reassessment")

        # Baseline: prior snapshot + seed DAS at cycle 0 via the baseline save path.
        _create_profile_snapshot(uid, _baseline_scores(3.0))
        # Simulate the baseline DAS seeding that save_profile_snapshot would do.
        for dim in qb.get_dimensions():
            with get_db_session() as conn:
                conn.execute(
                    "INSERT INTO dimension_assessment_state (id, user_id, dimension, last_assessed_cycle, "
                    "last_assessment_kind, last_score, flagged_for_full_reassessment, updated_at) "
                    "VALUES (?, ?, ?, 0, 'baseline', 3.0, 0, ?)",
                    (str(uuid.uuid4()), uid, dim, datetime.utcnow().isoformat()),
                )

        # Fresh reassessment responses (all high) drive a blend.
        _seed_assessment_state(uid, _full_responses(5))

        snap = generate_reassessment_snapshot(uid)
        assert snap["event_type"] == "reassessment.scored"
        assert snap["current_cycle"] == 1
        # Partition is exhaustive.
        sentinel = snap["sentinel"]
        covered = set(sentinel["targeted_dimensions"]) | set(sentinel["sentinel_dimensions"]) | set(sentinel["carried_dimensions"])
        assert covered == set(qb.get_dimensions())

        # Persist and verify invariants.
        save_result = save_profile_snapshot(uid, "First reassessment narrative.")
        assert save_result["saved"] is True

        assert _reassessment_cycle(uid) == 1
        das = _das_map(uid)
        for dim in sentinel["sentinel_dimensions"]:
            assert das[dim]["last_assessed_cycle"] == 1
            assert das[dim]["last_assessment_kind"] == "sentinel"
        for dim in sentinel["targeted_dimensions"]:
            assert das[dim]["last_assessed_cycle"] == 1
        # Carried dims keep their cycle-0 record (untouched this cycle).
        for dim in sentinel["carried_dimensions"]:
            assert das[dim]["last_assessed_cycle"] == 0

    def test_staleness_progresses_across_cycles(self):
        qb = get_question_bank()
        uid = _create_user(phase="reassessment")
        _create_profile_snapshot(uid, _baseline_scores(3.0))
        for dim in qb.get_dimensions():
            with get_db_session() as conn:
                conn.execute(
                    "INSERT INTO dimension_assessment_state (id, user_id, dimension, last_assessed_cycle, "
                    "last_assessment_kind, last_score, flagged_for_full_reassessment, updated_at) "
                    "VALUES (?, ?, ?, 0, 'baseline', 3.0, 0, ?)",
                    (str(uuid.uuid4()), uid, dim, datetime.utcnow().isoformat()),
                )

        # Cycle 1
        _seed_assessment_state(uid, _full_responses(4))
        generate_reassessment_snapshot(uid)
        save_profile_snapshot(uid, "cycle 1")
        assert _reassessment_cycle(uid) == 1

        # Carried dims from cycle 1 are now 1 cycle stale.
        staleness = get_dimension_staleness(uid)
        assert staleness["current_cycle"] == 1
        # At least one carried dim should show staleness == 1.
        assert any(v == 1 for v in staleness["staleness"].values())

    def test_large_shift_flags_dimension_for_full_reassessment(self):
        qb = get_question_bank()
        uid = _create_user(phase="reassessment")
        # Prior at the floor, fresh at the ceiling → maximum normalized shift.
        _create_profile_snapshot(uid, _baseline_scores(1.0))
        for dim in qb.get_dimensions():
            with get_db_session() as conn:
                conn.execute(
                    "INSERT INTO dimension_assessment_state (id, user_id, dimension, last_assessed_cycle, "
                    "last_assessment_kind, last_score, flagged_for_full_reassessment, updated_at) "
                    "VALUES (?, ?, ?, 0, 'baseline', 1.0, 0, ?)",
                    (str(uuid.uuid4()), uid, dim, datetime.utcnow().isoformat()),
                )
        _seed_assessment_state(uid, _full_responses(5))

        snap = generate_reassessment_snapshot(uid)
        flagged = snap["sentinel"]["flagged_for_full_reassessment"]
        # The huge swing on sentinel dims should flag at least one.
        assert len(flagged) >= 1

        save_profile_snapshot(uid, "flagging cycle")
        das = _das_map(uid)
        for dim in flagged:
            assert das[dim]["flagged_for_full_reassessment"] == 1

    def test_reassessment_without_prior_snapshot_errors(self):
        uid = _create_user(phase="reassessment")
        _seed_assessment_state(uid, _full_responses(3))
        result = generate_reassessment_snapshot(uid)
        assert "error" in result
        assert _reassessment_cycle(uid) == 0  # no cycle increment on error
