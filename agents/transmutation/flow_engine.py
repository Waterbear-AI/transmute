"""Core mathematical computation engine for transmutation flow analysis.

Implements the v13 mathematical framework:
- Per-Maslow-level flow tracking (D+in, D+out, D-in, D-out)
- Filtering F = D-(in) - D-(out) and Amplification A = D+(out) - D+(in)
- Moral Work M = τF + A per level
- Weighted Total Moral Work W = Σ(wn × Mn)
- Moral Capital (C+) and Moral Debt (C-)
"""

from typing import Any

from models.moral_profile import FlowValues, LevelFlows, MoralLedgerEntry, MoralProfile

MASLOW_LEVELS = [
    "physiological",
    "safety",
    "belonging",
    "esteem",
    "self-actualization",
]

# Archetype-to-flow mapping:
# Each archetype contributes to D+/D- flows based on the v13 model.
# Transmuter: filters deprivation (D-in high, D-out low) and emits fulfillment (D+out high)
# Absorber: absorbs both (D-in high, D+in high)
# Magnifier: amplifies both (D-out high for deprivation, D+out high for fulfillment)
# Extractor: extracts fulfillment (D+in high, D-out high)
# Conduit: neutral pass-through (no flow contribution)
ARCHETYPE_FLOW_MAP: dict[str, dict[str, float]] = {
    "transmuter": {"d_plus_out": 1.0, "d_minus_in": 1.0},
    "absorber": {"d_plus_in": 1.0, "d_minus_in": 1.0},
    "magnifier": {"d_plus_out": 1.0, "d_minus_out": 1.0},
    "extractor": {"d_plus_in": 1.0, "d_minus_out": 1.0},
    "conduit": {},
}


def compute_flows_per_level(
    scenario_responses: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> dict[str, FlowValues]:
    """Compute D+/D- flow values per Maslow level from scenario responses.

    Groups scenarios by maslow_level, then aggregates archetype weights from
    chosen responses into D+in, D+out, D-in, D-out flows. Computes derived
    Filtering (F) and Amplification (A) values.

    Args:
        scenario_responses: Dict of {scenario_id: {choice: "a", quadrant_weight: {...}}}
        scenarios: List of scenario definitions from questions.json

    Returns:
        Dict mapping maslow level name to FlowValues.
    """
    # Build scenario lookup
    scenario_map = {s["id"]: s for s in scenarios}

    # Initialize per-level accumulators
    level_flows: dict[str, dict[str, float]] = {
        level: {"d_plus_in": 0.0, "d_plus_out": 0.0, "d_minus_in": 0.0, "d_minus_out": 0.0}
        for level in MASLOW_LEVELS
    }

    for scenario_id, response in scenario_responses.items():
        scenario = scenario_map.get(scenario_id)
        if not scenario:
            continue

        maslow_level = scenario.get("maslow_level")
        if not maslow_level or maslow_level not in level_flows:
            continue

        # Get quadrant weights from the response
        qw = response.get("quadrant_weight", {})

        # Map archetype weights to flow components
        for archetype, weight in qw.items():
            flow_map = ARCHETYPE_FLOW_MAP.get(archetype, {})
            for flow_key, multiplier in flow_map.items():
                level_flows[maslow_level][flow_key] += weight * multiplier

    # Convert to FlowValues with computed F and A
    result: dict[str, FlowValues] = {}
    for level in MASLOW_LEVELS:
        flows = level_flows[level]
        filtering = flows["d_minus_in"] - flows["d_minus_out"]
        amplification = flows["d_plus_out"] - flows["d_plus_in"]
        result[level] = FlowValues(
            d_plus_in=flows["d_plus_in"],
            d_plus_out=flows["d_plus_out"],
            d_minus_in=flows["d_minus_in"],
            d_minus_out=flows["d_minus_out"],
            filtering=filtering,
            amplification=amplification,
        )

    return result


def compute_moral_work(
    flows: dict[str, FlowValues],
    tau: float = 1.0,
) -> list[float]:
    """Compute Moral Work M = τF + A at each Maslow level.

    Args:
        flows: Dict mapping maslow level to FlowValues.
        tau: Asymmetry coefficient (default 1.0).

    Returns:
        List of 5 M values, one per Maslow level in canonical order.
    """
    return [
        tau * flows[level].filtering + flows[level].amplification
        if level in flows
        else 0.0
        for level in MASLOW_LEVELS
    ]


def compute_weighted_total(
    moral_work: list[float],
    weights: list[int | float] | None = None,
) -> float:
    """Compute Weighted Total Moral Work W = Σ(wn × Mn).

    Args:
        moral_work: List of M values per Maslow level.
        weights: Weights per level (default [5, 4, 3, 2, 1]).

    Returns:
        Scalar W value.
    """
    if weights is None:
        weights = [5, 4, 3, 2, 1]

    return sum(w * m for w, m in zip(weights, moral_work))


def compute_moral_capital_debt(
    flows: dict[str, FlowValues],
) -> MoralLedgerEntry:
    """Compute Moral Capital (C+) and Moral Debt (C-) from flow values.

    C+ accumulates from net positive transmutation (filtering + emission).
    C- accumulates from net negative patterns (amplification of deprivation + absorption).

    Args:
        flows: Dict mapping maslow level to FlowValues.

    Returns:
        MoralLedgerEntry with c_plus and c_minus values.
    """
    c_plus = 0.0
    c_minus = 0.0

    for flow in flows.values():
        # Positive contributions: filtering deprivation and amplifying fulfillment
        c_plus += max(0.0, flow.filtering) + max(0.0, flow.amplification)
        # Negative contributions: passing through deprivation and absorbing fulfillment
        c_minus += abs(min(0.0, flow.filtering)) + abs(min(0.0, flow.amplification))

    return MoralLedgerEntry(c_plus=c_plus, c_minus=c_minus)


def compute_full_profile(
    scenario_responses: dict[str, Any],
    scenarios: list[dict[str, Any]],
    tau: float = 1.0,
    weights: list[int | float] | None = None,
) -> MoralProfile:
    """Compute the complete moral profile from scenario responses.

    Orchestrates all computation steps and returns a full MoralProfile.

    Args:
        scenario_responses: Dict of scenario responses.
        scenarios: List of scenario definitions.
        tau: Asymmetry coefficient.
        weights: Maslow level weights.

    Returns:
        Complete MoralProfile with all computed values.
    """
    if weights is None:
        weights = [5, 4, 3, 2, 1]

    flows = compute_flows_per_level(scenario_responses, scenarios)
    moral_work = compute_moral_work(flows, tau)
    weighted_total = compute_weighted_total(moral_work, weights)
    ledger = compute_moral_capital_debt(flows)

    levels = [
        LevelFlows(level=level, flows=flows.get(level, FlowValues()))
        for level in MASLOW_LEVELS
    ]

    return MoralProfile(
        levels=levels,
        moral_work=moral_work,
        weighted_total=weighted_total,
        tau=tau,
        weights=list(weights),
        moral_capital=ledger.c_plus,
        moral_debt=ledger.c_minus,
    )
