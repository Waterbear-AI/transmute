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
    normalize_score,
    score_question_subset,
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


class TestNormalizeScore:
    """Unit tests for normalize_score: maps 1–5 Likert to 0–100."""

    def test_lo_maps_to_zero(self):
        assert normalize_score(1.0) == 0.0

    def test_hi_maps_to_hundred(self):
        assert normalize_score(5.0) == 100.0

    def test_midpoint_maps_to_fifty(self):
        assert normalize_score(3.0) == 50.0

    def test_custom_range(self):
        # normalize_score(5.0, lo=0.0, hi=10.0) → 50.0
        assert normalize_score(5.0, lo=0.0, hi=10.0) == 50.0

    def test_default_scale_quarter(self):
        # 2.0 on 1–5 → (2-1)/(5-1)*100 = 25.0
        assert normalize_score(2.0) == 25.0

    def test_default_scale_three_quarter(self):
        # 4.0 on 1–5 → (4-1)/(5-1)*100 = 75.0
        assert normalize_score(4.0) == 75.0


class TestScoreQuestionSubset:
    """Unit tests for score_question_subset with a mock QuestionBank."""

    @staticmethod
    def _make_qb(questions):
        """Build a minimal mock QuestionBank from a list of question dicts."""
        class MockQB:
            def __init__(self, qs):
                self._by_id = {q["id"]: q for q in qs}
                self.scale_types = {"agreement_5": {"points": 5}}

            def get_question_by_id(self, qid):
                return self._by_id.get(qid)

        return MockQB(questions)

    def test_basic_averaging(self):
        """Averages scores for a single dimension correctly."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "Attention", "reverse_scored": False, "scale_type": "agreement_5"},
            {"id": "q2", "dimension": "Focus", "sub_dimension": "Attention", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        responses = {"q1": {"score": 3}, "q2": {"score": 5}}
        result = score_question_subset(responses, ["q1", "q2"], qb)
        assert "Focus" in result
        assert result["Focus"]["score"] == 4.0
        assert result["Focus"]["answered"] == 2

    def test_ignores_questions_not_in_subset(self):
        """Only considers question IDs in the provided subset."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "Attention", "reverse_scored": False, "scale_type": "agreement_5"},
            {"id": "q2", "dimension": "Focus", "sub_dimension": "Attention", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        # q2 is answered but NOT in the sentinel subset
        responses = {"q1": {"score": 3}, "q2": {"score": 5}}
        result = score_question_subset(responses, ["q1"], qb)
        assert result["Focus"]["score"] == 3.0
        assert result["Focus"]["answered"] == 1

    def test_reverse_scoring_applied(self):
        """Reverse-scored questions use (points+1) - raw."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": True, "scale_type": "agreement_5"},
        ])
        responses = {"q1": {"score": 2}}
        result = score_question_subset(responses, ["q1"], qb)
        # (5+1) - 2 = 4
        assert result["Focus"]["score"] == 4.0

    def test_na_score_excluded(self):
        """Questions with score=None are treated as N/A and excluded."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            {"id": "q2", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        responses = {"q1": {"score": None}, "q2": {"score": 4}}
        result = score_question_subset(responses, ["q1", "q2"], qb)
        assert result["Focus"]["score"] == 4.0
        assert result["Focus"]["answered"] == 1

    def test_absent_dim_when_no_answered_question(self):
        """A dimension is absent when no sentinel questions are answered."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        # q1 not in responses at all
        result = score_question_subset({}, ["q1"], qb)
        assert "Focus" not in result

    def test_absent_dim_when_all_na(self):
        """A dimension is absent when all answered scores are None."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        result = score_question_subset({"q1": {"score": None}}, ["q1"], qb)
        assert "Focus" not in result

    def test_sub_dimension_averaging(self):
        """Sub-dimensions are averaged independently."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "Attention", "reverse_scored": False, "scale_type": "agreement_5"},
            {"id": "q2", "dimension": "Focus", "sub_dimension": "Clarity", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        responses = {"q1": {"score": 2}, "q2": {"score": 4}}
        result = score_question_subset(responses, ["q1", "q2"], qb)
        assert result["Focus"]["sub_dimensions"]["Attention"]["score"] == 2.0
        assert result["Focus"]["sub_dimensions"]["Clarity"]["score"] == 4.0

    def test_sub_dim_absent_when_no_answered(self):
        """Sub-dimension absent from result when no sentinel questions answered for it."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "Attention", "reverse_scored": False, "scale_type": "agreement_5"},
            {"id": "q2", "dimension": "Focus", "sub_dimension": "Clarity", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        # Only q1 answered; q2 is in subset but no response
        responses = {"q1": {"score": 3}}
        result = score_question_subset(responses, ["q1", "q2"], qb)
        assert "Attention" in result["Focus"]["sub_dimensions"]
        assert "Clarity" not in result["Focus"]["sub_dimensions"]

    def test_multiple_dimensions(self):
        """Multiple dimensions returned when questions span dimensions."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            {"id": "q2", "dimension": "Resilience", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        responses = {"q1": {"score": 3}, "q2": {"score": 5}}
        result = score_question_subset(responses, ["q1", "q2"], qb)
        assert "Focus" in result
        assert "Resilience" in result

    def test_unknown_question_id_ignored(self):
        """Question IDs not found in qb are silently ignored."""
        qb = self._make_qb([
            {"id": "q1", "dimension": "Focus", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
        ])
        responses = {"q1": {"score": 3}, "unknown": {"score": 5}}
        result = score_question_subset(responses, ["q1", "unknown"], qb)
        assert "Focus" in result
        assert result["Focus"]["answered"] == 1
