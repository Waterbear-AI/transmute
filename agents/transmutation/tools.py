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


def generate_roadmap(user_id: str) -> dict[str, Any]:
    """Generate a development roadmap from the user's profile.

    Fetches the most recent profile snapshot, identifies weakest
    transmutation linkages, and returns roadmap data for the LLM
    to refine before saving.
    """
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT scores FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not row:
        return {"error": "No profile snapshot found. Complete assessment first."}

    scores = json.loads(row["scores"] or "{}")

    # Identify weakest dimensions by score
    ranked = sorted(scores.items(), key=lambda x: x[1].get("score", 0) if isinstance(x[1], dict) else x[1])
    weakest = ranked[:3] if len(ranked) >= 3 else ranked

    return {
        "profile_scores": scores,
        "weakest_dimensions": [
            {"dimension": dim, "score": data.get("score", 0) if isinstance(data, dict) else data}
            for dim, data in weakest
        ],
        "step_count": 3,
        "instruction": (
            "Create a 3-step roadmap targeting these dimensions. "
            "Each step should include: education context, a concrete practice "
            "mapped to a transmutation operation, and a reflective conversation prompt."
        ),
    }


def save_roadmap(user_id: str, roadmap: dict) -> dict[str, Any]:
    """Persist a development roadmap. Emits development.roadmap SSE event."""
    roadmap_id = str(uuid.uuid4())

    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO development_roadmap (id, user_id, roadmap, created_at) VALUES (?, ?, ?, ?)",
            (roadmap_id, user_id, json.dumps(roadmap), datetime.utcnow().isoformat()),
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
) -> dict[str, Any]:
    """Log a practice journal entry. Emits development.practice SSE event.

    Also checks total entry count to signal readiness for reassessment
    (10 entries threshold).
    """
    entry_id = str(uuid.uuid4())

    with get_db_session() as conn:
        conn.execute(
            "INSERT INTO practice_journal (id, user_id, practice_id, reflection, self_rating, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, user_id, practice_id, reflection, self_rating, datetime.utcnow().isoformat()),
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
        deltas[dim] = {
            "previous": prev_score,
            "current": curr_score,
            "delta": delta,
            "direction": "up" if delta > 0 else "down" if delta < 0 else "stable",
        }

    # Quadrant shift
    curr_q = current_quadrant.get("quadrant", "")
    prev_q = previous_quadrant.get("quadrant", "")
    quadrant_shifted = curr_q != prev_q

    return {
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

        # 1. Pattern Stability: delta < 5% for 2 consecutive cycles
        # Cycle 1: current vs previous, Cycle 2: previous vs before_previous
        def compute_max_delta(scores_a, scores_b):
            max_delta = 0
            for dim in set(list(scores_a.keys()) + list(scores_b.keys())):
                a = scores_a.get(dim, {})
                b = scores_b.get(dim, {})
                sa = a.get("score", 0) if isinstance(a, dict) else a
                sb = b.get("score", 0) if isinstance(b, dict) else b
                max_delta = max(max_delta, abs(sa - sb))
            return round(max_delta, 2)

        delta_cycle1 = compute_max_delta(scores[0], scores[1])
        delta_cycle2 = compute_max_delta(scores[1], scores[2])

        stability_met = delta_cycle1 < 5 and delta_cycle2 < 5
        indicators["pattern_stability"]["met"] = stability_met
        indicators["pattern_stability"]["evidence"] = (
            f"Cycle 1 max delta: {delta_cycle1}%, Cycle 2 max delta: {delta_cycle2}% "
            f"(threshold: <5% for both)"
        )

        # 2. Quadrant Consolidation: same quadrant for 2 consecutive reassessments
        q0 = quadrants[0].get("quadrant", "")
        q1 = quadrants[1].get("quadrant", "")
        q2 = quadrants[2].get("quadrant", "")

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
