"""Pure leverage computation module — no DB, no side effects.

Implements deterministic transmutation-leverage gap ranking and practice
linkage validation. This is the server-side source of truth for ranking;
the LLM authors narrative only.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Bounded set of valid transmutation operations (ADR-2: no stringly-typed ops).
TRANSMUTATION_OPERATIONS: set[str] = {"filtering", "amplification", "conduit", "none"}

# Base alignment impact for non-axis entries (Conduit Recognition + all other dims).
_BASE_ALIGNMENT: float = 0.10

# Per-point axis impact for Transmutation Capacity sub-dimensions (derived from
# _calculate_quadrant: each 1-point change in a sub-dim score moves the axis by 0.25).
_PER_POINT_AXIS: float = 0.25

# Axis map: TC sub-dimension → (axis, direction, per_point_impact, operation)
# "raise" means headroom = (5 - score) / 4; "lower" means headroom = (score - 1) / 4
_TC_AXIS_MAP: dict[str, dict[str, Any]] = {
    "Deprivation Filtering": {
        "axis": "y",
        "direction": "raise",
        "per_point_impact": _PER_POINT_AXIS,
        "operation": "filtering",
    },
    "Amplification Awareness": {
        "axis": "y",
        "direction": "raise",
        "per_point_impact": _PER_POINT_AXIS,
        "operation": "filtering",
    },
    "Fulfillment Emission": {
        "axis": "x",
        "direction": "raise",
        "per_point_impact": _PER_POINT_AXIS,
        "operation": "amplification",
    },
    "Absorption Patterns": {
        "axis": "x",
        "direction": "lower",
        "per_point_impact": _PER_POINT_AXIS,
        "operation": "amplification",
    },
    "Conduit Recognition": {
        "axis": None,
        "direction": "raise",
        "per_point_impact": _BASE_ALIGNMENT,
        "operation": "conduit",
    },
}


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def rank_transmutation_gaps(
    scores: dict[str, Any],
    *,
    top_n: int | None = None,
) -> list[dict]:
    """Rank transmutation gaps by leverage — not raw lowest score.

    Scores are dimension-level averages on the 1-5 Likert scale (the shape
    returned by scoring_engine._score_likert_by_dimension or profile_snapshots).
    Expects either ``scores["Transmutation Capacity"]["sub_dimensions"]`` for
    TC sub-dimension breakdown, or falls back to dimension-level only.

    The leverage formula:
      headroom  = (5 - score) / 4  for "raise" entries
                  (score - 1) / 4  for "lower" entries
      axis_deficit = clamp((1 - axis_value) / 2, 0, 1)  for axis entries
                     0                                     for non-axis entries
      leverage = headroom * per_point_impact * (0.5 + axis_deficit)

    Stable sort: leverage desc, then (-headroom, dimension, sub_dimension).

    Args:
        scores: Dict mapping dimension name → {score: float, sub_dimensions: {...}}.
        top_n:  If given, return only the top-N entries.

    Returns:
        List of dicts: {dimension, sub_dimension, score, headroom, leverage,
                        operation, rationale}.
    """
    # Import here to keep module pure (no top-level circular dep concern);
    # scoring_engine is part of the same package.
    from agents.transmutation.scoring_engine import _calculate_quadrant  # type: ignore[import]

    # Derive axis values from current scores.
    # Call with empty scenario_responses and qb=None — qb is unused inside the
    # function body when only dim_scores is needed (verified scoring_engine.py).
    quadrant = _calculate_quadrant(scores, {}, None)
    axis_x: float = quadrant.get("x", 0.0)  # A-axis (Amplification)
    axis_y: float = quadrant.get("y", 0.0)  # F-axis (Filtering)

    entries: list[dict] = []

    tc_data = scores.get("Transmutation Capacity", {})
    tc_sub_dims = tc_data.get("sub_dimensions", {})

    # 1. Transmutation Capacity sub-dimensions (axis-aware)
    for sub_dim, mapping in _TC_AXIS_MAP.items():
        sub_score = tc_sub_dims.get(sub_dim, {}).get("score")
        if sub_score is None:
            # Fall back to TC dimension score if sub-dim missing.
            sub_score = tc_data.get("score", 3.0)

        direction: str = mapping["direction"]
        if direction == "raise":
            headroom = (5.0 - sub_score) / 4.0
        else:  # "lower"
            headroom = (sub_score - 1.0) / 4.0

        axis = mapping["axis"]
        if axis == "x":
            axis_deficit = _clamp((1.0 - axis_x) / 2.0, 0.0, 1.0)
        elif axis == "y":
            axis_deficit = _clamp((1.0 - axis_y) / 2.0, 0.0, 1.0)
        else:
            axis_deficit = 0.0

        per_point_impact: float = mapping["per_point_impact"]
        leverage = headroom * per_point_impact * (0.5 + axis_deficit)

        rationale = (
            f"TC sub-dimension '{sub_dim}': score={sub_score:.2f}, "
            f"headroom={headroom:.3f}, axis_deficit={axis_deficit:.3f}, "
            f"leverage={leverage:.4f}"
        )

        entries.append({
            "dimension": "Transmutation Capacity",
            "sub_dimension": sub_dim,
            "score": sub_score,
            "headroom": headroom,
            "leverage": leverage,
            "operation": mapping["operation"],
            "rationale": rationale,
        })

    # 2. All other dimensions (non-axis, BASE_ALIGNMENT, "none" operation)
    for dim, dim_data in scores.items():
        if dim == "Transmutation Capacity":
            continue  # handled above
        dim_score = dim_data.get("score", 3.0) if isinstance(dim_data, dict) else float(dim_data)
        headroom = (5.0 - dim_score) / 4.0
        leverage = headroom * _BASE_ALIGNMENT * 0.5  # axis_deficit=0

        rationale = (
            f"Dimension '{dim}': score={dim_score:.2f}, "
            f"headroom={headroom:.3f}, leverage={leverage:.4f}"
        )

        entries.append({
            "dimension": dim,
            "sub_dimension": None,
            "score": dim_score,
            "headroom": headroom,
            "leverage": leverage,
            "operation": "none",
            "rationale": rationale,
        })

    # Stable sort: leverage desc, tie-break by (-headroom, dimension, sub_dimension or "")
    entries.sort(
        key=lambda e: (
            -e["leverage"],
            -e["headroom"],
            e["dimension"],
            e["sub_dimension"] or "",
        )
    )

    if top_n is not None:
        entries = entries[:top_n]

    return entries


def validate_practice_linkage(
    dimension: str | None,
    sub_dimension: str | None,
    transmutation_operation: str | None,
    dimensions_index: dict[str, list[str]],
) -> list[str]:
    """Validate practice linkage fields against the taxonomy and operation enum.

    Args:
        dimension:               Dimension name to link (may be None).
        sub_dimension:           Sub-dimension name (may be None).
        transmutation_operation: Operation name from TRANSMUTATION_OPERATIONS (may be None).
        dimensions_index:        Mapping of dimension → [sub_dimensions] from QuestionBank.

    Returns:
        List of human-readable error strings. Empty list means the linkage is valid.
    """
    errors: list[str] = []

    # If sub_dimension or operation provided, dimension is required.
    if (sub_dimension or transmutation_operation) and not dimension:
        errors.append(
            "dimension is required when sub_dimension or transmutation_operation is provided"
        )
        # Cannot validate further without a dimension.
        return errors

    if dimension is not None:
        if dimension not in dimensions_index:
            errors.append(
                f"Unknown dimension '{dimension}'. "
                f"Valid dimensions: {sorted(dimensions_index.keys())}"
            )
        elif sub_dimension is not None:
            valid_subs = dimensions_index[dimension]
            if sub_dimension not in valid_subs:
                errors.append(
                    f"Unknown sub_dimension '{sub_dimension}' for dimension '{dimension}'. "
                    f"Valid sub_dimensions: {sorted(valid_subs)}"
                )

    if transmutation_operation is not None:
        if transmutation_operation not in TRANSMUTATION_OPERATIONS:
            errors.append(
                f"Unknown transmutation_operation '{transmutation_operation}'. "
                f"Valid operations: {sorted(TRANSMUTATION_OPERATIONS)}"
            )

    return errors
