import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class AssessmentState(BaseModel):
    id: str
    user_id: str
    session_id: Optional[str] = None
    responses: Optional[dict[str, Any]] = None
    scenario_responses: Optional[dict[str, Any]] = None
    current_phase: str = "assessment"
    completed_dimensions: Optional[list[str]] = None
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

        return cls(
            id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            responses=responses,
            scenario_responses=scenario_responses,
            current_phase=row["current_phase"],
            completed_dimensions=completed_dims,
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
