"""Assessment API endpoints.

Direct endpoints for Likert responses (bypassing the agent for efficiency),
question retrieval, and assessment progress tracking.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user_id
from db.database import get_db_session
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.tools import _compute_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assessment", tags=["assessment"])


# --- Request/Response Models ---

class SingleResponseRequest(BaseModel):
    question_id: str
    score: Optional[int] = None
    skipped_reason: Optional[str] = None


class BatchResponseRequest(BaseModel):
    responses: list[SingleResponseRequest]


class ResponseSaveResult(BaseModel):
    saved: bool
    question_id: str
    progress: dict[str, Any]


class BatchResponseResult(BaseModel):
    saved: int
    errors: list[str]
    progress: dict[str, Any]


class AssessmentProgressResponse(BaseModel):
    exists: bool
    responses: Optional[dict[str, Any]] = None
    scenario_responses: Optional[dict[str, Any]] = None
    completed_dimensions: Optional[list[str]] = None
    current_phase: Optional[str] = None
    progress: Optional[dict[str, Any]] = None


# --- Endpoints ---

@router.get("/questions")
def get_questions(user_id: str = Depends(get_current_user_id)):
    """Return the full question bank for frontend rendering."""
    qb = get_question_bank()
    return qb.get_full_data()


@router.get("/state", response_model=AssessmentProgressResponse)
def get_state(user_id: str = Depends(get_current_user_id)):
    """Return current assessment progress for the authenticated user."""
    from agents.transmutation.tools import get_assessment_state
    return get_assessment_state(user_id)


@router.post("/responses", response_model=ResponseSaveResult)
def save_response(
    body: SingleResponseRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Save a single Likert response directly (bypasses agent)."""
    qb = get_question_bank()
    question = qb.get_question_by_id(body.question_id)
    if not question:
        raise HTTPException(status_code=404, detail=f"Question not found: {body.question_id}")

    with get_db_session() as conn:
        _validate_assessment_phase(conn, user_id)
        responses, scenario_responses = _get_or_create_state(conn, user_id)

        responses[body.question_id] = {
            "score": body.score,
            "skipped_reason": body.skipped_reason,
            "dimension": question["dimension"],
            "sub_dimension": question["sub_dimension"],
            "answered_at": datetime.utcnow().isoformat(),
        }

        conn.execute(
            "UPDATE assessment_state SET responses = ?, updated_at = ? WHERE user_id = ? AND id = (SELECT id FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1)",
            (json.dumps(responses), datetime.utcnow().isoformat(), user_id, user_id),
        )

    progress = _compute_progress(responses, scenario_responses)
    return ResponseSaveResult(saved=True, question_id=body.question_id, progress=progress)


@router.post("/responses/batch", response_model=BatchResponseResult)
def save_responses_batch(
    body: BatchResponseRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Save multiple Likert responses in a single transaction."""
    qb = get_question_bank()
    errors = []
    saved_count = 0

    with get_db_session() as conn:
        _validate_assessment_phase(conn, user_id)
        responses, scenario_responses = _get_or_create_state(conn, user_id)

        for resp in body.responses:
            question = qb.get_question_by_id(resp.question_id)
            if not question:
                errors.append(f"Question not found: {resp.question_id}")
                continue

            responses[resp.question_id] = {
                "score": resp.score,
                "skipped_reason": resp.skipped_reason,
                "dimension": question["dimension"],
                "sub_dimension": question["sub_dimension"],
                "answered_at": datetime.utcnow().isoformat(),
            }
            saved_count += 1

        conn.execute(
            "UPDATE assessment_state SET responses = ?, updated_at = ? WHERE user_id = ? AND id = (SELECT id FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1)",
            (json.dumps(responses), datetime.utcnow().isoformat(), user_id, user_id),
        )

    progress = _compute_progress(responses, scenario_responses)
    return BatchResponseResult(saved=saved_count, errors=errors, progress=progress)


# --- Helpers ---

def _validate_assessment_phase(conn, user_id: str) -> None:
    """Ensure user is in assessment phase."""
    row = conn.execute(
        "SELECT current_phase FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row["current_phase"] != "assessment":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot save responses in phase: {row['current_phase']}",
        )


def _get_or_create_state(conn, user_id: str) -> tuple[dict, dict]:
    """Get existing assessment state or create a new one. Returns (responses, scenario_responses)."""
    row = conn.execute(
        "SELECT id, responses, scenario_responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()

    if row:
        responses = json.loads(row["responses"] or "{}")
        scenario_responses = json.loads(row["scenario_responses"] or "{}")
        return responses, scenario_responses

    state_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO assessment_state (id, user_id, responses, scenario_responses, created_at) VALUES (?, ?, '{}', '{}', ?)",
        (state_id, user_id, datetime.utcnow().isoformat()),
    )
    return {}, {}
