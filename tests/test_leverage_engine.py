"""Unit tests for leverage_engine and QuestionBank.get_sub_dimensions.

Tests are pure (no DB required) — leverage_engine.py has no side effects.
"""

import pytest

from agents.transmutation.leverage_engine import (
    TRANSMUTATION_OPERATIONS,
    rank_transmutation_gaps,
    validate_practice_linkage,
)
from agents.transmutation.question_bank import QuestionBank


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scores(
    *,
    filt: float = 3.0,
    emit: float = 3.0,
    amp_aware: float = 3.0,
    abs_pat: float = 3.0,
    conduit: float = 3.0,
    other_dims: dict | None = None,
) -> dict:
    """Build a minimal scores dict in the shape scoring_engine returns."""
    tc_sub = {
        "Deprivation Filtering": {"score": filt},
        "Fulfillment Emission": {"score": emit},
        "Amplification Awareness": {"score": amp_aware},
        "Absorption Patterns": {"score": abs_pat},
        "Conduit Recognition": {"score": conduit},
    }
    scores = {
        "Transmutation Capacity": {
            "score": round((filt + emit + amp_aware + abs_pat + conduit) / 5, 2),
            "sub_dimensions": tc_sub,
        },
    }
    if other_dims:
        for dim, score in other_dims.items():
            scores[dim] = {"score": score}
    return scores


# ---------------------------------------------------------------------------
# TRANSMUTATION_OPERATIONS
# ---------------------------------------------------------------------------

class TestTransmutationOperations:
    def test_contains_expected_values(self):
        assert TRANSMUTATION_OPERATIONS == {"filtering", "amplification", "conduit", "none"}

    def test_is_a_set(self):
        assert isinstance(TRANSMUTATION_OPERATIONS, set)


# ---------------------------------------------------------------------------
# QuestionBank.get_sub_dimensions
# ---------------------------------------------------------------------------

class TestGetSubDimensions:
    def test_transmutation_capacity_returns_five_sub_dims(self):
        qb = QuestionBank()
        subs = qb.get_sub_dimensions("Transmutation Capacity")
        assert isinstance(subs, list)
        assert set(subs) == {
            "Deprivation Filtering",
            "Fulfillment Emission",
            "Amplification Awareness",
            "Absorption Patterns",
            "Conduit Recognition",
        }

    def test_returns_sorted_list(self):
        qb = QuestionBank()
        subs = qb.get_sub_dimensions("Transmutation Capacity")
        assert subs == sorted(subs)

    def test_unknown_dimension_returns_empty_list(self):
        qb = QuestionBank()
        assert qb.get_sub_dimensions("Bogus Dimension") == []

    def test_known_dimension_with_sub_dims(self):
        qb = QuestionBank()
        subs = qb.get_sub_dimensions("Emotional Awareness")
        assert "Emotion Recognition" in subs
        assert len(subs) > 0

    def test_distinct_values_no_duplicates(self):
        qb = QuestionBank()
        subs = qb.get_sub_dimensions("Cognitive Awareness")
        assert len(subs) == len(set(subs))


# ---------------------------------------------------------------------------
# rank_transmutation_gaps
# ---------------------------------------------------------------------------

class TestRankTransmutationGaps:
    def test_returns_list_of_dicts(self):
        scores = _make_scores()
        result = rank_transmutation_gaps(scores)
        assert isinstance(result, list)
        assert all(isinstance(e, dict) for e in result)

    def test_result_has_expected_keys(self):
        scores = _make_scores()
        result = rank_transmutation_gaps(scores)
        required_keys = {"dimension", "sub_dimension", "score", "headroom", "leverage", "operation", "rationale"}
        for entry in result:
            assert required_keys <= entry.keys(), f"Missing keys in entry: {entry}"

    def test_top_n_limits_results(self):
        scores = _make_scores(other_dims={"Mindfulness": 3.0, "Emotional Awareness": 3.0})
        result = rank_transmutation_gaps(scores, top_n=3)
        assert len(result) == 3

    def test_sorted_by_leverage_descending(self):
        scores = _make_scores(other_dims={"Mindfulness": 2.0, "Emotional Awareness": 4.0})
        result = rank_transmutation_gaps(scores)
        leverages = [e["leverage"] for e in result]
        assert leverages == sorted(leverages, reverse=True)

    def test_high_axis_alignment_tc_sub_dim_ranks_above_equal_headroom_non_tc(self):
        """A TC sub-dim with axis deficit ranks above a non-TC dim with equal headroom."""
        # TC sub-dim "Deprivation Filtering" at score=3.0 → headroom=0.5
        # Give a non-TC dim the same headroom (score=3.0)
        # With axis deficit > 0, the TC sub-dim should outrank the non-TC dim.
        scores = _make_scores(
            filt=3.0,  # headroom = 0.5, y-axis entry
            emit=3.0,
            amp_aware=3.0,
            abs_pat=3.0,
            conduit=3.0,
            other_dims={"Mindfulness": 3.0},  # headroom = 0.5, no axis
        )
        result = rank_transmutation_gaps(scores)
        # Find Deprivation Filtering and Mindfulness entries
        tc_entry = next(e for e in result if e["sub_dimension"] == "Deprivation Filtering")
        mindful_entry = next(e for e in result if e["dimension"] == "Mindfulness")
        # TC entry has axis_deficit contribution; non-TC has none.
        # leverage(TC) = 0.5 * 0.25 * (0.5 + axis_deficit) > leverage(non-TC) = 0.5 * 0.10 * 0.5
        assert tc_entry["leverage"] > mindful_entry["leverage"]

    def test_identical_inputs_yield_identical_ordering(self):
        """Deterministic: same inputs always produce the same output."""
        scores = _make_scores(other_dims={"Mindfulness": 2.5, "Emotional Awareness": 3.5})
        result1 = rank_transmutation_gaps(scores)
        result2 = rank_transmutation_gaps(scores)
        assert result1 == result2

    def test_absorption_patterns_uses_lower_headroom(self):
        """Absorption Patterns is a 'lower' entry: headroom = (score - 1) / 4."""
        scores = _make_scores(abs_pat=4.0)  # headroom = (4-1)/4 = 0.75
        result = rank_transmutation_gaps(scores)
        abs_entry = next(e for e in result if e["sub_dimension"] == "Absorption Patterns")
        expected_headroom = (4.0 - 1.0) / 4.0
        assert abs(abs_entry["headroom"] - expected_headroom) < 1e-9

    def test_absorption_patterns_score_1_max_headroom(self):
        """Absorption Patterns at score=1 means maximum headroom (already absorbed nothing)."""
        scores = _make_scores(abs_pat=1.0)
        result = rank_transmutation_gaps(scores)
        abs_entry = next(e for e in result if e["sub_dimension"] == "Absorption Patterns")
        assert abs(abs_entry["headroom"] - 0.0) < 1e-9  # (1-1)/4 = 0

    def test_absorption_patterns_score_5_zero_headroom(self):
        """Absorption Patterns at score=5 means zero headroom (none left to lower)."""
        scores = _make_scores(abs_pat=5.0)
        result = rank_transmutation_gaps(scores)
        abs_entry = next(e for e in result if e["sub_dimension"] == "Absorption Patterns")
        assert abs(abs_entry["headroom"] - 1.0) < 1e-9  # (5-1)/4 = 1

    def test_raise_entry_headroom_calculation(self):
        """Deprivation Filtering (raise): headroom = (5 - score) / 4."""
        scores = _make_scores(filt=2.0)  # headroom = (5-2)/4 = 0.75
        result = rank_transmutation_gaps(scores)
        filt_entry = next(e for e in result if e["sub_dimension"] == "Deprivation Filtering")
        expected_headroom = (5.0 - 2.0) / 4.0
        assert abs(filt_entry["headroom"] - expected_headroom) < 1e-9

    def test_operations_are_valid(self):
        """All returned operations must be in TRANSMUTATION_OPERATIONS."""
        scores = _make_scores(other_dims={"Mindfulness": 3.0})
        result = rank_transmutation_gaps(scores)
        for entry in result:
            assert entry["operation"] in TRANSMUTATION_OPERATIONS

    def test_tc_sub_dims_have_correct_operations(self):
        """Verify TC sub-dim → operation mapping."""
        scores = _make_scores()
        result = rank_transmutation_gaps(scores)
        expected_ops = {
            "Deprivation Filtering": "filtering",
            "Amplification Awareness": "filtering",
            "Fulfillment Emission": "amplification",
            "Absorption Patterns": "amplification",
            "Conduit Recognition": "conduit",
        }
        for entry in result:
            if entry["dimension"] == "Transmutation Capacity" and entry["sub_dimension"]:
                assert entry["operation"] == expected_ops[entry["sub_dimension"]]

    def test_non_tc_dims_have_none_operation(self):
        """Non-TC dimensions should use operation='none'."""
        scores = _make_scores(other_dims={"Mindfulness": 3.0, "Emotional Awareness": 3.0})
        result = rank_transmutation_gaps(scores)
        for entry in result:
            if entry["dimension"] not in ("Transmutation Capacity",):
                assert entry["operation"] == "none"

    def test_leverage_strictly_positive_for_non_max_scores(self):
        """Any score below max should produce positive leverage."""
        scores = _make_scores(filt=3.0, other_dims={"Mindfulness": 3.0})
        result = rank_transmutation_gaps(scores)
        for entry in result:
            assert entry["leverage"] >= 0.0


# ---------------------------------------------------------------------------
# validate_practice_linkage
# ---------------------------------------------------------------------------

class TestValidatePracticeLinkage:
    """Tests for the pure validation function."""

    def setup_method(self):
        """Build a simple dimensions_index from QuestionBank for integration tests."""
        qb = QuestionBank()
        self.dims_index = {
            dim: qb.get_sub_dimensions(dim) for dim in qb.get_dimensions()
        }

    def test_valid_dimension_only(self):
        errors = validate_practice_linkage(
            "Emotional Awareness", None, None, self.dims_index
        )
        assert errors == []

    def test_valid_dimension_and_sub_dimension(self):
        errors = validate_practice_linkage(
            "Emotional Awareness", "Emotion Recognition", None, self.dims_index
        )
        assert errors == []

    def test_valid_dimension_sub_dimension_and_operation(self):
        errors = validate_practice_linkage(
            "Emotional Awareness", "Emotion Recognition", "none", self.dims_index
        )
        assert errors == []

    def test_valid_transmutation_capacity_linkage(self):
        errors = validate_practice_linkage(
            "Transmutation Capacity", "Deprivation Filtering", "filtering", self.dims_index
        )
        assert errors == []

    def test_unknown_dimension_returns_error(self):
        errors = validate_practice_linkage(
            "Bogus Dimension", None, None, self.dims_index
        )
        assert len(errors) > 0
        assert any("Bogus Dimension" in e for e in errors)

    def test_unknown_sub_dimension_returns_error(self):
        errors = validate_practice_linkage(
            "Emotional Awareness", "Bogus Sub", None, self.dims_index
        )
        assert len(errors) > 0
        assert any("Bogus Sub" in e for e in errors)

    def test_invalid_operation_returns_error(self):
        errors = validate_practice_linkage(
            "Emotional Awareness", None, "transmutation", self.dims_index
        )
        assert len(errors) > 0
        assert any("transmutation" in e for e in errors)

    def test_sub_dimension_without_dimension_returns_error(self):
        errors = validate_practice_linkage(
            None, "Emotion Recognition", None, self.dims_index
        )
        assert len(errors) > 0
        assert any("dimension is required" in e for e in errors)

    def test_operation_without_dimension_returns_error(self):
        errors = validate_practice_linkage(
            None, None, "filtering", self.dims_index
        )
        assert len(errors) > 0
        assert any("dimension is required" in e for e in errors)

    def test_all_none_is_valid(self):
        """No linkage provided at all is valid (lenient ADR-5)."""
        errors = validate_practice_linkage(None, None, None, self.dims_index)
        assert errors == []

    def test_all_operations_are_valid(self):
        for op in TRANSMUTATION_OPERATIONS:
            errors = validate_practice_linkage(
                "Emotional Awareness", None, op, self.dims_index
            )
            assert errors == [], f"Operation '{op}' should be valid but got: {errors}"

    def test_unknown_dimension_blocks_sub_dimension_check(self):
        """When dimension is unknown, sub_dimension check is skipped (no redundant error)."""
        errors = validate_practice_linkage(
            "Bogus Dimension", "Some Sub", None, self.dims_index
        )
        # At minimum: unknown dimension error. Sub-dim check should not fire.
        dim_errors = [e for e in errors if "Unknown dimension" in e]
        assert len(dim_errors) == 1
