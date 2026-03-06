import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"


class QuestionBank:
    """Loads and indexes data/questions.json for efficient access."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DATA_DIR / "questions.json"
        self._data: dict[str, Any] = {}
        self._questions_by_id: dict[str, dict] = {}
        self._questions_by_dimension: dict[str, list[dict]] = {}
        self._scenarios_by_id: dict[str, dict] = {}
        self._loaded = False

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

    def get_full_data(self) -> dict[str, Any]:
        """Return the full question bank JSON (for GET /api/assessment/questions)."""
        self._ensure_loaded()
        return self._data


# Module-level singleton
_question_bank: Optional[QuestionBank] = None


def get_question_bank() -> QuestionBank:
    global _question_bank
    if _question_bank is None:
        _question_bank = QuestionBank()
    return _question_bank
