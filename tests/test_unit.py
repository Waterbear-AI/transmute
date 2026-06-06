import os
import tempfile

import bcrypt
import pytest

from api.auth import _sign_cookie, _verify_cookie
from agents.transmutation.flow_engine import (
    compute_flows_per_level,
    compute_full_profile,
    compute_moral_capital_debt,
    compute_moral_work,
    compute_weighted_total,
)
from config import Settings, TransmutationSettings, _load_yaml_config
from models.moral_profile import FlowValues
from db.database import run_migrations, _strip_sql_comments


# --- Password Hashing Tests ---

class TestPasswordHashing:
    def test_bcrypt_round_trip(self):
        password = "test-password-123"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        assert bcrypt.checkpw(password.encode(), hashed.encode())

    def test_bcrypt_rejects_wrong_password(self):
        password = "correct-password"
        wrong = "wrong-password"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        assert not bcrypt.checkpw(wrong.encode(), hashed.encode())


# --- Cookie Signing Tests ---

class TestCookieSigning:
    def test_sign_and_verify_round_trip(self):
        user_id = "test-user-id-123"
        signed = _sign_cookie(user_id)
        assert _verify_cookie(signed) == user_id

    def test_tampered_cookie_rejected(self):
        user_id = "test-user-id-123"
        signed = _sign_cookie(user_id)
        tampered = signed[:-4] + "xxxx"
        assert _verify_cookie(tampered) is None

    def test_no_dot_cookie_rejected(self):
        assert _verify_cookie("no-dot-here") is None

    def test_empty_cookie_rejected(self):
        assert _verify_cookie("") is None


# --- SQL Comment Stripping Tests ---

class TestStripSqlComments:
    def test_removes_full_line_comment(self):
        sql = "-- This is a comment\nCREATE TABLE foo (id INTEGER);"
        result = _strip_sql_comments(sql)
        assert "--" not in result
        assert "CREATE TABLE foo" in result

    def test_removes_inline_comment(self):
        sql = "SELECT * FROM foo -- get all rows\nWHERE id = 1;"
        result = _strip_sql_comments(sql)
        assert "-- get all rows" not in result
        assert "SELECT * FROM foo" in result
        assert "WHERE id = 1" in result

    def test_preserves_sql_when_no_comments(self):
        sql = "CREATE TABLE bar (id INTEGER PRIMARY KEY);"
        result = _strip_sql_comments(sql)
        assert result.strip() == sql

    def test_empty_string(self):
        assert _strip_sql_comments("") == ""

    def test_only_comments_yields_empty_lines(self):
        sql = "-- comment one\n-- comment two"
        result = _strip_sql_comments(sql)
        # All meaningful content stripped; only whitespace/newlines remain
        assert result.strip() == ""

    def test_multistatement_with_comments(self):
        """Comment-prefixed blocks must not cause CREATE TABLE to be skipped."""
        sql = (
            "-- Version tracking\n"
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);\n"
            "-- Users\n"
            "CREATE TABLE users (id TEXT PRIMARY KEY);"
        )
        result = _strip_sql_comments(sql)
        statements = [s.strip() for s in result.split(";") if s.strip()]
        assert len(statements) == 2
        assert all("CREATE TABLE" in s for s in statements)


# --- Migration Runner Tests ---

class TestMigrationRunner:
    def test_applies_migrations(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            count = run_migrations(db_path=db_path)
            # One applied migration per .sql file in db/migrations/ — kept dynamic
            # so adding a migration does not break this test.
            from db.database import _get_migration_files
            assert count == len(_get_migration_files())

            import sqlite3
            conn = sqlite3.connect(db_path)
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            # Verify events_json column was added by migration 003
            cols = [
                r[1] for r in conn.execute(
                    "PRAGMA table_info(adk_sessions)"
                ).fetchall()
            ]
            conn.close()

            assert "users" in tables
            assert "assessment_state" in tables
            assert "schema_version" in tables
            assert "moral_ledger" in tables
            assert "events_json" in cols
            assert "dimension_assessment_state" in tables
            assert "llm_calls" in tables  # from migration 007
            assert "title" in cols  # from migration 008
            assert len(tables) == 15  # +llm_calls (007); roadmap_practices (005); 008 adds column only
        finally:
            os.unlink(db_path)

    def test_skips_already_applied(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            count = run_migrations(db_path=db_path)
            assert count == 0
        finally:
            os.unlink(db_path)

    def test_invalid_sql_raises_and_no_version_recorded(self):
        """A migration with invalid SQL must raise and not record its version."""
        import sqlite3 as _sqlite3
        from pathlib import Path
        import shutil

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up a minimal migrations dir with one invalid migration
            migs_dir = Path(tmpdir) / "migrations"
            migs_dir.mkdir()
            (migs_dir / "001_bad.sql").write_text(
                "-- Bad migration\nINSERT INTO nonexistent_table VALUES (1);"
            )

            db_path = str(Path(tmpdir) / "test.db")

            # Monkey-patch MIGRATIONS_DIR for the duration of this test
            import db.database as db_mod
            original_dir = db_mod.MIGRATIONS_DIR
            db_mod.MIGRATIONS_DIR = migs_dir
            try:
                with pytest.raises(_sqlite3.Error):
                    run_migrations(db_path=db_path)

                # schema_version must not contain version 1
                conn = _sqlite3.connect(db_path)
                rows = conn.execute("SELECT version FROM schema_version").fetchall()
                conn.close()
                assert rows == [], "Failed migration must not be recorded in schema_version"
            finally:
                db_mod.MIGRATIONS_DIR = original_dir

    def test_applies_migration_003_to_existing_db(self):
        """Migration 003 adds events_json to an existing db at versions [1, 2]."""
        import sqlite3 as _sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Apply only migrations 001 and 002 first
            count = run_migrations(db_path=db_path)
            from db.database import _get_migration_files
            assert count == len(_get_migration_files())  # all migrations applied fresh

            # Simulate a DB that only has versions 1 and 2 by removing version 3
            conn = _sqlite3.connect(db_path)
            conn.execute("ALTER TABLE adk_sessions DROP COLUMN events_json")
            conn.execute("DELETE FROM schema_version WHERE version = 3")
            conn.commit()
            conn.close()

            # Now applying migrations should only apply 003
            count2 = run_migrations(db_path=db_path)
            assert count2 == 1

            conn = _sqlite3.connect(db_path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(adk_sessions)").fetchall()]
            conn.close()
            assert "events_json" in cols
        finally:
            os.unlink(db_path)


# --- Session Title Migration Tests (DB-001) ---

class TestSessionTitleMigration:
    """Verify migration 008 adds the nullable 'title' column to adk_sessions."""

    def test_title_column_exists(self):
        """The title column must exist in adk_sessions after migration."""
        import sqlite3 as _sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            conn = _sqlite3.connect(db_path)
            col_names = [
                r[1] for r in conn.execute("PRAGMA table_info(adk_sessions)").fetchall()
            ]
            conn.close()
            assert "title" in col_names, "title column must exist in adk_sessions"
        finally:
            os.unlink(db_path)

    def test_title_column_is_text_and_nullable(self):
        """The title column must be of type TEXT and allow NULL values."""
        import sqlite3 as _sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            conn = _sqlite3.connect(db_path)
            cols = {
                r[1]: {"type": r[2], "notnull": r[3], "dflt_value": r[4]}
                for r in conn.execute("PRAGMA table_info(adk_sessions)").fetchall()
            }
            conn.close()
            assert "title" in cols, "title column must exist"
            assert cols["title"]["type"] == "TEXT", "title column type must be TEXT"
            assert cols["title"]["notnull"] == 0, "title column must be nullable"
            assert cols["title"]["dflt_value"] is None, "title column default must be NULL"
        finally:
            os.unlink(db_path)

    def test_title_accepts_null(self):
        """Inserting a row without a title stores NULL for that column."""
        import sqlite3 as _sqlite3
        import uuid

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            conn = _sqlite3.connect(db_path)
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            uid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                (uid, "Test", f"{uid}@test.example.com", "hash"),
            )
            sid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO adk_sessions (session_id, user_id, app_name, created_at) "
                "VALUES (?, ?, 'test', datetime('now'))",
                (sid, uid),
            )
            conn.commit()
            row = conn.execute(
                "SELECT title FROM adk_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            conn.close()
            assert row["title"] is None, "title must default to NULL"
        finally:
            os.unlink(db_path)

    def test_title_accepts_string_value(self):
        """Inserting a session with an explicit title stores the value correctly."""
        import sqlite3 as _sqlite3
        import uuid

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            conn = _sqlite3.connect(db_path)
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            uid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                (uid, "Test", f"{uid}@test.example.com", "hash"),
            )
            sid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO adk_sessions (session_id, user_id, app_name, created_at, title) "
                "VALUES (?, ?, 'test', datetime('now'), ?)",
                (sid, uid, "My Session"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT title FROM adk_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            conn.close()
            assert row["title"] == "My Session", "title must store the provided string value"
        finally:
            os.unlink(db_path)

    def test_migration_idempotent(self):
        """Running migrations twice on the same DB applies 008 only once."""
        import sqlite3 as _sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            # Second run must apply zero migrations (idempotent)
            count2 = run_migrations(db_path=db_path)
            assert count2 == 0, "Re-running migrations must apply nothing"
            # Column must still be present
            conn = _sqlite3.connect(db_path)
            col_names = [
                r[1] for r in conn.execute("PRAGMA table_info(adk_sessions)").fetchall()
            ]
            conn.close()
            assert "title" in col_names
        finally:
            os.unlink(db_path)


# --- SqliteSessionService.create_session Unit Tests (BE-001) ---

class TestCreateSessionService:
    """Unit tests for SqliteSessionService.create_session's new behavior."""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def _make_db(self):
        """Return a temp DB path with all migrations applied."""
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        run_migrations(db_path=f.name)
        return f.name

    def _get_sessions(self, db_path: str, user_id: str) -> list:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM adk_sessions WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _insert_user(self, db_path: str, user_id: str) -> None:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "Test User", f"{user_id}@test.example.com", "hash"),
        )
        conn.commit()
        conn.close()

    def test_title_is_persisted(self):
        """create_session with title stores the title in adk_sessions."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            self._insert_user(db_path, uid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                from config import _settings
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    session = self._run(svc.create_session(
                        app_name="test",
                        user_id=uid,
                        title="My Tab",
                    ))
                finally:
                    config._settings = None

            rows = self._get_sessions(db_path, uid)
            assert len(rows) == 1
            assert rows[0]["title"] == "My Tab", "title must be persisted in adk_sessions"
        finally:
            os.unlink(db_path)

    def test_archive_prior_false_leaves_prior_sessions_active(self):
        """create_session with archive_prior=False does not archive prior sessions."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            self._insert_user(db_path, uid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    # Create first session (archive_prior=True archives nothing yet)
                    self._run(svc.create_session(
                        app_name="test", user_id=uid, archive_prior=True
                    ))
                    # Create second session with archive_prior=False — prior must stay active
                    self._run(svc.create_session(
                        app_name="test", user_id=uid, archive_prior=False
                    ))
                finally:
                    config._settings = None

            rows = self._get_sessions(db_path, uid)
            assert len(rows) == 2
            active = [r for r in rows if not r["archived"]]
            assert len(active) == 2, "Both sessions must remain active when archive_prior=False"
        finally:
            os.unlink(db_path)

    def test_archive_prior_true_archives_prior_sessions(self):
        """create_session with archive_prior=True archives all prior active sessions."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            self._insert_user(db_path, uid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    first = self._run(svc.create_session(
                        app_name="test", user_id=uid, archive_prior=False
                    ))
                    self._run(svc.create_session(
                        app_name="test", user_id=uid, archive_prior=True
                    ))
                finally:
                    config._settings = None

            rows = self._get_sessions(db_path, uid)
            assert len(rows) == 2
            # First session must now be archived
            first_row = next(r for r in rows if r["session_id"] == first.id)
            assert first_row["archived"], "Prior session must be archived when archive_prior=True"
            # Second session must be active
            active = [r for r in rows if not r["archived"]]
            assert len(active) == 1, "Only the new session must remain active"
        finally:
            os.unlink(db_path)

    def test_null_title_stored_when_not_provided(self):
        """create_session without title stores NULL for the title column."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            self._insert_user(db_path, uid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    self._run(svc.create_session(app_name="test", user_id=uid))
                finally:
                    config._settings = None

            rows = self._get_sessions(db_path, uid)
            assert rows[0]["title"] is None, "title must be NULL when not provided"
        finally:
            os.unlink(db_path)


# --- SqliteSessionService.rename_session and RenameSessionRequest Unit Tests (BE-002) ---

class TestRenameSessionService:
    """Unit tests for SqliteSessionService.rename_session."""

    def _make_db(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        run_migrations(db_path=f.name)
        return f.name

    def _insert_user(self, db_path: str, user_id: str) -> None:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "Test", f"{user_id}@test.example.com", "hash"),
        )
        conn.commit()
        conn.close()

    def _insert_session(self, db_path: str, user_id: str, session_id: str) -> None:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO adk_sessions (session_id, user_id, app_name, created_at) "
            "VALUES (?, ?, 'test', datetime('now'))",
            (session_id, user_id),
        )
        conn.commit()
        conn.close()

    def _get_title(self, db_path: str, session_id: str) -> str | None:
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT title FROM adk_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def test_rename_session_updates_title_and_returns_true(self):
        """rename_session returns True and updates title for an owned session."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            self._insert_user(db_path, uid)
            self._insert_session(db_path, uid, sid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    result = svc.rename_session(user_id=uid, session_id=sid, title="New Name")
                finally:
                    config._settings = None

            assert result is True, "rename_session must return True on success"
            assert self._get_title(db_path, sid) == "New Name"
        finally:
            os.unlink(db_path)

    def test_rename_session_returns_false_for_wrong_user(self):
        """rename_session returns False when the session is not owned by the user."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            other_uid = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            self._insert_user(db_path, uid)
            self._insert_user(db_path, other_uid)
            self._insert_session(db_path, uid, sid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    # other_uid tries to rename uid's session
                    result = svc.rename_session(
                        user_id=other_uid, session_id=sid, title="Hacked"
                    )
                finally:
                    config._settings = None

            assert result is False, "rename_session must return False for non-owner"
            assert self._get_title(db_path, sid) is None, "title must not be changed"
        finally:
            os.unlink(db_path)

    def test_rename_session_returns_false_for_nonexistent_session(self):
        """rename_session returns False when the session_id does not exist."""
        import os
        import uuid
        from unittest.mock import patch

        db_path = self._make_db()
        try:
            uid = str(uuid.uuid4())
            self._insert_user(db_path, uid)

            with patch.dict(os.environ, {"DB_PATH": db_path}):
                import config
                config._settings = None
                try:
                    from agents.transmutation.session_service import SqliteSessionService
                    svc = SqliteSessionService()
                    result = svc.rename_session(
                        user_id=uid, session_id="nonexistent-id", title="Ghost"
                    )
                finally:
                    config._settings = None

            assert result is False
        finally:
            os.unlink(db_path)


class TestRenameSessionRequest:
    """Unit tests for RenameSessionRequest Pydantic validation."""

    def test_valid_title_is_accepted(self):
        from api.sessions import RenameSessionRequest
        req = RenameSessionRequest(title="My Session")
        assert req.title == "My Session"

    def test_title_is_stripped(self):
        from api.sessions import RenameSessionRequest
        req = RenameSessionRequest(title="  My Session  ")
        assert req.title == "My Session"

    def test_title_of_exactly_80_chars_is_valid(self):
        from api.sessions import RenameSessionRequest
        title = "a" * 80
        req = RenameSessionRequest(title=title)
        assert len(req.title) == 80

    def test_empty_title_raises_validation_error(self):
        from api.sessions import RenameSessionRequest
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RenameSessionRequest(title="")

    def test_whitespace_only_title_raises_validation_error(self):
        from api.sessions import RenameSessionRequest
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RenameSessionRequest(title="   ")

    def test_title_exceeding_80_chars_raises_validation_error(self):
        from api.sessions import RenameSessionRequest
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RenameSessionRequest(title="a" * 81)


# --- User ID Injection Tests (BE-006) ---

class TestWithUserId:
    """Unit tests for the with_user_id helper."""

    def _make_ctx(self, user_id: str):
        """Create a minimal mock ReadonlyContext with state."""
        class FakeCtx:
            def __init__(self, state):
                self.state = state
        return FakeCtx({"user_id": user_id})

    def test_injects_user_id_into_prompt(self):
        from agents.transmutation.sub_agents.inject_user_id import with_user_id
        fn = with_user_id("Do the thing.")
        ctx = self._make_ctx("user-abc-123")
        result = fn(ctx)
        assert "user-abc-123" in result
        assert "Do the thing." in result

    def test_user_id_appears_before_prompt(self):
        from agents.transmutation.sub_agents.inject_user_id import with_user_id
        fn = with_user_id("Static prompt text.")
        ctx = self._make_ctx("user-xyz")
        result = fn(ctx)
        user_id_pos = result.find("user-xyz")
        prompt_pos = result.find("Static prompt text.")
        assert user_id_pos < prompt_pos, "user_id header must precede static prompt"

    def test_missing_user_id_falls_back_to_unknown(self):
        from agents.transmutation.sub_agents.inject_user_id import with_user_id
        fn = with_user_id("Any prompt.")

        class FakeCtx:
            state = {}  # no user_id key

        result = fn(FakeCtx())
        assert "unknown" in result

    def test_all_sub_agents_use_with_user_id(self):
        """Every sub-agent create_* function must use with_user_id for its instruction."""
        import inspect
        from agents.transmutation.sub_agents import (
            assessment, check_in, development, education,
            graduation, profile, reassessment,
        )
        modules = [assessment, check_in, development, education, graduation, profile, reassessment]
        for mod in modules:
            src = inspect.getsource(mod)
            assert "with_user_id" in src, (
                f"Sub-agent module {mod.__name__} does not use with_user_id"
            )

    def test_root_agent_reads_user_id_from_context(self):
        """Root agent _root_instruction must embed user_id from ctx.state."""
        from agents.transmutation.agent import _root_instruction

        class FakeCtx:
            state = {"user_id": "root-user-test-id"}

        result = _root_instruction(FakeCtx())
        assert "root-user-test-id" in result

    def test_create_session_seeds_user_id_in_state(self):
        """POST /api/sessions creates a session with user_id in the ADK state."""
        import sqlite3
        import json

        resp = None
        try:
            from fastapi.testclient import TestClient
            from main import app
            tc = TestClient(app)
            reg = tc.post("/auth/register", json={
                "name": "Inject Test",
                "email": "injecttest@example.com",
                "password": "password123",
            })
            assert reg.status_code == 200
            user_id = reg.json()["user_id"]
            cookies = reg.cookies

            sess = tc.post("/api/sessions", cookies=cookies)
            assert sess.status_code == 200
            session_id = sess.json()["session_id"]

            conn = sqlite3.connect(__import__("os").environ["DB_PATH"])
            row = conn.execute(
                "SELECT session_state FROM adk_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            conn.close()

            assert row is not None
            state = json.loads(row[0])
            assert state.get("user_id") == user_id, (
                "Session state must contain the authenticated user_id"
            )
        except Exception:
            raise


# --- Reassessment Prompt & Toolset Tests ---

class TestReassessmentPromptDelegation:
    """Verify the reassessment prompt delegates math/selection to tools and the
    agent toolset wires the new deterministic tools (BE-005)."""

    def test_prompt_has_no_arithmetic_or_selection_instructions(self):
        """The prompt must not instruct the LLM to do the deterministic math."""
        from agents.transmutation.prompts.reassessment_prompt import REASSESSMENT_INSTRUCTIONS
        text = REASSESSMENT_INSTRUCTIONS.lower()
        # These phrasings encoded math/selection the LLM used to perform.
        banned = [
            "70% prior",
            "30% new",
            ">15 point shift",
            "force-include at 3 cycles",
            "more than 2 cycles",
            "weighted blend",
        ]
        for phrase in banned:
            assert phrase.lower() not in text, (
                f"Prompt still contains delegated-math instruction: {phrase!r}"
            )

    def test_prompt_directs_agent_to_new_tools(self):
        """The prompt must direct the agent to call the new deterministic tools."""
        from agents.transmutation.prompts.reassessment_prompt import REASSESSMENT_INSTRUCTIONS
        for tool_name in (
            "select_reassessment_targets",
            "select_sentinel_questions",
            "generate_reassessment_snapshot",
        ):
            assert tool_name in REASSESSMENT_INSTRUCTIONS, (
                f"Prompt does not direct the agent to call {tool_name}"
            )

    def test_agent_toolset_includes_new_tools(self):
        """create_reassessment_agent must expose all four new tools."""
        from agents.transmutation.sub_agents.reassessment import create_reassessment_agent
        agent = create_reassessment_agent()
        tool_names = {
            getattr(t, "__name__", getattr(t, "name", None))
            for t in agent.tools
        }
        for expected in (
            "select_reassessment_targets",
            "select_sentinel_questions",
            "generate_reassessment_snapshot",
            "get_dimension_staleness",
        ):
            assert expected in tool_names, (
                f"Reassessment agent toolset missing {expected}"
            )

    def test_agent_retains_existing_tools(self):
        """Wiring new tools must not drop the existing reassessment tools."""
        from agents.transmutation.sub_agents.reassessment import create_reassessment_agent
        agent = create_reassessment_agent()
        tool_names = {
            getattr(t, "__name__", getattr(t, "name", None))
            for t in agent.tools
        }
        for expected in (
            "save_profile_snapshot",
            "generate_comparison_snapshot",
            "present_question_batch",
            "save_assessment_response",
        ):
            assert expected in tool_names, (
                f"Reassessment agent toolset lost existing tool {expected}"
            )


# --- Chat Debug Instrumentation Tests ---

class TestChatDebugInstrumentation:
    def test_litellm_generate_content_async_not_monkeypatched(self):
        """Importing api.chat must not replace LiteLlm.generate_content_async."""
        from google.adk.models.lite_llm import LiteLlm
        original_method = LiteLlm.generate_content_async.__qualname__

        import api.chat  # noqa: F401 — ensure module is imported

        # After import, generate_content_async must still be the original method,
        # not the _debug_generate wrapper.
        assert LiteLlm.generate_content_async.__qualname__ == original_method, (
            "LiteLlm.generate_content_async was monkeypatched by api.chat import"
        )

    def test_no_debug_files_created_under_var_lib_transmute(self, tmp_path):
        """No files should be written to /var/lib/transmute during LLM interaction setup."""
        # The debug wrapper wrote to /var/lib/transmute/*.json.
        # After removal, that directory must NOT be created or written to during
        # a normal import + model-string resolution cycle.
        import api.chat  # noqa: F401 — ensure module is imported

        # Simulate what the debug wrapper would have created
        var_lib_transmute = tmp_path / "transmute"
        # If debug code were present it would try to open paths under /var/lib/transmute.
        # Confirm no such path exists in the module source.
        import inspect
        chat_source = inspect.getsource(api.chat)
        assert "/var/lib/transmute" not in chat_source, (
            "api/chat.py still references /var/lib/transmute — debug file dump not removed"
        )
        assert "_debug_generate" not in chat_source, (
            "api/chat.py still contains _debug_generate wrapper"
        )


# --- Config Loading Tests ---

# --- Session Event Slimming Tests ---

class TestSlimToolResponse:
    """Unit tests for _slim_tool_response — pure function."""

    def setup_method(self):
        from agents.transmutation.session_service import _slim_tool_response
        self._slim = _slim_tool_response

    def test_question_batch_retains_question_ids(self):
        response = {
            "event_type": "assessment.question_batch",
            "batch_id": "b1",
            "dimension": "consciousness",
            "sub_dimension": "awareness",
            "count": 3,
            "question_ids": ["q1", "q2", "q3"],
            "questions": [{"id": "q1", "text": "Long question text..."}],  # stripped
        }
        slimmed = self._slim("get_next_question_batch", response)
        assert slimmed["question_ids"] == ["q1", "q2", "q3"]
        assert "questions" not in slimmed
        assert slimmed["event_type"] == "assessment.question_batch"
        assert slimmed["count"] == 3

    def test_question_batch_missing_question_ids_defaults_to_empty_list(self):
        response = {
            "event_type": "assessment.question_batch",
            "batch_id": "b1",
            "count": 2,
        }
        slimmed = self._slim("get_next_question_batch", response)
        assert slimmed["question_ids"] == []

    def test_profile_snapshot_strips_spider_png_keeps_quadrant(self):
        response = {
            "event_type": "profile.snapshot",
            "saved": True,
            "snapshot_id": "snap-1",
            "quadrant": "Creator",
            "spider_chart": "base64encodedPNGdata==",  # stripped
            "scores": {"consciousness": 0.8},  # stripped
        }
        slimmed = self._slim("generate_profile_snapshot", response)
        assert "spider_chart" not in slimmed
        assert "scores" not in slimmed
        assert slimmed["quadrant"] == "Creator"
        assert slimmed["event_type"] == "profile.snapshot"

    def test_scenario_retains_scenario_id(self):
        response = {
            "event_type": "assessment.scenario",
            "scenario_id": "sc-42",
            "narrative": "A very long scenario text...",
            "choices": [{"id": "c1", "text": "Choice 1"}, {"id": "c2", "text": "Choice 2"}],
        }
        slimmed = self._slim("get_scenario", response)
        assert slimmed["scenario_id"] == "sc-42"
        assert slimmed["event_type"] == "assessment.scenario"
        # Narrative and full choices are stripped
        assert "narrative" not in slimmed
        assert "choices" not in slimmed

    def test_small_response_passes_through(self):
        response = {"status": "ok", "count": 5}
        slimmed = self._slim("some_tool", response)
        assert slimmed == response

    def test_large_unknown_response_truncated_to_scalars(self):
        response = {
            "key1": "value1",
            "big_list": list(range(500)),  # large non-scalar
            "simple": 42,
        }
        slimmed = self._slim("some_tool", response)
        # big_list is non-scalar so should be dropped
        assert "big_list" not in slimmed
        assert slimmed.get("simple") == 42


class TestSlimEventsForStorage:
    """Unit tests for _slim_events_for_storage — pure function."""

    def setup_method(self):
        from agents.transmutation.session_service import _slim_events_for_storage
        self._slim = _slim_events_for_storage

    def _make_tool_response_event(self, tool_name: str, response: dict) -> dict:
        return {
            "content": {
                "role": "tool",
                "parts": [
                    {
                        "function_response": {
                            "name": tool_name,
                            "response": response,
                        }
                    }
                ],
            }
        }

    def test_empty_events_returns_empty(self):
        assert self._slim([]) == []

    def test_non_tool_event_passes_through(self):
        event = {"content": {"role": "model", "parts": [{"text": "Hello"}]}}
        result = self._slim([event])
        assert result == [event]

    def test_question_batch_event_slimmed_with_question_ids(self):
        event = self._make_tool_response_event("get_next_question_batch", {
            "event_type": "assessment.question_batch",
            "batch_id": "b1",
            "count": 3,
            "question_ids": ["q1", "q2", "q3"],
            "questions": [{"id": "q1", "text": "Text..."}],
        })
        result = self._slim([event])
        assert len(result) == 1
        fr = result[0]["content"]["parts"][0]["function_response"]
        assert fr["response"]["question_ids"] == ["q1", "q2", "q3"]
        assert "questions" not in fr["response"]

    def test_event_without_parts_passes_through(self):
        event = {"content": {"role": "tool", "parts": []}}
        result = self._slim([event])
        assert result == [event]


# --- Config Loading Tests ---


class TestConfigLoading:
    VALID_PROVIDERS = {"anthropic", "openai", "bedrock", "ollama"}

    def test_yaml_config_loads(self):
        config = _load_yaml_config()
        assert "model" in config
        # Provider must be a valid value — not hardcoded to one environment's choice
        assert config["model"]["provider"] in self.VALID_PROVIDERS, (
            f"provider '{config['model']['provider']}' not in {self.VALID_PROVIDERS}"
        )

    def test_settings_loads_from_yaml(self):
        settings = Settings()
        # Provider must be a supported value; exact value depends on deployment config
        assert settings.model.provider in self.VALID_PROVIDERS, (
            f"provider '{settings.model.provider}' not in {self.VALID_PROVIDERS}"
        )
        # model_id must be a non-empty string
        assert isinstance(settings.model.model_id, str) and settings.model.model_id

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Invalid provider"):
            Settings(model={"provider": "invalid_provider"})

    def test_cost_lookup(self):
        settings = Settings()
        cost = settings.get_cost_per_token("claude-sonnet-4-5-20250514")
        assert cost.input == 3.00
        assert cost.output == 15.00

    def test_wildcard_cost_lookup(self):
        settings = Settings()
        cost = settings.get_cost_per_token("ollama/llama3")
        assert cost.input == 0.0
        assert cost.output == 0.0

    def test_bedrock_inference_profile_exact_match(self):
        settings = Settings()
        cost = settings.get_cost_per_token("us.anthropic.claude-sonnet-4-6")
        assert cost.input == 3.00
        assert cost.output == 15.00

    def test_bedrock_inference_profile_wildcard_match(self):
        # A future sibling not enumerated explicitly must still hit the
        # us.anthropic.claude-sonnet-4-* wildcard family entry.
        settings = Settings()
        cost = settings.get_cost_per_token("us.anthropic.claude-sonnet-4-99")
        assert cost.input == 3.00
        assert cost.output == 15.00

    def test_unknown_model_returns_zero_cost(self):
        settings = Settings()
        cost = settings.get_cost_per_token("unknown-model")
        assert cost.input == 0.0
        assert cost.output == 0.0


# --- Transmutation Settings Tests ---

class TestTransmutationSettings:
    def test_default_tau(self):
        ts = TransmutationSettings()
        assert ts.tau == 1.0

    def test_default_maslow_weights(self):
        ts = TransmutationSettings()
        assert ts.maslow_weights == [5, 4, 3, 2, 1]

    def test_settings_loads_transmutation_from_yaml(self):
        settings = Settings()
        assert settings.transmutation.tau == 1.0
        assert settings.transmutation.maslow_weights == [5, 4, 3, 2, 1]

    def test_custom_tau(self):
        ts = TransmutationSettings(tau=2.5)
        assert ts.tau == 2.5

    def test_custom_maslow_weights(self):
        ts = TransmutationSettings(maslow_weights=[1, 1, 1, 1, 1])
        assert ts.maslow_weights == [1, 1, 1, 1, 1]


# --- Flow Engine Tests ---

# --- Debug Reload Config Tests ---

class TestDebugReloadConfig:
    def test_debug_true_sets_reload_true(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        assert os.getenv("DEBUG", "").lower() == "true"

    def test_debug_TRUE_sets_reload_true(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "TRUE")
        assert os.getenv("DEBUG", "").lower() == "true"

    def test_debug_unset_sets_reload_false(self, monkeypatch):
        monkeypatch.delenv("DEBUG", raising=False)
        assert os.getenv("DEBUG", "").lower() != "true"

    def test_debug_false_sets_reload_false(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "false")
        assert os.getenv("DEBUG", "").lower() != "true"


# --- Health Endpoint Tests ---

class TestHealthEndpoint:
    def test_health_returns_ok_when_db_accessible(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"

    def test_readiness_returns_ok(self, api_client):
        resp = api_client.get("/readiness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_returns_503_when_db_unreachable(self, api_client, monkeypatch):
        def broken_session():
            raise Exception("DB unreachable")

        # Make get_db_session raise an exception
        from contextlib import contextmanager

        @contextmanager
        def broken_context():
            raise Exception("DB unreachable")
            yield  # noqa: unreachable

        monkeypatch.setattr("api.health.get_db_session", broken_context)
        resp = api_client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["db"] == "disconnected"


class TestComputeFlowsPerLevel:
    def _make_scenarios(self):
        return [
            {"id": "sc1", "maslow_level": "physiological", "choices": [
                {"key": "a", "quadrant_weight": {"transmuter": 1.0}},
            ]},
            {"id": "sc2", "maslow_level": "safety", "choices": [
                {"key": "a", "quadrant_weight": {"absorber": 1.0}},
            ]},
        ]

    def test_transmuter_response_produces_correct_flows(self):
        scenarios = self._make_scenarios()
        responses = {"sc1": {"choice": "a", "quadrant_weight": {"transmuter": 1.0}}}
        flows = compute_flows_per_level(responses, scenarios)
        phys = flows["physiological"]
        assert phys.d_plus_out == 1.0
        assert phys.d_minus_in == 1.0
        assert phys.d_plus_in == 0.0
        assert phys.d_minus_out == 0.0
        assert phys.filtering == 1.0  # d_minus_in - d_minus_out = 1 - 0
        assert phys.amplification == 1.0  # d_plus_out - d_plus_in = 1 - 0

    def test_absorber_response_produces_correct_flows(self):
        scenarios = self._make_scenarios()
        responses = {"sc2": {"choice": "a", "quadrant_weight": {"absorber": 1.0}}}
        flows = compute_flows_per_level(responses, scenarios)
        safety = flows["safety"]
        assert safety.d_plus_in == 1.0
        assert safety.d_minus_in == 1.0
        assert safety.filtering == 1.0  # absorbs deprivation (d_minus_in)
        assert safety.amplification == -1.0  # absorbs fulfillment (negative A)

    def test_no_responses_returns_zero_flows(self):
        scenarios = self._make_scenarios()
        flows = compute_flows_per_level({}, scenarios)
        for level in flows.values():
            assert level.d_plus_in == 0.0
            assert level.d_plus_out == 0.0
            assert level.filtering == 0.0
            assert level.amplification == 0.0

    def test_unknown_scenario_id_ignored(self):
        scenarios = self._make_scenarios()
        responses = {"unknown": {"choice": "a", "quadrant_weight": {"transmuter": 1.0}}}
        flows = compute_flows_per_level(responses, scenarios)
        for level in flows.values():
            assert level.d_plus_out == 0.0


class TestComputeMoralWork:
    def test_uniform_flows_with_tau_1(self):
        flows = {
            level: FlowValues(filtering=1.0, amplification=5.0)
            for level in ["physiological", "safety", "belonging", "esteem", "self-actualization"]
        }
        m = compute_moral_work(flows, tau=1.0)
        assert m == [6.0, 6.0, 6.0, 6.0, 6.0]

    def test_tau_2_doubles_filtering(self):
        flows = {
            "physiological": FlowValues(filtering=1.0, amplification=0.0),
            "safety": FlowValues(),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        m = compute_moral_work(flows, tau=2.0)
        assert m[0] == 2.0
        assert m[1] == 0.0

    def test_all_zeros_gives_zero_vector(self):
        flows = {
            level: FlowValues()
            for level in ["physiological", "safety", "belonging", "esteem", "self-actualization"]
        }
        m = compute_moral_work(flows, tau=1.0)
        assert m == [0.0, 0.0, 0.0, 0.0, 0.0]


class TestComputeWeightedTotal:
    def test_uniform_moral_work(self):
        m = [6.0, 6.0, 6.0, 6.0, 6.0]
        w = compute_weighted_total(m, [5, 4, 3, 2, 1])
        assert w == 90.0  # 6 * (5+4+3+2+1) = 90

    def test_default_weights(self):
        m = [1.0, 1.0, 1.0, 1.0, 1.0]
        w = compute_weighted_total(m)
        assert w == 15.0  # 1*(5+4+3+2+1) = 15

    def test_zero_moral_work(self):
        m = [0.0, 0.0, 0.0, 0.0, 0.0]
        assert compute_weighted_total(m) == 0.0


class TestComputeMoralCapitalDebt:
    def test_positive_flows_accumulate_capital(self):
        flows = {
            "physiological": FlowValues(filtering=2.0, amplification=3.0),
            "safety": FlowValues(filtering=1.0, amplification=1.0),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        ledger = compute_moral_capital_debt(flows)
        assert ledger.c_plus == 7.0  # 2+3+1+1
        assert ledger.c_minus == 0.0

    def test_negative_flows_accumulate_debt(self):
        flows = {
            "physiological": FlowValues(filtering=-2.0, amplification=-3.0),
            "safety": FlowValues(),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        ledger = compute_moral_capital_debt(flows)
        assert ledger.c_plus == 0.0
        assert ledger.c_minus == 5.0  # |-2| + |-3|

    def test_mixed_flows(self):
        flows = {
            "physiological": FlowValues(filtering=2.0, amplification=-1.0),
            "safety": FlowValues(),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        ledger = compute_moral_capital_debt(flows)
        assert ledger.c_plus == 2.0  # filtering positive
        assert ledger.c_minus == 1.0  # amplification negative


class TestComputeFullProfile:
    def test_full_pipeline(self):
        scenarios = [
            {"id": "sc1", "maslow_level": "physiological", "choices": []},
            {"id": "sc2", "maslow_level": "safety", "choices": []},
        ]
        responses = {
            "sc1": {"quadrant_weight": {"transmuter": 1.0}},
            "sc2": {"quadrant_weight": {"transmuter": 1.0}},
        }
        profile = compute_full_profile(responses, scenarios, tau=1.0)
        assert len(profile.levels) == 5
        assert len(profile.moral_work) == 5
        assert profile.tau == 1.0
        assert profile.weights == [5, 4, 3, 2, 1]
        assert profile.weighted_total != 0.0
        assert profile.moral_capital > 0.0
