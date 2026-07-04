"""Unit tests for present_comprehension_question tool (BE-001).

Covers:
  - Returns correct payload structure with stem and options
  - Excludes correct_option and explanation from payload
  - Selects the next unanswered question when question_id is omitted
  - Returns no_questions when all questions are answered
  - Returns error on unknown question_id
  - Handles missing education_progress row (treats as no answered questions)
"""

import json
import uuid

import pytest

from db.database import get_db_session
from agents.transmutation.tools import present_comprehension_question


# ── helpers ──────────────────────────────────────────────────────────────────

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


def _set_progress(user_id: str, progress: dict) -> None:
    """Insert education_progress for a user."""
    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
            (user_id, json.dumps(progress)),
        )


# Real IDs from comprehension_checks.json (Emotional Awareness & Regulation / what_this_means)
DIM = "Emotional Awareness & Regulation"
CAT = "what_this_means"
Q1_ID = "cc_ea_cat1_q1"
Q2_ID = "cc_ea_cat1_q2"


# ── success path ─────────────────────────────────────────────────────────────

class TestPresentComprehensionQuestionSuccess:
    def test_returns_event_type(self):
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        assert result.get("event_type") == "education.comprehension"

    def test_returns_required_fields(self):
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        for field in ("dimension", "category", "question_id", "stem", "options"):
            assert field in result, f"Missing field: {field}"

    def test_options_have_key_and_text_only(self):
        """Each option must have key and text, and nothing else."""
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        assert isinstance(result["options"], list)
        assert len(result["options"]) > 0
        for opt in result["options"]:
            assert set(opt.keys()) == {"key", "text"}, (
                f"Option has unexpected keys: {set(opt.keys())}"
            )

    def test_excludes_correct_option(self):
        """correct_option must never appear in the returned payload."""
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        assert "correct_option" not in result
        # Also ensure it's not leaked inside the options list
        for opt in result["options"]:
            assert "correct_option" not in opt

    def test_excludes_explanation(self):
        """explanation must never appear in the returned payload."""
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        assert "explanation" not in result

    def test_dimension_and_category_match_request(self):
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        assert result["dimension"] == DIM
        assert result["category"] == CAT

    def test_question_id_matches_request(self):
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, Q1_ID)
        assert result["question_id"] == Q1_ID


# ── next-unanswered selection ─────────────────────────────────────────────────

class TestNextUnansweredSelection:
    def test_no_progress_returns_first_question(self):
        """When no education_progress exists, present the first question."""
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT)
        assert result.get("event_type") == "education.comprehension"
        assert result["question_id"] == Q1_ID

    def test_skips_already_answered_questions(self):
        """When Q1 is answered, the tool should return Q2."""
        uid = _create_user()
        _set_progress(uid, {
            DIM: {
                CAT: {
                    "understanding_score": 100,
                    "questions_answered": [Q1_ID],
                    "questions_correct": [Q1_ID],
                    "last_discussed": None,
                    "reflection_given": False,
                }
            }
        })
        result = present_comprehension_question(uid, DIM, CAT)
        assert result.get("event_type") == "education.comprehension"
        assert result["question_id"] == Q2_ID

    def test_missing_dimension_in_progress_presents_first_question(self):
        """Progress row exists but dimension is absent → treat as no answered."""
        uid = _create_user()
        _set_progress(uid, {"Other Dimension": {}})
        result = present_comprehension_question(uid, DIM, CAT)
        assert result.get("event_type") == "education.comprehension"
        assert result["question_id"] == Q1_ID

    def test_missing_category_in_progress_presents_first_question(self):
        """Dimension exists in progress but category is absent → first question."""
        uid = _create_user()
        _set_progress(uid, {DIM: {"other_category": {"questions_answered": []}}})
        result = present_comprehension_question(uid, DIM, CAT)
        assert result.get("event_type") == "education.comprehension"
        assert result["question_id"] == Q1_ID


# ── no_questions path ─────────────────────────────────────────────────────────

class TestNoQuestionsRemaining:
    def test_all_answered_returns_no_questions(self):
        """When all questions in the category are answered, return no_questions."""
        uid = _create_user()

        # Find all question IDs for the category from the question bank
        from agents.transmutation.question_bank import get_question_bank
        qb = get_question_bank()
        all_qs = qb.get_comprehension_questions_for_category(DIM, CAT)
        all_ids = [q["id"] for q in all_qs]

        _set_progress(uid, {
            DIM: {
                CAT: {
                    "understanding_score": 100,
                    "questions_answered": all_ids,
                    "questions_correct": all_ids,
                    "last_discussed": None,
                    "reflection_given": False,
                }
            }
        })

        result = present_comprehension_question(uid, DIM, CAT)
        assert result.get("status") == "no_questions"
        assert result["dimension"] == DIM
        assert result["category"] == CAT
        assert "event_type" not in result

    def test_nonexistent_category_returns_no_questions(self):
        """A category with no questions in the bank returns no_questions."""
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, "nonexistent_category")
        assert result.get("status") == "no_questions"
        assert "event_type" not in result

    def test_nonexistent_dimension_returns_no_questions(self):
        """A dimension with no questions in the bank returns no_questions."""
        uid = _create_user()
        result = present_comprehension_question(uid, "Nonexistent Dimension", CAT)
        assert result.get("status") == "no_questions"
        assert "event_type" not in result


# ── error path ────────────────────────────────────────────────────────────────

class TestUnknownQuestionId:
    def test_bogus_id_returns_error(self):
        uid = _create_user()
        result = present_comprehension_question(uid, DIM, CAT, "bogus_question_id")
        assert "error" in result
        assert "event_type" not in result

    def test_error_does_not_raise_exception(self):
        """The tool must return a dict, never raise."""
        uid = _create_user()
        try:
            result = present_comprehension_question(uid, DIM, CAT, "totally_unknown")
            assert isinstance(result, dict)
        except Exception as exc:
            pytest.fail(f"present_comprehension_question raised unexpectedly: {exc}")
