"""Results API endpoint for the Results Panel.

Aggregates assessment state and profile snapshots for frontend display.
"""

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user_id
from db.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/results", tags=["results"])


class ProfileSnapshotResponse(BaseModel):
    id: str
    scores: Optional[dict[str, Any]] = None
    quadrant_placement: Optional[dict[str, Any]] = None
    interpretation: Optional[str] = None
    has_spider_chart: bool = False
    created_at: Optional[str] = None


class AssessmentSummary(BaseModel):
    exists: bool
    answered: int = 0
    total: int = 0
    scenarios_completed: int = 0
    scenarios_total: int = 0
    current_phase: Optional[str] = None


class ResultsResponse(BaseModel):
    user_id: str
    current_phase: Optional[str] = None
    assessment: AssessmentSummary
    profiles: list[ProfileSnapshotResponse]
    latest_profile: Optional[ProfileSnapshotResponse] = None


@router.get("/{target_user_id}", response_model=ResultsResponse)
def get_results(
    target_user_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return aggregated results for the Results Panel.

    Users can only access their own results.
    """
    if target_user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's results")

    with get_db_session() as conn:
        # Get user phase
        user_row = conn.execute(
            "SELECT current_phase FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")

        current_phase = user_row["current_phase"]

        # Get assessment state
        assessment_row = conn.execute(
            "SELECT responses, scenario_responses, current_phase FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if assessment_row:
            responses = json.loads(assessment_row["responses"] or "{}")
            scenario_responses = json.loads(assessment_row["scenario_responses"] or "{}")
            from agents.transmutation.question_bank import get_question_bank
            qb = get_question_bank()
            assessment = AssessmentSummary(
                exists=True,
                answered=len(responses),
                total=len(qb.get_all_questions()),
                scenarios_completed=len(scenario_responses),
                scenarios_total=len(qb.get_all_scenarios()),
                current_phase=assessment_row["current_phase"],
            )
        else:
            assessment = AssessmentSummary(exists=False)

        # Get profile snapshots
        profile_rows = conn.execute(
            "SELECT id, scores, quadrant_placement, interpretation, spider_chart, created_at FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()

        profiles = []
        for row in profile_rows:
            profiles.append(ProfileSnapshotResponse(
                id=row["id"],
                scores=json.loads(row["scores"]) if row["scores"] else None,
                quadrant_placement=json.loads(row["quadrant_placement"]) if row["quadrant_placement"] else None,
                interpretation=row["interpretation"],
                has_spider_chart=row["spider_chart"] is not None,
                created_at=row["created_at"],
            ))

    return ResultsResponse(
        user_id=user_id,
        current_phase=current_phase,
        assessment=assessment,
        profiles=profiles,
        latest_profile=profiles[0] if profiles else None,
    )
