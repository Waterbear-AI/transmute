"""Unit tests for scoring_engine axis convention and data repair.

Validates:
- _map_archetype uses v13 convention (x=A, y=F)
- _calculate_quadrant returns v13-aligned coordinates
- _enrich_scenario_responses backfills missing fields
"""

import pytest

from agents.transmutation.scoring_engine import (
    _map_archetype,
    _calculate_quadrant,
    _enrich_scenario_responses,
    CONDUIT_THRESHOLD,
)


class TestMapArchetypeV13Convention:
    """Verify _map_archetype uses v13 quadrant layout:
    (+A, +F) = Transmuter (top-right)
    (+A, -F) = Magnifier (bottom-right)
    (-A, +F) = Absorber (top-left)
    (-A, -F) = Extractor (bottom-left)
    (0,  0)  = Conduit (center)
    """

    def test_transmuter_positive_a_positive_f(self):
        assert _map_archetype(0.5, 0.5) == "transmuter"

    def test_magnifier_positive_a_negative_f(self):
        assert _map_archetype(0.5, -0.5) == "magnifier"

    def test_absorber_negative_a_positive_f(self):
        assert _map_archetype(-0.5, 0.5) == "absorber"

    def test_extractor_negative_a_negative_f(self):
        assert _map_archetype(-0.5, -0.5) == "extractor"

    def test_conduit_at_origin(self):
        assert _map_archetype(0.0, 0.0) == "conduit"

    def test_conduit_within_threshold(self):
        assert _map_archetype(CONDUIT_THRESHOLD, CONDUIT_THRESHOLD) == "conduit"
        assert _map_archetype(-CONDUIT_THRESHOLD, -CONDUIT_THRESHOLD) == "conduit"

    def test_not_conduit_just_outside_threshold(self):
        val = CONDUIT_THRESHOLD + 0.01
        assert _map_archetype(val, val) != "conduit"

    def test_axes_boundary_positive_x_zero_y(self):
        # x > threshold, y = 0 → on the A axis, F=0 → Transmuter side (y >= 0)
        assert _map_archetype(0.5, 0.0) == "transmuter"

    def test_axes_boundary_zero_x_positive_y(self):
        # x = 0, y > threshold → on the F axis, A=0 → Transmuter side (x >= 0)
        assert _map_archetype(0.0, 0.5) == "transmuter"


class TestCalculateQuadrantAxisConvention:
    """Verify _calculate_quadrant returns x=A (amplification) and y=F (filtering)."""

    @staticmethod
    def _make_dim_scores(filt=3.0, emit=3.0, amp_aware=3.0, absorb=3.0):
        """Build dim_scores dict with specific Transmutation Capacity sub-dimensions."""
        return {
            "Transmutation Capacity": {
                "insufficient_data": False,
                "sub_dimensions": {
                    "Deprivation Filtering": {"score": filt},
                    "Fulfillment Emission": {"score": emit},
                    "Amplification Awareness": {"score": amp_aware},
                    "Absorption Patterns": {"score": absorb},
                },
            }
        }

    def test_high_emission_gives_positive_x(self):
        """High fulfillment emission → positive x (amplification axis)."""
        dim_scores = self._make_dim_scores(emit=5.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["x"] > 0

    def test_high_filtering_gives_positive_y(self):
        """High deprivation filtering → positive y (filtering axis)."""
        dim_scores = self._make_dim_scores(filt=5.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["y"] > 0

    def test_high_absorption_gives_negative_x(self):
        """High absorption → negative x (absorbs fulfillment)."""
        dim_scores = self._make_dim_scores(absorb=5.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["x"] < 0

    def test_all_neutral_gives_conduit(self):
        """All scores at 3.0 → origin → conduit."""
        dim_scores = self._make_dim_scores()
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["archetype"] == "conduit"
        assert result["x"] == 0.0
        assert result["y"] == 0.0

    def test_high_emit_high_filter_gives_transmuter(self):
        """High emission + high filtering → (+A, +F) = Transmuter."""
        dim_scores = self._make_dim_scores(filt=5.0, emit=5.0, amp_aware=5.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["archetype"] == "transmuter"
        assert result["x"] > 0  # +A
        assert result["y"] > 0  # +F

    def test_high_emit_low_filter_gives_magnifier(self):
        """High emission + low filtering → (+A, -F) = Magnifier."""
        dim_scores = self._make_dim_scores(filt=1.0, emit=5.0, amp_aware=1.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["archetype"] == "magnifier"
        assert result["x"] > 0  # +A
        assert result["y"] < 0  # -F

    def test_low_emit_high_filter_gives_absorber(self):
        """Low emission + high filtering → (-A, +F) = Absorber."""
        dim_scores = self._make_dim_scores(filt=5.0, emit=1.0, amp_aware=5.0, absorb=5.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["archetype"] == "absorber"
        assert result["x"] < 0  # -A
        assert result["y"] > 0  # +F

    def test_insufficient_data_returns_undetermined(self):
        dim_scores = {"Transmutation Capacity": {"insufficient_data": True}}
        result = _calculate_quadrant(dim_scores, {}, None)
        assert result["archetype"] == "undetermined"

    def test_scenario_votes_nudge_toward_archetype(self):
        """Scenario votes for transmuter should nudge toward +X, +Y."""
        dim_scores = self._make_dim_scores()  # neutral Likert
        scenario_responses = {
            "s1": {"quadrant_weight": {"transmuter": 1.0}},
            "s2": {"quadrant_weight": {"transmuter": 1.0}},
            "s3": {"quadrant_weight": {"transmuter": 1.0}},
        }
        result = _calculate_quadrant(dim_scores, scenario_responses, None)
        assert result["x"] > 0  # Transmuter → +A
        assert result["y"] > 0  # Transmuter → +F
        assert result["archetype"] == "transmuter"

    def test_values_clamped_to_range(self):
        """Output should always be in [-1, 1]."""
        dim_scores = self._make_dim_scores(filt=5.0, emit=5.0, amp_aware=5.0, absorb=1.0)
        result = _calculate_quadrant(dim_scores, {}, None)
        assert -1.0 <= result["x"] <= 1.0
        assert -1.0 <= result["y"] <= 1.0


class TestEnrichScenarioResponses:
    """Verify _enrich_scenario_responses backfills missing fields."""

    @staticmethod
    def _make_mock_qb():
        """Create a minimal mock question bank."""
        class MockQB:
            def get_scenario_by_id(self, scenario_id):
                scenarios = {
                    "sc-1": {
                        "id": "sc-1",
                        "maslow_level": "safety",
                        "choices": [
                            {"key": "a", "quadrant_weight": {"transmuter": 0.8, "absorber": 0.2}},
                            {"key": "b", "quadrant_weight": {"magnifier": 0.7, "extractor": 0.3}},
                        ],
                    },
                    "sc-2": {
                        "id": "sc-2",
                        "maslow_level": "esteem",
                        "choices": [
                            {"key": "a", "quadrant_weight": {"absorber": 1.0}},
                        ],
                    },
                }
                return scenarios.get(scenario_id)
        return MockQB()

    def test_backfills_missing_quadrant_weight(self):
        responses = {
            "sc-1": {"choice": "a"},  # Missing quadrant_weight
        }
        result = _enrich_scenario_responses(responses, self._make_mock_qb())
        assert result["sc-1"]["quadrant_weight"] == {"transmuter": 0.8, "absorber": 0.2}

    def test_backfills_missing_maslow_level(self):
        responses = {
            "sc-1": {"choice": "a", "quadrant_weight": {"transmuter": 1.0}},  # Missing maslow_level
        }
        result = _enrich_scenario_responses(responses, self._make_mock_qb())
        assert result["sc-1"]["maslow_level"] == "safety"

    def test_backfills_both_missing_fields(self):
        responses = {
            "sc-2": {"choice": "a"},  # Missing both
        }
        result = _enrich_scenario_responses(responses, self._make_mock_qb())
        assert result["sc-2"]["quadrant_weight"] == {"absorber": 1.0}
        assert result["sc-2"]["maslow_level"] == "esteem"

    def test_does_not_overwrite_existing_fields(self):
        responses = {
            "sc-1": {
                "choice": "a",
                "quadrant_weight": {"conduit": 1.0},  # Already has weight (different from lookup)
                "maslow_level": "belonging",  # Already has level (different from lookup)
            },
        }
        result = _enrich_scenario_responses(responses, self._make_mock_qb())
        assert result["sc-1"]["quadrant_weight"] == {"conduit": 1.0}  # Unchanged
        assert result["sc-1"]["maslow_level"] == "belonging"  # Unchanged

    def test_skips_unknown_scenario(self):
        responses = {
            "unknown-id": {"choice": "a"},
        }
        result = _enrich_scenario_responses(responses, self._make_mock_qb())
        assert "quadrant_weight" not in result["unknown-id"]

    def test_handles_empty_responses(self):
        result = _enrich_scenario_responses({}, self._make_mock_qb())
        assert result == {}

    def test_logs_warning_on_backfill(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _enrich_scenario_responses(
                {"sc-1": {"choice": "a"}},
                self._make_mock_qb(),
            )
        assert "Backfilled missing fields for scenario sc-1" in caplog.text
