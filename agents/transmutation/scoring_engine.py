"""Deterministic scoring and quadrant placement logic.

Converts raw assessment responses into dimension scores and archetype placement.
All calculations are pure functions — no DB access, no side effects.
"""

from typing import Any, Optional

from agents.transmutation.flow_engine import compute_full_profile
from agents.transmutation.question_bank import get_question_bank
from config import get_settings

# Archetype thresholds on the 2-axis model.
# X-axis: deprivation handling (negative = filters, positive = amplifies)
# Y-axis: fulfillment handling (negative = absorbs, positive = emits)
# Values are normalized to [-1, 1].
CONDUIT_THRESHOLD = 0.15  # within this radius of center = Conduit


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
        insufficient = na_pct > 0.2 or len(dim_scores) == 0

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

    X-axis (deprivation handling):
      Negative = filters deprivation (good), Positive = amplifies deprivation (bad)
      Derived from: Deprivation Filtering (inverted), Amplification Awareness

    Y-axis (fulfillment handling):
      Positive = emits fulfillment (good), Negative = absorbs fulfillment (bad)
      Derived from: Fulfillment Emission, Absorption Patterns (inverted)

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
    # X-axis: high filtering = negative X (filters), low filtering + low amp awareness = positive X (amplifies)
    # Filtering: high score → good at filtering → X goes negative (filter side)
    # Amp awareness: high score → notices when amplifying → X goes negative (filter side)
    x_filter = -(filt_score - 3.0) / 2.0  # high filtering pushes X negative
    x_amp = -(amp_score - 3.0) / 2.0      # high amp awareness also pushes X negative
    x_likert = (x_filter + x_amp) / 2.0

    # Y-axis: high emission = positive Y (emits), high absorption = negative Y (absorbs)
    y_emit = (emit_score - 3.0) / 2.0   # high emission pushes Y positive
    y_abs = -(abs_score - 3.0) / 2.0    # high absorption pushes Y negative
    y_likert = (y_emit + y_abs) / 2.0

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

    # Map scenario votes to X, Y nudges
    # Transmuter: -X, +Y; Absorber: -X, -Y; Magnifier: +X, +Y; Extractor: +X, -Y; Conduit: 0, 0
    if total_scenario_votes > 0:
        scenario_x = (
            archetype_votes["magnifier"] + archetype_votes["extractor"]
            - archetype_votes["transmuter"] - archetype_votes["absorber"]
        ) / total_scenario_votes
        scenario_y = (
            archetype_votes["transmuter"] + archetype_votes["magnifier"]
            - archetype_votes["absorber"] - archetype_votes["extractor"]
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

    Quadrant layout:
        (-X, +Y) = Transmuter  |  (+X, +Y) = Magnifier
        (-X, -Y) = Absorber    |  (+X, -Y) = Extractor
        (center)  = Conduit
    """
    # Check for Conduit (near center)
    if abs(x) <= CONDUIT_THRESHOLD and abs(y) <= CONDUIT_THRESHOLD:
        return "conduit"

    # Determine quadrant
    if x <= 0 and y >= 0:
        return "transmuter"
    elif x > 0 and y >= 0:
        return "magnifier"
    elif x <= 0 and y < 0:
        return "absorber"
    else:
        return "extractor"
