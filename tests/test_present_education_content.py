"""Unit tests for present_education_content and get_education_content tools (BE-001).

Covers:
  - Successful capture returns the expected event payload and upserts a row
  - Re-calling with the same (user_id, dimension, category) overwrites (upsert)
  - Unknown dimension / unknown category / empty content / oversized content
    are all rejected without writing a row
  - get_education_content groups captured content by dimension -> category
"""

import uuid

import pytest

from db.database import get_db_session
from agents.transmutation.tools import (
    present_education_content,
    get_education_content,
    EDUCATION_CONTENT_MAX_LEN,
)


# Real dimension/category from the question bank / canonical categories.
DIM = "Emotional Awareness & Regulation"
CAT = "what_this_means"
CAT_LABEL = "What This Means"


def _create_user(phase: str = "education") -> str:
    """Insert a minimal test user and return user_id."""
    uid = str(uuid.uuid4())
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, current_phase) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, "Test", f"{uid}@test.com", "hash", phase),
        )
    return uid


def _fetch_row(user_id: str, dimension: str, category: str):
    with get_db_session() as conn:
        return conn.execute(
            "SELECT content FROM education_content "
            "WHERE user_id = ? AND dimension = ? AND category = ?",
            (user_id, dimension, category),
        ).fetchone()


def _count_rows(user_id: str) -> int:
    with get_db_session() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM education_content WHERE user_id = ?",
            (user_id,),
        ).fetchone()["n"]


# ── success path ─────────────────────────────────────────────────────────────

class TestPresentEducationContentSuccess:
    def test_returns_event_type(self):
        uid = _create_user()
        result = present_education_content(uid, DIM, CAT, "Some explanation text.")
        assert result.get("event_type") == "education.content"
        assert result.get("status") == "success"

    def test_returns_required_fields(self):
        uid = _create_user()
        result = present_education_content(uid, DIM, CAT, "Some explanation text.")
        for field in ("dimension", "category", "category_label", "content"):
            assert field in result, f"Missing field: {field}"
        assert result["dimension"] == DIM
        assert result["category"] == CAT
        assert result["category_label"] == CAT_LABEL
        assert result["content"] == "Some explanation text."

    def test_upserts_row(self):
        uid = _create_user()
        present_education_content(uid, DIM, CAT, "Some explanation text.")
        row = _fetch_row(uid, DIM, CAT)
        assert row is not None
        assert row["content"] == "Some explanation text."

    def test_recall_overwrites_single_row(self):
        """Calling twice for the same (user, dimension, category) upserts, not duplicates."""
        uid = _create_user()
        present_education_content(uid, DIM, CAT, "First version.")
        present_education_content(uid, DIM, CAT, "Second version.")

        assert _count_rows(uid) == 1
        row = _fetch_row(uid, DIM, CAT)
        assert row["content"] == "Second version."

    def test_different_categories_create_separate_rows(self):
        uid = _create_user()
        present_education_content(uid, DIM, "what_this_means", "Text A")
        present_education_content(uid, DIM, "your_score", "Text B")
        assert _count_rows(uid) == 2


# ── validation / error path ───────────────────────────────────────────────────

class TestPresentEducationContentValidation:
    def test_unknown_dimension_returns_error(self):
        uid = _create_user()
        result = present_education_content(uid, "Bogus Dimension", CAT, "text")
        assert result.get("status") == "error"
        assert "event_type" not in result
        assert _count_rows(uid) == 0

    def test_unknown_category_returns_error(self):
        uid = _create_user()
        result = present_education_content(uid, DIM, "not_a_category", "text")
        assert result.get("status") == "error"
        assert "event_type" not in result
        assert _count_rows(uid) == 0

    def test_empty_content_returns_error(self):
        uid = _create_user()
        result = present_education_content(uid, DIM, CAT, "")
        assert result.get("status") == "error"
        assert "event_type" not in result
        assert _count_rows(uid) == 0

    def test_whitespace_only_content_returns_error(self):
        uid = _create_user()
        result = present_education_content(uid, DIM, CAT, "   \n\t  ")
        assert result.get("status") == "error"
        assert _count_rows(uid) == 0

    def test_oversized_content_returns_error(self):
        uid = _create_user()
        oversized = "x" * (EDUCATION_CONTENT_MAX_LEN + 1)
        result = present_education_content(uid, DIM, CAT, oversized)
        assert result.get("status") == "error"
        assert "event_type" not in result
        assert _count_rows(uid) == 0

    def test_max_length_content_succeeds(self):
        uid = _create_user()
        exactly_max = "x" * EDUCATION_CONTENT_MAX_LEN
        result = present_education_content(uid, DIM, CAT, exactly_max)
        assert result.get("status") == "success"

    def test_error_does_not_raise_exception(self):
        uid = _create_user()
        try:
            result = present_education_content(uid, "Nonexistent Dim", CAT, "text")
            assert isinstance(result, dict)
        except Exception as exc:
            pytest.fail(f"present_education_content raised unexpectedly: {exc}")


# ── get_education_content ─────────────────────────────────────────────────────

class TestGetEducationContent:
    def test_no_content_returns_empty_dict(self):
        uid = _create_user()
        result = get_education_content(uid)
        assert result == {}

    def test_returns_content_grouped_by_dimension_and_category(self):
        uid = _create_user()
        present_education_content(uid, DIM, "what_this_means", "Text A")
        present_education_content(uid, DIM, "your_score", "Text B")

        result = get_education_content(uid)
        assert result[DIM]["what_this_means"] == "Text A"
        assert result[DIM]["your_score"] == "Text B"

    def test_only_returns_requesting_users_content(self):
        uid1 = _create_user()
        uid2 = _create_user()
        present_education_content(uid1, DIM, CAT, "User 1 content")
        present_education_content(uid2, DIM, CAT, "User 2 content")

        result1 = get_education_content(uid1)
        result2 = get_education_content(uid2)
        assert result1[DIM][CAT] == "User 1 content"
        assert result2[DIM][CAT] == "User 2 content"

    def test_reflects_overwrite(self):
        uid = _create_user()
        present_education_content(uid, DIM, CAT, "First version.")
        present_education_content(uid, DIM, CAT, "Second version.")
        result = get_education_content(uid)
        assert result[DIM][CAT] == "Second version."
