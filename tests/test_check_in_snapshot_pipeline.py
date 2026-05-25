"""Tests for generate_check_in_snapshot (the deterministic check-in scoring helper).

Covers the happy path, every documented missing-precondition error branch,
the contract shape of both success and error returns, and the _profile_cache
marker invariant. Save-side persistence invariants (no DAS seeding, no
reassessment_cycle bump) are exercised by BE-002 + TEST-001 once the
save_profile_snapshot branch lands.
"""

import json
import uuid
from datetime import datetime, timedelta

from db.database import get_db_session
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.tools import (
    generate_check_in_snapshot,
    save_profile_snapshot,
    generate_profile_snapshot,
    SNAPSHOT_KIND_CHECK_IN,
    _profile_cache,
)


def _create_user(uid: str | None = None, phase: str = "check_in") -> str:
    uid = uid or str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, "Test", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _full_responses(score: int = 4) -> dict:
    """Answer every question in the bank with the given Likert score."""
    qb = get_question_bank()
    return {
        q["id"]: {"score": score}
        for dim in qb.get_dimensions()
        for q in qb.get_questions_by_dimension(dim)
    }


def _seed_assessment_state(user_id: str, responses: dict) -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO assessment_state (id, user_id, responses, scenario_responses, current_phase, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, json.dumps(responses), "{}", "check_in",
             datetime.utcnow().isoformat()),
        )


def _create_baseline_snapshot(user_id: str, archetype: str = "transmuter") -> str:
    """Insert a production-shape baseline snapshot row for use as the graduation anchor."""
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO profile_snapshots (id, user_id, scores, quadrant_placement, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                sid,
                user_id,
                json.dumps({"dim1": {"score": 4.0}}),
                json.dumps({"archetype": archetype}),
                (datetime.utcnow() - timedelta(days=90)).isoformat(),
            ),
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


class TestGenerateCheckInSnapshot:
    def test_happy_path_returns_scored_payload(self):
        uid = _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        result = generate_check_in_snapshot(uid)

        assert "error" not in result
        assert result["event_type"] == "checkin.scored"
        assert result["has_spider_chart"] is True
        assert isinstance(result["scores"], dict)
        assert len(result["scores"]) > 0
        assert isinstance(result["quadrant"], dict)
        assert "archetype" in result["quadrant"]

    def test_cache_marker_set_to_check_in_kind(self):
        uid = _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        generate_check_in_snapshot(uid)

        assert uid in _profile_cache
        assert _profile_cache[uid]["kind"] == SNAPSHOT_KIND_CHECK_IN
        assert SNAPSHOT_KIND_CHECK_IN == "check_in"
        assert "scores" in _profile_cache[uid]
        assert "quadrant" in _profile_cache[uid]
        assert "spider_chart" in _profile_cache[uid]
        # Reassessment marker MUST be absent on the check-in path.
        assert "sentinel" not in _profile_cache[uid]

    def test_no_assessment_state_returns_error(self):
        uid = _create_user()
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        result = generate_check_in_snapshot(uid)
        assert result == {"error": "No assessment data found for user."}
        assert uid not in _profile_cache

    def test_empty_responses_returns_no_assessment_error(self):
        """Empty responses dict is treated identically to a missing row (PD-4)."""
        uid = _create_user()
        _seed_assessment_state(uid, {})  # row exists, but responses == {}
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        result = generate_check_in_snapshot(uid)
        assert result == {"error": "No assessment data found for user."}
        assert uid not in _profile_cache

    def test_no_graduation_record_returns_error(self):
        uid = _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        # Intentionally no graduation record.

        result = generate_check_in_snapshot(uid)
        assert result == {"error": "No graduation record found."}
        assert uid not in _profile_cache

    def test_null_final_snapshot_id_returns_no_baseline_error(self):
        uid = _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        _create_graduation_record(uid, None)  # NULL final_snapshot_id

        result = generate_check_in_snapshot(uid)
        assert result == {"error": "No graduation baseline snapshot found."}
        assert uid not in _profile_cache

    def test_baseline_belongs_to_another_user_returns_no_baseline_error(self):
        """The helper enforces user-scope on the baseline snapshot lookup —
        a snapshot owned by another user does NOT satisfy the precondition,
        even if graduation_record references its id (defends against id leakage)."""
        uid = _create_user()
        other = _create_user(phase="graduated")
        _seed_assessment_state(uid, _full_responses(4))
        # Baseline exists, but is owned by `other`, not `uid`.
        foreign_baseline = _create_baseline_snapshot(other)
        _create_graduation_record(uid, foreign_baseline)

        result = generate_check_in_snapshot(uid)
        assert result == {"error": "No graduation baseline snapshot found."}
        assert uid not in _profile_cache

    def test_success_contract_shape(self):
        """Pin the exact key set on the success return."""
        uid = _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        result = generate_check_in_snapshot(uid)

        required = {"event_type", "scores", "quadrant", "insufficient_dimensions", "has_spider_chart"}
        assert required.issubset(result.keys())
        # flow_data is conditional on the scoring engine producing one — assert
        # only that, if present, it round-trips JSON-serialisable.
        if "flow_data" in result:
            assert isinstance(result["flow_data"], dict)
        assert "error" not in result

    def test_error_contract_shape(self):
        """Every error return has exactly one key: 'error'."""
        uid = _create_user()
        result = generate_check_in_snapshot(uid)
        assert set(result.keys()) == {"error"}
        assert isinstance(result["error"], str)

    def test_does_not_increment_reassessment_cycle(self):
        """generate is a pure read+stage step — must NOT mutate users.reassessment_cycle."""
        uid = _create_user()
        with get_db_session() as conn:
            conn.execute("UPDATE users SET reassessment_cycle = 2 WHERE id = ?", (uid,))
        _seed_assessment_state(uid, _full_responses(4))
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        generate_check_in_snapshot(uid)

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT reassessment_cycle FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["reassessment_cycle"] == 2  # unchanged

    def test_does_not_write_dimension_assessment_state(self):
        """generate must not touch dimension_assessment_state; that's a save-time concern."""
        uid = _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)

        with get_db_session() as conn:
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]

        generate_check_in_snapshot(uid)

        with get_db_session() as conn:
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]
        assert before == after  # no rows added


class TestSaveCheckInBranch:
    """save_profile_snapshot's new check-in branch:
    persist snapshot + (conditional) moral_ledger; explicitly NO DAS seeding
    and NO users.reassessment_cycle bump."""

    def _setup_ready_to_save(self, uid: str | None = None) -> tuple[str, str]:
        uid = uid or _create_user()
        _seed_assessment_state(uid, _full_responses(4))
        baseline = _create_baseline_snapshot(uid)
        _create_graduation_record(uid, baseline)
        gen = generate_check_in_snapshot(uid)
        assert "error" not in gen
        return uid, baseline

    def test_save_persists_snapshot_row(self):
        uid, _ = self._setup_ready_to_save()
        result = save_profile_snapshot(uid, "3-month check-in narrative.")

        assert result["saved"] is True
        assert result["event_type"] == "checkin.snapshot_saved"
        snap_id = result["snapshot_id"]

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT id, user_id, interpretation FROM profile_snapshots WHERE id = ?",
                (snap_id,),
            ).fetchone()
        assert row is not None
        assert row["user_id"] == uid
        assert row["interpretation"] == "3-month check-in narrative."

    def test_save_stamps_kind_into_quadrant_placement(self):
        """The persisted snapshot is self-describing — quadrant_placement.kind ==
        SNAPSHOT_KIND_CHECK_IN mirrors how reassessment stamps `sentinel`."""
        uid, _ = self._setup_ready_to_save()
        result = save_profile_snapshot(uid, "narrative")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT quadrant_placement FROM profile_snapshots WHERE id = ?",
                (result["snapshot_id"],),
            ).fetchone()
        placement = json.loads(row["quadrant_placement"])
        assert placement.get("kind") == SNAPSHOT_KIND_CHECK_IN
        # archetype key must still be present (the production placement shape).
        assert "archetype" in placement
        # sentinel key MUST NOT be present on the check-in branch.
        assert "sentinel" not in placement

    def test_save_does_not_bump_reassessment_cycle(self):
        uid = _create_user()
        with get_db_session() as conn:
            conn.execute("UPDATE users SET reassessment_cycle = 7 WHERE id = ?", (uid,))
        self._setup_ready_to_save(uid)

        save_profile_snapshot(uid, "narrative")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT reassessment_cycle FROM users WHERE id = ?", (uid,)
            ).fetchone()
        assert row["reassessment_cycle"] == 7  # untouched

    def test_save_does_not_seed_dimension_assessment_state(self):
        uid, _ = self._setup_ready_to_save()
        with get_db_session() as conn:
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]

        save_profile_snapshot(uid, "narrative")

        with get_db_session() as conn:
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]
        assert before == after  # no rows added

    def test_save_inserts_moral_ledger_when_flow_profile_present(self):
        """When generate_check_in_snapshot produces a flow_profile (the common
        path), save persists Moral Capital (C+) and Moral Debt (C-) to
        moral_ledger as one new row, mirroring baseline behaviour."""
        uid, _ = self._setup_ready_to_save()
        # If the generator produced a flow_profile, exactly 1 ledger row should
        # be added on save. If it didn't, we assert zero (PD-2 default A but
        # defensive when the engine cannot produce one — see below test).
        had_flow = _profile_cache[uid].get("flow_profile") is not None
        result = save_profile_snapshot(uid, "narrative")

        with get_db_session() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM moral_ledger WHERE snapshot_id = ?",
                (result["snapshot_id"],),
            ).fetchone()["n"]
        assert n == (1 if had_flow else 0)

    def test_save_skips_moral_ledger_when_flow_profile_absent(self):
        """The no-flow branch (defensive) inserts 0 rows. Direct-injected cache
        because reaching this branch via generate would require contriving a
        flow-engine failure, which is out of scope for this task."""
        uid = _create_user()
        # Direct-inject a cache entry with kind=check_in and no flow_profile.
        _profile_cache[uid] = {
            "scores": {"dim1": {"score": 4.0}},
            "quadrant": {"archetype": "transmuter"},
            "insufficient_dimensions": [],
            "spider_chart": b"\x89PNG",  # placeholder bytes; column is BLOB
            "flow_profile": None,
            "kind": SNAPSHOT_KIND_CHECK_IN,
        }

        result = save_profile_snapshot(uid, "narrative")

        with get_db_session() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM moral_ledger WHERE snapshot_id = ?",
                (result["snapshot_id"],),
            ).fetchone()["n"]
        assert n == 0

    def test_save_pops_profile_cache(self):
        uid, _ = self._setup_ready_to_save()
        assert uid in _profile_cache  # generated, awaiting save
        save_profile_snapshot(uid, "narrative")
        assert uid not in _profile_cache  # popped

    def test_save_returns_check_in_event_type_not_profile_snapshot(self):
        """Cheap precedence guard: check-in branch must take priority over
        baseline default and reach the new event_type."""
        uid, _ = self._setup_ready_to_save()
        result = save_profile_snapshot(uid, "narrative")
        assert result["event_type"] == "checkin.snapshot_saved"
        assert result["event_type"] != "profile.snapshot"


class TestExistingBranchRegressions:
    """Regression guards: BE-002 must NOT change the baseline or reassessment paths."""

    def test_baseline_path_still_seeds_das_at_cycle_zero(self):
        uid = _create_user(phase="assessment")
        _seed_assessment_state(uid, _full_responses(3))

        gen = generate_profile_snapshot(uid)
        assert "error" not in gen
        # No sentinel, no kind → baseline branch.
        assert _profile_cache[uid].get("sentinel") is None
        assert _profile_cache[uid].get("kind") is None

        save_profile_snapshot(uid, "baseline narrative")

        with get_db_session() as conn:
            rows = conn.execute(
                "SELECT dimension, last_assessed_cycle, last_assessment_kind FROM dimension_assessment_state WHERE user_id = ?",
                (uid,),
            ).fetchall()
        assert len(rows) >= 1  # baseline seeded at least one dim
        for row in rows:
            assert row["last_assessed_cycle"] == 0
            assert row["last_assessment_kind"] == "baseline"

    def test_baseline_path_does_not_stamp_kind_or_sentinel(self):
        uid = _create_user(phase="assessment")
        _seed_assessment_state(uid, _full_responses(3))
        generate_profile_snapshot(uid)
        result = save_profile_snapshot(uid, "baseline narrative")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT quadrant_placement FROM profile_snapshots WHERE id = ?",
                (result["snapshot_id"],),
            ).fetchone()
        placement = json.loads(row["quadrant_placement"])
        assert "kind" not in placement
        assert "sentinel" not in placement
        # And event_type must still be the baseline one.
        assert result["event_type"] == "profile.snapshot"
