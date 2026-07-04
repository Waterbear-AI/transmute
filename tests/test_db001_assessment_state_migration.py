"""Tests for the 010_add_assessment_state_columns.sql migration.

Verifies:
- assessment_state gains assessment_tier, flagged_dimensions,
  deep_dive_dimensions, early_result columns with correct types and defaults
- assessment_tier is NOT NULL with default 'transmute_core'
- AssessmentState.from_row correctly maps the new columns, including
  JSON-decoding the nullable list/dict columns and handling NULLs
"""

import uuid

from db.database import get_db_session
from models.assessment_state import AssessmentState


def _insert_user(user_id: str, email: str) -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "Test User", email, "hashedpw"),
        )


class TestAssessmentStateMigrationSchema:
    """Verify the assessment_state table schema after migration."""

    def test_new_columns_exist(self):
        with get_db_session() as conn:
            cols = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(assessment_state)").fetchall()
            }

        for col in (
            "assessment_tier",
            "flagged_dimensions",
            "deep_dive_dimensions",
            "early_result",
        ):
            assert col in cols, f"Column '{col}' should exist in assessment_state"

    def test_assessment_tier_not_null_with_default(self):
        with get_db_session() as conn:
            cols = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(assessment_state)").fetchall()
            }

        assert cols["assessment_tier"]["notnull"] == 1, "assessment_tier should be NOT NULL"
        assert cols["assessment_tier"]["dflt_value"] == "'transmute_core'"

    def test_json_columns_are_nullable(self):
        with get_db_session() as conn:
            cols = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(assessment_state)").fetchall()
            }

        for col in ("flagged_dimensions", "deep_dive_dimensions", "early_result"):
            assert cols[col]["notnull"] == 0, f"{col} should be nullable"

    def test_default_value_on_insert(self):
        """A new row that omits assessment_tier defaults to 'transmute_core'."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        aid = str(uuid.uuid4())

        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO assessment_state (id, user_id) VALUES (?, ?)",
                (aid, uid),
            )
            row = conn.execute(
                "SELECT * FROM assessment_state WHERE id=?", (aid,)
            ).fetchone()

        assert row["assessment_tier"] == "transmute_core"
        assert row["flagged_dimensions"] is None
        assert row["deep_dive_dimensions"] is None
        assert row["early_result"] is None


class TestAssessmentStateFromRow:
    """Verify AssessmentState.from_row maps the new columns correctly."""

    def test_from_row_maps_defaults(self):
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        aid = str(uuid.uuid4())

        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO assessment_state (id, user_id) VALUES (?, ?)",
                (aid, uid),
            )
            row = conn.execute(
                "SELECT * FROM assessment_state WHERE id=?", (aid,)
            ).fetchone()

        state = AssessmentState.from_row(row)
        assert state.assessment_tier == "transmute_core"
        assert state.flagged_dimensions is None
        assert state.deep_dive_dimensions is None
        assert state.early_result is None

    def test_from_row_maps_populated_json_columns(self):
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        aid = str(uuid.uuid4())

        with get_db_session() as conn:
            conn.execute(
                """INSERT INTO assessment_state
                   (id, user_id, assessment_tier, flagged_dimensions,
                    deep_dive_dimensions, early_result)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    aid,
                    uid,
                    "awareness_core",
                    '["emotional_regulation", "systems_thinking"]',
                    '["emotional_regulation"]',
                    '{"tier": "transmute_core", "summary": "early result"}',
                ),
            )
            row = conn.execute(
                "SELECT * FROM assessment_state WHERE id=?", (aid,)
            ).fetchone()

        state = AssessmentState.from_row(row)
        assert state.assessment_tier == "awareness_core"
        assert state.flagged_dimensions == ["emotional_regulation", "systems_thinking"]
        assert state.deep_dive_dimensions == ["emotional_regulation"]
        assert state.early_result == {"tier": "transmute_core", "summary": "early result"}
