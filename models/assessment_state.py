import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

# The tiered assessment flow a user progresses through:
#   transmute_core -> validated_scale -> deep_dive (entered only for flagged dimensions)
AssessmentTier = Literal["transmute_core", "validated_scale", "deep_dive"]


class AssessmentState(BaseModel):
    id: str
    user_id: str
    session_id: Optional[str] = None
    responses: Optional[dict[str, Any]] = None
    scenario_responses: Optional[dict[str, Any]] = None
    current_phase: str = "assessment"
    completed_dimensions: Optional[list[str]] = None
    assessment_tier: AssessmentTier = "transmute_core"
    flagged_dimensions: Optional[list[str]] = None
    deep_dive_dimensions: Optional[list[str]] = None
    early_result: Optional[dict[str, Any]] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "AssessmentState":
        """Create an AssessmentState from a sqlite3.Row."""
        responses = row["responses"]
        if isinstance(responses, str):
            responses = json.loads(responses) if responses else None

        scenario_responses = row["scenario_responses"]
        if isinstance(scenario_responses, str):
            scenario_responses = json.loads(scenario_responses) if scenario_responses else None

        completed_dims = row["completed_dimensions"]
        if isinstance(completed_dims, str):
            completed_dims = json.loads(completed_dims) if completed_dims else None

        flagged_dims = row["flagged_dimensions"]
        if isinstance(flagged_dims, str):
            flagged_dims = json.loads(flagged_dims) if flagged_dims else None

        deep_dive_dims = row["deep_dive_dimensions"]
        if isinstance(deep_dive_dims, str):
            deep_dive_dims = json.loads(deep_dive_dims) if deep_dive_dims else None

        early_result = row["early_result"]
        if isinstance(early_result, str):
            early_result = json.loads(early_result) if early_result else None

        return cls(
            id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            responses=responses,
            scenario_responses=scenario_responses,
            current_phase=row["current_phase"],
            completed_dimensions=completed_dims,
            assessment_tier=row["assessment_tier"],
            flagged_dimensions=flagged_dims,
            deep_dive_dimensions=deep_dive_dims,
            early_result=early_result,
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
