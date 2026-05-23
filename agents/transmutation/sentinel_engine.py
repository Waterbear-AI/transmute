"""Deterministic sentinel scoring and dimension selection logic.

Pure module — no DB access, no side effects. Mirrors the style of scoring_engine.py.

Implements the 70/30 reassessment blend, normalized shift flagging, staleness-based
sentinel selection, and force-inclusion rules.
"""

import logging

from agents.transmutation.scoring_engine import normalize_score

logger = logging.getLogger(__name__)


def compute_sentinel_scores(
    prior_scores: dict,
    fresh_scores: dict,
    targeted_dimensions: list,
    sentinel_dimensions: list,
    *,
    prior_weight: float = 0.7,
    new_weight: float = 0.3,
    shift_threshold_normalized: float = 15.0,
) -> dict:
    """Blend prior and fresh scores into a single deterministic result.

    Blending rules at BOTH dimension and sub-dimension granularity:
    - targeted  → final = fresh (100% new) for dim and every sub-dim
    - sentinel  → final = prior_weight·prior + new_weight·fresh for dim and each
                  sub-dim that has a fresh value; sub-dim with no fresh value carries prior
    - untouched → carry prior unchanged

    Shift flagging (targeted and sentinel only):
        shift_normalized = abs(normalize(new_signal) − normalize(prior))
        shift_flagged    = shift_normalized > shift_threshold_normalized
        sentinel dims that flag are added to flagged_for_full_reassessment.

    Args:
        prior_scores:    {dim: {"score": float, "sub_dimensions": {sd: {"score": float, ...}}}}
        fresh_scores:    Same shape; for sentinel dims, MUST be the sentinel-subset score
                         (from score_question_subset), not a whole-dim re-score.
        targeted_dimensions:  Dims that were fully re-answered this cycle.
        sentinel_dimensions:  Dims that received a lighter sentinel-subset refresh.
        prior_weight:    Weight for prior score in sentinel blend (default 0.7).
        new_weight:      Weight for fresh score in sentinel blend (default 0.3).
        shift_threshold_normalized: Flag when |Δ| > this value on the 0-100 scale.

    Returns:
        {
            "dimensions": {
                dim: {
                    "score": float,
                    "sub_dimensions": {sd: {"score": float, "source": str,
                                           "prior": float, "new": float|None}},
                    "source": "targeted"|"sentinel"|"carried",
                    "prior": float,
                    "new": float|None,
                    "shift_normalized": float,
                    "shift_flagged": bool,
                }
            },
            "flagged_for_full_reassessment": [dim, ...],
            "blend": {"prior_weight": float, "new_weight": float},
            "shift_threshold_normalized": float,
        }
    """
    targeted_set = set(targeted_dimensions)
    sentinel_set = set(sentinel_dimensions)

    dimensions_result: dict = {}
    flagged: list = []

    for dim, prior_dim_data in prior_scores.items():
        prior_dim_score = prior_dim_data.get("score", 0.0)
        prior_sub_dims = prior_dim_data.get("sub_dimensions", {})

        if dim in targeted_set:
            fresh_dim_data = fresh_scores.get(dim, {})
            new_dim_score = fresh_dim_data.get("score", prior_dim_score)

            # Blend sub-dimensions: 100% fresh
            sub_dim_results = _blend_sub_dimensions(
                prior_sub_dims,
                fresh_dim_data.get("sub_dimensions", {}),
                source="targeted",
                prior_weight=1.0,
                new_weight=0.0,
                is_targeted=True,
            )

            shift_normalized = abs(
                normalize_score(new_dim_score) - normalize_score(prior_dim_score)
            )
            shift_flagged = shift_normalized > shift_threshold_normalized

            dimensions_result[dim] = {
                "score": round(new_dim_score, 4),
                "sub_dimensions": sub_dim_results,
                "source": "targeted",
                "prior": prior_dim_score,
                "new": new_dim_score,
                "shift_normalized": round(shift_normalized, 4),
                "shift_flagged": shift_flagged,
            }

        elif dim in sentinel_set:
            fresh_dim_data = fresh_scores.get(dim, {})
            fresh_dim_score = fresh_dim_data.get("score")

            if fresh_dim_score is not None:
                new_dim_score = prior_weight * prior_dim_score + new_weight * fresh_dim_score
            else:
                # No fresh sentinel signal for this dim — carry prior
                new_dim_score = prior_dim_score
                fresh_dim_score = None

            sub_dim_results = _blend_sub_dimensions(
                prior_sub_dims,
                fresh_dim_data.get("sub_dimensions", {}),
                source="sentinel",
                prior_weight=prior_weight,
                new_weight=new_weight,
                is_targeted=False,
            )

            # Use the sentinel fresh value (or prior if no fresh) as "new_signal"
            new_signal = fresh_dim_score if fresh_dim_score is not None else prior_dim_score
            shift_normalized = abs(
                normalize_score(new_signal) - normalize_score(prior_dim_score)
            )
            shift_flagged = shift_normalized > shift_threshold_normalized

            if shift_flagged:
                flagged.append(dim)
                logger.warning(
                    "Sentinel dimension flagged for full reassessment: %s "
                    "(shift_normalized=%.2f > threshold=%.2f)",
                    dim, shift_normalized, shift_threshold_normalized,
                )

            dimensions_result[dim] = {
                "score": round(new_dim_score, 4),
                "sub_dimensions": sub_dim_results,
                "source": "sentinel",
                "prior": prior_dim_score,
                "new": fresh_dim_score,
                "shift_normalized": round(shift_normalized, 4),
                "shift_flagged": shift_flagged,
            }

        else:
            # Untouched — carry prior unchanged
            sub_dim_results = {
                sd: {
                    "score": sd_data.get("score", 0.0),
                    "source": "carried",
                    "prior": sd_data.get("score", 0.0),
                    "new": None,
                }
                for sd, sd_data in prior_sub_dims.items()
            }

            dimensions_result[dim] = {
                "score": prior_dim_score,
                "sub_dimensions": sub_dim_results,
                "source": "carried",
                "prior": prior_dim_score,
                "new": None,
                "shift_normalized": 0.0,
                "shift_flagged": False,
            }

    return {
        "dimensions": dimensions_result,
        "flagged_for_full_reassessment": flagged,
        "blend": {"prior_weight": prior_weight, "new_weight": new_weight},
        "shift_threshold_normalized": shift_threshold_normalized,
    }


def _blend_sub_dimensions(
    prior_sub_dims: dict,
    fresh_sub_dims: dict,
    source: str,
    prior_weight: float,
    new_weight: float,
    is_targeted: bool,
) -> dict:
    """Blend prior and fresh sub-dimension scores according to source rules.

    For targeted: final = fresh (if available), else carry prior.
    For sentinel: final = prior_weight·prior + new_weight·fresh (if fresh available),
                  else carry prior (no blend for sub-dim with no fresh value).
    """
    result: dict = {}

    # All sub-dims known from prior
    all_sub_dims = set(prior_sub_dims.keys()) | set(fresh_sub_dims.keys())

    for sd in all_sub_dims:
        prior_sd_data = prior_sub_dims.get(sd, {})
        prior_sd_score = prior_sd_data.get("score", 0.0)
        fresh_sd_data = fresh_sub_dims.get(sd)
        fresh_sd_score = fresh_sd_data.get("score") if fresh_sd_data else None

        if is_targeted:
            if fresh_sd_score is not None:
                final_score = fresh_sd_score
                new_val = fresh_sd_score
            else:
                final_score = prior_sd_score
                new_val = None
        else:
            # sentinel
            if fresh_sd_score is not None:
                final_score = prior_weight * prior_sd_score + new_weight * fresh_sd_score
                new_val = fresh_sd_score
            else:
                # No fresh value for this sub-dim — carry prior
                final_score = prior_sd_score
                new_val = None

        result[sd] = {
            "score": round(final_score, 4),
            "source": source if new_val is not None else "carried",
            "prior": prior_sd_score,
            "new": new_val,
        }

    return result


def select_sentinel_dimensions(
    staleness_by_dim: dict,
    prior_scores: dict,
    excluded: list,
    *,
    k: int = 3,
    force_include_cycles: int = 3,
) -> dict:
    """Select sentinel dimensions for the next reassessment cycle.

    Candidates are all dims NOT in `excluded` (the targeted dims).
    Selection priority:
    1. Force-include: any candidate with staleness >= force_include_cycles.
    2. Rank remaining candidates by:
       a. staleness DESC
       b. extremity DESC  (extremity = abs(normalize(prior_score) - 50); no prior → 0)
       c. dimension name ASC  (stable tie-breaker)
    3. selected = forced ∪ top-ranked up to max(k, len(forced)).

    Args:
        staleness_by_dim: {dim: int} mapping of staleness values.
        prior_scores:     {dim: {"score": float, ...}} — used for extremity calculation.
        excluded:         Dims to never select (targeted dims for this cycle).
        k:                Target number of sentinel dims (default 3).
        force_include_cycles: Staleness threshold for forced inclusion (default 3).

    Returns:
        {
            "selected":    [dim, ...],
            "forced":      [dim, ...],
            "reason_by_dim": {dim: "staleness=N, extremity=F.FF"},
        }
    """
    excluded_set = set(excluded)

    # All dimensions known from staleness map or prior scores
    all_dims = set(staleness_by_dim.keys()) | set(prior_scores.keys())
    candidates = [d for d in all_dims if d not in excluded_set]

    def _extremity(dim: str) -> float:
        prior_dim = prior_scores.get(dim, {})
        prior_score = prior_dim.get("score") if isinstance(prior_dim, dict) else None
        if prior_score is None:
            return 0.0
        return abs(normalize_score(prior_score) - 50.0)

    def _staleness(dim: str) -> int:
        return staleness_by_dim.get(dim, 0)

    # Identify forced dims
    forced = [d for d in candidates if _staleness(d) >= force_include_cycles]

    # Rank non-forced candidates
    non_forced = [d for d in candidates if d not in forced]
    non_forced_sorted = sorted(
        non_forced,
        key=lambda d: (-_staleness(d), -_extremity(d), d),
    )

    # Combine: forced first, then top-k non-forced
    target_count = max(k, len(forced))
    slots_remaining = target_count - len(forced)
    selected = forced + non_forced_sorted[:max(0, slots_remaining)]

    # Build reason strings
    reason_by_dim: dict = {}
    for dim in selected:
        staleness_val = _staleness(dim)
        extremity_val = _extremity(dim)
        if dim in forced:
            reason = f"staleness={staleness_val} (force-included), extremity={extremity_val:.2f}"
        else:
            reason = f"staleness={staleness_val}, extremity={extremity_val:.2f}"
        reason_by_dim[dim] = reason

    return {
        "selected": selected,
        "forced": forced,
        "reason_by_dim": reason_by_dim,
    }
