import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from db.database import get_db_session
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.scoring_engine import (
    score_responses,
    normalize_score,
    score_question_subset,
    _calculate_quadrant,
)
from agents.transmutation.spider_chart import generate_spider_chart
from agents.transmutation.leverage_engine import (
    rank_transmutation_gaps,
    validate_practice_linkage,
    TRANSMUTATION_OPERATIONS,
)

logger = logging.getLogger(__name__)

# Phase ordering for validation (linear + loop phases)
PHASE_ORDER = [
    "orientation", "assessment", "profile", "education",
    "development", "reassessment", "graduation", "graduated", "check_in",
]

# Allowed phase transitions (some are non-linear due to loops)
ALLOWED_TRANSITIONS = {
    "orientation": ["assessment"],
    "assessment": ["profile"],
    "profile": ["education"],
    "education": ["development"],
    "development": ["reassessment"],
    "reassessment": ["development", "graduation"],
    "graduation": ["graduated"],
    "graduated": ["check_in"],
    "check_in": ["graduated", "development"],
}


# ── Exit-gate thresholds (normalized 0–100 scale) ──────────────────────
# These thresholds are compared against scores mapped to 0–100 via
# scoring_engine.normalize_score(score, lo=1.0, hi=5.0). The underlying
# engine still emits raw 1–5 Likert scores; only the threshold *comparisons*
# are normalized, mirroring sentinel_engine's shift convention.

# Max per-dimension movement (in 0–100 points) between two reassessment
# cycles for the pair to count as "stable" for graduation. Re-expresses the
# original "< 5%" intent on the normalized scale; ≈ 0.2 on the raw 1–5 scale.
GRADUATION_STABILITY_MAX_NORMALIZED = 5.0

# Per-dimension drop (in 0–100 points) since the graduation baseline that
# counts as regression at check-in. Mirrors sentinel_engine's
# shift_threshold_normalized (15.0, the "significant shift" magnitude);
# ≈ 0.6 on the raw 1–5 scale.
CHECK_IN_REGRESSION_DROP_NORMALIZED = 15.0

# Developmental ordering of the v13 archetypes, used to detect a quadrant
# "downgrade" at check-in (a lower rank than the graduation baseline).
# Grounded in scoring_engine._map_archetype: transmuter (+amplification,
# +filtering) is the apex; extractor (−,−) the floor; absorber (−,+) and
# magnifier (+,−) are one-sided (tied); conduit (center/balanced) sits
# between the one-sided pair and the apex (see spec PD-1). Archetypes absent
# from this map (e.g. "undetermined") have no rank and are treated as "no
# quadrant signal", never as a downgrade.
ARCHETYPE_RANK = {
    "extractor": 0,
    "magnifier": 1,
    "absorber": 1,
    "conduit": 2,
    "transmuter": 3,
}

# Cache marker that routes save_profile_snapshot into the check-in
# persistence branch (no dimension_assessment_state seeding, no
# reassessment_cycle bump). Set by generate_check_in_snapshot; read by
# save_profile_snapshot. A single named constant keeps producer and
# consumer in sync — never re-type the literal "check_in" at call sites.
SNAPSHOT_KIND_CHECK_IN = "check_in"


def _snapshot_archetype(placement: dict) -> str:
    """Read the archetype from a stored quadrant_placement dict.

    Production snapshots persist the scoring_engine._calculate_quadrant output,
    which carries an ``"archetype"`` key (never ``"quadrant"``). This accessor
    reads ``"archetype"`` first, falls back to a legacy/test ``"quadrant"`` key,
    and returns ``""`` when neither is present — the single source of truth for
    reading an archetype off a snapshot.
    """
    if not isinstance(placement, dict):
        return ""
    return placement.get("archetype") or placement.get("quadrant") or ""


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
            "current_phase": "assessment",
            "progress": _compute_progress({}, {}),
        }

    responses = json.loads(row["responses"] or "{}")
    scenario_responses = json.loads(row["scenario_responses"] or "{}")
    completed_dims = json.loads(row["completed_dimensions"] or "[]")

    # Return summary only — full responses stay in the DB to keep context small
    return {
        "exists": True,
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

    Phase gates enforce server-side predicates for each transition.
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
        allowed = ALLOWED_TRANSITIONS.get(current, [])
        if new_phase not in allowed:
            return {"error": f"Cannot transition from {current} to {new_phase}"}

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

        if new_phase == "development" and current == "education":
            gate = _check_education_completion_gate(conn, user_id)
            if gate:
                return gate

        if new_phase == "reassessment":
            gate = _check_development_completion_gate(conn, user_id)
            if gate:
                return gate

        if new_phase == "development" and current == "reassessment":
            # Requires updated profile (comparison snapshot exists)
            snapshots = conn.execute(
                "SELECT COUNT(*) as cnt FROM profile_snapshots WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if snapshots["cnt"] < 2:
                return {"error": "Comparison snapshot must exist before returning to development"}

        if new_phase == "graduation" and current == "reassessment":
            # 2-of-3 graduation indicators must be met (checked via tool)
            pass  # evaluate_graduation_readiness is called by the agent before this

        if new_phase == "graduated" and current == "graduation":
            record = conn.execute(
                "SELECT id FROM graduation_record WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not record:
                return {"error": "Graduation record must be saved before advancing to graduated"}

        if new_phase == "graduated" and current == "graduation":
            conn.execute(
                "UPDATE users SET graduated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), user_id),
            )

        conn.execute(
            "UPDATE users SET current_phase = ? WHERE id = ?",
            (new_phase, user_id),
        )

    return {
        "event_type": "phase.transition",
        "success": True,
        "from": current,
        "to": new_phase,
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


def _check_education_completion_gate(conn, user_id: str) -> Optional[dict]:
    """Check if education is complete enough to advance to development.

    Gate: top 3 weakest priority dimensions each have >= 60% comprehension
    score AND all 5 categories per dimension have >= 1 question answered.
    """
    progress_row = conn.execute(
        "SELECT progress FROM education_progress WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if not progress_row:
        return {"error": "No education progress found"}

    progress = json.loads(progress_row["progress"] or "{}")

    # Get profile to identify weakest dimensions
    profile_row = conn.execute(
        "SELECT scores FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()

    if not profile_row:
        return {"error": "No profile snapshot found"}

    scores = json.loads(profile_row["scores"] or "{}")
    ranked = sorted(
        scores.items(),
        key=lambda x: x[1].get("score", 0) if isinstance(x[1], dict) else x[1],
    )
    top3_dims = [dim for dim, _ in ranked[:3]]

    required_categories = [
        "what_this_means", "your_score", "daily_effects",
        "strengths_gaps", "external_interaction",
    ]

    qb = get_question_bank()
    for dim in top3_dims:
        dim_progress = progress.get(dim, {})

        # Check all 5 categories have >= 1 question answered.
        # A category with NO authored comprehension content in the question bank
        # is waived (and logged) rather than blocking forever — a learner cannot
        # answer questions that do not exist. Categories that DO have content
        # remain strictly required. After full content authoring this waiver is
        # unreachable; it exists solely to prevent a future content gap from
        # re-introducing a permanent education -> development dead-end.
        for cat in required_categories:
            cat_data = dim_progress.get(cat, {})
            if cat_data.get("questions_answered"):
                continue
            if not qb.get_comprehension_questions_for_category(dim, cat):
                logger.warning(
                    "Education gate: no comprehension content for %s / %s; "
                    "waiving this category so a content gap cannot permanently "
                    "block advancement.",
                    dim, cat,
                )
                continue
            return {
                "error": f"Dimension '{dim}' category '{cat}' has no comprehension questions answered",
                "dimension": dim,
                "category": cat,
            }

        # Check comprehension score >= 60% across answered categories
        total_correct = 0
        total_answered = 0
        for cat in required_categories:
            cat_data = dim_progress.get(cat, {})
            total_correct += len(cat_data.get("questions_correct", []))
            total_answered += len(cat_data.get("questions_answered", []))

        if total_answered > 0:
            score = total_correct / total_answered * 100
            if score < 60:
                return {
                    "error": f"Dimension '{dim}' comprehension score is {score:.0f}% (minimum 60% required)",
                    "dimension": dim,
                    "score": round(score),
                }

    return None


def _check_development_completion_gate(conn, user_id: str) -> Optional[dict]:
    """Check if development is complete enough to advance to reassessment.

    Gate: 10 practice entries OR 30 days elapsed since roadmap creation.
    """
    # Check practice entry count
    count_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM practice_journal WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    entry_count = count_row["cnt"] if count_row else 0

    if entry_count >= 10:
        return None  # Gate passes

    # Check days since roadmap creation
    roadmap_row = conn.execute(
        "SELECT created_at FROM development_roadmap WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
        (user_id,),
    ).fetchone()

    if roadmap_row:
        created = datetime.fromisoformat(roadmap_row["created_at"])
        days_elapsed = (datetime.utcnow() - created).days
        if days_elapsed >= 30:
            return None  # Gate passes

    return {
        "error": f"Development requires 10 practice entries or 30 days elapsed. Current: {entry_count} entries.",
        "entries": entry_count,
    }


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


def get_next_question_batch(
    user_id: str, dimension: str, batch_size: int = 5
) -> dict[str, Any]:
    """Get the next batch of unanswered questions for a dimension.

    Call this to discover which question IDs to present next.
    Returns question IDs and metadata for the next unanswered questions
    in the specified dimension. Use the returned question_ids with
    present_question_batch() to actually show them to the user.

    Args:
        user_id: The user's ID.
        dimension: The dimension name (e.g. "Emotional Awareness", "Social Awareness").
        batch_size: Number of questions to return (default 5, max 10).
    """
    qb = get_question_bank()
    dim_questions = qb.get_questions_by_dimension(dimension)

    if not dim_questions:
        available = qb.get_dimensions()
        return {
            "error": f"Unknown dimension: {dimension}",
            "available_dimensions": available,
        }

    # Get already-answered question IDs
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    answered_ids = set()
    if row:
        responses = json.loads(row["responses"] or "{}")
        answered_ids = set(responses.keys())

    # Filter to unanswered questions in this dimension
    unanswered = [q for q in dim_questions if q["id"] not in answered_ids]
    batch = unanswered[: min(batch_size, 10)]

    return {
        "dimension": dimension,
        "question_ids": [q["id"] for q in batch],
        "questions_preview": [{"id": q["id"], "text": q["text"][:80]} for q in batch],
        "remaining_in_dimension": len(unanswered) - len(batch),
        "total_in_dimension": len(dim_questions),
        "answered_in_dimension": len(dim_questions) - len(unanswered),
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

    # Resolve scale labels into each question for frontend rendering
    scale_types = qb.scale_types
    enriched = []
    for q in questions:
        eq = dict(q)
        st = scale_types.get(q.get("scale_type", ""), {})
        eq["scale_labels"] = st.get("labels", ["Strongly Disagree", "Disagree", "Neutral", "Agree", "Strongly Agree"])
        enriched.append(eq)

    # The full question data is sent to the frontend via SSE (event_type triggers it).
    # But we return a slim version to the agent to keep context window small —
    # the agent doesn't need full question text/labels in its conversation history.
    batch_id = f"batch_{enriched[0]['dimension'].lower().replace(' ', '_')}_{len(enriched)}" if enriched else "batch_empty"
    return {
        "event_type": "assessment.question_batch",
        "batch_id": batch_id,
        "sub_dimension": enriched[0].get("sub_dimension", "") if enriched else "",
        "dimension": enriched[0].get("dimension", "") if enriched else "",
        "questions": enriched,  # Full data needed by SSE/frontend
        # question_ids MUST be present so the storage slimmer preserves them;
        # /api/sessions/{id}/history reads them back to re-hydrate the
        # LikertCard widget after a page reload. Without this, slimmed events
        # carry an empty list and the widgets vanish on refresh.
        "question_ids": [q["id"] for q in enriched],
        "count": len(enriched),
        "missing": missing,
        # Agent-facing summary (the SSE layer strips 'questions' after emitting)
        "_agent_summary": f"Presented {len(enriched)} questions to user. Wait for their responses.",
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

    Orchestrates: fetch assessment → score → quadrant placement → spider chart → flow profile.
    Returns the profile data for the LLM to interpret before saving.

    The flow_data key (when present) contains the v13 moral profile:
    M vector, weighted total W, moral capital C+, moral debt C-, and per-level flows.
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
    flow_profile = result.get("flow_profile")
    _profile_cache[user_id] = {
        "scores": result["dimensions"],
        "quadrant": result["quadrant"],
        "insufficient_dimensions": result["insufficient_dimensions"],
        "spider_chart": chart_png,
        "flow_profile": flow_profile,
    }

    # Return data for LLM interpretation (no binary blob)
    response = {
        "scores": result["dimensions"],
        "quadrant": result["quadrant"],
        "insufficient_dimensions": result["insufficient_dimensions"],
        "has_spider_chart": True,
    }
    if flow_profile:
        response["flow_data"] = flow_profile.model_dump()
    return response


# Temporary cache for profile data between generate and save calls
_profile_cache: dict[str, dict[str, Any]] = {}


def _upsert_dimension_assessment_state(
    conn,
    user_id: str,
    dimension: str,
    last_assessed_cycle: int,
    last_assessment_kind: str,
    last_score: Optional[float],
    flagged: bool,
) -> None:
    """Idempotently upsert one dimension_assessment_state row.

    Uses the UNIQUE(user_id, dimension) constraint to upsert in a single
    atomic statement (no check-then-act race). Caller owns the transaction.
    """
    conn.execute(
        """INSERT INTO dimension_assessment_state
               (id, user_id, dimension, last_assessed_cycle, last_assessment_kind,
                last_score, flagged_for_full_reassessment, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, dimension) DO UPDATE SET
               last_assessed_cycle = excluded.last_assessed_cycle,
               last_assessment_kind = excluded.last_assessment_kind,
               last_score = excluded.last_score,
               flagged_for_full_reassessment = excluded.flagged_for_full_reassessment,
               updated_at = excluded.updated_at""",
        (
            str(uuid.uuid4()),
            user_id,
            dimension,
            last_assessed_cycle,
            last_assessment_kind,
            last_score,
            1 if flagged else 0,
            datetime.utcnow().isoformat(),
        ),
    )


def _dim_score(scores: dict, dim: str) -> Optional[float]:
    """Extract a dimension's numeric score from a snapshot scores dict."""
    data = scores.get(dim)
    if isinstance(data, dict):
        return data.get("score")
    if isinstance(data, (int, float)):
        return float(data)
    return None


def save_profile_snapshot(
    user_id: str,
    interpretation: str,
    structured_insights: dict | None = None,
) -> dict[str, Any]:
    """Persist a profile snapshot with the LLM's narrative interpretation.

    Must be called after generate_profile_snapshot, generate_reassessment_snapshot,
    or generate_check_in_snapshot. Saves scores, quadrant, spider chart,
    interpretation, flow_data, and structured_insights to the profile_snapshots
    table. Also persists Moral Capital (C+) and Moral Debt (C-) to the
    moral_ledger table.

    Three persistence paths, selected by markers on the cached profile (all
    run inside a single DB transaction for atomicity):

    - Check-in path (kind == SNAPSHOT_KIND_CHECK_IN): persist snapshot +
      moral_ledger only. Explicitly does NOT seed dimension_assessment_state
      and does NOT increment users.reassessment_cycle — post-graduation
      check-ins must not disturb the development-phase cycle counter.
    - Reassessment path (sentinel block present): increment users.reassessment_cycle,
      then upsert dimension_assessment_state for each targeted/sentinel dimension with
      the new cycle, kind, blended score, and flagged_for_full_reassessment status
      (cleared for dims no longer flagged).
    - Baseline path (neither marker): seed dimension_assessment_state for ALL
      13 dimensions at cycle 0 with kind='baseline'. users.reassessment_cycle stays 0.

    Branch precedence is check-in → reassessment → baseline. In practice
    `sentinel` and `kind` never co-occur (each generate_* helper sets only its
    own marker); the explicit ordering makes precedence audit-able.

    Args:
        user_id: The authenticated user's ID.
        interpretation: Short headline + one-line takeaway shown at the top of
            the Profile tab and in the chat transcript.
        structured_insights: Optional rich breakdown for the Profile tab.
            When provided, must follow this shape:
            {
              "strengths": [
                {"dimension": str, "level": str, "score": float, "note": str}
              ],
              "growth_areas": [
                {"dimension": str, "level": str, "score": float, "note": str}
              ],
              "cross_dimensional_insights": [str]  # 2-3 short paragraphs
            }
            Defaults to None; existing callers that omit this argument are
            unaffected (column stored as NULL, tab falls back to interpretation).
    """
    cached = _profile_cache.pop(user_id, None)
    if not cached:
        return {"error": "No generated profile found. Call generate_profile_snapshot first."}

    snapshot_id = str(uuid.uuid4())
    sentinel_block = cached.get("sentinel")
    is_reassessment = sentinel_block is not None
    is_check_in = cached.get("kind") == SNAPSHOT_KIND_CHECK_IN

    # Persist branch metadata alongside the quadrant JSON (free-form dict,
    # extra keys are ignored by quadrant readers) so the snapshot self-describes
    # which path produced it.
    quadrant_payload = dict(cached["quadrant"]) if isinstance(cached["quadrant"], dict) else cached["quadrant"]
    if isinstance(quadrant_payload, dict):
        if is_check_in:
            quadrant_payload["kind"] = SNAPSHOT_KIND_CHECK_IN
        elif is_reassessment:
            quadrant_payload["sentinel"] = sentinel_block

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

        # Serialize flow data if present
        flow_profile = cached.get("flow_profile")
        flow_data_json = flow_profile.model_dump_json() if flow_profile else None

        # Serialize structured_insights; coerce non-dict to None to keep the
        # snapshot atomic and avoid agent-induced errors.
        if isinstance(structured_insights, dict) and structured_insights:
            structured_insights_json = json.dumps(structured_insights)
        else:
            structured_insights_json = None

        conn.execute(
            """INSERT INTO profile_snapshots
               (id, user_id, session_id, scores, quadrant_placement, spider_chart, interpretation, previous_snapshot_id, created_at, flow_data, structured_insights)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                user_id,
                session_id,
                json.dumps(cached["scores"]),
                json.dumps(quadrant_payload),
                cached["spider_chart"],
                interpretation,
                prev_id,
                datetime.utcnow().isoformat(),
                flow_data_json,
                structured_insights_json,
            ),
        )

        # Persist moral capital/debt to moral_ledger
        if flow_profile:
            conn.execute(
                """INSERT INTO moral_ledger
                   (id, user_id, snapshot_id, c_plus, c_minus, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    user_id,
                    snapshot_id,
                    flow_profile.moral_capital,
                    flow_profile.moral_debt,
                    datetime.utcnow().isoformat(),
                ),
            )

        # ── Dimension assessment state tracking (same transaction) ──────────
        if is_check_in:
            # Check-in path: snapshot + moral_ledger only.
            # NO dimension_assessment_state writes (would clobber development
            # cycle history); NO users.reassessment_cycle bump (cycle counter
            # tracks development-phase reassessments, not post-graduation
            # check-ins). The snapshot row above + moral_ledger row above are
            # the entirety of the persisted state for this branch.
            pass
        elif is_reassessment:
            # Reassessment path: increment cycle and upsert targeted/sentinel dims.
            new_cycle = sentinel_block.get("cycle")
            if new_cycle is None:
                cycle_row = conn.execute(
                    "SELECT reassessment_cycle FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                new_cycle = (cycle_row["reassessment_cycle"] if cycle_row else 0) + 1

            conn.execute(
                "UPDATE users SET reassessment_cycle = ? WHERE id = ?",
                (new_cycle, user_id),
            )

            flagged_set = set(sentinel_block.get("flagged_for_full_reassessment", []))
            targeted = sentinel_block.get("targeted_dimensions", [])
            sentinel = sentinel_block.get("sentinel_dimensions", [])

            for dim in targeted:
                _upsert_dimension_assessment_state(
                    conn, user_id, dim,
                    last_assessed_cycle=new_cycle,
                    last_assessment_kind="targeted",
                    last_score=_dim_score(cached["scores"], dim),
                    # Targeted dims are not added to the flagged list by the engine,
                    # so any prior flag on a now-targeted dim is cleared here.
                    flagged=dim in flagged_set,
                )
            for dim in sentinel:
                _upsert_dimension_assessment_state(
                    conn, user_id, dim,
                    last_assessed_cycle=new_cycle,
                    last_assessment_kind="sentinel",
                    last_score=_dim_score(cached["scores"], dim),
                    flagged=dim in flagged_set,
                )
        else:
            # Baseline path: seed all 13 dimensions at cycle 0.
            for dim in cached["scores"].keys():
                _upsert_dimension_assessment_state(
                    conn, user_id, dim,
                    last_assessed_cycle=0,
                    last_assessment_kind="baseline",
                    last_score=_dim_score(cached["scores"], dim),
                    flagged=False,
                )

    # Build response with all data the frontend profile tab needs
    quadrant_data = cached["quadrant"]
    quadrant_name = quadrant_data.get("quadrant", "Unknown") if isinstance(quadrant_data, dict) else str(quadrant_data)

    response: dict[str, Any] = {
        "event_type": "checkin.snapshot_saved" if is_check_in else "profile.snapshot",
        "saved": True,
        "snapshot_id": snapshot_id,
        "scores": cached["scores"],
        "quadrant": quadrant_name,
        "quadrant_placement": quadrant_data,
        "interpretation": interpretation,
        # Include the dict (not the JSON string) so the SSE payload is directly
        # usable by the frontend without an extra parse step.
        "structured_insights": structured_insights if isinstance(structured_insights, dict) and structured_insights else None,
    }

    # Spider chart is persisted in DB — frontend fetches it via /api/results.
    # NOT included here because base64 PNG (~260KB) would blow up the LLM context window.
    response["has_spider_chart"] = cached.get("spider_chart") is not None

    if flow_profile:
        response["flow_data"] = flow_profile.model_dump()

    return response


# ── Education Agent tools ──────────────────────────────────────────────


def get_education_progress(user_id: str) -> dict[str, Any]:
    """Retrieve education progress for a user.

    Returns per-dimension, per-category progress including understanding
    scores, questions answered/correct, and reflection status.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT progress FROM education_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if not row:
        return {"exists": False, "progress": {}}

    progress = json.loads(row["progress"] or "{}")

    # Compute summary stats
    total_categories = 0
    completed_categories = 0
    for dim, categories in progress.items():
        for cat, data in categories.items():
            total_categories += 1
            if data.get("understanding_score", 0) >= 70:
                completed_categories += 1

    return {
        "exists": True,
        "progress": progress,
        "summary": {
            "total_categories": total_categories,
            "completed_categories": completed_categories,
            "completion_pct": round(
                completed_categories / total_categories * 100, 1
            )
            if total_categories > 0
            else 0,
        },
    }


def present_comprehension_question(
    user_id: str,
    dimension: str,
    category: str,
    question_id: Optional[str] = None,
) -> dict[str, Any]:
    """Present a comprehension check question to the user as an interactive widget.

    Call this instead of writing question text as markdown — the returned payload
    triggers an interactive StructuredChoice card in the frontend via SSE.

    If ``question_id`` is provided, that specific question is returned.
    If omitted, the tool selects the next unanswered question for the given
    dimension and category based on the user's education_progress.

    Args:
        user_id: The authenticated user's ID.
        dimension: Transmutation dimension (e.g. "Emotional Awareness").
        category: One of the five canonical categories per dimension
            (e.g. "what_this_means", "your_score", "daily_effects",
            "strengths_gaps", "external_interaction").
        question_id: Optional specific question ID to present. When omitted,
            the next unanswered question is selected automatically.

    Returns:
        On success: dict with ``event_type`` = "education.comprehension",
            plus ``dimension``, ``category``, ``question_id``, ``stem``,
            and ``options`` (list of {key, text} — correct_option and
            explanation are NEVER included).
        When all answered: {"status": "no_questions", "dimension": ..., "category": ...}
        On unknown question_id: {"error": "<message>"}
    """
    qb = get_question_bank()

    if question_id is not None:
        # Specific question requested — look it up directly.
        question = qb.get_comprehension_question_by_id(question_id)
        if question is None:
            logger.warning(
                "present_comprehension_question: unknown question_id=%s", question_id
            )
            return {"error": f"Comprehension question '{question_id}' not found."}
    else:
        # Auto-select the next unanswered question for this dimension + category.
        all_questions = qb.get_comprehension_questions_for_category(dimension, category)
        if not all_questions:
            logger.debug(
                "present_comprehension_question: no questions for %s/%s",
                dimension, category,
            )
            return {"status": "no_questions", "dimension": dimension, "category": category}

        # Fetch answered question IDs from education_progress (read-only).
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT progress FROM education_progress WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        answered_ids: set[str] = set()
        if row:
            progress = json.loads(row["progress"] or "{}")
            answered_ids = set(
                progress.get(dimension, {}).get(category, {}).get("questions_answered", [])
            )

        # Pick the first question not yet answered.
        question = next(
            (q for q in all_questions if q["id"] not in answered_ids),
            None,
        )
        if question is None:
            logger.debug(
                "present_comprehension_question: all questions answered for %s/%s",
                dimension, category,
            )
            return {"status": "no_questions", "dimension": dimension, "category": category}

    # Build the safe payload — explicitly exclude correct_option and explanation
    # so the client cannot read the answer from the SSE stream.
    safe_options = [{"key": o["key"], "text": o["text"]} for o in question["options"]]

    return {
        "event_type": "education.comprehension",
        "dimension": dimension,
        "category": category,
        "question_id": question["id"],
        "stem": question["stem"],
        "options": safe_options,
    }


def record_comprehension_answer(
    user_id: str,
    dimension: str,
    category: str,
    question_id: str,
    selected_option: str,
) -> dict[str, Any]:
    """Record a comprehension check answer and update education progress.

    Looks up the correct answer from comprehension_checks.json,
    updates the education_progress JSON, and returns feedback.
    """
    qb = get_question_bank()
    question = qb.get_comprehension_question_by_id(question_id)
    if not question:
        return {"error": f"Comprehension question not found: {question_id}"}

    correct = selected_option == question["correct_option"]

    with get_db_session() as conn:
        row = conn.execute(
            "SELECT progress FROM education_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row:
            progress = json.loads(row["progress"] or "{}")
        else:
            progress = {}

        # Initialize dimension and category if needed
        if dimension not in progress:
            progress[dimension] = {}
        if category not in progress[dimension]:
            progress[dimension][category] = {
                "understanding_score": 0,
                "questions_answered": [],
                "questions_correct": [],
                "last_discussed": None,
                "reflection_given": False,
            }

        cat_data = progress[dimension][category]

        # Record the answer (avoid duplicates)
        if question_id not in cat_data["questions_answered"]:
            cat_data["questions_answered"].append(question_id)
            if correct:
                cat_data["questions_correct"].append(question_id)

        # Recompute understanding score
        answered_count = len(cat_data["questions_answered"])
        correct_count = len(cat_data["questions_correct"])
        cat_data["understanding_score"] = round(
            correct_count / answered_count * 100
        ) if answered_count > 0 else 0
        cat_data["last_discussed"] = datetime.utcnow().isoformat()

        # Upsert education_progress
        if row:
            conn.execute(
                "UPDATE education_progress SET progress = ? WHERE user_id = ?",
                (json.dumps(progress), user_id),
            )
        else:
            conn.execute(
                "INSERT INTO education_progress (user_id, progress) VALUES (?, ?)",
                (user_id, json.dumps(progress)),
            )

    # Count categories covered for this dimension
    dim_progress = progress.get(dimension, {})
    qb_categories = qb.get_comprehension_categories(dimension)
    categories_covered = len(
        [c for c in qb_categories if c in dim_progress]
    )

    return {
        "event_type": "education.comprehension",
        "correct": correct,
        "explanation": question.get("explanation", ""),
        "reflection_prompt": question.get("reflection_prompt"),
        "score": cat_data["understanding_score"],
        "dimension": dimension,
        "category": category,
        "question_id": question_id,
        "categories_covered": categories_covered,
        "categories_total": len(qb_categories),
    }


# ── Development Agent tools ────────────────────────────────────────────


def get_development_roadmap(user_id: str) -> dict[str, Any]:
    """Retrieve the current (most recent) development roadmap for a user."""
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT * FROM development_roadmap WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"exists": False}

    return {
        "exists": True,
        "id": row["id"],
        "roadmap": json.loads(row["roadmap"] or "{}"),
        "parent_roadmap_id": row["parent_roadmap_id"],
        "created_at": row["created_at"],
    }


def rank_gaps(user_id: str, top_n: int = 3) -> dict[str, Any]:
    """Return the top-N transmutation gaps ranked by leverage for a user.

    Loads the latest profile snapshot scores and ranks gaps using the
    deterministic leverage formula (axis-aware). The LLM must NOT compute
    ranking itself — this tool is the source of truth (business-logic-protection).

    Args:
        user_id: The user whose gaps to rank.
        top_n:   Number of top gaps to return (default 3).

    Returns:
        {"ranked_targets": [...], "source_snapshot_id": str}
        or {"error": "No profile snapshot found"} if no snapshot exists.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT id, scores FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"error": "No profile snapshot found"}

    scores = json.loads(row["scores"] or "{}")
    ranked = rank_transmutation_gaps(scores, top_n=top_n)

    return {
        "ranked_targets": ranked,
        "source_snapshot_id": row["id"],
    }


def generate_roadmap(user_id: str) -> dict[str, Any]:
    """Generate a development roadmap from the user's profile.

    Fetches the most recent profile snapshot and returns the top-N leverage
    targets from the deterministic ranking. The LLM authors narrative practices
    targeting these gaps — ranking is never delegated to the LLM.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT id, scores FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"error": "No profile snapshot found. Complete assessment first."}

    scores = json.loads(row["scores"] or "{}")
    leverage_targets = rank_transmutation_gaps(scores, top_n=3)

    return {
        "leverage_targets": leverage_targets,
        "profile_scores": scores,
        "step_count": 3,
        "instruction": (
            "Write narrative practices targeting these leverage_targets. "
            "For each target, define a structured practice with a unique practice_id, "
            "title, dimension, sub_dimension (if applicable), and transmutation_operation. "
            "Persist via save_roadmap with a top-level 'practices' array. "
            "Log each practice via log_practice_entry passing dimension, sub_dimension, "
            "and transmutation_operation. "
            "Do NOT compute leverage or rankings yourself — use the ranked leverage_targets above."
        ),
    }


def save_roadmap(user_id: str, roadmap: dict) -> dict[str, Any]:
    """Persist a development roadmap. Emits development.roadmap SSE event.

    Recognizes an optional top-level ``roadmap["practices"]`` array of structured
    practice dicts ({"practice_id", "title", "dimension", "sub_dimension"|None,
    "transmutation_operation"}). When present, validates every practice and upserts
    into roadmap_practices in the same transaction. On any validation failure,
    returns an error and saves nothing.

    Legacy roadmaps with no ``practices`` key are saved as-is with no upsert.
    """
    qb = get_question_bank()
    dimensions_index = {dim: qb.get_sub_dimensions(dim) for dim in qb.get_dimensions()}

    practices = roadmap.get("practices")
    if practices is not None:
        # Validate every practice before touching the DB.
        all_validation_errors: list[str] = []
        for practice in practices:
            errors = validate_practice_linkage(
                practice.get("dimension"),
                practice.get("sub_dimension"),
                practice.get("transmutation_operation"),
                dimensions_index,
            )
            if errors:
                all_validation_errors.extend(
                    [f"practice '{practice.get('practice_id', '?')}': {e}" for e in errors]
                )
        if all_validation_errors:
            return {
                "error": "Invalid practice linkage in roadmap",
                "validation_errors": all_validation_errors,
            }

    roadmap_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
            (roadmap_id, user_id, json.dumps(roadmap), now),
        )

        if practices is not None:
            for practice in practices:
                rp_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO roadmap_practices
                       (id, user_id, roadmap_id, practice_id, title, dimension, sub_dimension, transmutation_operation, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, practice_id) DO UPDATE SET
                           roadmap_id = excluded.roadmap_id,
                           title = excluded.title,
                           dimension = excluded.dimension,
                           sub_dimension = excluded.sub_dimension,
                           transmutation_operation = excluded.transmutation_operation""",
                    (
                        rp_id,
                        user_id,
                        roadmap_id,
                        practice["practice_id"],
                        practice.get("title"),
                        practice["dimension"],
                        practice.get("sub_dimension"),
                        practice.get("transmutation_operation"),
                        now,
                    ),
                )
        else:
            logger.info(
                "save_roadmap: legacy roadmap shape (no 'practices' key) saved without linkage upsert",
                extra={"user_id": user_id, "roadmap_id": roadmap_id},
            )

    return {
        "event_type": "development.roadmap",
        "saved": True,
        "roadmap_id": roadmap_id,
        "roadmap": roadmap,
    }


def log_practice_entry(
    user_id: str,
    practice_id: str,
    reflection: str,
    self_rating: int,
    dimension: Optional[str] = None,
    sub_dimension: Optional[str] = None,
    transmutation_operation: Optional[str] = None,
) -> dict[str, Any]:
    """Log a practice journal entry. Emits development.practice SSE event.

    Optional linkage args (dimension, sub_dimension, transmutation_operation) are
    validated when provided; on failure returns {"error":..., "validation_errors":[...]}
    and writes nothing. When omitted but practice_id matches a roadmap_practices row,
    linkage is backfilled from it. Legacy callers (positional only) are unaffected.

    Also checks total entry count to signal readiness for reassessment
    (10 entries threshold).
    """
    linkage_provided = dimension is not None or sub_dimension is not None or transmutation_operation is not None

    if linkage_provided:
        qb = get_question_bank()
        dimensions_index = {dim: qb.get_sub_dimensions(dim) for dim in qb.get_dimensions()}
        errors = validate_practice_linkage(dimension, sub_dimension, transmutation_operation, dimensions_index)
        if errors:
            return {
                "error": "Invalid practice linkage",
                "validation_errors": errors,
            }

    entry_id = str(uuid.uuid4())

    with get_db_session() as conn:
        # Backfill linkage from roadmap_practices when not explicitly provided.
        if not linkage_provided:
            rp_row = conn.execute(
                "SELECT dimension, sub_dimension, transmutation_operation FROM roadmap_practices WHERE user_id = ? AND practice_id = ?",
                (user_id, practice_id),
            ).fetchone()
            if rp_row:
                dimension = rp_row["dimension"]
                sub_dimension = rp_row["sub_dimension"]
                transmutation_operation = rp_row["transmutation_operation"]
                logger.info(
                    "log_practice_entry: backfilled linkage from roadmap_practices",
                    extra={"user_id": user_id, "practice_id": practice_id, "dimension": dimension},
                )

        conn.execute(
            """INSERT INTO practice_journal
               (id, user_id, practice_id, reflection, self_rating, created_at,
                dimension, sub_dimension, transmutation_operation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id, user_id, practice_id, reflection, self_rating,
                datetime.utcnow().isoformat(),
                dimension, sub_dimension, transmutation_operation,
            ),
        )

        # Count total entries for reassessment trigger
        count_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM practice_journal WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        total_entries = count_row["cnt"] if count_row else 0

        # Check for downward trend on this practice (3+ entries)
        trend_rows = conn.execute(
            "SELECT self_rating FROM practice_journal WHERE user_id = ? AND practice_id = ? ORDER BY created_at DESC LIMIT 3",
            (user_id, practice_id),
        ).fetchall()

    downward_trend = False
    if len(trend_rows) >= 3:
        ratings = [r["self_rating"] for r in trend_rows]
        # Most recent first; downward if each is <= the one after it
        downward_trend = all(ratings[i] <= ratings[i + 1] for i in range(len(ratings) - 1))

    return {
        "event_type": "development.practice",
        "saved": True,
        "entry_id": entry_id,
        "practice_id": practice_id,
        "self_rating": self_rating,
        "total_entries": total_entries,
        "reassessment_ready": total_entries >= 10,
        "downward_trend": downward_trend,
    }


def check_roadmap_targets_gaps(user_id: str, roadmap: dict) -> dict[str, Any]:
    """Check how well a roadmap's practices cover the user's top-leverage gaps.

    Compares the dimensions/sub-dimensions targeted by roadmap["practices"] against
    the top-leverage gaps from rank_gaps. Returns which gaps are covered and which
    high-leverage gaps are missed.

    Args:
        user_id: The user whose profile snapshot to use for gap ranking.
        roadmap:  The roadmap dict, optionally containing a top-level "practices" array.

    Returns:
        {"top_gaps": [...], "covered": [...], "uncovered_high_leverage": [...], "coverage_pct": float}
        or {"error": ...} if no profile snapshot is found.
    """
    gaps_result = rank_gaps(user_id, top_n=5)
    if "error" in gaps_result:
        return gaps_result

    top_gaps = gaps_result["ranked_targets"]

    # Collect (dimension, sub_dimension) pairs targeted by roadmap practices.
    practices = roadmap.get("practices") or []
    targeted: set[tuple[str, Optional[str]]] = {
        (p.get("dimension"), p.get("sub_dimension")) for p in practices
    }

    covered: list[dict] = []
    uncovered_high_leverage: list[dict] = []

    for gap in top_gaps:
        key = (gap["dimension"], gap["sub_dimension"])
        if key in targeted:
            covered.append(gap)
        else:
            uncovered_high_leverage.append(gap)

    total = len(top_gaps)
    coverage_pct = round(len(covered) / total * 100.0, 1) if total > 0 else 0.0

    return {
        "top_gaps": top_gaps,
        "covered": covered,
        "uncovered_high_leverage": uncovered_high_leverage,
        "coverage_pct": coverage_pct,
    }


def get_practice_history(user_id: str, practice_id: str) -> dict[str, Any]:
    """Retrieve past journal entries for a specific practice."""
    with get_db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM practice_journal WHERE user_id = ? AND practice_id = ? ORDER BY created_at ASC",
            (user_id, practice_id),
        ).fetchall()

    entries = [
        {
            "id": r["id"],
            "reflection": r["reflection"],
            "self_rating": r["self_rating"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]

    return {
        "practice_id": practice_id,
        "entries": entries,
        "count": len(entries),
    }


def update_roadmap(
    user_id: str,
    adjustment_reason: str,
    retain_practices: list[str],
    drop_practices: list[str],
) -> dict[str, Any]:
    """Adjust the current roadmap with a 7-day cooldown.

    Creates a new development_roadmap row linked via parent_roadmap_id.
    Can swap practices but NOT change targeted dimensions.
    Rejects if the most recent roadmap was created less than 7 days ago.
    """
    with get_db_session() as conn:
        current = conn.execute(
            "SELECT * FROM development_roadmap WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if not current:
            return {"error": "No existing roadmap to adjust"}

        # Enforce 7-day cooldown
        created_at = datetime.fromisoformat(current["created_at"])
        days_since = (datetime.utcnow() - created_at).days
        if days_since < 7:
            return {
                "error": "Roadmap adjustment cooldown active",
                "days_remaining": 7 - days_since,
                "message": f"Roadmap was last updated {days_since} day(s) ago. Wait {7 - days_since} more day(s).",
            }

        current_roadmap = json.loads(current["roadmap"] or "{}")

        # Build adjusted roadmap preserving targeted dimensions
        adjusted = {
            **current_roadmap,
            "adjustment_reason": adjustment_reason,
            "retain_practices": retain_practices,
            "drop_practices": drop_practices,
            "adjusted_at": datetime.utcnow().isoformat(),
        }

        new_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO development_roadmap (id, user_id, parent_roadmap_id, roadmap, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id, user_id, current["id"], json.dumps(adjusted), datetime.utcnow().isoformat()),
        )

    return {
        "event_type": "development.roadmap",
        "saved": True,
        "roadmap_id": new_id,
        "parent_roadmap_id": current["id"],
        "adjustment_reason": adjustment_reason,
        "roadmap": adjusted,
    }


# ── Reassessment Agent tools ──────────────────────────────────────────


def generate_comparison_snapshot(
    user_id: str, previous_snapshot_id: str
) -> dict[str, Any]:
    """Compute delta scores and quadrant shift between two profile snapshots.

    Compares the most recent snapshot against a specified previous one
    (typically the pre-reassessment snapshot or graduation snapshot).

    When both snapshots contain flow_data, the result includes a flow_deltas
    dict with per-metric deltas: moral_work vector, weighted_total, moral_capital,
    and moral_debt (each with previous, current, and delta values).
    """
    with get_db_session() as conn:
        current = conn.execute(
            "SELECT * FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        previous = conn.execute(
            "SELECT * FROM profile_snapshots WHERE id = ? AND user_id = ?",
            (previous_snapshot_id, user_id),
        ).fetchone()

    if not current:
        return {"error": "No current profile snapshot found"}
    if not previous:
        return {"error": f"Previous snapshot not found: {previous_snapshot_id}"}

    current_scores = json.loads(current["scores"] or "{}")
    previous_scores = json.loads(previous["scores"] or "{}")
    current_quadrant = json.loads(current["quadrant_placement"] or "{}")
    previous_quadrant = json.loads(previous["quadrant_placement"] or "{}")

    # Compute per-dimension deltas
    deltas = {}
    for dim in set(list(current_scores.keys()) + list(previous_scores.keys())):
        curr_val = current_scores.get(dim, {})
        prev_val = previous_scores.get(dim, {})
        curr_score = curr_val.get("score", 0) if isinstance(curr_val, dict) else curr_val
        prev_score = prev_val.get("score", 0) if isinstance(prev_val, dict) else prev_val
        delta = round(curr_score - prev_score, 2)
        # Raw deltas are on the engine's 1–5 scale. The *_normalized fields
        # express the same change on the 0–100 scale (scoring_engine.normalize_score)
        # so consumers never have to guess which scale a delta is on.
        prev_norm = round(normalize_score(prev_score), 2)
        curr_norm = round(normalize_score(curr_score), 2)
        deltas[dim] = {
            "previous": prev_score,
            "current": curr_score,
            "delta": delta,
            "previous_normalized": prev_norm,
            "current_normalized": curr_norm,
            "delta_normalized": round(curr_norm - prev_norm, 2),
            "direction": "up" if delta > 0 else "down" if delta < 0 else "stable",
        }

    # Quadrant shift (archetype-aware; production snapshots store "archetype")
    curr_q = _snapshot_archetype(current_quadrant)
    prev_q = _snapshot_archetype(previous_quadrant)
    quadrant_shifted = curr_q != prev_q

    # Flow data deltas
    flow_deltas = None
    current_flow_raw = current["flow_data"]
    previous_flow_raw = previous["flow_data"]
    if current_flow_raw and previous_flow_raw:
        from models.moral_profile import MoralProfile

        current_flow = MoralProfile.model_validate_json(current_flow_raw)
        previous_flow = MoralProfile.model_validate_json(previous_flow_raw)

        moral_work_deltas = [
            round(c - p, 4)
            for c, p in zip(current_flow.moral_work, previous_flow.moral_work)
        ]
        flow_deltas = {
            "moral_work": {
                "previous": previous_flow.moral_work,
                "current": current_flow.moral_work,
                "delta": moral_work_deltas,
            },
            "weighted_total": {
                "previous": previous_flow.weighted_total,
                "current": current_flow.weighted_total,
                "delta": round(current_flow.weighted_total - previous_flow.weighted_total, 4),
            },
            "moral_capital": {
                "previous": previous_flow.moral_capital,
                "current": current_flow.moral_capital,
                "delta": round(current_flow.moral_capital - previous_flow.moral_capital, 4),
            },
            "moral_debt": {
                "previous": previous_flow.moral_debt,
                "current": current_flow.moral_debt,
                "delta": round(current_flow.moral_debt - previous_flow.moral_debt, 4),
            },
        }

    result = {
        "current_snapshot_id": current["id"],
        "previous_snapshot_id": previous_snapshot_id,
        "deltas": deltas,
        "quadrant_shift": {
            "previous": prev_q,
            "current": curr_q,
            "shifted": quadrant_shifted,
        },
        "current_created_at": current["created_at"],
        "previous_created_at": previous["created_at"],
    }
    if flow_deltas:
        result["flow_deltas"] = flow_deltas
    return result


def evaluate_graduation_readiness(user_id: str) -> dict[str, Any]:
    """Deterministically check the 3 graduation convergence indicators.

    Indicators (any 2 of 3 triggers graduation):
    1. Pattern Stability: Delta < 5% across all targeted dims for 2 consecutive cycles
    2. Quadrant Consolidation: Same quadrant for 2 consecutive reassessments
    3. Self-Assessed Readiness: User explicitly indicated readiness (stored in session state)

    NOT criteria: reaching Transmuter quadrant, minimum score, time deadline.
    """
    with get_db_session() as conn:
        # Get the 3 most recent profile snapshots (need 3 to check "2 consecutive")
        snapshots = conn.execute(
            "SELECT * FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 3",
            (user_id,),
        ).fetchall()

        # Check self-assessed readiness from session state
        session_row = conn.execute(
            "SELECT session_state FROM adk_sessions WHERE user_id = ? AND archived = FALSE ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    indicators = {
        "pattern_stability": {"met": False, "evidence": ""},
        "quadrant_consolidation": {"met": False, "evidence": ""},
        "self_assessed_readiness": {"met": False, "evidence": ""},
    }

    if len(snapshots) < 3:
        indicators["pattern_stability"]["evidence"] = f"Only {len(snapshots)} snapshots; need at least 3 for two consecutive cycles"
        indicators["quadrant_consolidation"]["evidence"] = f"Only {len(snapshots)} snapshots; need at least 3"
    else:
        # Snapshots are newest-first: [current, previous, before_previous]
        scores = [json.loads(s["scores"] or "{}") for s in snapshots]
        quadrants = [json.loads(s["quadrant_placement"] or "{}") for s in snapshots]

        # 1. Pattern Stability: max per-dimension movement < threshold on the
        # normalized 0–100 scale for 2 consecutive cycles. Scores are normalized
        # via scoring_engine.normalize_score before differencing, so the
        # comparison is scale-correct (raw 1–5 deltas could never exceed 5).
        # Cycle 1: current vs previous, Cycle 2: previous vs before_previous.
        def compute_max_delta(scores_a, scores_b):
            max_delta = 0.0
            # Intersection only: a dimension absent on either side is skipped,
            # never defaulted to 0 (which normalize_score maps to -25 and would
            # manufacture a phantom delta).
            for dim in set(scores_a.keys()) & set(scores_b.keys()):
                a = scores_a[dim]
                b = scores_b[dim]
                sa = a.get("score") if isinstance(a, dict) else a
                sb = b.get("score") if isinstance(b, dict) else b
                if sa is None or sb is None:
                    continue
                delta = abs(normalize_score(sa) - normalize_score(sb))
                max_delta = max(max_delta, delta)
            return round(max_delta, 2)

        delta_cycle1 = compute_max_delta(scores[0], scores[1])
        delta_cycle2 = compute_max_delta(scores[1], scores[2])

        stability_met = (
            delta_cycle1 < GRADUATION_STABILITY_MAX_NORMALIZED
            and delta_cycle2 < GRADUATION_STABILITY_MAX_NORMALIZED
        )
        indicators["pattern_stability"]["met"] = stability_met
        indicators["pattern_stability"]["evidence"] = (
            f"Cycle 1 max normalized delta: {delta_cycle1} pts, "
            f"Cycle 2 max normalized delta: {delta_cycle2} pts "
            f"(threshold: < {GRADUATION_STABILITY_MAX_NORMALIZED} pts on 0–100 scale for both)"
        )

        # 2. Quadrant Consolidation: same archetype for 2 consecutive reassessments
        q0 = _snapshot_archetype(quadrants[0])
        q1 = _snapshot_archetype(quadrants[1])
        q2 = _snapshot_archetype(quadrants[2])

        consolidation_met = q0 == q1 and q1 == q2 and q0 != ""
        indicators["quadrant_consolidation"]["met"] = consolidation_met
        indicators["quadrant_consolidation"]["evidence"] = (
            f"Last 3 quadrants: [{q0}, {q1}, {q2}] "
            f"({'all same' if consolidation_met else 'not consolidated'})"
        )

    # 3. Self-Assessed Readiness: check session state
    if session_row:
        state = json.loads(session_row["session_state"] or "{}")
        readiness = state.get("self_assessed_readiness", False)
        indicators["self_assessed_readiness"]["met"] = bool(readiness)
        indicators["self_assessed_readiness"]["evidence"] = (
            "User indicated readiness" if readiness else "User has not indicated readiness"
        )
    else:
        indicators["self_assessed_readiness"]["evidence"] = "No active session found"

    # Count how many indicators are met
    met_count = sum(1 for ind in indicators.values() if ind["met"])
    graduation_ready = met_count >= 2

    return {
        "event_type": "graduation.readiness",
        "graduation_ready": graduation_ready,
        "indicators_met": met_count,
        "indicators_required": 2,
        "indicators": indicators,
    }


# ── Graduation Agent tools ─────────────────────────────────────────────


def get_longitudinal_snapshots(user_id: str) -> dict[str, Any]:
    """Retrieve all profile snapshots for a user (timeline view).

    Returns snapshots in chronological order for longitudinal review
    during the graduation closing sequence.
    """
    with get_db_session() as conn:
        rows = conn.execute(
            "SELECT id, session_id, scores, quadrant_placement, interpretation, created_at "
            "FROM profile_snapshots WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()

    snapshots = [
        {
            "id": r["id"],
            "scores": json.loads(r["scores"] or "{}"),
            "quadrant_placement": json.loads(r["quadrant_placement"] or "{}"),
            "interpretation": r["interpretation"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]

    return {
        "snapshots": snapshots,
        "count": len(snapshots),
    }


def generate_graduation_artifacts(user_id: str) -> dict[str, Any]:
    """Generate practice map and pattern narrative data for graduation.

    Aggregates practice journal entries, roadmap history, and longitudinal
    snapshots to provide data the LLM uses to write a pattern narrative.
    """
    with get_db_session() as conn:
        # Get all practice entries
        practices = conn.execute(
            "SELECT practice_id, reflection, self_rating, created_at "
            "FROM practice_journal WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()

        # Get roadmap history
        roadmaps = conn.execute(
            "SELECT id, parent_roadmap_id, roadmap, created_at "
            "FROM development_roadmap WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()

        # Get first and last snapshots
        first_snapshot = conn.execute(
            "SELECT scores, quadrant_placement, created_at "
            "FROM profile_snapshots WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
            (user_id,),
        ).fetchone()

        last_snapshot = conn.execute(
            "SELECT scores, quadrant_placement, created_at "
            "FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        # Get graduation readiness indicators
        readiness = evaluate_graduation_readiness(user_id)

    # Build practice map: {practice_id: [entries]}
    practice_map: dict[str, list[dict]] = {}
    for p in practices:
        pid = p["practice_id"]
        if pid not in practice_map:
            practice_map[pid] = []
        practice_map[pid].append({
            "reflection": p["reflection"],
            "self_rating": p["self_rating"],
            "created_at": p["created_at"],
        })

    # Compute growth trajectory
    initial_scores = json.loads(first_snapshot["scores"] or "{}") if first_snapshot else {}
    final_scores = json.loads(last_snapshot["scores"] or "{}") if last_snapshot else {}

    growth = {}
    for dim in set(list(initial_scores.keys()) + list(final_scores.keys())):
        init = initial_scores.get(dim, {})
        final = final_scores.get(dim, {})
        init_score = init.get("score", 0) if isinstance(init, dict) else init
        final_score = final.get("score", 0) if isinstance(final, dict) else final
        growth[dim] = {
            "initial": init_score,
            "final": final_score,
            "change": round(final_score - init_score, 2),
        }

    return {
        "practice_map": practice_map,
        "total_practices": len(practices),
        "unique_practices": len(practice_map),
        "roadmap_count": len(roadmaps),
        "growth_trajectory": growth,
        "initial_quadrant": _snapshot_archetype(json.loads(first_snapshot["quadrant_placement"] or "{}")) if first_snapshot else "",
        "final_quadrant": _snapshot_archetype(json.loads(last_snapshot["quadrant_placement"] or "{}")) if last_snapshot else "",
        "graduation_indicators": readiness.get("indicators", {}),
    }


def save_graduation_record(
    user_id: str,
    pattern_narrative: str,
    graduation_indicators: dict,
) -> dict[str, Any]:
    """Persist graduation record with LLM narrative and indicator evidence.

    Emits graduation.complete SSE event.
    """
    record_id = str(uuid.uuid4())

    with get_db_session() as conn:
        # Get snapshot IDs
        final_snapshot = conn.execute(
            "SELECT id FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        initial_snapshot = conn.execute(
            "SELECT id FROM profile_snapshots WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
            (user_id,),
        ).fetchone()

        # Build practice map summary
        practices = conn.execute(
            "SELECT practice_id, COUNT(*) as cnt FROM practice_journal WHERE user_id = ? GROUP BY practice_id",
            (user_id,),
        ).fetchall()

        practice_map = {p["practice_id"]: p["cnt"] for p in practices}

        conn.execute(
            """INSERT INTO graduation_record
               (id, user_id, final_snapshot_id, initial_snapshot_id, practice_map, pattern_narrative, graduation_indicators, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                user_id,
                final_snapshot["id"] if final_snapshot else None,
                initial_snapshot["id"] if initial_snapshot else None,
                json.dumps(practice_map),
                pattern_narrative,
                json.dumps(graduation_indicators),
                datetime.utcnow().isoformat(),
            ),
        )

    return {
        "event_type": "graduation.complete",
        "saved": True,
        "record_id": record_id,
        "pattern_narrative": pattern_narrative,
        "graduation_indicators": graduation_indicators,
    }


# ── Check-in Agent tools ───────────────────────────────────────────────


def get_graduation_record(user_id: str) -> dict[str, Any]:
    """Retrieve the graduation record for a user.

    Returns graduation data including the final snapshot ID (used as
    comparison baseline for check-in assessments).
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT * FROM graduation_record WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"exists": False}

    return {
        "exists": True,
        "id": row["id"],
        "final_snapshot_id": row["final_snapshot_id"],
        "initial_snapshot_id": row["initial_snapshot_id"],
        "practice_map": json.loads(row["practice_map"] or "{}"),
        "pattern_narrative": row["pattern_narrative"],
        "graduation_indicators": json.loads(row["graduation_indicators"] or "{}"),
        "created_at": row["created_at"],
    }


def save_check_in_log(
    user_id: str,
    snapshot_id: str,
    graduation_snapshot_id: str,
    regression_detected: bool,
    re_entered_development: bool = False,
) -> dict[str, Any]:
    """Log a post-graduation check-in result. Emits checkin.complete SSE event.

    Records the check-in snapshot, links to graduation baseline, and flags
    whether regression was detected and whether the user re-entered development.
    """
    log_id = str(uuid.uuid4())

    with get_db_session() as conn:
        conn.execute(
            """INSERT INTO check_in_log
               (id, user_id, snapshot_id, graduation_snapshot_id, regression_detected, re_entered_development, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                user_id,
                snapshot_id,
                graduation_snapshot_id,
                regression_detected,
                re_entered_development,
                datetime.utcnow().isoformat(),
            ),
        )

    return {
        "event_type": "checkin.complete",
        "saved": True,
        "log_id": log_id,
        "regression_detected": regression_detected,
        "re_entered_development": re_entered_development,
    }


def detect_check_in_regression(user_id: str) -> dict[str, Any]:
    """Deterministically decide whether a graduated user has regressed.

    Compares the user's latest (check-in) profile snapshot against their
    graduation baseline (graduation_record.final_snapshot_id). Mirrors the
    sentinel/graduation normalized-0–100 math:
      - a dimension regresses when its normalized score dropped by MORE than
        CHECK_IN_REGRESSION_DROP_NORMALIZED points since graduation;
      - the quadrant "downgrades" when the current archetype ranks below the
        baseline archetype per ARCHETYPE_RANK.

    Reads only — no writes, no SSE. The check-in agent NARRATES this verdict and
    passes ``regression_detected`` to save_check_in_log; it does not decide
    regression itself.

    Returns the stable contract:
        {
          "regression_detected": bool,
          "evaluated": bool,              # False when baseline/snapshot missing
          "reason": str,
          "threshold_normalized": float,
          "regressed_dimensions": [
            {"dimension": str, "baseline_normalized": float,
             "current_normalized": float, "drop_normalized": float}
          ],
          "quadrant": {"baseline": str, "current": str, "downgraded": bool},
          "baseline_snapshot_id": str | None,
          "check_in_snapshot_id": str | None,
        }
    """
    def _not_evaluated(reason: str, baseline_id=None, check_in_id=None) -> dict[str, Any]:
        return {
            "regression_detected": False,
            "evaluated": False,
            "reason": reason,
            "threshold_normalized": CHECK_IN_REGRESSION_DROP_NORMALIZED,
            "regressed_dimensions": [],
            "quadrant": {"baseline": "", "current": "", "downgraded": False},
            "baseline_snapshot_id": baseline_id,
            "check_in_snapshot_id": check_in_id,
        }

    record = get_graduation_record(user_id)
    if not record.get("exists"):
        return _not_evaluated("No graduation record; check-in regression cannot be evaluated")
    baseline_id = record.get("final_snapshot_id")
    if not baseline_id:
        return _not_evaluated("Graduation record has no final snapshot baseline")

    with get_db_session() as conn:
        baseline = conn.execute(
            "SELECT id, scores, quadrant_placement FROM profile_snapshots WHERE id = ? AND user_id = ?",
            (baseline_id, user_id),
        ).fetchone()
        current = conn.execute(
            "SELECT id, scores, quadrant_placement FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not baseline:
        return _not_evaluated(f"Graduation baseline snapshot not found: {baseline_id}", baseline_id)
    if not current:
        return _not_evaluated("No check-in snapshot found", baseline_id)
    if current["id"] == baseline_id:
        # The graduation baseline is still the most recent snapshot — no
        # check-in reassessment has been recorded yet. Don't compare a
        # snapshot against itself.
        return _not_evaluated("No check-in snapshot since graduation", baseline_id, current["id"])

    check_in_id = current["id"]
    baseline_scores = json.loads(baseline["scores"] or "{}")
    current_scores = json.loads(current["scores"] or "{}")

    # Per-dimension normalized drop over the INTERSECTION of dimensions present
    # in both snapshots. A dimension missing on either side is skipped, never
    # defaulted to 0 (which normalize_score maps to -25, manufacturing a drop).
    regressed: list[dict[str, Any]] = []
    for dim in set(baseline_scores.keys()) & set(current_scores.keys()):
        b = baseline_scores[dim]
        c = current_scores[dim]
        b_score = b.get("score") if isinstance(b, dict) else b
        c_score = c.get("score") if isinstance(c, dict) else c
        if b_score is None or c_score is None:
            continue
        b_norm = normalize_score(b_score)
        c_norm = normalize_score(c_score)
        drop = b_norm - c_norm
        if drop > CHECK_IN_REGRESSION_DROP_NORMALIZED:
            regressed.append({
                "dimension": dim,
                "baseline_normalized": round(b_norm, 2),
                "current_normalized": round(c_norm, 2),
                "drop_normalized": round(drop, 2),
            })
    regressed.sort(key=lambda d: (-d["drop_normalized"], d["dimension"]))

    # Quadrant downgrade: current archetype ranks below the baseline archetype.
    # Archetypes absent from ARCHETYPE_RANK (e.g. "undetermined"/"") yield no
    # rank → quadrant signal is skipped, never counted as a downgrade.
    baseline_arch = _snapshot_archetype(json.loads(baseline["quadrant_placement"] or "{}"))
    current_arch = _snapshot_archetype(json.loads(current["quadrant_placement"] or "{}"))
    b_rank = ARCHETYPE_RANK.get(baseline_arch)
    c_rank = ARCHETYPE_RANK.get(current_arch)
    quadrant_downgraded = b_rank is not None and c_rank is not None and c_rank < b_rank

    regression_detected = bool(regressed) or quadrant_downgraded

    if regression_detected:
        parts = []
        if regressed:
            parts.append(
                f"{len(regressed)} dimension(s) dropped > {CHECK_IN_REGRESSION_DROP_NORMALIZED} pts on the 0–100 scale ("
                + ", ".join(f"{r['dimension']} −{r['drop_normalized']}" for r in regressed)
                + ")"
            )
        if quadrant_downgraded:
            parts.append(f"quadrant downgraded ({baseline_arch} → {current_arch})")
        reason = "Regression detected: " + "; ".join(parts)
        logger.warning(
            "Check-in regression detected for user %s: regressed_dims=%d, "
            "quadrant %s->%s (downgraded=%s)",
            user_id, len(regressed), baseline_arch, current_arch, quadrant_downgraded,
        )
    else:
        reason = "No regression detected: all dimensions within threshold and no quadrant downgrade"

    return {
        "regression_detected": regression_detected,
        "evaluated": True,
        "reason": reason,
        "threshold_normalized": CHECK_IN_REGRESSION_DROP_NORMALIZED,
        "regressed_dimensions": regressed,
        "quadrant": {
            "baseline": baseline_arch,
            "current": current_arch,
            "downgraded": quadrant_downgraded,
        },
        "baseline_snapshot_id": baseline_id,
        "check_in_snapshot_id": check_in_id,
    }


def generate_check_in_snapshot(user_id: str) -> dict[str, Any]:
    """Deterministically score a post-graduation check-in reassessment.

    Mirrors `generate_profile_snapshot` (full-13-dim scoring + quadrant +
    optional flow profile), but pre-checks the graduation baseline and tags
    the cached payload with `kind=SNAPSHOT_KIND_CHECK_IN` so the matching
    `save_profile_snapshot` branch persists the snapshot without seeding
    `dimension_assessment_state` (baseline behaviour) and without bumping
    `users.reassessment_cycle` (sentinel-reassessment behaviour). No LLM call.

    Call this in the check-in flow AFTER the user has answered all 13
    dimensions (assessment_state populated) and BEFORE
    `save_profile_snapshot`. Do not call any other `generate_*` tool between
    this and the save — `_profile_cache` is single-slot per user.

    Args:
        user_id: User performing the check-in. Securely injected via
            `with_user_id`; never accepted from the LLM.

    Returns:
        On success:
            {
                "event_type": "checkin.scored",
                "scores": {dim: {...}, ...},
                "quadrant": {"archetype": ..., ...},
                "insufficient_dimensions": [...],
                "has_spider_chart": True,
                "flow_data": <serialized MoralProfile if available>,  # optional
            }
        On missing precondition (one of three; the LLM must narrate and stop):
            {"error": "No assessment data found for user."}
            {"error": "No graduation record found."}
            {"error": "No graduation baseline snapshot found."}
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT responses, scenario_responses FROM assessment_state "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        logger.warning("check_in_snapshot: no assessment_state for user=%s", user_id)
        return {"error": "No assessment data found for user."}

    responses = json.loads(row["responses"] or "{}")
    scenario_responses = json.loads(row["scenario_responses"] or "{}")

    # Empty responses are treated identically to a missing row — the LLM
    # narrates "we don't have your responses yet" in both cases (PD-4).
    if not responses:
        logger.warning(
            "check_in_snapshot: assessment_state.responses empty for user=%s",
            user_id,
        )
        return {"error": "No assessment data found for user."}

    # Baseline precondition: a graduation record must exist with a
    # final_snapshot_id that resolves to an actual profile_snapshots row.
    # Without it, generate_comparison_snapshot and detect_check_in_regression
    # have no baseline to compare against, so the check-in is meaningless.
    grad = get_graduation_record(user_id)
    if not grad.get("exists"):
        logger.warning("check_in_snapshot: no graduation_record for user=%s", user_id)
        return {"error": "No graduation record found."}

    baseline_id = grad.get("final_snapshot_id")
    if not baseline_id:
        logger.warning(
            "check_in_snapshot: graduation_record has no final_snapshot_id for user=%s",
            user_id,
        )
        return {"error": "No graduation baseline snapshot found."}

    with get_db_session() as conn:
        baseline_row = conn.execute(
            "SELECT id FROM profile_snapshots WHERE id = ? AND user_id = ?",
            (baseline_id, user_id),
        ).fetchone()

    if not baseline_row:
        logger.warning(
            "check_in_snapshot: baseline snapshot %s missing for user=%s",
            baseline_id, user_id,
        )
        return {"error": "No graduation baseline snapshot found."}

    # Deterministic scoring (same path baseline + reassessment use).
    result = score_responses(responses, scenario_responses)
    chart_png = generate_spider_chart(result["dimensions"])
    flow_profile = result.get("flow_profile")

    # Stage to the single-slot cache for save_profile_snapshot to persist.
    # The "kind" marker routes save_profile_snapshot into the check-in
    # branch (skips DAS seeding AND reassessment_cycle bump).
    _profile_cache[user_id] = {
        "scores": result["dimensions"],
        "quadrant": result["quadrant"],
        "insufficient_dimensions": result["insufficient_dimensions"],
        "spider_chart": chart_png,
        "flow_profile": flow_profile,
        "kind": SNAPSHOT_KIND_CHECK_IN,
    }

    logger.info("check_in_snapshot: scored user=%s baseline=%s", user_id, baseline_id)

    response: dict[str, Any] = {
        "event_type": "checkin.scored",
        "scores": result["dimensions"],
        "quadrant": result["quadrant"],
        "insufficient_dimensions": result["insufficient_dimensions"],
        "has_spider_chart": True,
    }
    if flow_profile:
        response["flow_data"] = flow_profile.model_dump()
    return response


# ── Reassessment selection tools ───────────────────────────────────────


def get_dimension_staleness(user_id: str) -> dict[str, Any]:
    """Return the current reassessment cycle and per-dimension staleness for a user.

    Staleness = current_cycle − last_assessed_cycle for each dimension.
    Dimensions never assessed default to last_assessed_cycle=0 and
    staleness=current_cycle (they are as stale as the cycle count allows).

    Returns:
        {
            "current_cycle": int,
            "staleness": {dim: int},
            "last_assessed_cycle": {dim: int},
        }
    """
    qb = get_question_bank()
    all_dims = qb.get_dimensions()

    with get_db_session() as conn:
        user_row = conn.execute(
            "SELECT reassessment_cycle FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if not user_row:
            return {"error": f"User {user_id} not found"}

        current_cycle = user_row["reassessment_cycle"]

        das_rows = conn.execute(
            "SELECT dimension, last_assessed_cycle FROM dimension_assessment_state WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    last_assessed: dict[str, int] = {row["dimension"]: row["last_assessed_cycle"] for row in das_rows}

    staleness: dict[str, int] = {}
    last_assessed_result: dict[str, int] = {}
    for dim in all_dims:
        lac = last_assessed.get(dim, 0)
        last_assessed_result[dim] = lac
        staleness[dim] = current_cycle - lac

    return {
        "current_cycle": current_cycle,
        "staleness": staleness,
        "last_assessed_cycle": last_assessed_result,
    }


def _extract_dimensions_from_roadmap(roadmap: Any, known_dims: set) -> list:
    """Extract known dimension names from free-form roadmap JSON.

    Scans string values at keys dimension/dimensions/target/targets and
    within steps[] arrays. Returns exact matches only (case-sensitive, de-duplicated,
    order-preserving). Non-matching strings are silently ignored.
    """
    found: list = []
    seen: set = set()

    def _add(candidate: Any) -> None:
        if isinstance(candidate, str) and candidate in known_dims and candidate not in seen:
            found.append(candidate)
            seen.add(candidate)

    def _scan_obj(obj: Any) -> None:
        if isinstance(obj, dict):
            for key in ("dimension", "dimensions", "target", "targets"):
                val = obj.get(key)
                if isinstance(val, list):
                    for item in val:
                        _add(item)
                else:
                    _add(val)
            # Recurse into steps[]
            for step in obj.get("steps", []):
                _scan_obj(step)
            # Recurse into nested dicts
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    _scan_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                _scan_obj(item)

    _scan_obj(roadmap)
    return found


def select_reassessment_targets(user_id: str) -> dict[str, Any]:
    """Determine which dimensions are targeted, sentinel, forced, and carried for reassessment.

    Orchestrates the selection by:
    1. Extracting targeted dims from the development roadmap (exact dimension name match).
    2. Adding any dims flagged for full reassessment from the prior cycle.
    3. Running staleness-based sentinel selection (excluding targeted dims).
    4. Classifying all remaining dims as carried.

    No-roadmap / no-match fallback: if no dimensions extractable from roadmap,
    targeted_dimensions = flagged-from-prior-cycle dims only (possibly empty).
    This is a valid all-sentinel/all-carried partition, not an error.

    Returns:
        {
            "targeted_dimensions": [dim, ...],
            "sentinel_dimensions": [dim, ...],
            "forced_dimensions": [dim, ...],
            "carried_dimensions": [dim, ...],
        }
    """
    from agents.transmutation.sentinel_engine import select_sentinel_dimensions

    qb = get_question_bank()
    all_dims = qb.get_dimensions()
    known_dims = set(all_dims)

    # Step 1: Extract roadmap-targeted dims
    roadmap_result = get_development_roadmap(user_id)
    roadmap_dims: list = []
    if roadmap_result.get("exists"):
        roadmap_json = roadmap_result.get("roadmap", {})
        roadmap_dims = _extract_dimensions_from_roadmap(roadmap_json, known_dims)

    # Step 2: Add prior-cycle flagged dims
    with get_db_session() as conn:
        flagged_rows = conn.execute(
            "SELECT dimension FROM dimension_assessment_state WHERE user_id = ? AND flagged_for_full_reassessment = 1",
            (user_id,),
        ).fetchall()
    flagged_dims = [row["dimension"] for row in flagged_rows]

    # Merge: roadmap dims + flagged, de-duplicated, order-preserving
    targeted_set: set = set()
    targeted: list = []
    for dim in roadmap_dims + flagged_dims:
        if dim in known_dims and dim not in targeted_set:
            targeted.append(dim)
            targeted_set.add(dim)

    # Step 3: Sentinel selection from remaining dims
    staleness_result = get_dimension_staleness(user_id)
    if "error" in staleness_result:
        return staleness_result

    staleness_by_dim = staleness_result["staleness"]

    # Fetch prior scores for extremity calculation
    with get_db_session() as conn:
        snap_row = conn.execute(
            "SELECT scores FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    prior_scores: dict = {}
    if snap_row:
        prior_scores = json.loads(snap_row["scores"] or "{}")

    sentinel_result = select_sentinel_dimensions(
        staleness_by_dim,
        prior_scores,
        excluded=targeted,
    )

    sentinel_dims = sentinel_result["selected"]
    forced_dims = sentinel_result["forced"]

    # Step 4: Everything else is carried
    targeted_and_sentinel = targeted_set | set(sentinel_dims)
    carried = [d for d in all_dims if d not in targeted_and_sentinel]

    logger.info(
        "Reassessment targets selected: targeted=%d, sentinel=%d (forced=%d), carried=%d",
        len(targeted), len(sentinel_dims), len(forced_dims), len(carried),
    )

    return {
        "targeted_dimensions": targeted,
        "sentinel_dimensions": sentinel_dims,
        "forced_dimensions": forced_dims,
        "carried_dimensions": carried,
    }


def select_sentinel_questions(
    user_id: str,
    dimensions: list,
    n: int = 5,
) -> dict[str, Any]:
    """Select question IDs for sentinel dimensions, prioritizing by response extremity.

    For each dimension, picks up to n question IDs whose prior responses were most
    extreme (closest to 1 or 5 on the Likert scale). Questions with no prior response
    are included last (so novel questions fill remaining slots).

    Args:
        user_id:    User to fetch prior responses for.
        dimensions: Sentinel dimension names to select questions from.
        n:          Number of questions per dimension (default 5).

    Returns:
        {
            "question_ids": [qid, ...],       # flat list, all selected IDs
            "by_dimension": {dim: [qid, ...]}, # per-dimension breakdown
        }
    """
    qb = get_question_bank()

    # Fetch prior responses (most recent assessment state)
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    prior_responses: dict = {}
    if row:
        prior_responses = json.loads(row["responses"] or "{}")

    all_question_ids: list = []
    by_dimension: dict = {}

    for dim in dimensions:
        questions = qb.get_questions_by_dimension(dim)
        if not questions:
            by_dimension[dim] = []
            continue

        def _extremity_key(q: dict) -> float:
            resp = prior_responses.get(q["id"])
            if resp is None:
                return -1.0  # no prior → least extreme → last
            score = resp.get("score")
            if score is None:
                return -1.0
            # extremity = distance from midpoint 3.0 on 1-5 scale; max=2.0
            return abs(score - 3.0)

        sorted_qs = sorted(questions, key=_extremity_key, reverse=True)
        selected_ids = [q["id"] for q in sorted_qs[:n]]
        by_dimension[dim] = selected_ids
        all_question_ids.extend(selected_ids)

    return {
        "question_ids": all_question_ids,
        "by_dimension": by_dimension,
    }


def generate_reassessment_snapshot(user_id: str) -> dict[str, Any]:
    """Generate a deterministic blended reassessment snapshot.

    Orchestrates the full reassessment scoring pipeline:
    1. Load the prior snapshot scores (the carry baseline). No prior → error.
    2. Load the current assessment_state responses. None → error.
    3. Determine targeted / sentinel / carried dimensions (select_reassessment_targets).
    4. Pick extremity-weighted sentinel question IDs (select_sentinel_questions).
    5. Re-score targeted dims from the full current responses (score_responses).
    6. Compute the sentinel signal from the sentinel question subset only
       (score_question_subset).
    7. Merge fresh signals (targeted → full re-score, sentinel → subset signal) and
       blend against the prior with compute_sentinel_scores (70/30 default).
    8. Recompute the quadrant from the BLENDED Transmutation Capacity sub-dimensions.
    9. Populate _profile_cache[user_id] for save_profile_snapshot to persist.

    The math is fully deterministic; no narrative is generated here. The returned
    sentinel block carries the per-dimension source/shift metadata and the
    flagged_for_full_reassessment list for the next cycle.

    Args:
        user_id: User to reassess. Securely injected via with_user_id.

    Returns:
        On success:
            {
                "event_type": "reassessment.scored",
                "scores": {dim: {...blended...}},
                "quadrant": {x, y, archetype, ...},
                "sentinel": {dimensions, flagged_for_full_reassessment, blend, ...},
                "current_cycle": int,  # cycle this reassessment will become once saved
            }
        On error:
            {"error": "No prior snapshot found for user."}  or
            {"error": "No assessment data found for user."}
    """
    from agents.transmutation.sentinel_engine import compute_sentinel_scores

    qb = get_question_bank()

    with get_db_session() as conn:
        prior_row = conn.execute(
            "SELECT scores FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        assessment_row = conn.execute(
            "SELECT responses, scenario_responses FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        cycle_row = conn.execute(
            "SELECT reassessment_cycle FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if not prior_row or not prior_row["scores"]:
        return {"error": "No prior snapshot found for user."}

    if not assessment_row:
        return {"error": "No assessment data found for user."}

    prior_scores = json.loads(prior_row["scores"] or "{}")
    if not prior_scores:
        return {"error": "No prior snapshot found for user."}

    responses = json.loads(assessment_row["responses"] or "{}")
    scenario_responses = json.loads(assessment_row["scenario_responses"] or "{}")

    # The cycle this reassessment becomes once persisted (current + 1).
    prior_cycle = cycle_row["reassessment_cycle"] if cycle_row else 0
    current_cycle = prior_cycle + 1

    # Step 1: Deterministic plan — which dims are targeted vs sentinel vs carried.
    targets = select_reassessment_targets(user_id)
    if "error" in targets:
        return targets

    targeted_dimensions = targets["targeted_dimensions"]
    sentinel_dimensions = targets["sentinel_dimensions"]

    # Step 2: Sentinel question IDs (extremity-weighted) for the sentinel signal.
    sentinel_picks = select_sentinel_questions(user_id, sentinel_dimensions)
    sentinel_question_ids = sentinel_picks["question_ids"]

    # Step 3: Full re-score from current responses (provides targeted fresh scores).
    full_fresh = score_responses(responses, scenario_responses)
    targeted_fresh_scores = full_fresh["dimensions"]

    # Step 4: Sentinel signal from only the selected sentinel questions.
    sentinel_fresh_signal = score_question_subset(responses, sentinel_question_ids, qb)

    # Step 5: Merge fresh signals — targeted dims use the full re-score, sentinel dims
    # use the lighter subset signal. compute_sentinel_scores reads one fresh dict.
    merged_fresh: dict[str, Any] = {}
    for dim in targeted_dimensions:
        if dim in targeted_fresh_scores:
            merged_fresh[dim] = targeted_fresh_scores[dim]
    for dim in sentinel_dimensions:
        if dim in sentinel_fresh_signal:
            merged_fresh[dim] = sentinel_fresh_signal[dim]

    # Step 6: Blend prior + fresh deterministically (70/30 for sentinel, 100% for targeted).
    blended = compute_sentinel_scores(
        prior_scores,
        merged_fresh,
        targeted_dimensions,
        sentinel_dimensions,
    )
    blended_scores = blended["dimensions"]

    # Step 7: Recompute the quadrant from the BLENDED Transmutation Capacity sub-dims.
    # _calculate_quadrant reads sub_dimensions[*]["score"], which the blended shape
    # carries, so the quadrant reflects blended (not just fresh) capacity.
    quadrant = _calculate_quadrant(blended_scores, scenario_responses, qb)

    # Step 8: Build the sentinel metadata block persisted alongside the snapshot.
    sentinel_block = {
        "dimensions": {
            dim: {
                "source": data["source"],
                "shift_normalized": data["shift_normalized"],
                "shift_flagged": data["shift_flagged"],
            }
            for dim, data in blended_scores.items()
        },
        "flagged_for_full_reassessment": blended["flagged_for_full_reassessment"],
        "blend": blended["blend"],
        "shift_threshold_normalized": blended["shift_threshold_normalized"],
        "targeted_dimensions": targeted_dimensions,
        "sentinel_dimensions": sentinel_dimensions,
        "carried_dimensions": targets["carried_dimensions"],
        "cycle": current_cycle,
    }

    # Step 9: Generate spider chart from the blended dimension scores.
    chart_png = generate_spider_chart(blended_scores)

    # Populate the cache for save_profile_snapshot. The presence of "sentinel" routes
    # save_profile_snapshot down the reassessment persistence path.
    _profile_cache[user_id] = {
        "scores": blended_scores,
        "quadrant": quadrant,
        "insufficient_dimensions": full_fresh.get("insufficient_dimensions", []),
        "spider_chart": chart_png,
        "flow_profile": full_fresh.get("flow_profile"),
        "sentinel": sentinel_block,
    }

    return {
        "event_type": "reassessment.scored",
        "scores": blended_scores,
        "quadrant": quadrant,
        "sentinel": sentinel_block,
        "current_cycle": current_cycle,
    }
