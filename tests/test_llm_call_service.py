"""Tests for SqliteSessionService.record_llm_call and list_llm_calls.

Covers:
- record_llm_call: successful insertion with all fields
- record_llm_call: error handling — logs on DB error, never re-raises
- list_llm_calls: basic retrieval, newest-first ordering, has_more flag
- list_llm_calls: keyset pagination with before_id
- list_llm_calls: user isolation (user A cannot see user B's calls)
"""

import sqlite3
import uuid
from unittest.mock import MagicMock, patch

import pytest

from agents.transmutation.session_service import SqliteSessionService
from db.database import get_db_session


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _insert_user(user_id: str, email: str = "") -> None:
    email = email or f"{user_id}@test.example.com"
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, "Test User", email, "pw"),
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


def _make_svc() -> SqliteSessionService:
    return SqliteSessionService()


def _row_count(user_id: str) -> int:
    with get_db_session() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE user_id=?", (user_id,)
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# record_llm_call — successful insertion
# ---------------------------------------------------------------------------

class TestRecordLlmCall:
    def test_inserts_all_fields(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        sid = _insert_session(uid)

        _make_svc().record_llm_call(
            session_id=sid,
            user_id=uid,
            author="transmutation_agent",
            phase="assessment",
            model_id="gemini-1.5-flash",
            input_tokens=300,
            output_tokens=150,
            cost_usd=0.0045,
        )

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT * FROM llm_calls WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()

        assert row is not None
        assert row["session_id"] == sid
        assert row["user_id"] == uid
        assert row["author"] == "transmutation_agent"
        assert row["phase"] == "assessment"
        assert row["model_id"] == "gemini-1.5-flash"
        assert row["input_tokens"] == 300
        assert row["output_tokens"] == 150
        assert row["cost_usd"] == pytest.approx(0.0045)
        assert row["created_at"] is not None

    def test_inserts_with_null_session(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)

        _make_svc().record_llm_call(
            session_id=None,
            user_id=uid,
            author=None,
            phase=None,
            model_id=None,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

        assert _row_count(uid) == 1

    def test_created_at_is_set_explicitly(self):
        """created_at should be an explicit UTC ISO string, not NULL."""
        uid = str(uuid.uuid4())
        _insert_user(uid)

        _make_svc().record_llm_call(
            session_id=None,
            user_id=uid,
            author=None,
            phase=None,
            model_id=None,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
        )

        with get_db_session() as conn:
            row = conn.execute(
                "SELECT created_at FROM llm_calls WHERE user_id=?", (uid,)
            ).fetchone()

        assert row["created_at"] is not None
        assert len(row["created_at"]) >= 10  # at least "YYYY-MM-DD"


class TestRecordLlmCallErrorHandling:
    def test_db_error_is_logged_and_not_raised(self, caplog):
        """A sqlite3.Error during insertion must be caught, logged, and silently
        absorbed — never re-raised so chat stream is unaffected."""
        uid = str(uuid.uuid4())
        svc = _make_svc()

        with patch(
            "agents.transmutation.session_service.get_db_session"
        ) as mock_ctx:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = sqlite3.OperationalError("no such table: llm_calls")
            mock_ctx.return_value = mock_conn

            # Must NOT raise
            svc.record_llm_call(
                session_id=None,
                user_id=uid,
                author="agent",
                phase="test",
                model_id="m",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )

        assert any("record_llm_call failed" in r.message for r in caplog.records)

    def test_db_error_logs_user_id_not_pii(self, caplog):
        """Error log should contain user_id but not PII like email."""
        uid = str(uuid.uuid4())
        svc = _make_svc()

        with patch(
            "agents.transmutation.session_service.get_db_session"
        ) as mock_ctx:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = sqlite3.OperationalError("disk full")
            mock_ctx.return_value = mock_conn

            svc.record_llm_call(
                session_id="sess-123",
                user_id=uid,
                author="a",
                phase="p",
                model_id="m",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )

        error_msgs = " ".join(r.message for r in caplog.records if r.levelname == "ERROR")
        assert uid in error_msgs


# ---------------------------------------------------------------------------
# list_llm_calls — basic retrieval
# ---------------------------------------------------------------------------

class TestListLlmCallsBasic:
    def _record(self, svc, uid, n=1):
        for _ in range(n):
            svc.record_llm_call(
                session_id=None,
                user_id=uid,
                author="agent",
                phase="p",
                model_id="m",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
            )

    def test_returns_empty_for_new_user(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        items, has_more = _make_svc().list_llm_calls(uid, limit=10)
        assert items == []
        assert has_more is False

    def test_returns_calls_for_user(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        self._record(svc, uid, 3)

        items, has_more = svc.list_llm_calls(uid, limit=10)
        assert len(items) == 3
        assert has_more is False

    def test_ordered_newest_first(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        self._record(svc, uid, 3)

        items, _ = svc.list_llm_calls(uid, limit=10)
        ids = [item["id"] for item in items]
        assert ids == sorted(ids, reverse=True), "Results should be newest-first (id DESC)"

    def test_has_more_false_when_fits_in_limit(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        self._record(svc, uid, 5)

        _, has_more = svc.list_llm_calls(uid, limit=10)
        assert has_more is False

    def test_has_more_true_when_more_exist(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        self._record(svc, uid, 6)

        items, has_more = svc.list_llm_calls(uid, limit=5)
        assert len(items) == 5
        assert has_more is True

    def test_returned_items_are_dicts(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        self._record(svc, uid, 1)

        items, _ = svc.list_llm_calls(uid, limit=10)
        assert isinstance(items[0], dict)

    def test_item_contains_expected_fields(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        svc.record_llm_call(
            session_id=None,
            user_id=uid,
            author="agent",
            phase="test",
            model_id="flash",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.002,
        )

        items, _ = svc.list_llm_calls(uid, limit=10)
        item = items[0]
        for field in ("id", "session_id", "author", "phase", "model_id",
                      "input_tokens", "output_tokens", "cost_usd", "created_at"):
            assert field in item, f"Expected field '{field}' in item"


# ---------------------------------------------------------------------------
# list_llm_calls — pagination
# ---------------------------------------------------------------------------

class TestListLlmCallsPagination:
    def _insert_n(self, svc, uid, n):
        """Insert n records and return a list of their ids in insertion order."""
        ids = []
        for _ in range(n):
            svc.record_llm_call(
                session_id=None,
                user_id=uid,
                author="a",
                phase="p",
                model_id="m",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )
        with get_db_session() as conn:
            rows = conn.execute(
                "SELECT id FROM llm_calls WHERE user_id=? ORDER BY id ASC", (uid,)
            ).fetchall()
        return [r["id"] for r in rows]

    def test_before_id_returns_older_records(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        ids = self._insert_n(svc, uid, 10)  # ids[0] is oldest, ids[9] is newest

        # First page: 5 newest (ids 9..5)
        page1, has_more1 = svc.list_llm_calls(uid, limit=5)
        assert len(page1) == 5
        assert has_more1 is True
        assert page1[0]["id"] == ids[9]
        assert page1[-1]["id"] == ids[5]

        # Second page: before oldest id from page1
        oldest_on_page1 = page1[-1]["id"]
        page2, has_more2 = svc.list_llm_calls(uid, limit=5, before_id=oldest_on_page1)
        assert len(page2) == 5
        assert has_more2 is False
        assert page2[0]["id"] == ids[4]
        assert page2[-1]["id"] == ids[0]

    def test_before_id_excludes_the_cursor_row_itself(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        ids = self._insert_n(svc, uid, 3)

        items, _ = svc.list_llm_calls(uid, limit=10, before_id=ids[2])
        returned_ids = [i["id"] for i in items]
        assert ids[2] not in returned_ids
        assert ids[1] in returned_ids
        assert ids[0] in returned_ids

    def test_before_id_with_no_older_records(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        ids = self._insert_n(svc, uid, 3)

        items, has_more = svc.list_llm_calls(uid, limit=10, before_id=ids[0])
        assert items == []
        assert has_more is False

    def test_limit_clamped_to_100(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        # Insert 110 rows
        for _ in range(110):
            svc.record_llm_call(
                session_id=None, user_id=uid, author="a", phase="p",
                model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
            )

        items, has_more = svc.list_llm_calls(uid, limit=200)
        assert len(items) == 100
        assert has_more is True

    def test_limit_clamped_minimum_1(self):
        uid = str(uuid.uuid4())
        _insert_user(uid)
        svc = _make_svc()
        svc.record_llm_call(
            session_id=None, user_id=uid, author="a", phase="p",
            model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
        )

        items, _ = svc.list_llm_calls(uid, limit=0)
        assert len(items) == 1


# ---------------------------------------------------------------------------
# Integration: user isolation
# ---------------------------------------------------------------------------

class TestListLlmCallsUserIsolation:
    def test_user_a_cannot_see_user_b_calls(self):
        uid_a = str(uuid.uuid4())
        uid_b = str(uuid.uuid4())
        _insert_user(uid_a)
        _insert_user(uid_b)
        svc = _make_svc()

        # Insert 3 calls for A, 5 calls for B
        for _ in range(3):
            svc.record_llm_call(
                session_id=None, user_id=uid_a, author="a", phase="p",
                model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
            )
        for _ in range(5):
            svc.record_llm_call(
                session_id=None, user_id=uid_b, author="b", phase="p",
                model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
            )

        items_a, _ = svc.list_llm_calls(uid_a, limit=100)
        items_b, _ = svc.list_llm_calls(uid_b, limit=100)

        assert len(items_a) == 3
        assert len(items_b) == 5
        assert all(r["author"] == "a" for r in items_a)
        assert all(r["author"] == "b" for r in items_b)

    def test_no_cross_user_data_in_pagination(self):
        uid_a = str(uuid.uuid4())
        uid_b = str(uuid.uuid4())
        _insert_user(uid_a)
        _insert_user(uid_b)
        svc = _make_svc()

        # Insert 10 calls each
        for _ in range(10):
            svc.record_llm_call(
                session_id=None, user_id=uid_a, author="only_a", phase="p",
                model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
            )
            svc.record_llm_call(
                session_id=None, user_id=uid_b, author="only_b", phase="p",
                model_id="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
            )

        page1_a, _ = svc.list_llm_calls(uid_a, limit=5)
        oldest_a = page1_a[-1]["id"]
        page2_a, _ = svc.list_llm_calls(uid_a, limit=5, before_id=oldest_a)

        all_a_items = page1_a + page2_a
        # None of user B's ids should appear — user_id column is not returned,
        # but author field is "only_a" for all A rows and "only_b" for B rows.
        assert all(item["author"] == "only_a" for item in all_a_items)
