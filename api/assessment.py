"""Assessment API endpoints.

Direct endpoints for Likert responses (bypassing the agent for efficiency),
question retrieval, and assessment progress tracking.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import get_current_user_id
from db.database import get_db_session
from rate_limit import limiter
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.scoring_engine import compute_early_transmute_result
from agents.transmutation.tools import _compute_progress, RESPONSE_SAVE_PHASES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assessment", tags=["assessment"])


# --- Request/Response Models ---

class SingleResponseRequest(BaseModel):
    question_id: str
    score: Optional[int] = None
    skipped_reason: Optional[str] = None
    # Scenario (SJT) responses from the ScenarioCard widget set type="scenario"
    # and choice_key; Likert responses leave these unset and use score.
    type: Optional[str] = None
    choice_key: Optional[str] = None


class BatchResponseRequest(BaseModel):
    responses: list[SingleResponseRequest]


class ResponseSaveResult(BaseModel):
    saved: bool
    question_id: str
    progress: dict[str, Any]
    early_result: Optional[dict[str, Any]] = None


class BatchResponseResult(BaseModel):
    saved: int
    errors: list[str]
    progress: dict[str, Any]
    early_result: Optional[dict[str, Any]] = None


class AssessmentProgressResponse(BaseModel):
    exists: bool
    responses: Optional[dict[str, Any]] = None
    scenario_responses: Optional[dict[str, Any]] = None
    completed_dimensions: Optional[list[str]] = None
    current_phase: Optional[str] = None
    progress: Optional[dict[str, Any]] = None
    assessment_tier: Optional[str] = None
    flagged_dimensions: Optional[list[str]] = None
    deep_dive_dimensions: Optional[list[str]] = None
    early_result: Optional[dict[str, Any]] = None


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
@limiter.limit("60/minute")
def save_response(
    request: Request,
    body: SingleResponseRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Save a single Likert or scenario response directly (bypasses agent)."""
    qb = get_question_bank()

    # Scenario (SJT) responses live in a separate index and persist to
    # scenario_responses. quadrant_weight is scoring data resolved server-side
    # from the scenario definition — never trusted from the client. Mirrors
    # tools.save_scenario_response.
    if body.type == "scenario":
        scenario = qb.get_scenario_by_id(body.question_id)
        if not scenario:
            raise HTTPException(status_code=404, detail=f"Scenario not found: {body.question_id}")

        quadrant_weight = None
        for c in scenario.get("choices", []):
            if c["key"] == body.choice_key:
                quadrant_weight = c.get("quadrant_weight", {})
                break
        if quadrant_weight is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid choice '{body.choice_key}' for scenario {body.question_id}",
            )

        with get_db_session() as conn:
            _validate_assessment_phase(conn, user_id)
            responses, scenario_responses = _get_or_create_state(conn, user_id)

            scenario_responses[body.question_id] = {
                "choice": body.choice_key,
                "quadrant_weight": quadrant_weight,
                "maslow_level": scenario.get("maslow_level"),
                "answered_at": datetime.utcnow().isoformat(),
            }

            conn.execute(
                "UPDATE assessment_state SET scenario_responses = ?, updated_at = ? WHERE user_id = ? AND id = (SELECT id FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1)",
                (json.dumps(scenario_responses), datetime.utcnow().isoformat(), user_id, user_id),
            )

            # Scenarios are always transmute-relevant: recompute the cached
            # early_result (if one already exists) so the results panel never
            # shows a stale archetype after the user changes a prior choice.
            early_result = _maybe_regenerate_early_result(conn, user_id, responses, scenario_responses)

        progress = _compute_progress(responses, scenario_responses)
        return ResponseSaveResult(
            saved=True, question_id=body.question_id, progress=progress, early_result=early_result
        )

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

        early_result = None
        if _is_transmute_relevant(question):
            early_result = _maybe_regenerate_early_result(conn, user_id, responses, scenario_responses)

    progress = _compute_progress(responses, scenario_responses)
    return ResponseSaveResult(
        saved=True, question_id=body.question_id, progress=progress, early_result=early_result
    )


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

        any_transmute_relevant = False
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
            if _is_transmute_relevant(question):
                any_transmute_relevant = True

        conn.execute(
            "UPDATE assessment_state SET responses = ?, updated_at = ? WHERE user_id = ? AND id = (SELECT id FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1)",
            (json.dumps(responses), datetime.utcnow().isoformat(), user_id, user_id),
        )

        early_result = None
        if any_transmute_relevant:
            early_result = _maybe_regenerate_early_result(conn, user_id, responses, scenario_responses)

    progress = _compute_progress(responses, scenario_responses)
    return BatchResponseResult(saved=saved_count, errors=errors, progress=progress, early_result=early_result)


# --- Helpers ---

def _is_transmute_relevant(question: dict[str, Any]) -> bool:
    """True if a Likert question's answer feeds the Tier-1 early_result.

    Mirrors the Tier-1 sufficiency check in evaluate_transmute_core_complete:
    transmute_core-tier items (fallback: Transmutation Capacity dimension,
    in case tier is absent on older question-bank entries) are the only
    Likert items compute_early_transmute_result actually consumes. Awareness
    items are upserted normally but never trigger a recompute -- the full
    dimension profile is computed fresh at profile time, so there is no
    stale early_result to fix for those edits.
    """
    return (
        question.get("tier") == "transmute_core"
        or question.get("dimension") == "Transmutation Capacity"
    )


def _maybe_regenerate_early_result(
    conn, user_id: str, responses: dict, scenario_responses: dict
) -> Optional[dict[str, Any]]:
    """Recompute + persist the cached early_result after a transmute-relevant edit.

    Uses the SAME transaction/connection as the triggering upsert
    (backend-transaction-management: upsert + recompute + persist atomically
    -- no window where the answer is saved but the cached score is stale).

    _get_or_create_state deliberately does not SELECT early_result (it only
    needs responses/scenario_responses to build the upsert payload), so this
    helper does its own dedicated read of the current early_result.

    If early_result is NULL, Tier 1 hasn't completed yet -- the initial
    early_result is produced by evaluate_transmute_core_complete once
    sufficiency is reached, so there is nothing to regenerate yet and this
    returns None (anti-patterns-happy-path-only: an explicit "nothing to do"
    branch, not an exception).

    If early_result already exists, recomputes it from current answers via
    the pure compute_early_transmute_result (scoring math is never
    reimplemented here) and persists it with the same computed_at timestamp
    shape the agent path writes at tools.py:935-943, so no consumer of
    early_result ever sees a payload missing that field.
    """
    row = conn.execute(
        "SELECT id, early_result FROM assessment_state WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()

    if not row or not row["early_result"]:
        return None

    result = compute_early_transmute_result(responses, scenario_responses)
    stored = {**result, "computed_at": datetime.utcnow().isoformat()}

    conn.execute(
        "UPDATE assessment_state SET early_result = ?, updated_at = ? WHERE id = ?",
        (json.dumps(stored), datetime.utcnow().isoformat(), row["id"]),
    )

    logger.info(
        "early_result regenerated on edit",
        extra={"user_id": user_id, "archetype": stored.get("archetype")},
    )

    return stored


def _validate_assessment_phase(conn, user_id: str) -> None:
    """Ensure user is in a phase that allows saving Likert responses.

    Accepts assessment, reassessment, and check_in phases (RESPONSE_SAVE_PHASES).
    Raises 403 Forbidden for any other phase so callers cannot bypass the gate
    by manipulating their phase state (anti-patterns-error-swallowing: explicit
    rejection, not silent pass-through).
    """
    row = conn.execute(
        "SELECT current_phase FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row["current_phase"] not in RESPONSE_SAVE_PHASES:
        raise HTTPException(
            status_code=403,
            detail="Forbidden",
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
