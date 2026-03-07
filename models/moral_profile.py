"""Pydantic models for the v13 transmutation flow mathematical framework.

These models represent the computed moral profile derived from scenario
responses. The flow computation pipeline is:

  scenario responses → per-level D+/D- flows → F, A → M = τF + A → W = Σ(wn × Mn)

All models are serializable to/from JSON for storage in the profile_snapshots
table's flow_data column.
"""

from pydantic import BaseModel, Field


class FlowValues(BaseModel):
    """Raw and derived flow values at a single Maslow level.

    Raw flows track fulfillment (D+) and deprivation (D-) movement:
      - d_plus_in / d_plus_out: fulfillment flowing in/out
      - d_minus_in / d_minus_out: deprivation flowing in/out

    Derived values computed from raw flows:
      - filtering (F): D-(in) - D-(out) — how much deprivation is absorbed
      - amplification (A): D+(out) - D+(in) — how much fulfillment is generated
    """

    d_plus_in: float = 0.0
    d_plus_out: float = 0.0
    d_minus_in: float = 0.0
    d_minus_out: float = 0.0
    filtering: float = 0.0
    amplification: float = 0.0


class LevelFlows(BaseModel):
    """Pairs a Maslow hierarchy level with its computed flow values.

    The five canonical levels are: physiological, safety, belonging,
    esteem, self-actualization.
    """

    level: str
    flows: FlowValues = Field(default_factory=FlowValues)


class MoralProfile(BaseModel):
    """Complete moral profile produced by the flow computation pipeline.

    Attributes:
        levels: Per-Maslow-level flow breakdowns (5 entries).
        moral_work: The M vector — M[n] = τ * F[n] + A[n] for each level.
        weighted_total: Scalar W = Σ(w[n] × M[n]), the overall moral work score.
        tau: Asymmetry coefficient used in the M calculation.
        weights: Per-level weights used in the W calculation (default [5,4,3,2,1]).
        moral_capital: C+ — accumulated positive transmutation value.
        moral_debt: C- — accumulated negative transmutation value.
    """

    levels: list[LevelFlows] = Field(default_factory=list)
    moral_work: list[float] = Field(default_factory=list)
    weighted_total: float = 0.0
    tau: float = 1.0
    weights: list[float] = Field(default_factory=lambda: [5, 4, 3, 2, 1])
    moral_capital: float = 0.0
    moral_debt: float = 0.0


class MoralLedgerEntry(BaseModel):
    """Moral Capital (C+) and Moral Debt (C-) for a single profile snapshot.

    Persisted to the moral_ledger table and linked to a profile_snapshots row.
    C+ accumulates from positive filtering and amplification; C- from negative.
    """

    c_plus: float = 0.0
    c_minus: float = 0.0
