from pydantic import BaseModel, Field


class FlowValues(BaseModel):
    """Per-level transmutation flow values (D+in, D+out, D-in, D-out, F, A)."""

    d_plus_in: float = 0.0
    d_plus_out: float = 0.0
    d_minus_in: float = 0.0
    d_minus_out: float = 0.0
    filtering: float = 0.0
    amplification: float = 0.0


class LevelFlows(BaseModel):
    """Combines a Maslow level identifier with its computed flow values."""

    level: str
    flows: FlowValues = Field(default_factory=FlowValues)


class MoralProfile(BaseModel):
    """Encapsulates the full moral profile: M vector, W, tau, weights, and per-level flows."""

    levels: list[LevelFlows] = Field(default_factory=list)
    moral_work: list[float] = Field(default_factory=list)
    weighted_total: float = 0.0
    tau: float = 1.0
    weights: list[float] = Field(default_factory=lambda: [5, 4, 3, 2, 1])
    moral_capital: float = 0.0
    moral_debt: float = 0.0


class MoralLedgerEntry(BaseModel):
    """Entry for Moral Capital (C+) and Moral Debt (C-) tracking."""

    c_plus: float = 0.0
    c_minus: float = 0.0
