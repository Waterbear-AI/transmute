"""Contract + behavior tests for education comprehension content.

Two layers:
  1. Data-integrity contract over data/comprehension_checks.json (AC1-AC6).
     The loader (question_bank.py) performs NO validation, so this is the only
     guard against content drift (missing category, bad answer key, dup id).
  2. Gate/tool behavior that the content unblocks (AC7-AC8), including the R6
     content-gap guard in _check_education_completion_gate.
"""

import json
import uuid
from pathlib import Path

import pytest

from agents.transmutation.question_bank import DATA_DIR, QuestionBank
import agents.transmutation.question_bank as qb_mod
from agents.transmutation import tools
from db.database import get_db_session

# ── The contract (intentionally defined here, not imported, so the test pins it) ──
# v2 (DOC-001): 8 dimensions, replacing the v1 13-dimension set. Cut: Cognitive
# Awareness (merged into Meta-Cognitive), Environmental Awareness (folded into
# Systemic/Temporal), Flow Awareness (redundant with scenarios), Physical
# Awareness (folded into Emotional Awareness & Regulation's body-noticing
# facet), Spatial Awareness, Social Awareness (folded into Relational
# Awareness & Compassion), Temporal Awareness (merged into Systemic/Temporal).
# Added: Reflective Functioning, Self-Compassion, Relational Awareness &
# Compassion. Renamed: Emotional Awareness -> Emotional Awareness & Regulation,
# Systemic Awareness -> Systemic/Temporal Awareness, Mindfulness -> Mindful
# Presence.
CANONICAL_DIMENSIONS = {
    "Transmutation Capacity", "Emotional Awareness & Regulation", "Reflective Functioning",
    "Self-Compassion", "Relational Awareness & Compassion", "Meta-Cognitive Awareness",
    "Mindful Presence", "Systemic/Temporal Awareness",
}
REQUIRED_CATEGORIES = {
    "what_this_means", "your_score", "daily_effects",
    "strengths_gaps", "external_interaction",
}
VALID_TYPES = {"apply_concept", "identify_pattern", "predict_outcome"}
VALID_DIFFICULTY = {"foundational", "applied"}
MIN_QUESTIONS_PER_CATEGORY = 2
EXPECTED_TOTAL = 80

CONTENT_PATH = DATA_DIR / "comprehension_checks.json"


@pytest.fixture(scope="module")
def content() -> dict:
    return json.loads(CONTENT_PATH.read_text())


def _all_questions(content: dict):
    for dim, cats in content.items():
        for cat, questions in cats.items():
            for q in questions:
                yield dim, cat, q


# ── AC1-AC6: data-integrity contract ──────────────────────────────────────

class TestComprehensionContentIntegrity:
    def test_all_canonical_dimensions_present(self, content):
        """AC1: exactly the 13 canonical dimensions, names matching questions.json."""
        assert set(content.keys()) == CANONICAL_DIMENSIONS

    def test_each_dimension_has_five_categories(self, content):
        """AC2: every dimension has exactly the 5 canonical categories."""
        for dim, cats in content.items():
            assert set(cats.keys()) == REQUIRED_CATEGORIES, dim

    def test_coverage_and_total(self, content):
        """AC3: >=2 questions per (dim, category); total == 130."""
        total = 0
        for dim, cats in content.items():
            for cat in REQUIRED_CATEGORIES:
                n = len(cats[cat])
                assert n >= MIN_QUESTIONS_PER_CATEGORY, f"{dim}/{cat} has {n}"
                total += n
        assert total == EXPECTED_TOTAL

    def test_required_fields_and_enums(self, content):
        """AC4: required fields present; type/difficulty within enums."""
        required = {"id", "type", "stem", "options", "correct_option",
                    "explanation", "difficulty"}
        for dim, cat, q in _all_questions(content):
            missing = required - q.keys()
            assert not missing, f"{q.get('id')} missing {missing}"
            assert q["type"] in VALID_TYPES, f"{q['id']} type={q['type']}"
            assert q["difficulty"] in VALID_DIFFICULTY, f"{q['id']} diff={q['difficulty']}"
            assert isinstance(q["stem"], str) and q["stem"].strip()
            assert isinstance(q["explanation"], str) and q["explanation"].strip()

    def test_option_keys_unique_and_correct_option_valid(self, content):
        """AC5: option keys unique; correct_option is one of them."""
        for dim, cat, q in _all_questions(content):
            keys = [o["key"] for o in q["options"]]
            assert len(keys) >= 2, q["id"]
            assert len(keys) == len(set(keys)), f"{q['id']} dup option keys"
            assert q["correct_option"] in keys, \
                f"{q['id']} correct_option {q['correct_option']} not in {keys}"

    def test_ids_globally_unique(self, content):
        """AC6: every question id is unique across the whole file."""
        ids = [q["id"] for _, _, q in _all_questions(content)]
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, f"duplicate ids: {dupes}"


# ── Gate/tool behavior (AC7-AC8) ──────────────────────────────────────────

def _seed_user(conn, user_id: str, phase: str = "education") -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, password_hash, current_phase) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, "T", f"{user_id}@example.com", "x", phase),
    )


def _seed_profile_scores(conn, user_id: str, scores: dict) -> None:
    conn.execute(
        "INSERT INTO profile_snapshots (id, user_id, scores) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, json.dumps(scores)),
    )


def _seed_education_progress(conn, user_id: str, progress: dict) -> None:
    conn.execute(
        "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
        (user_id, json.dumps(progress)),
    )


def _answered_all_categories(correct: bool = True) -> dict:
    """Progress block answering all 5 categories (100% if correct)."""
    return {
        cat: {
            "questions_answered": ["seed_q"],
            "questions_correct": ["seed_q"] if correct else [],
        }
        for cat in REQUIRED_CATEGORIES
    }


class TestRecordAnswerForNewlyAuthoredDimension:
    def test_correct_answer_in_previously_contentless_dimension(self):
        """AC7: record_comprehension_answer resolves for a v2 dimension's question."""
        # Mindful Presence is one of the newly regenerated v2 dimensions.
        qb = qb_mod.get_question_bank()
        qid = "cc_mp_cat1_q1"
        question = qb.get_comprehension_question_by_id(qid)
        assert question is not None, "new comprehension content not loaded"

        uid = str(uuid.uuid4())
        with get_db_session() as conn:
            _seed_user(conn, uid)

        result = tools.record_comprehension_answer(
            uid, "Mindful Presence", "what_this_means", qid, question["correct_option"],
        )
        assert result["correct"] is True
        assert result["score"] == 100
        assert result["categories_total"] == 5  # all 5 categories now have content


class TestEducationGateWithFullContent:
    def test_gate_passes_when_contentless_dims_now_covered(self):
        """AC8a: top-3 weakest v2 dimensions pass when answered."""
        uid = str(uuid.uuid4())
        # Make three v2 dimensions the weakest three.
        scores = {
            "Mindful Presence": {"score": 1.0},
            "Self-Compassion": {"score": 1.1},
            "Systemic/Temporal Awareness": {"score": 1.2},
            "Emotional Awareness & Regulation": {"score": 4.0},
        }
        progress = {
            dim: _answered_all_categories(correct=True)
            for dim in ("Mindful Presence", "Self-Compassion", "Systemic/Temporal Awareness")
        }
        with get_db_session() as conn:
            _seed_user(conn, uid)
            _seed_profile_scores(conn, uid, scores)
            _seed_education_progress(conn, uid, progress)
            result = tools._check_education_completion_gate(conn, uid)
        assert result is None  # gate passes

    def test_gate_blocks_when_weak_dim_below_60_percent(self):
        """Sanity: a content-bearing dim answered but <60% correct still blocks."""
        uid = str(uuid.uuid4())
        scores = {"Mindful Presence": {"score": 1.0}}
        progress = {"Mindful Presence": _answered_all_categories(correct=False)}  # 0%
        with get_db_session() as conn:
            _seed_user(conn, uid)
            _seed_profile_scores(conn, uid, scores)
            _seed_education_progress(conn, uid, progress)
            result = tools._check_education_completion_gate(conn, uid)
        assert result is not None
        assert result["dimension"] == "Mindful Presence"


class TestR6ContentGapGuard:
    """R6: a content-LESS category is waived (logged); content-bearing ones still block."""

    @pytest.fixture
    def synthetic_bank(self, tmp_path):
        """Inject a QuestionBank whose 'Physical Awareness' lacks external_interaction."""
        # cat1-4 have content; external_interaction is absent (the content gap).
        synthetic = {
            "Physical Awareness": {
                "what_this_means": [{"id": "syn_q1", "correct_option": "a",
                                     "options": [{"key": "a", "text": "x"}]}],
                "your_score": [{"id": "syn_q2", "correct_option": "a",
                                "options": [{"key": "a", "text": "x"}]}],
                "daily_effects": [{"id": "syn_q3", "correct_option": "a",
                                   "options": [{"key": "a", "text": "x"}]}],
                "strengths_gaps": [{"id": "syn_q4", "correct_option": "a",
                                    "options": [{"key": "a", "text": "x"}]}],
                # external_interaction intentionally missing
            }
        }
        path = tmp_path / "synthetic_comprehension.json"
        path.write_text(json.dumps(synthetic))
        original = qb_mod._question_bank
        qb_mod._question_bank = QuestionBank(comprehension_path=path)
        yield
        qb_mod._question_bank = original  # test isolation: restore the real bank

    def test_waives_contentless_category_and_logs(self, synthetic_bank, caplog):
        """AC8b: the empty external_interaction category is waived, not blocking."""
        uid = str(uuid.uuid4())
        scores = {"Physical Awareness": {"score": 1.0}}  # sole dim -> top-3 == [it]
        # Answer the 4 categories that HAVE content; leave the empty one unanswered.
        progress = {"Physical Awareness": {
            cat: {"questions_answered": ["x"], "questions_correct": ["x"]}
            for cat in ("what_this_means", "your_score", "daily_effects", "strengths_gaps")
        }}
        with get_db_session() as conn:
            _seed_user(conn, uid)
            _seed_profile_scores(conn, uid, scores)
            _seed_education_progress(conn, uid, progress)
            with caplog.at_level("WARNING"):
                result = tools._check_education_completion_gate(conn, uid)
        assert result is None  # waived, not blocked
        assert any("external_interaction" in r.message for r in caplog.records)

    def test_still_blocks_contentful_unanswered_category(self, synthetic_bank):
        """A category that HAS content but is unanswered still blocks (not waived)."""
        uid = str(uuid.uuid4())
        scores = {"Physical Awareness": {"score": 1.0}}
        # Answer only cat1-3; leave strengths_gaps (has content) unanswered.
        progress = {"Physical Awareness": {
            cat: {"questions_answered": ["x"], "questions_correct": ["x"]}
            for cat in ("what_this_means", "your_score", "daily_effects")
        }}
        with get_db_session() as conn:
            _seed_user(conn, uid)
            _seed_profile_scores(conn, uid, scores)
            _seed_education_progress(conn, uid, progress)
            result = tools._check_education_completion_gate(conn, uid)
        assert result is not None
        assert result["category"] == "strengths_gaps"  # content-bearing, still required
