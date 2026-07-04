"""Deterministic scoring and quadrant placement logic.

Converts raw assessment responses into dimension scores and archetype placement.
All calculations are pure functions — no DB access, no side effects.
"""

import logging
from typing import Any, Optional

from agents.transmutation.flow_engine import compute_full_profile
from agents.transmutation.question_bank import get_question_bank
from config import get_settings

logger = logging.getLogger(__name__)

# Archetype thresholds on the 2-axis model (v13 convention).
# X-axis: Amplification A = fulfillment emission (positive = emits, negative = absorbs)
# Y-axis: Filtering F = deprivation filtering (positive = filters, negative = amplifies)
# Values are normalized to [-1, 1].
CONDUIT_THRESHOLD = 0.15  # within this radius of center = Conduit


def _enrich_scenario_responses(
    scenario_responses: dict[str, Any],
    qb,
) -> dict[str, Any]:
    """Backfill missing quadrant_weight and maslow_level in scenario responses.

    Scenario responses created outside save_scenario_response (e.g., manual seeding,
    direct API) may lack these fields. Looks up the scenario definition and chosen
    option to populate them.
    """
    for scenario_id, response in scenario_responses.items():
        needs_weight = "quadrant_weight" not in response
        needs_maslow = "maslow_level" not in response

        if not needs_weight and not needs_maslow:
            continue

        scenario = qb.get_scenario_by_id(scenario_id)
        if not scenario:
            continue

        if needs_maslow:
            response["maslow_level"] = scenario.get("maslow_level")

        if needs_weight:
            choice_key = response.get("choice")
            for c in scenario.get("choices", []):
                if c["key"] == choice_key:
                    response["quadrant_weight"] = c.get("quadrant_weight", {})
                    break

        if needs_weight or needs_maslow:
            logger.warning(
                "Backfilled missing fields for scenario %s: quadrant_weight=%s, maslow_level=%s",
                scenario_id, needs_weight, needs_maslow,
            )

    return scenario_responses


def normalize_score(score: float, lo: float = 1.0, hi: float = 5.0) -> float:
    """Map a raw Likert score from [lo, hi] to the [0, 100] scale.

    Uses the formula (score - lo) / (hi - lo) * 100 so that lo→0 and hi→100.
    Exported for reuse by the sentinel engine and downstream projects (P2).

    Args:
        score: Raw score value (typically 1–5 Likert).
        lo: Lower bound of the raw scale (default 1.0).
        hi: Upper bound of the raw scale (default 5.0).

    Returns:
        Score mapped to 0–100.
    """
    return (score - lo) / (hi - lo) * 100.0


def score_question_subset(
    responses: dict,
    question_ids: list,
    qb,
) -> dict:
    """Compute dimension/sub-dimension averages from ONLY the given question IDs.

    Applies the same reverse-scoring (reverse_scored → (points+1)−raw) and
    None/N-A rules as _score_likert_by_dimension, but restricted to the
    provided question_ids (the sentinel signal).

    Args:
        responses: Mapping of question_id → {"score": float|None, ...}.
        question_ids: The subset of question IDs to consider.
        qb: QuestionBank instance providing question metadata.

    Returns:
        {dim: {"score": float, "sub_dimensions": {sd: {"score": float, "answered": int}}, "answered": int}}
        Only dimensions with ≥1 answered sentinel question are included;
        dimensions/sub-dimensions with no answered sentinel question are absent.
    """
    qid_set = set(question_ids)

    # Accumulate per-(dim, sub_dim) scores
    dim_data: dict[str, dict] = {}

    for qid in question_ids:
        q = qb.get_question_by_id(qid)
        if q is None:
            continue

        dim = q["dimension"]
        sub_dim = q.get("sub_dimension", "general")

        if dim not in dim_data:
            dim_data[dim] = {"scores": [], "sub_dimensions": {}}
        if sub_dim not in dim_data[dim]["sub_dimensions"]:
            dim_data[dim]["sub_dimensions"][sub_dim] = []

        if qid not in responses:
            continue

        resp = responses[qid]
        raw_score = resp.get("score")

        # Handle N/A responses
        if raw_score is None:
            continue

        # Apply reverse scoring
        if q.get("reverse_scored"):
            scale_type = q.get("scale_type", "agreement_5")
            points = qb.scale_types.get(scale_type, {}).get("points", 5)
            raw_score = (points + 1) - raw_score

        dim_data[dim]["scores"].append(raw_score)
        dim_data[dim]["sub_dimensions"][sub_dim].append(raw_score)

    # Build result dict — only include dims with ≥1 answered question
    result: dict[str, dict] = {}
    for dim, data in dim_data.items():
        dim_scores = data["scores"]
        if not dim_scores:
            continue  # No answered questions for this dim in the subset

        sub_dim_results: dict[str, dict] = {}
        for sd_name, sd_scores in data["sub_dimensions"].items():
            if sd_scores:
                sub_dim_results[sd_name] = {
                    "score": round(sum(sd_scores) / len(sd_scores), 2),
                    "answered": len(sd_scores),
                }
            # Sub-dims with no answered questions are absent (caller carries prior)

        result[dim] = {
            "score": round(sum(dim_scores) / len(dim_scores), 2),
            "sub_dimensions": sub_dim_results,
            "answered": len(dim_scores),
        }

    return result


def score_responses(
    responses: dict[str, Any],
    scenario_responses: dict[str, Any],
) -> dict[str, Any]:
    """Score all responses into dimension averages and compute flow-based moral profile.

    Runs two parallel scoring paths:
    1. Likert-based dimension/sub-dimension scoring and quadrant placement (existing)
    2. Flow-based moral profile via flow_engine (v13 framework)

    Returns:
        {
            "dimensions": { "Emotional Awareness": { "score": 3.5, "sub_dimensions": {...} } },
            "insufficient_dimensions": ["dim_name", ...],
            "quadrant": { "x": float, "y": float, "archetype": str },
            "flow_profile": MoralProfile with M vector, W, C+, C-,
        }
    """
    qb = get_question_bank()

    # Enrich scenario responses that may be missing quadrant_weight/maslow_level
    scenario_responses = _enrich_scenario_responses(scenario_responses, qb)

    dim_scores = _score_likert_by_dimension(responses, qb)
    quadrant = _calculate_quadrant(dim_scores, scenario_responses, qb)

    # Collect insufficient dimensions
    insufficient = [
        dim for dim, data in dim_scores.items()
        if data.get("insufficient_data")
    ]

    # Compute flow-based moral profile (parallel to dimension scoring)
    settings = get_settings()
    flow_profile = compute_full_profile(
        scenario_responses=scenario_responses,
        scenarios=qb.get_all_scenarios(),
        tau=settings.transmutation.tau,
        weights=settings.transmutation.maslow_weights,
    )

    return {
        "dimensions": dim_scores,
        "insufficient_dimensions": insufficient,
        "quadrant": quadrant,
        "flow_profile": flow_profile,
    }


def compute_early_transmute_result(
    responses: dict[str, Any],
    scenario_responses: dict[str, Any],
) -> dict[str, Any]:
    """Compute the Tier-1 "early transmute result" event payload.

    Wraps _calculate_quadrant for the (x, y, archetype) triple, but computes
    its OWN confidence band and confidence_reason -- deliberately NOT reusing
    _calculate_quadrant's built-in "confidence" field, which is scenario-vote-
    count-only (>=3 votes -> "high") and carries no explanatory reason. This
    function's confidence instead reflects how much Tier-1 data (both TC
    Likert items AND scenarios) actually went into the placement:

        high   -- >= CONF_HIGH_TC TC items answered AND
                  >= CONF_HIGH_SCEN scenarios answered
        low    -- < MIN_ITEMS_PER_DIM TC items answered OR
                  < MIN_SCENARIOS scenarios answered
        medium -- everything else (enough data to be meaningful, not enough
                  for the high-confidence bar)

    Returns a dict whose event_type is mandatory: api/chat.py's existing
    tool-return-dict re-emit path (only dicts containing "event_type" are
    re-emitted as a named SSE event) is how this rides to the frontend --
    there is no separate emit call here.
    """
    qb = get_question_bank()

    scenario_responses = _enrich_scenario_responses(scenario_responses, qb)
    dim_scores = _score_likert_by_dimension(responses, qb)
    quadrant = _calculate_quadrant(dim_scores, scenario_responses, qb)

    tc_data = dim_scores.get("Transmutation Capacity", {})
    tc_answered = tc_data.get("answered", 0)
    scenarios_answered = len(scenario_responses)

    settings = get_settings().transmutation
    confidence, confidence_reason = _early_result_confidence(
        tc_answered=tc_answered,
        scenarios_answered=scenarios_answered,
        min_items_per_dim=settings.MIN_ITEMS_PER_DIM,
        min_scenarios=settings.MIN_SCENARIOS,
        conf_high_tc=settings.CONF_HIGH_TC,
        conf_high_scen=settings.CONF_HIGH_SCEN,
    )

    return {
        "event_type": "assessment.transmute_result",
        "archetype": quadrant["archetype"],
        "x": quadrant["x"],
        "y": quadrant["y"],
        "confidence": confidence,
        "confidence_reason": confidence_reason,
    }


def _early_result_confidence(
    tc_answered: int,
    scenarios_answered: int,
    min_items_per_dim: int,
    min_scenarios: int,
    conf_high_tc: int,
    conf_high_scen: int,
) -> tuple[str, str]:
    """Confidence band + plain-language reason for compute_early_transmute_result.

    Split out from compute_early_transmute_result for readability (it has one
    job: turn two answered-counts into a confidence band and an honest,
    Barnum-mitigating explanation of why).
    """
    if tc_answered < min_items_per_dim or scenarios_answered < min_scenarios:
        return (
            "low",
            f"Based on {tc_answered} core answers and {scenarios_answered} scenario "
            "responses so far -- too early to say much. A few more answers will "
            "sharpen this.",
        )

    if tc_answered >= conf_high_tc and scenarios_answered >= conf_high_scen:
        return (
            "high",
            f"Based on {tc_answered} core answers and {scenarios_answered} scenario "
            "responses -- a solid first read on your pattern.",
        )

    return (
        "medium",
        f"Based on {tc_answered} core answers and {scenarios_answered} scenario "
        "responses so far. A few more in the next section will sharpen this.",
    )


def _score_likert_by_dimension(
    responses: dict[str, Any],
    qb,
) -> dict[str, Any]:
    """Aggregate Likert scores per dimension and sub-dimension."""
    dimensions: dict[str, dict[str, Any]] = {}

    for dim in qb.get_dimensions():
        questions = qb.get_questions_by_dimension(dim)
        total_questions = len(questions)

        dim_scores: list[float] = []
        na_count = 0
        sub_dims: dict[str, dict[str, Any]] = {}

        for q in questions:
            qid = q["id"]
            sub_dim = q.get("sub_dimension", "general")

            if sub_dim not in sub_dims:
                sub_dims[sub_dim] = {"scores": [], "na_count": 0, "total": 0}
            sub_dims[sub_dim]["total"] += 1

            if qid not in responses:
                continue

            resp = responses[qid]
            raw_score = resp.get("score")

            # Handle N/A
            if raw_score is None:
                na_count += 1
                sub_dims[sub_dim]["na_count"] += 1
                continue

            # Handle reverse scoring
            if q.get("reverse_scored"):
                scale_type = q.get("scale_type", "agreement_5")
                points = qb.scale_types.get(scale_type, {}).get("points", 5)
                raw_score = (points + 1) - raw_score

            dim_scores.append(raw_score)
            sub_dims[sub_dim]["scores"].append(raw_score)

        # Compute dimension-level stats
        na_pct = na_count / total_questions if total_questions > 0 else 0
        # Sufficiency: absolute minimum answered items, not a percentage of
        # N/A responses. A dimension can now have plenty of items but only a
        # few actually administered (v2 tiered/screener-first flow), so an
        # na_pct-based rule would wrongly flag a fully-answered screener as
        # insufficient just because most of the dimension's OTHER items were
        # never shown. Scope: this function only -- score_question_subset
        # (the sentinel scorer) is untouched and keeps its own
        # any-answered-item-scores behavior.
        min_items = get_settings().transmutation.MIN_ITEMS_PER_DIM
        insufficient = len(dim_scores) < min_items

        avg = sum(dim_scores) / len(dim_scores) if dim_scores else 0.0

        # Compute sub-dimension averages
        sub_dim_results = {}
        for sd_name, sd_data in sub_dims.items():
            sd_scores = sd_data["scores"]
            sub_dim_results[sd_name] = {
                "score": round(sum(sd_scores) / len(sd_scores), 2) if sd_scores else 0.0,
                "answered": len(sd_scores),
                "total": sd_data["total"],
                "na_count": sd_data["na_count"],
            }

        dimensions[dim] = {
            "score": round(avg, 2),
            "answered": len(dim_scores),
            "total": total_questions,
            "na_count": na_count,
            "na_pct": round(na_pct, 3),
            "insufficient_data": insufficient,
            "sub_dimensions": sub_dim_results,
        }

    return dimensions


def _calculate_quadrant(
    dim_scores: dict[str, Any],
    scenario_responses: dict[str, Any],
    qb,
) -> dict[str, Any]:
    """Calculate quadrant placement from transmutation scores and scenarios.

    Returns (x, y) in v13 convention:
      X-axis = Amplification A (horizontal): positive = emits fulfillment, negative = absorbs
      Y-axis = Filtering F (vertical): positive = filters deprivation, negative = amplifies

    Quadrant layout (v13):
      (+X, +Y) = Transmuter (top-right)   |  (-X, +Y) = Absorber (top-left)
      (+X, -Y) = Magnifier (bottom-right) |  (-X, -Y) = Extractor (bottom-left)

    Scenario quadrant_weights contribute a weighted vote toward archetypes.
    """
    tc = dim_scores.get("Transmutation Capacity", {})
    sub_dims = tc.get("sub_dimensions", {})

    # Check if transmutation data is sufficient
    if tc.get("insufficient_data"):
        return {
            "x": 0.0,
            "y": 0.0,
            "archetype": "undetermined",
            "confidence": "low",
            "reason": "Insufficient transmutation capacity data",
        }

    # Extract sub-dimension scores (1-5 scale)
    filt_score = sub_dims.get("Deprivation Filtering", {}).get("score", 3.0)
    emit_score = sub_dims.get("Fulfillment Emission", {}).get("score", 3.0)
    amp_score = sub_dims.get("Amplification Awareness", {}).get("score", 3.0)
    abs_score = sub_dims.get("Absorption Patterns", {}).get("score", 3.0)

    # Normalize to [-1, 1] range (3.0 = center)
    # Filtering sub-scores → Y-axis (F): high filtering = positive Y
    filt_component = (filt_score - 3.0) / 2.0   # high filtering → +Y
    amp_component = (amp_score - 3.0) / 2.0     # high amp awareness → +Y (notices and stops amplifying)
    y_likert = (filt_component + amp_component) / 2.0

    # Emission sub-scores → X-axis (A): high emission = positive X
    emit_component = (emit_score - 3.0) / 2.0   # high emission → +X
    abs_component = -(abs_score - 3.0) / 2.0    # high absorption → -X
    x_likert = (emit_component + abs_component) / 2.0

    # Incorporate scenario quadrant_weights
    archetype_votes = {
        "transmuter": 0.0,
        "absorber": 0.0,
        "magnifier": 0.0,
        "extractor": 0.0,
        "conduit": 0.0,
    }

    for sr in scenario_responses.values():
        qw = sr.get("quadrant_weight", {})
        for archetype, weight in qw.items():
            if archetype in archetype_votes:
                archetype_votes[archetype] += weight

    total_scenario_votes = sum(archetype_votes.values())

    # Map scenario votes to X, Y nudges (v13 convention)
    # X = A: Transmuter +X, Magnifier +X (both emit fulfillment); Absorber -X, Extractor -X
    # Y = F: Transmuter +Y, Absorber +Y (both filter deprivation); Magnifier -Y, Extractor -Y
    if total_scenario_votes > 0:
        scenario_x = (
            archetype_votes["transmuter"] + archetype_votes["magnifier"]
            - archetype_votes["absorber"] - archetype_votes["extractor"]
        ) / total_scenario_votes
        scenario_y = (
            archetype_votes["transmuter"] + archetype_votes["absorber"]
            - archetype_votes["magnifier"] - archetype_votes["extractor"]
        ) / total_scenario_votes
    else:
        scenario_x = 0.0
        scenario_y = 0.0

    # Blend: 60% Likert, 40% scenario
    likert_weight = 0.6
    scenario_weight = 0.4 if total_scenario_votes > 0 else 0.0
    blend_total = likert_weight + scenario_weight

    x = (x_likert * likert_weight + scenario_x * scenario_weight) / blend_total
    y = (y_likert * likert_weight + scenario_y * scenario_weight) / blend_total

    # Clamp to [-1, 1]
    x = max(-1.0, min(1.0, x))
    y = max(-1.0, min(1.0, y))

    archetype = _map_archetype(x, y)

    return {
        "x": round(x, 3),
        "y": round(y, 3),
        "archetype": archetype,
        "confidence": "high" if total_scenario_votes >= 3 else "medium",
    }


def _map_archetype(x: float, y: float) -> str:
    """Map (x, y) coordinates to one of the 5 archetypes.

    v13 convention (X = Amplification, Y = Filtering):
        (+X, +Y) = Transmuter (top-right)    |  (-X, +Y) = Absorber (top-left)
        (+X, -Y) = Magnifier (bottom-right)  |  (-X, -Y) = Extractor (bottom-left)
        (center)  = Conduit
    """
    # Check for Conduit (near center)
    if abs(x) <= CONDUIT_THRESHOLD and abs(y) <= CONDUIT_THRESHOLD:
        return "conduit"

    # Determine quadrant
    if x >= 0 and y >= 0:
        return "transmuter"
    elif x >= 0 and y < 0:
        return "magnifier"
    elif x < 0 and y >= 0:
        return "absorber"
    else:
        return "extractor"
