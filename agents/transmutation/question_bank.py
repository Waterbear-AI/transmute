import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"


class QuestionBank:
    """Loads and indexes data/questions.json and data/comprehension_checks.json."""

    def __init__(
        self,
        path: Optional[Path] = None,
        comprehension_path: Optional[Path] = None,
    ):
        self._path = path or DATA_DIR / "questions.json"
        self._comprehension_path = comprehension_path or DATA_DIR / "comprehension_checks.json"
        self._data: dict[str, Any] = {}
        self._questions_by_id: dict[str, dict] = {}
        self._questions_by_dimension: dict[str, list[dict]] = {}
        self._scenarios_by_id: dict[str, dict] = {}
        self._loaded = False
        # Comprehension checks: {dimension: {category: [questions]}}
        self._comprehension_data: dict[str, dict[str, list[dict]]] = {}
        # Fast lookup: {question_id: question_dict}
        self._comprehension_by_id: dict[str, dict] = {}
        self._comprehension_loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        with open(self._path) as f:
            self._data = json.load(f)

        for q in self._data.get("questions", []):
            self._questions_by_id[q["id"]] = q
            dim = q["dimension"]
            self._questions_by_dimension.setdefault(dim, []).append(q)

        for s in self._data.get("scenarios", []):
            self._scenarios_by_id[s["id"]] = s

        self._loaded = True
        logger.info(
            "Loaded question bank: %d questions, %d scenarios, %d dimensions",
            len(self._questions_by_id),
            len(self._scenarios_by_id),
            len(self._questions_by_dimension),
        )
        self._harden_sub_dimensions()

    def _harden_sub_dimensions(self) -> None:
        """Warn about items with a missing/blank sub_dimension field.

        `get_sub_dimensions()` derives its index purely from item tags (there
        is no separately declared sub-dimension registry in this codebase --
        `leverage_engine.rank_transmutation_gaps` validates caller-supplied
        sub-dimensions against this same derived index). That means a
        sub-dimension can never be "referenced with zero items" from inside
        `QuestionBank` alone -- the actual silent-data-loss risk visible here
        is an item whose `sub_dimension` is missing or blank, which would
        otherwise fall back to the implicit `"general"` bucket in
        `scoring_engine._score_likert_by_dimension` without anyone noticing
        the item never got the sub-dimension breakdown its dimension expects.
        """
        for dimension, questions in self._questions_by_dimension.items():
            for q in questions:
                sub = q.get("sub_dimension")
                if not sub:
                    logger.warning(
                        "Question bank item missing sub_dimension: "
                        "id=%r dimension=%r -- falls back to 'general'",
                        q.get("id"),
                        dimension,
                    )

    @property
    def meta(self) -> dict[str, Any]:
        self._ensure_loaded()
        return self._data.get("meta", {})

    @property
    def scale_types(self) -> dict[str, Any]:
        return self.meta.get("scale_types", {})

    def get_all_questions(self) -> list[dict]:
        self._ensure_loaded()
        return self._data.get("questions", [])

    def get_all_scenarios(self) -> list[dict]:
        self._ensure_loaded()
        return self._data.get("scenarios", [])

    def get_question_by_id(self, question_id: str) -> Optional[dict]:
        self._ensure_loaded()
        return self._questions_by_id.get(question_id)

    def get_questions_by_dimension(self, dimension: str) -> list[dict]:
        self._ensure_loaded()
        return self._questions_by_dimension.get(dimension, [])

    def get_scenario_by_id(self, scenario_id: str) -> Optional[dict]:
        self._ensure_loaded()
        return self._scenarios_by_id.get(scenario_id)

    def get_dimensions(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._questions_by_dimension.keys())

    def get_sub_dimensions(self, dimension: str) -> list[str]:
        """Return distinct sub_dimension values for a given dimension.

        Derived from indexed questions. Returns [] for an unknown dimension.
        """
        self._ensure_loaded()
        questions = self._questions_by_dimension.get(dimension)
        if questions is None:
            return []
        seen: set[str] = set()
        result: list[str] = []
        for q in questions:
            sub = q.get("sub_dimension")
            if sub and sub not in seen:
                seen.add(sub)
                result.append(sub)
        return sorted(result)

    def get_questions_by_tier(self, tier: str) -> list[dict]:
        """Return all Likert items whose `tier` field matches, across dimensions.

        Tiers: "transmute_core" (Tier 1), "awareness_core" (Tier 2),
        "awareness_deepdive" (Tier 3). Returns [] for an unknown tier.
        """
        self._ensure_loaded()
        return [q for q in self._questions_by_id.values() if q.get("tier") == tier]

    def get_screener_items(self, dimension: Optional[str] = None) -> list[dict]:
        """Return items flagged `is_screener=True`.

        With `dimension`, scopes to that dimension only (returns [] for an
        unknown dimension or a dimension with no screener items). Without it,
        returns every screener item across the whole bank.
        """
        self._ensure_loaded()
        if dimension is not None:
            questions = self._questions_by_dimension.get(dimension, [])
        else:
            questions = self._questions_by_id.values()
        return [q for q in questions if q.get("is_screener")]

    def get_items_by_instrument(self, instrument: str) -> list[dict]:
        """Return all items citing the given `instrument` value verbatim."""
        self._ensure_loaded()
        return [q for q in self._questions_by_id.values() if q.get("instrument") == instrument]

    def get_full_data(self) -> dict[str, Any]:
        """Return the full question bank JSON (for GET /api/assessment/questions)."""
        self._ensure_loaded()
        return self._data

    # --- Comprehension checks ---

    def _ensure_comprehension_loaded(self) -> None:
        if self._comprehension_loaded:
            return

        try:
            with open(self._comprehension_path) as f:
                self._comprehension_data = json.load(f)
        except FileNotFoundError:
            logger.warning("comprehension_checks.json not found at %s", self._comprehension_path)
            self._comprehension_data = {}
            self._comprehension_loaded = True
            return

        total = 0
        for dim, categories in self._comprehension_data.items():
            for cat, questions in categories.items():
                for q in questions:
                    self._comprehension_by_id[q["id"]] = q
                    total += 1

        self._comprehension_loaded = True
        logger.info(
            "Loaded comprehension checks: %d questions across %d dimensions",
            total,
            len(self._comprehension_data),
        )

    def get_comprehension_question(
        self, dimension: str, category: str, question_id: str
    ) -> Optional[dict]:
        """Retrieve a comprehension check question by dimension, category, and ID."""
        self._ensure_comprehension_loaded()

        categories = self._comprehension_data.get(dimension)
        if categories is None:
            return None

        questions = categories.get(category)
        if questions is None:
            return None

        for q in questions:
            if q["id"] == question_id:
                return q

        return None

    def get_comprehension_question_by_id(self, question_id: str) -> Optional[dict]:
        """Retrieve a comprehension check question by ID only (fast lookup)."""
        self._ensure_comprehension_loaded()
        return self._comprehension_by_id.get(question_id)

    def get_comprehension_dimensions(self) -> list[str]:
        """Return all dimensions that have comprehension checks."""
        self._ensure_comprehension_loaded()
        return sorted(self._comprehension_data.keys())

    def get_comprehension_categories(self, dimension: str) -> list[str]:
        """Return all categories for a dimension's comprehension checks."""
        self._ensure_comprehension_loaded()
        categories = self._comprehension_data.get(dimension, {})
        return sorted(categories.keys())

    def get_comprehension_questions_for_category(
        self, dimension: str, category: str
    ) -> list[dict]:
        """Return all comprehension questions for a specific dimension + category."""
        self._ensure_comprehension_loaded()
        return self._comprehension_data.get(dimension, {}).get(category, [])


# Module-level singleton
_question_bank: Optional[QuestionBank] = None


def get_question_bank() -> QuestionBank:
    global _question_bank
    if _question_bank is None:
        _question_bank = QuestionBank()
    return _question_bank
