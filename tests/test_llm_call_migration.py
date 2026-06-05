"""Tests for the 007_llm_call_history.sql migration.

Verifies:
- llm_calls table created with correct columns, types, constraints, defaults
- Foreign key constraints to adk_sessions and users with ON DELETE CASCADE
- Indexes idx_llm_calls_user_id and idx_llm_calls_user_created exist
- Migration applies successfully via run_migrations (tested via reset_db autouse)
"""

import sqlite3
import uuid

import pytest

from db.database import get_db_session


def _insert_user(user_id: str, email: str) -> None:
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "Test User", email, "hashedpw"),
        )


def _insert_session(user_id: str) -> str:
    sid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO adk_sessions (session_id, user_id, app_name, created_at)
               VALUES (?, ?, 'transmutation', datetime('now'))""",
            (sid, user_id),
        )
    return sid


def _insert_llm_call(
    user_id: str,
    session_id: str | None = None,
    author: str = "test_agent",
    phase: str = "orientation",
    model_id: str = "gemini-1.5-flash",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.001,
) -> int:
    with get_db_session() as conn:
        cursor = conn.execute(
            """INSERT INTO llm_calls
               (session_id, user_id, author, phase, model_id,
                input_tokens, output_tokens, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, author, phase, model_id,
             input_tokens, output_tokens, cost_usd),
        )
        return cursor.lastrowid


class TestLlmCallsMigration:
    """Verify the llm_calls table schema is correct after migration."""

    def test_table_exists(self):
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_calls'"
            ).fetchone()
        assert row is not None, "llm_calls table should exist after migration"

    def test_columns_and_defaults(self):
        """Verify all expected columns exist with correct defaults."""
        with get_db_session() as conn:
            cols = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(llm_calls)").fetchall()
            }

        expected_columns = [
            "id", "session_id", "user_id", "author", "phase",
            "model_id", "input_tokens", "output_tokens", "cost_usd", "created_at",
        ]
        for col in expected_columns:
            assert col in cols, f"Column '{col}' should exist in llm_calls"

        # NOT NULL constraints
        assert cols["user_id"]["notnull"] == 1, "user_id should be NOT NULL"
        assert cols["input_tokens"]["notnull"] == 1, "input_tokens should be NOT NULL"
        assert cols["output_tokens"]["notnull"] == 1, "output_tokens should be NOT NULL"
        assert cols["cost_usd"]["notnull"] == 1, "cost_usd should be NOT NULL"

        # Nullable columns
        assert cols["session_id"]["notnull"] == 0, "session_id should be nullable"
        assert cols["author"]["notnull"] == 0, "author should be nullable"
        assert cols["phase"]["notnull"] == 0, "phase should be nullable"
        assert cols["model_id"]["notnull"] == 0, "model_id should be nullable"

    def test_primary_key_autoincrement(self):
        """id should be INTEGER PRIMARY KEY AUTOINCREMENT."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")

        row_id_1 = _insert_llm_call(uid)
        row_id_2 = _insert_llm_call(uid)
        assert row_id_2 > row_id_1, "id should auto-increment"

    def test_default_values(self):
        """input_tokens, output_tokens, cost_usd default to 0; created_at is set."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")

        with get_db_session() as conn:
            conn.execute(
                "INSERT INTO llm_calls (user_id) VALUES (?)", (uid,)
            )
            row = conn.execute(
                "SELECT * FROM llm_calls WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()

        assert row["input_tokens"] == 0
        assert row["output_tokens"] == 0
        assert row["cost_usd"] == 0.0
        assert row["created_at"] is not None

    def test_indexes_exist(self):
        """idx_llm_calls_user_id and idx_llm_calls_user_created should be created."""
        with get_db_session() as conn:
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='llm_calls'"
                ).fetchall()
            }

        assert "idx_llm_calls_user_id" in indexes, \
            "idx_llm_calls_user_id index should exist"
        assert "idx_llm_calls_user_created" in indexes, \
            "idx_llm_calls_user_created index should exist"

    def test_insert_with_session(self):
        """Can insert a full llm_calls row referencing both user and session."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        sid = _insert_session(uid)

        row_id = _insert_llm_call(
            user_id=uid,
            session_id=sid,
            author="transmutation_agent",
            phase="assessment",
            model_id="gemini-1.5-flash",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.002,
        )

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT * FROM llm_calls WHERE id=?", (row_id,)
            ).fetchone()

        assert row["user_id"] == uid
        assert row["session_id"] == sid
        assert row["author"] == "transmutation_agent"
        assert row["phase"] == "assessment"
        assert row["model_id"] == "gemini-1.5-flash"
        assert row["input_tokens"] == 200
        assert row["output_tokens"] == 100
        assert row["cost_usd"] == pytest.approx(0.002)

    def test_insert_without_session(self):
        """Can insert a row with null session_id."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")

        row_id = _insert_llm_call(user_id=uid, session_id=None)

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT session_id FROM llm_calls WHERE id=?", (row_id,)
            ).fetchone()

        assert row["session_id"] is None


class TestLlmCallsCascadeDelete:
    """Verify ON DELETE CASCADE behavior."""

    def test_cascade_delete_on_user_delete(self):
        """Deleting a user should cascade-delete their llm_calls rows."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        _insert_llm_call(uid)
        _insert_llm_call(uid)

        with get_db_session() as conn:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE user_id=?", (uid,)
            ).fetchone()[0]
        assert count_before == 2

        with get_db_session() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))

        with get_db_session() as conn:
            count_after = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE user_id=?", (uid,)
            ).fetchone()[0]
        assert count_after == 0, "llm_calls should be cascade-deleted when user is deleted"

    def test_cascade_delete_on_session_delete(self):
        """Deleting a session should cascade-delete its llm_calls rows."""
        uid = str(uuid.uuid4())
        _insert_user(uid, f"{uid}@test.example.com")
        sid = _insert_session(uid)

        _insert_llm_call(uid, session_id=sid)
        _insert_llm_call(uid, session_id=sid)
        # One call with no session — should remain
        _insert_llm_call(uid, session_id=None)

        with get_db_session() as conn:
            conn.execute("DELETE FROM adk_sessions WHERE session_id=?", (sid,))

        with get_db_session() as conn:
            session_calls = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE session_id=?", (sid,)
            ).fetchone()[0]
            all_user_calls = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE user_id=?", (uid,)
            ).fetchone()[0]

        assert session_calls == 0, "session-linked llm_calls should be deleted"
        assert all_user_calls == 1, "unlinked llm_call should remain"
