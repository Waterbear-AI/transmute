"""Unit tests for sentinel_engine: compute_sentinel_scores and select_sentinel_dimensions.

Validates:
- 70/30 blend for sentinel dims (dim + sub-dims)
- 100% fresh for targeted dims
- Prior carry for untouched dims
- Normalized shift flagging (>15 → flagged, <=15 → not flagged)
- select_sentinel_dimensions: staleness ranking, extremity tie-break, force-inclusion, excluded never selected
"""

import pytest

from agents.transmutation.sentinel_engine import (
    compute_sentinel_scores,
    select_sentinel_dimensions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dim(score: float, sub_dims: dict | None = None) -> dict:
    """Build a minimal dim-score dict."""
    sub_dimensions = {}
    if sub_dims:
        for sd_name, sd_score in sub_dims.items():
            sub_dimensions[sd_name] = {"score": sd_score, "answered": 1}
    return {"score": score, "sub_dimensions": sub_dimensions}


# ---------------------------------------------------------------------------
# TestComputeSentinelScores — blending
# ---------------------------------------------------------------------------

class TestComputeSentinelScoresBlending:
    """Verify blending rules at dimension and sub-dimension level."""

    def test_sentinel_blend_dim_score(self):
        """Sentinel dim: 0.7*3.0 + 0.3*4.0 = 3.3"""
        prior = {"Focus": _make_dim(3.0)}
        fresh = {"Focus": _make_dim(4.0)}
        result = compute_sentinel_scores(prior, fresh, [], ["Focus"])
        assert result["dimensions"]["Focus"]["score"] == pytest.approx(3.3, abs=1e-4)
        assert result["dimensions"]["Focus"]["source"] == "sentinel"

    def test_sentinel_blend_default_weights(self):
        """Default 0.7/0.3 blend applied correctly."""
        prior = {"Calm": _make_dim(2.0)}
        fresh = {"Calm": _make_dim(5.0)}
        result = compute_sentinel_scores(prior, fresh, [], ["Calm"])
        expected = 0.7 * 2.0 + 0.3 * 5.0  # 1.4 + 1.5 = 2.9
        assert result["dimensions"]["Calm"]["score"] == pytest.approx(expected, abs=1e-4)

    def test_targeted_dim_is_100_percent_fresh(self):
        """Targeted dim: final == fresh regardless of prior."""
        prior = {"Focus": _make_dim(2.0)}
        fresh = {"Focus": _make_dim(4.5)}
        result = compute_sentinel_scores(prior, fresh, ["Focus"], [])
        assert result["dimensions"]["Focus"]["score"] == pytest.approx(4.5, abs=1e-4)
        assert result["dimensions"]["Focus"]["source"] == "targeted"

    def test_untouched_dim_carries_prior(self):
        """Untouched dim: final == prior with source='carried'."""
        prior = {"Resilience": _make_dim(3.5)}
        fresh = {}
        result = compute_sentinel_scores(prior, fresh, [], [])
        assert result["dimensions"]["Resilience"]["score"] == pytest.approx(3.5, abs=1e-4)
        assert result["dimensions"]["Resilience"]["source"] == "carried"
        assert result["dimensions"]["Resilience"]["new"] is None

    def test_sentinel_sub_dim_blend(self):
        """Sentinel sub-dims with fresh values are blended 70/30."""
        prior = {"TC": _make_dim(3.0, {"Filtering": 3.0, "Emission": 2.5})}
        fresh = {"TC": _make_dim(4.0, {"Filtering": 4.0})}
        result = compute_sentinel_scores(prior, fresh, [], ["TC"])
        sub = result["dimensions"]["TC"]["sub_dimensions"]
        # Filtering has fresh value → blended
        assert sub["Filtering"]["score"] == pytest.approx(0.7 * 3.0 + 0.3 * 4.0, abs=1e-4)
        # Emission has no fresh value → carry prior
        assert sub["Emission"]["score"] == pytest.approx(2.5, abs=1e-4)
        assert sub["Emission"]["source"] == "carried"

    def test_sentinel_sub_dim_no_fresh_carries_prior(self):
        """Sub-dim with no fresh value carries prior (no blend)."""
        prior = {"X": _make_dim(3.0, {"A": 2.0, "B": 4.0})}
        fresh = {"X": _make_dim(4.0, {"A": 5.0})}  # B has no fresh
        result = compute_sentinel_scores(prior, fresh, [], ["X"])
        sub = result["dimensions"]["X"]["sub_dimensions"]
        assert sub["B"]["score"] == pytest.approx(4.0, abs=1e-4)  # prior carried
        assert sub["B"]["new"] is None

    def test_targeted_sub_dim_is_100_percent_fresh(self):
        """Targeted sub-dims use fresh values when available."""
        prior = {"Focus": _make_dim(3.0, {"Attention": 2.0})}
        fresh = {"Focus": _make_dim(4.0, {"Attention": 5.0})}
        result = compute_sentinel_scores(prior, fresh, ["Focus"], [])
        sub = result["dimensions"]["Focus"]["sub_dimensions"]
        assert sub["Attention"]["score"] == pytest.approx(5.0, abs=1e-4)
        assert sub["Attention"]["source"] == "targeted"

    def test_custom_blend_weights(self):
        """Custom prior/new weights respected."""
        prior = {"X": _make_dim(2.0)}
        fresh = {"X": _make_dim(4.0)}
        result = compute_sentinel_scores(
            prior, fresh, [], ["X"], prior_weight=0.5, new_weight=0.5
        )
        assert result["dimensions"]["X"]["score"] == pytest.approx(3.0, abs=1e-4)
        assert result["blend"]["prior_weight"] == 0.5
        assert result["blend"]["new_weight"] == 0.5

    def test_sentinel_dim_no_fresh_carries_prior(self):
        """Sentinel dim with no fresh signal at all carries prior."""
        prior = {"Ghost": _make_dim(3.0)}
        fresh = {}  # No fresh data for Ghost
        result = compute_sentinel_scores(prior, fresh, [], ["Ghost"])
        assert result["dimensions"]["Ghost"]["score"] == pytest.approx(3.0, abs=1e-4)
        assert result["dimensions"]["Ghost"]["new"] is None

    def test_all_dims_present_in_result(self):
        """All dims from prior_scores appear in result, regardless of category."""
        prior = {
            "A": _make_dim(3.0),
            "B": _make_dim(4.0),
            "C": _make_dim(2.5),
        }
        fresh = {"A": _make_dim(5.0)}
        result = compute_sentinel_scores(prior, fresh, ["A"], ["B"])
        assert set(result["dimensions"].keys()) == {"A", "B", "C"}

    def test_empty_prior_returns_empty(self):
        """Empty prior_scores → empty result with no flagged dims."""
        result = compute_sentinel_scores({}, {}, [], [])
        assert result["dimensions"] == {}
        assert result["flagged_for_full_reassessment"] == []


# ---------------------------------------------------------------------------
# TestComputeSentinelScoresShiftFlagging
# ---------------------------------------------------------------------------

class TestComputeSentinelScoresShiftFlagging:
    """Verify normalized shift calculation and flagging."""

    def test_shift_above_threshold_flags_sentinel(self):
        """shift_normalized > 15 on a sentinel dim → flagged."""
        # prior=1.0 → normalized 0; fresh=5.0 → normalized 100; shift=100 >> 15
        prior = {"Big": _make_dim(1.0)}
        fresh = {"Big": _make_dim(5.0)}
        result = compute_sentinel_scores(prior, fresh, [], ["Big"])
        dim = result["dimensions"]["Big"]
        assert dim["shift_flagged"] is True
        assert "Big" in result["flagged_for_full_reassessment"]

    def test_shift_at_threshold_not_flagged(self):
        """shift_normalized == 15 → NOT flagged (strict >)."""
        # Want shift_normalized = exactly 15.0
        # normalize(x) = (x-1)/4*100; shift = 15 → Δnorm = 15 → Δraw = 15*4/100 = 0.6
        # prior=3.0 (norm=50), fresh=3.6 (norm=65); shift=15.0
        prior = {"Edge": _make_dim(3.0)}
        fresh = {"Edge": _make_dim(3.6)}
        result = compute_sentinel_scores(prior, fresh, [], ["Edge"])
        dim = result["dimensions"]["Edge"]
        assert dim["shift_normalized"] == pytest.approx(15.0, abs=0.01)
        assert dim["shift_flagged"] is False
        assert "Edge" not in result["flagged_for_full_reassessment"]

    def test_shift_below_threshold_not_flagged(self):
        """shift_normalized < 15 → not flagged."""
        prior = {"Stable": _make_dim(3.0)}
        fresh = {"Stable": _make_dim(3.4)}  # shift = (3.4-3.0)/4*100 = 10 < 15
        result = compute_sentinel_scores(prior, fresh, [], ["Stable"])
        dim = result["dimensions"]["Stable"]
        assert dim["shift_flagged"] is False
        assert "Stable" not in result["flagged_for_full_reassessment"]

    def test_targeted_dim_flag_not_in_flagged_list(self):
        """Even if a targeted dim has large shift, it is NOT added to flagged_for_full_reassessment
        (flagging only applies to sentinel dims per spec)."""
        prior = {"Focus": _make_dim(1.0)}
        fresh = {"Focus": _make_dim(5.0)}
        result = compute_sentinel_scores(prior, fresh, ["Focus"], [])
        # Targeted dim: shift_flagged is set but NOT added to flagged_for_full_reassessment
        assert "Focus" not in result["flagged_for_full_reassessment"]

    def test_multiple_sentinel_dims_only_large_shift_flagged(self):
        """Only sentinel dims with shift > threshold appear in flagged list."""
        prior = {
            "Big": _make_dim(1.0),
            "Small": _make_dim(3.0),
        }
        fresh = {
            "Big": _make_dim(5.0),   # large shift
            "Small": _make_dim(3.2), # tiny shift
        }
        result = compute_sentinel_scores(prior, fresh, [], ["Big", "Small"])
        assert "Big" in result["flagged_for_full_reassessment"]
        assert "Small" not in result["flagged_for_full_reassessment"]

    def test_custom_threshold(self):
        """Custom shift_threshold_normalized respected."""
        prior = {"X": _make_dim(3.0)}
        fresh = {"X": _make_dim(3.4)}  # shift = 10
        result = compute_sentinel_scores(
            prior, fresh, [], ["X"], shift_threshold_normalized=5.0
        )
        assert result["dimensions"]["X"]["shift_flagged"] is True

    def test_carried_dim_has_zero_shift(self):
        """Untouched (carried) dim always has shift_normalized=0 and shift_flagged=False."""
        prior = {"Carried": _make_dim(4.0)}
        result = compute_sentinel_scores(prior, {}, [], [])
        dim = result["dimensions"]["Carried"]
        assert dim["shift_normalized"] == 0.0
        assert dim["shift_flagged"] is False

    def test_result_includes_blend_metadata(self):
        """Return dict includes blend weights and threshold."""
        result = compute_sentinel_scores({}, {}, [], [])
        assert result["blend"]["prior_weight"] == 0.7
        assert result["blend"]["new_weight"] == 0.3
        assert result["shift_threshold_normalized"] == 15.0


# ---------------------------------------------------------------------------
# TestSelectSentinelDimensions
# ---------------------------------------------------------------------------

class TestSelectSentinelDimensions:
    """Verify sentinel dimension selection: ranking, force-include, excluded."""

    def _prior(self, score: float) -> dict:
        return {"score": score, "sub_dimensions": {}}

    def test_basic_selection_top_k_by_staleness(self):
        """Returns top-k dims ranked by staleness desc."""
        staleness = {"A": 2, "B": 1, "C": 3}
        prior = {"A": self._prior(3.0), "B": self._prior(3.0), "C": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=2)
        # C(3) > A(2) > B(1) → top-2 = [C, A]
        assert set(result["selected"]) == {"C", "A"}

    def test_excluded_never_selected(self):
        """Excluded dims are never in selected."""
        staleness = {"A": 5, "B": 3, "C": 1}
        prior = {"A": self._prior(3.0), "B": self._prior(3.0), "C": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=["A"], k=2)
        assert "A" not in result["selected"]

    def test_force_include_staleness_gte_threshold(self):
        """Dims with staleness >= force_include_cycles are always included."""
        staleness = {"Stale": 3, "Fresh": 0}
        prior = {"Stale": self._prior(3.0), "Fresh": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=1)
        assert "Stale" in result["selected"]
        assert "Stale" in result["forced"]

    def test_force_include_beyond_k(self):
        """Force-included dims expand selection beyond k."""
        staleness = {"A": 3, "B": 3, "C": 3, "D": 1}
        prior = {d: self._prior(3.0) for d in staleness}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=2)
        # A, B, C all forced → selected >= 3 even though k=2
        assert set(result["forced"]) == {"A", "B", "C"}
        assert len(result["selected"]) >= 3

    def test_extremity_tiebreak(self):
        """Equal staleness → higher extremity selected first."""
        staleness = {"High": 1, "Low": 1}
        # High extremity: score=5.0 → normalize=100 → extremity=|100-50|=50
        # Low  extremity: score=3.0 → normalize=50  → extremity=|50-50|=0
        prior = {"High": self._prior(5.0), "Low": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=1)
        assert "High" in result["selected"]

    def test_name_alphabetical_tiebreak(self):
        """Equal staleness and extremity → alphabetical order (asc)."""
        staleness = {"Beta": 1, "Alpha": 1}
        prior = {"Beta": self._prior(3.0), "Alpha": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=1)
        assert "Alpha" in result["selected"]

    def test_no_prior_gives_extremity_zero(self):
        """Dim with no prior score has extremity=0."""
        staleness = {"NoPrior": 1}
        result = select_sentinel_dimensions(staleness, {}, excluded=[], k=1)
        assert "NoPrior" in result["selected"]
        assert "extremity=0.00" in result["reason_by_dim"]["NoPrior"]

    def test_reason_by_dim_populated(self):
        """reason_by_dim contains staleness and extremity info for each selected dim."""
        staleness = {"X": 2}
        prior = {"X": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=1)
        assert "X" in result["reason_by_dim"]
        assert "staleness=2" in result["reason_by_dim"]["X"]

    def test_empty_candidates_returns_empty(self):
        """No candidates (all excluded) → empty selected."""
        staleness = {"A": 5}
        prior = {"A": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=["A"], k=3)
        assert result["selected"] == []
        assert result["forced"] == []

    def test_selected_is_subset_of_candidates(self):
        """Selected dims are never in excluded."""
        staleness = {"A": 3, "B": 2, "C": 1}
        prior = {d: self._prior(3.0) for d in staleness}
        excluded = ["A"]
        result = select_sentinel_dimensions(staleness, prior, excluded=excluded, k=3)
        for dim in result["selected"]:
            assert dim not in excluded

    def test_force_include_flag_in_reason(self):
        """Force-included dims have 'force-included' in their reason."""
        staleness = {"Forced": 3}
        prior = {"Forced": self._prior(3.0)}
        result = select_sentinel_dimensions(staleness, prior, excluded=[], k=1)
        assert "force-included" in result["reason_by_dim"]["Forced"]
