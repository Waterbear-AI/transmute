import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from db.database import get_db_session
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.scoring_engine import score_responses
from agents.transmutation.spider_chart import generate_spider_chart

logger = logging.getLogger(__name__)

# Phase ordering for validation
PHASE_ORDER = ["orientation", "assessment", "profile", "education", "development", "graduation"]


def get_assessment_state(user_id: str) -> dict[str, Any]:
    """Retrieve current assessment progress for a user.

    Returns progress including which questions are answered, remaining count,
    and per-dimension completion percentages.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT * FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {
            "exists": False,
            "responses": {},
            "scenario_responses": {},
            "completed_dimensions": [],
            "current_phase": "assessment",
            "progress": _compute_progress({}, {}),
        }

    responses = json.loads(row["responses"] or "{}")
    scenario_responses = json.loads(row["scenario_responses"] or "{}")
    completed_dims = json.loads(row["completed_dimensions"] or "[]")

    return {
        "exists": True,
        "id": row["id"],
        "session_id": row["session_id"],
        "responses": responses,
        "scenario_responses": scenario_responses,
        "completed_dimensions": completed_dims,
        "current_phase": row["current_phase"],
        "progress": _compute_progress(responses, scenario_responses),
    }


def _compute_progress(
    responses: dict[str, Any], scenario_responses: dict[str, Any]
) -> dict[str, Any]:
    """Compute per-dimension progress from responses."""
    qb = get_question_bank()
    all_questions = qb.get_all_questions()
    all_scenarios = qb.get_all_scenarios()

    dimension_progress = {}
    for dim in qb.get_dimensions():
        dim_questions = qb.get_questions_by_dimension(dim)
        total = len(dim_questions)
        answered = 0
        na_count = 0
        scores = []

        for q in dim_questions:
            if q["id"] in responses:
                resp = responses[q["id"]]
                answered += 1
                if resp.get("score") is None:
                    na_count += 1
                else:
                    scores.append(resp["score"])

        applicable_total = total - na_count
        avg_score = sum(scores) / len(scores) if scores else 0

        dimension_progress[dim] = {
            "answered": answered,
            "total": total,
            "na_count": na_count,
            "applicable_total": applicable_total,
            "completion_pct": round(answered / total * 100, 1) if total > 0 else 0,
            "avg_score": round(avg_score, 2),
            "insufficient_data": na_count > total * 0.2 if total > 0 else False,
        }

    return {
        "answered": len(responses),
        "total": len(all_questions),
        "scenarios_completed": len(scenario_responses),
        "scenarios_total": len(all_scenarios),
        "dimension_progress": dimension_progress,
    }


def get_user_profile(user_id: str) -> dict[str, Any]:
    """Retrieve the most recent profile snapshot for a user.

    Returns scores, quadrant placement, and interpretation.
    Available to all agents (no phase gate on reads).
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT * FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"exists": False}

    scores = json.loads(row["scores"] or "{}")
    quadrant = json.loads(row["quadrant_placement"] or "{}")

    return {
        "exists": True,
        "id": row["id"],
        "session_id": row["session_id"],
        "scores": scores,
        "quadrant_placement": quadrant,
        "interpretation": row["interpretation"],
        "has_spider_chart": row["spider_chart"] is not None,
        "created_at": row["created_at"],
    }


def advance_phase(user_id: str, new_phase: str, reason: str = "") -> dict[str, Any]:
    """Transition user to a new phase with validation.

    Phase gates:
    - orientation -> assessment: user must have sent >= 1 message (validated by caller)
    - assessment -> profile: applicability-aware completion required
    - profile -> education: snapshot must exist
    """
    if new_phase not in PHASE_ORDER:
        return {"error": f"Invalid phase: {new_phase}"}

    with get_db_session() as conn:
        row = conn.execute(
            "SELECT current_phase FROM users WHERE id = ?", (user_id,)
        ).fetchone()

        if not row:
            return {"error": "User not found"}

        current = row["current_phase"]
        current_idx = PHASE_ORDER.index(current) if current in PHASE_ORDER else -1
        new_idx = PHASE_ORDER.index(new_phase)

        if new_idx <= current_idx:
            return {"error": f"Cannot go from {current} to {new_phase}"}

        # Phase-specific gate checks
        if new_phase == "profile":
            gate = _check_assessment_completion_gate(conn, user_id)
            if gate:
                return gate

        if new_phase == "education":
            profile = conn.execute(
                "SELECT id FROM profile_snapshots WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not profile:
                return {"error": "Profile snapshot must exist before advancing to education"}

        conn.execute(
            "UPDATE users SET current_phase = ? WHERE id = ?",
            (new_phase, user_id),
        )

    return {
        "success": True,
        "previous_phase": current,
        "new_phase": new_phase,
        "reason": reason,
    }


def _check_assessment_completion_gate(conn, user_id: str) -> Optional[dict]:
    """Check if assessment is complete enough to advance to profile."""
    row = conn.execute(
        "SELECT responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()

    if not row:
        return {"error": "No assessment data found"}

    responses = json.loads(row["responses"] or "{}")
    qb = get_question_bank()

    for dim in qb.get_dimensions():
        dim_questions = qb.get_questions_by_dimension(dim)
        total = len(dim_questions)
        answered = sum(1 for q in dim_questions if q["id"] in responses)
        pct = answered / total if total > 0 else 0

        if pct < 0.6:
            return {
                "error": f"Dimension '{dim}' has only {pct:.0%} answered (minimum 60% required)",
                "dimension": dim,
                "answered": answered,
                "total": total,
            }

    return None


def flag_safety_concern(user_id: str, session_id: str, reason: str) -> dict[str, Any]:
    """Log a safety concern to the safety_log table."""
    concern_id = str(uuid.uuid4())

    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO safety_log (id, user_id, session_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (concern_id, user_id, session_id, reason, datetime.utcnow().isoformat()),
        )

    logger.warning("Safety concern flagged for user %s: %s", user_id, reason)

    return {
        "logged": True,
        "id": concern_id,
    }


def present_question_batch(user_id: str, question_ids: list[str]) -> dict[str, Any]:
    """Present a batch of Likert questions to the user.

    Returns full question data for the specified IDs, ready for frontend rendering.
    The SSE layer will emit this as an assessment.question_batch event.
    """
    qb = get_question_bank()
    questions = []
    missing = []

    for qid in question_ids:
        q = qb.get_question_by_id(qid)
        if q:
            questions.append(q)
        else:
            missing.append(qid)

    if missing:
        logger.warning("Question IDs not found: %s", missing)

    # Include scale definitions so the frontend can render labels
    scale_types = qb.scale_types

    return {
        "event_type": "assessment.question_batch",
        "questions": questions,
        "scale_types": scale_types,
        "count": len(questions),
        "missing": missing,
    }


def present_scenario(user_id: str, scenario_id: str) -> dict[str, Any]:
    """Present a behavioral scenario to the user.

    Returns scenario data with quadrant_weight stripped from choices
    (that's internal scoring data the frontend should not see).
    """
    qb = get_question_bank()
    scenario = qb.get_scenario_by_id(scenario_id)

    if not scenario:
        return {"error": f"Scenario not found: {scenario_id}"}

    # Deep copy choices and strip quadrant_weight
    safe_choices = []
    for choice in scenario.get("choices", []):
        safe_choices.append({
            "key": choice["key"],
            "text": choice["text"],
        })

    return {
        "event_type": "assessment.scenario",
        "scenario_id": scenario["id"],
        "narrative": scenario["narrative"],
        "choices": safe_choices,
        "follow_up_prompt": scenario.get("follow_up_prompt"),
        "maslow_level": scenario.get("maslow_level"),
        "order": scenario.get("order"),
    }


def save_assessment_response(
    user_id: str,
    question_id: str,
    score: Optional[int] = None,
    skipped_reason: Optional[str] = None,
) -> dict[str, Any]:
    """Save a single Likert assessment response (agent-orchestrated path).

    Validates the user is in assessment phase, then persists the response.
    score=None with skipped_reason='not_applicable' marks an N/A response.
    """
    qb = get_question_bank()
    question = qb.get_question_by_id(question_id)
    if not question:
        return {"error": f"Question not found: {question_id}"}

    with get_db_session() as conn:
        # Validate phase
        user_row = conn.execute(
            "SELECT current_phase FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user_row:
            return {"error": "User not found"}
        if user_row["current_phase"] != "assessment":
            return {"error": f"Cannot save responses in phase: {user_row['current_phase']}"}

        # Get or create assessment state
        row = conn.execute(
            "SELECT id, responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if row:
            state_id = row["id"]
            responses = json.loads(row["responses"] or "{}")
        else:
            state_id = str(uuid.uuid4())
            responses = {}
            conn.execute(
                "INSERT INTO assessment_state (id, user_id, responses, scenario_responses, created_at) VALUES (?, ?, '{}', '{}', ?)",
                (state_id, user_id, datetime.utcnow().isoformat()),
            )

        # Save the response
        responses[question_id] = {
            "score": score,
            "skipped_reason": skipped_reason,
            "dimension": question["dimension"],
            "sub_dimension": question["sub_dimension"],
            "answered_at": datetime.utcnow().isoformat(),
        }

        conn.execute(
            "UPDATE assessment_state SET responses = ?, updated_at = ? WHERE id = ?",
            (json.dumps(responses), datetime.utcnow().isoformat(), state_id),
        )

    # Return progress for SSE emission
    progress = _compute_progress(responses, {})
    return {
        "event_type": "assessment.progress",
        "saved": True,
        "question_id": question_id,
        "progress": progress,
    }


def save_scenario_response(
    user_id: str,
    scenario_id: str,
    choice: str,
    free_text: Optional[str] = None,
) -> dict[str, Any]:
    """Save a behavioral scenario response (agent-orchestrated path).

    Retrieves the quadrant_weight for the chosen option internally
    (this is scoring data, not exposed to the frontend).
    """
    qb = get_question_bank()
    scenario = qb.get_scenario_by_id(scenario_id)
    if not scenario:
        return {"error": f"Scenario not found: {scenario_id}"}

    # Find the chosen option and its quadrant_weight
    quadrant_weight = None
    for c in scenario.get("choices", []):
        if c["key"] == choice:
            quadrant_weight = c.get("quadrant_weight", {})
            break

    if quadrant_weight is None:
        return {"error": f"Invalid choice '{choice}' for scenario {scenario_id}"}

    with get_db_session() as conn:
        # Validate phase
        user_row = conn.execute(
            "SELECT current_phase FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user_row:
            return {"error": "User not found"}
        if user_row["current_phase"] != "assessment":
            return {"error": f"Cannot save responses in phase: {user_row['current_phase']}"}

        # Get or create assessment state
        row = conn.execute(
            "SELECT id, responses, scenario_responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if row:
            state_id = row["id"]
            responses = json.loads(row["responses"] or "{}")
            scenario_responses = json.loads(row["scenario_responses"] or "{}")
        else:
            state_id = str(uuid.uuid4())
            responses = {}
            scenario_responses = {}
            conn.execute(
                "INSERT INTO assessment_state (id, user_id, responses, scenario_responses, created_at) VALUES (?, ?, '{}', '{}', ?)",
                (state_id, user_id, datetime.utcnow().isoformat()),
            )

        # Save the scenario response (quadrant_weight stored internally for scoring)
        scenario_responses[scenario_id] = {
            "choice": choice,
            "free_text": free_text,
            "quadrant_weight": quadrant_weight,
            "maslow_level": scenario.get("maslow_level"),
            "answered_at": datetime.utcnow().isoformat(),
        }

        conn.execute(
            "UPDATE assessment_state SET scenario_responses = ?, updated_at = ? WHERE id = ?",
            (json.dumps(scenario_responses), datetime.utcnow().isoformat(), state_id),
        )

    # Return progress for SSE emission
    progress = _compute_progress(responses, scenario_responses)
    return {
        "event_type": "assessment.progress",
        "saved": True,
        "scenario_id": scenario_id,
        "progress": progress,
    }


def generate_profile_snapshot(user_id: str) -> dict[str, Any]:
    """Generate a complete profile from assessment data.

    Orchestrates: fetch assessment → score → quadrant placement → spider chart.
    Returns the profile data for the LLM to interpret before saving.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT responses, scenario_responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"error": "No assessment data found"}

    responses = json.loads(row["responses"] or "{}")
    scenario_responses = json.loads(row["scenario_responses"] or "{}")

    # Run deterministic scoring
    result = score_responses(responses, scenario_responses)

    # Generate spider chart
    chart_png = generate_spider_chart(result["dimensions"])

    # Store in module-level cache for save_profile_snapshot to retrieve
    _profile_cache[user_id] = {
        "scores": result["dimensions"],
        "quadrant": result["quadrant"],
        "insufficient_dimensions": result["insufficient_dimensions"],
        "spider_chart": chart_png,
    }

    # Return data for LLM interpretation (no binary blob)
    return {
        "scores": result["dimensions"],
        "quadrant": result["quadrant"],
        "insufficient_dimensions": result["insufficient_dimensions"],
        "has_spider_chart": True,
    }


# Temporary cache for profile data between generate and save calls
_profile_cache: dict[str, dict[str, Any]] = {}


def save_profile_snapshot(user_id: str, interpretation: str) -> dict[str, Any]:
    """Persist a profile snapshot with the LLM's narrative interpretation.

    Must be called after generate_profile_snapshot. Saves scores, quadrant,
    spider chart, and interpretation to the profile_snapshots table.
    """
    cached = _profile_cache.pop(user_id, None)
    if not cached:
        return {"error": "No generated profile found. Call generate_profile_snapshot first."}

    snapshot_id = str(uuid.uuid4())

    with get_db_session() as conn:
        # Find previous snapshot for chaining
        prev = conn.execute(
            "SELECT id FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        prev_id = prev["id"] if prev else None

        # Get current session_id
        session_row = conn.execute(
            "SELECT session_id FROM adk_sessions WHERE user_id = ? AND archived = FALSE ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        session_id = session_row["session_id"] if session_row else None

        conn.execute(
            """INSERT INTO profile_snapshots
               (id, user_id, session_id, scores, quadrant_placement, spider_chart, interpretation, previous_snapshot_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                user_id,
                session_id,
                json.dumps(cached["scores"]),
                json.dumps(cached["quadrant"]),
                cached["spider_chart"],
                interpretation,
                prev_id,
                datetime.utcnow().isoformat(),
            ),
        )

    return {
        "event_type": "profile.snapshot",
        "saved": True,
        "snapshot_id": snapshot_id,
        "scores": cached["scores"],
        "quadrant": cached["quadrant"],
        "interpretation": interpretation,
    }
