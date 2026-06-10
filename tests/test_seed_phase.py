"""Tests for scripts/seed_phase.py.

Coverage:
- Unit: CLI argument parsing (valid/invalid phases, archetypes, argument combos)
- Integration: seed_user (user creation + bcrypt), seed_assessment (responses + gate),
  seed_development (backdating, entries, gate), --force option,
  full-phase progression to 'graduated'.

All tests use the conftest reset_db fixture (autouse) for isolation.
Each test creates its own user — no shared state.
"""
from __future__ import annotations

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestArgparsing:
    """Unit tests for the CLI argument parser — no DB, no imports beyond argparse."""

    def setup_method(self):
        from scripts.seed_phase import build_parser
        self.parser = build_parser()

    def test_valid_phase_assessment(self):
        args = self.parser.parse_args(["--phase", "assessment", "--email", "a@b.com"])
        assert args.phase == "assessment"

    def test_valid_phase_graduated(self):
        args = self.parser.parse_args(["--phase", "graduated", "--email", "a@b.com"])
        assert args.phase == "graduated"

    def test_valid_phase_check_in(self):
        args = self.parser.parse_args(["--phase", "check_in", "--email", "a@b.com"])
        assert args.phase == "check_in"

    def test_invalid_phase_rejects(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(["--phase", "nonexistent", "--email", "a@b.com"])

    def test_orientation_not_a_valid_target(self):
        # orientation is excluded from choices (it is the initial state)
        with pytest.raises(SystemExit):
            self.parser.parse_args(["--phase", "orientation", "--email", "a@b.com"])

    def test_valid_archetype_absorber(self):
        args = self.parser.parse_args(
            ["--phase", "profile", "--email", "a@b.com", "--archetype", "absorber"]
        )
        assert args.archetype == "absorber"

    def test_invalid_archetype_rejects(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(
                ["--phase", "profile", "--email", "a@b.com", "--archetype", "phoenix"]
            )

    def test_defaults(self):
        args = self.parser.parse_args(["--phase", "profile", "--email", "a@b.com"])
        assert args.password == "Seed1234!"
        assert args.archetype == "transmuter"
        assert args.days_ago == 35
        assert args.entries == 10
        assert args.force is False
        assert args.with_completed_check_in is False

    def test_force_flag(self):
        args = self.parser.parse_args(
            ["--phase", "profile", "--email", "a@b.com", "--force"]
        )
        assert args.force is True

    def test_days_ago_and_entries(self):
        args = self.parser.parse_args(
            ["--phase", "development", "--email", "a@b.com", "--days-ago", "60", "--entries", "5"]
        )
        assert args.days_ago == 60
        assert args.entries == 5

    def test_db_flag(self):
        args = self.parser.parse_args(
            ["--phase", "assessment", "--email", "a@b.com", "--db", "/tmp/test.db"]
        )
        assert args.db == "/tmp/test.db"


class TestValidateArgs:
    """Unit tests for validate_args() — no DB."""

    def _make_args(self, **kwargs):
        import argparse
        defaults = {
            "phase": "development",
            "email": "x@y.com",
            "password": "Seed1234!",
            "archetype": "transmuter",
            "days_ago": 35,
            "entries": 10,
            "db": None,
            "force": False,
            "with_completed_check_in": False,
        }
        defaults.update(kwargs)
        ns = argparse.Namespace(**defaults)
        return ns

    def test_valid_args_pass(self):
        from scripts.seed_phase import validate_args
        validate_args(self._make_args())  # should not raise

    def test_days_ago_zero_raises(self):
        from scripts.seed_phase import validate_args
        with pytest.raises(Exception):
            validate_args(self._make_args(days_ago=0))

    def test_entries_zero_raises(self):
        from scripts.seed_phase import validate_args
        with pytest.raises(Exception):
            validate_args(self._make_args(entries=0))

    def test_with_completed_check_in_wrong_phase_raises(self):
        from scripts.seed_phase import validate_args
        with pytest.raises(Exception):
            validate_args(self._make_args(phase="development", with_completed_check_in=True))

    def test_with_completed_check_in_check_in_phase_ok(self):
        from scripts.seed_phase import validate_args
        validate_args(self._make_args(phase="check_in", with_completed_check_in=True))


# ---------------------------------------------------------------------------
# Integration tests: seed_user
# ---------------------------------------------------------------------------


class TestSeedUser:
    """Integration tests for seed_user() — verifies bcrypt and DB row."""

    def test_creates_user_row(self):
        from db.database import get_db_session
        from scripts.seed_phase import seed_user

        with get_db_session() as conn:
            uid = seed_user(conn, "seed@example.com", "TestPass1!")

        with get_db_session() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

        assert row is not None
        assert row["email"] == "seed@example.com"
        assert row["current_phase"] == "orientation"

    def test_password_is_bcrypt_hashed(self):
        import bcrypt
        from db.database import get_db_session
        from scripts.seed_phase import seed_user

        with get_db_session() as conn:
            uid = seed_user(conn, "bcrypt@example.com", "MySecret99")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE id = ?", (uid,)
            ).fetchone()

        assert bcrypt.checkpw(b"MySecret99", row["password_hash"].encode())

    def test_duplicate_email_raises(self):
        from db.database import get_db_session
        from scripts.seed_phase import seed_user

        with get_db_session() as conn:
            seed_user(conn, "dup@example.com", "pass1")

        with get_db_session() as conn:
            with pytest.raises(ValueError, match="already exists"):
                seed_user(conn, "dup@example.com", "pass2")


# ---------------------------------------------------------------------------
# Integration tests: seed_assessment
# ---------------------------------------------------------------------------


class TestSeedAssessment:
    """Integration tests for seed_assessment() — Likert responses + gate."""

    def _make_user(self) -> str:
        from db.database import get_db_session
        from scripts.seed_phase import seed_user
        with get_db_session() as conn:
            return seed_user(conn, f"assess-{os.urandom(4).hex()}@t.com", "pass")

    def test_writes_all_question_responses(self):
        from db.database import get_db_session
        from scripts.seed_phase import seed_assessment
        from agents.transmutation.question_bank import get_question_bank

        uid = self._make_user()
        qb = get_question_bank()
        total_qs = sum(len(qb.get_questions_by_dimension(d)) for d in qb.get_dimensions())

        with get_db_session() as conn:
            seed_assessment(conn, uid, "transmuter")

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT responses FROM assessment_state WHERE user_id = ?", (uid,)
            ).fetchone()

        responses = json.loads(row["responses"])
        assert len(responses) == total_qs

    def test_transmuter_archetype_yields_correct_placement(self):
        from db.database import get_db_session
        from scripts.seed_phase import seed_assessment, seed_profile

        uid = self._make_user()

        with get_db_session() as conn:
            seed_assessment(conn, uid, "transmuter")

        # Advance to profile so we can generate a snapshot
        from agents.transmutation.tools import advance_phase
        advance_phase(uid, "profile", reason="test")

        snapshot_id = seed_profile(uid)

        from db.database import get_db_session
        with get_db_session() as conn:
            snap = conn.execute(
                "SELECT quadrant_placement FROM profile_snapshots WHERE user_id = ?",
                (uid,),
            ).fetchone()

        placement = json.loads(snap["quadrant_placement"])
        archetype = placement.get("archetype") or placement.get("quadrant")
        assert archetype == "transmuter", f"Expected transmuter, got {archetype}"

    def test_gate_passes_after_seed(self):
        """advance_phase(profile) must succeed after seed_assessment."""
        from db.database import get_db_session
        from scripts.seed_phase import seed_assessment
        from agents.transmutation.tools import advance_phase

        uid = self._make_user()

        # orientation → assessment (no gate)
        adv = advance_phase(uid, "assessment", reason="setup")
        assert "error" not in adv, f"Unexpected error: {adv}"

        with get_db_session() as conn:
            seed_assessment(conn, uid, "conduit")

        result = advance_phase(uid, "profile", reason="gate test")
        assert "error" not in result, f"Gate failed: {result}"


# ---------------------------------------------------------------------------
# Integration tests: seed_development
# ---------------------------------------------------------------------------


class TestSeedDevelopment:
    """Integration tests for seed_development() — backdating and gate satisfaction."""

    def _seed_to_education_phase(self, archetype: str = "transmuter") -> str:
        """Helper: seed a user through to education phase (for development pre-reqs)."""
        from db.database import get_db_session
        from scripts.seed_phase import seed_user, seed_assessment, seed_profile, seed_education
        from agents.transmutation.tools import advance_phase
        import os

        email = f"dev-{os.urandom(4).hex()}@test.com"
        with get_db_session() as conn:
            uid = seed_user(conn, email, "pass")

        # orientation → assessment (no gate)
        advance_phase(uid, "assessment", reason="setup")

        with get_db_session() as conn:
            seed_assessment(conn, uid, archetype)

        advance_phase(uid, "profile", reason="setup")
        seed_profile(uid)
        advance_phase(uid, "education", reason="setup")
        seed_education(uid)
        advance_phase(uid, "development", reason="setup")
        return uid

    def test_backdated_roadmap_created_at(self):
        from db.database import get_db_session
        from scripts.seed_phase import seed_development
        from datetime import datetime

        uid = self._seed_to_education_phase()

        with get_db_session() as conn:
            seed_development(conn, uid, entries=2, days_ago=35)

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT created_at FROM development_roadmap WHERE user_id = ?",
                (uid,),
            ).fetchone()

        ts = datetime.fromisoformat(row["created_at"])
        days_elapsed = (datetime.utcnow() - ts).days
        assert days_elapsed >= 34, f"Expected >= 34 days ago, got {days_elapsed}"

    def test_entries_count_matches_parameter(self):
        from db.database import get_db_session
        from scripts.seed_phase import seed_development

        uid = self._seed_to_education_phase()

        with get_db_session() as conn:
            seed_development(conn, uid, entries=7, days_ago=35)

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM practice_journal WHERE user_id = ?",
                (uid,),
            ).fetchone()

        assert row["cnt"] == 7

    def test_development_gate_passes_with_10_entries(self):
        """advance_phase(reassessment) passes when entries >= 10."""
        from db.database import get_db_session
        from scripts.seed_phase import seed_development
        from agents.transmutation.tools import advance_phase

        uid = self._seed_to_education_phase()

        with get_db_session() as conn:
            seed_development(conn, uid, entries=10, days_ago=5)

        result = advance_phase(uid, "reassessment", reason="gate test")
        assert "error" not in result, f"Gate failed: {result}"

    def test_development_gate_passes_with_backdated_roadmap(self):
        """advance_phase(reassessment) passes when roadmap is 30+ days old."""
        from db.database import get_db_session
        from scripts.seed_phase import seed_development
        from agents.transmutation.tools import advance_phase

        uid = self._seed_to_education_phase()

        with get_db_session() as conn:
            seed_development(conn, uid, entries=2, days_ago=35)

        result = advance_phase(uid, "reassessment", reason="gate test")
        assert "error" not in result, f"Gate failed: {result}"

    def test_development_gate_fails_insufficient_entries_and_days(self):
        """Negative control: gate fails when entries < 10 AND roadmap < 30 days old."""
        from db.database import get_db_session
        from scripts.seed_phase import seed_development
        from agents.transmutation.tools import advance_phase

        uid = self._seed_to_education_phase()

        # 3 entries + only 2 days ago — satisfies neither branch of the gate
        with get_db_session() as conn:
            seed_development(conn, uid, entries=3, days_ago=2)

        result = advance_phase(uid, "reassessment", reason="negative gate test")
        assert "error" in result, (
            f"Expected gate to fail with insufficient entries+days, "
            f"but advance_phase succeeded: {result}"
        )


# ---------------------------------------------------------------------------
# Integration tests: check-in detection via detect_check_in_regression
# ---------------------------------------------------------------------------


class TestCheckInDetection:
    """Verify that detect_check_in_regression returns evaluated=True after
    a fully seeded check_in user (i.e. all prerequisite rows exist)."""

    def test_detect_regression_evaluated_after_seed(self):
        """detect_check_in_regression returns evaluated=True for a seeded check_in user."""
        from scripts.seed_phase import main
        from agents.transmutation.tools import detect_check_in_regression

        email = f"detect-{os.urandom(4).hex()}@test.com"
        rc = main([
            "--phase", "check_in",
            "--email", email,
            "--entries", "10",
            "--days-ago", "35",
        ])
        assert rc == 0

        from db.database import get_db_session
        with get_db_session() as conn:
            user = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
        uid = user["id"]

        result = detect_check_in_regression(uid)
        assert result.get("evaluated") is True, (
            f"Expected evaluated=True after full check_in seed, got: {result}"
        )


# ---------------------------------------------------------------------------
# Integration test: --force option
# ---------------------------------------------------------------------------


class TestForceOption:
    """Tests for --force: deletes existing user and re-seeds cleanly."""

    def test_force_deletes_and_recreates(self):
        from scripts.seed_phase import main

        email = f"force-{os.urandom(4).hex()}@test.com"

        # First seed
        rc1 = main(["--phase", "assessment", "--email", email])
        assert rc1 == 0

        # Without --force, should fail
        rc2 = main(["--phase", "assessment", "--email", email])
        assert rc2 == 1

        # With --force, should succeed and produce a new user
        rc3 = main(["--phase", "assessment", "--email", email, "--force"])
        assert rc3 == 0

    def test_force_leaves_only_one_user(self):
        from db.database import get_db_session
        from scripts.seed_phase import main

        email = f"oneuser-{os.urandom(4).hex()}@test.com"

        main(["--phase", "assessment", "--email", email])
        main(["--phase", "assessment", "--email", email, "--force"])

        with get_db_session() as conn:
            rows = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchall()

        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Integration test: full phase progression
# ---------------------------------------------------------------------------


class TestFullPhaseProgression:
    """Integration test: seed to 'graduated' and verify all gate evidence."""

    def test_seed_to_graduated_succeeds(self):
        from scripts.seed_phase import main

        email = f"grad-{os.urandom(4).hex()}@test.com"
        rc = main([
            "--phase", "graduated",
            "--email", email,
            "--archetype", "transmuter",
            "--entries", "10",
            "--days-ago", "35",
        ])
        assert rc == 0

    def test_graduated_user_has_all_required_rows(self):
        from db.database import get_db_session
        from scripts.seed_phase import main

        email = f"fullcheck-{os.urandom(4).hex()}@test.com"
        main([
            "--phase", "graduated",
            "--email", email,
            "--entries", "10",
            "--days-ago", "35",
        ])

        with get_db_session() as conn:
            user = conn.execute(
                "SELECT id, current_phase FROM users WHERE email = ?", (email,)
            ).fetchone()
            uid = user["id"]
            assert user["current_phase"] == "graduated"

            snap_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM profile_snapshots WHERE user_id = ?",
                (uid,),
            ).fetchone()["cnt"]
            assert snap_count >= 3, f"Expected >= 3 snapshots, got {snap_count}"

            pj_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM practice_journal WHERE user_id = ?",
                (uid,),
            ).fetchone()["cnt"]
            assert pj_count >= 10, f"Expected >= 10 journal entries, got {pj_count}"

            grad = conn.execute(
                "SELECT id FROM graduation_record WHERE user_id = ?", (uid,)
            ).fetchone()
            assert grad is not None, "graduation_record row missing"

    def test_seed_to_check_in_succeeds(self):
        """--phase check_in seeds all the way to the check_in phase."""
        from scripts.seed_phase import main

        email = f"checkin-{os.urandom(4).hex()}@test.com"
        rc = main([
            "--phase", "check_in",
            "--email", email,
            "--entries", "10",
            "--days-ago", "35",
        ])
        assert rc == 0

    def test_check_in_user_has_required_rows(self):
        """After --phase check_in: user is in check_in phase and check_in_log exists."""
        from db.database import get_db_session
        from scripts.seed_phase import main

        email = f"ci-check-{os.urandom(4).hex()}@test.com"
        main([
            "--phase", "check_in",
            "--email", email,
            "--entries", "10",
            "--days-ago", "35",
        ])

        with get_db_session() as conn:
            user = conn.execute(
                "SELECT id, current_phase FROM users WHERE email = ?", (email,)
            ).fetchone()
            uid = user["id"]
            assert user["current_phase"] == "check_in"

            # graduation_record must exist (prerequisite for check_in)
            grad = conn.execute(
                "SELECT id FROM graduation_record WHERE user_id = ?", (uid,)
            ).fetchone()
            assert grad is not None, "graduation_record row missing"

            # check_in_log must have one row
            log = conn.execute(
                "SELECT id FROM check_in_log WHERE user_id = ?", (uid,)
            ).fetchone()
            assert log is not None, "check_in_log row missing"

    def test_with_completed_check_in_advances_back_to_graduated(self):
        """--with-completed-check-in: user ends up in graduated, check_in_log exists."""
        from db.database import get_db_session
        from scripts.seed_phase import main

        email = f"ci-comp-{os.urandom(4).hex()}@test.com"
        rc = main([
            "--phase", "check_in",
            "--email", email,
            "--entries", "10",
            "--days-ago", "35",
            "--with-completed-check-in",
        ])
        assert rc == 0

        with get_db_session() as conn:
            user = conn.execute(
                "SELECT id, current_phase FROM users WHERE email = ?", (email,)
            ).fetchone()
            uid = user["id"]
            # After completing check-in the user advances back to graduated
            assert user["current_phase"] == "graduated"

            log = conn.execute(
                "SELECT id FROM check_in_log WHERE user_id = ?", (uid,)
            ).fetchone()
            assert log is not None, "check_in_log row missing after completed check-in"

    def test_seeded_user_phase_matches_target(self):
        """Each phase target produces a user in the expected phase.

        The seeder is cumulative: seeding to phase X means seeding all data
        for phase X and then advancing past it to the next phase. So the user's
        DB phase is the NEXT phase after the target in PHASE_ORDER.

        Exception: when the target is the final phase in a sequence (e.g.,
        "graduated"), the user stays in that phase.
        """
        from db.database import get_db_session
        from scripts.seed_phase import main, PHASE_ORDER

        # Map: CLI --phase argument → expected current_phase in DB after seeding.
        # Assessment data seeds and advances → "profile".
        # Profile data seeds and advances → "education".
        # Education data seeds and advances → "development".
        # Development data seeds and advances → "reassessment".
        phase_to_expected = {
            "assessment": "profile",
            "profile": "education",
            "education": "development",
            "development": "reassessment",
        }

        for phase, expected in phase_to_expected.items():
            email = f"phase-{phase}-{os.urandom(4).hex()}@test.com"
            rc = main([
                "--phase", phase,
                "--email", email,
                "--entries", "10",
                "--days-ago", "35",
            ])
            assert rc == 0, f"Seeder returned non-zero for phase={phase}"

            with get_db_session() as conn:
                row = conn.execute(
                    "SELECT current_phase FROM users WHERE email = ?", (email,)
                ).fetchone()
            assert row["current_phase"] == expected, (
                f"--phase {phase}: expected DB phase={expected}, got {row['current_phase']}"
            )
