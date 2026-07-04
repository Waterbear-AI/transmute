"""Unit tests for scoring_engine axis convention and data repair.

Validates:
- _map_archetype uses v13 convention (x=A, y=F)
- _calculate_quadrant returns v13-aligned coordinates
- _enrich_scenario_responses backfills missing fields
"""

import pytest

import config
from agents.transmutation.scoring_engine import (
    _map_archetype,
    _calculate_quadrant,
    _enrich_scenario_responses,
    _score_likert_by_dimension,
    compute_early_transmute_result,
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


# ---------------------------------------------------------------------------
# _score_likert_by_dimension: MIN_ITEMS_PER_DIM sufficiency (BE-002)
# ---------------------------------------------------------------------------


class TestScoreLikertByDimensionSufficiency:
    """Sufficiency rule: insufficient = answered_count < MIN_ITEMS_PER_DIM (=2).

    Replaces the old na_pct > 0.2 rule. Scope is _score_likert_by_dimension
    ONLY -- score_question_subset (sentinel, tested above) is a separate
    function with its own single-item-scores-fine behavior and must not
    change (TestScoreQuestionSubset above continues to pass unmodified).
    """

    @staticmethod
    def _make_qb(dimension_questions: dict[str, list[dict]]):
        """Build a minimal mock QuestionBank supporting get_dimensions() and
        get_questions_by_dimension(), as _score_likert_by_dimension needs."""

        class MockQB:
            def __init__(self, dim_qs):
                self._dim_qs = dim_qs
                self.scale_types = {"agreement_5": {"points": 5}, "frequency_5": {"points": 5}}

            def get_dimensions(self):
                return list(self._dim_qs.keys())

            def get_questions_by_dimension(self, dim):
                return self._dim_qs.get(dim, [])

        return MockQB(dimension_questions)

    def test_one_answered_item_is_insufficient(self):
        """MIN_ITEMS_PER_DIM default is 2 -- exactly 1 answered is insufficient."""
        qb = self._make_qb({
            "Focus": [
                {"id": "q1", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q2", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q3", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            ],
        })
        responses = {"q1": {"score": 4}}
        result = _score_likert_by_dimension(responses, qb)
        assert result["Focus"]["answered"] == 1
        assert result["Focus"]["insufficient_data"] is True

    def test_two_answered_items_is_sufficient(self):
        """Exactly MIN_ITEMS_PER_DIM (2) answered is sufficient."""
        qb = self._make_qb({
            "Focus": [
                {"id": "q1", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q2", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q3", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            ],
        })
        responses = {"q1": {"score": 4}, "q2": {"score": 3}}
        result = _score_likert_by_dimension(responses, qb)
        assert result["Focus"]["answered"] == 2
        assert result["Focus"]["insufficient_data"] is False

    def test_zero_answered_items_is_insufficient(self):
        qb = self._make_qb({
            "Focus": [
                {"id": "q1", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            ],
        })
        result = _score_likert_by_dimension({}, qb)
        assert result["Focus"]["answered"] == 0
        assert result["Focus"]["insufficient_data"] is True

    def test_all_items_answered_is_sufficient(self):
        """A small dimension where every item is answered (>= MIN_ITEMS_PER_DIM)."""
        qb = self._make_qb({
            "Focus": [
                {"id": "q1", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q2", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            ],
        })
        responses = {"q1": {"score": 4}, "q2": {"score": 2}}
        result = _score_likert_by_dimension(responses, qb)
        assert result["Focus"]["insufficient_data"] is False

    def test_na_responses_do_not_count_as_answered(self):
        """A response present with score=None (explicit N/A) still counts as
        unanswered for the MIN_ITEMS_PER_DIM check, matching the old na_pct
        rule's treatment of N/A responses."""
        qb = self._make_qb({
            "Focus": [
                {"id": "q1", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q2", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                {"id": "q3", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
            ],
        })
        responses = {"q1": {"score": 4}, "q2": {"score": None}}
        result = _score_likert_by_dimension(responses, qb)
        assert result["Focus"]["answered"] == 1
        assert result["Focus"]["insufficient_data"] is True

    def test_high_na_pct_no_longer_makes_a_dimension_insufficient_alone(self):
        """The OLD na_pct > 0.2 rule would have flagged this (2/12 answered =
        83% N/A). The NEW rule only cares about the absolute answered count,
        so 2 answered items (>= MIN_ITEMS_PER_DIM) is sufficient even in a
        large dimension where most items are unanswered."""
        qb = self._make_qb({
            "BigDim": [
                {"id": f"q{i}", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"}
                for i in range(12)
            ],
        })
        responses = {"q0": {"score": 4}, "q1": {"score": 3}}
        result = _score_likert_by_dimension(responses, qb)
        assert result["BigDim"]["answered"] == 2
        assert result["BigDim"]["insufficient_data"] is False

    def test_respects_configured_min_items_per_dim(self):
        """The threshold is read from TransmutationSettings.MIN_ITEMS_PER_DIM,
        not hardcoded -- raising it should raise the sufficiency bar."""
        config._settings = None
        settings = config.get_settings()
        original = settings.transmutation.MIN_ITEMS_PER_DIM
        try:
            settings.transmutation.MIN_ITEMS_PER_DIM = 3
            qb = self._make_qb({
                "Focus": [
                    {"id": "q1", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                    {"id": "q2", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                    {"id": "q3", "sub_dimension": "gen", "reverse_scored": False, "scale_type": "agreement_5"},
                ],
            })
            responses = {"q1": {"score": 4}, "q2": {"score": 3}}  # 2 answered, now < 3
            result = _score_likert_by_dimension(responses, qb)
            assert result["Focus"]["insufficient_data"] is True
        finally:
            settings.transmutation.MIN_ITEMS_PER_DIM = original


# ---------------------------------------------------------------------------
# compute_early_transmute_result (BE-002)
# ---------------------------------------------------------------------------


def _tc_responses(n_answered: int) -> dict:
    """Build responses answering the first n_answered of the 8 real
    Transmutation Capacity items from the actual v2 question bank, each with
    a neutral-ish score. Uses the real QuestionBank (not a mock) since
    compute_early_transmute_result internally calls get_question_bank()."""
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    tc_items = qb.get_questions_by_dimension("Transmutation Capacity")
    responses = {}
    for item in tc_items[:n_answered]:
        responses[item["id"]] = {"score": 4}
    return responses


def _scenario_responses(n_answered: int) -> dict:
    """Build scenario_responses answering the first n_answered of the 10 real
    scenarios, each choosing option 'a' (transmuter-weighted in every
    scenario per data/questions.json)."""
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    scenarios = qb.get_all_scenarios()
    responses = {}
    for s in scenarios[:n_answered]:
        responses[s["id"]] = {"choice": "a"}
    return responses


class TestComputeEarlyTransmuteResult:
    """compute_early_transmute_result: wraps _calculate_quadrant for
    (x, y, archetype) but computes its own confidence + confidence_reason."""

    def test_returns_expected_dict_shape(self):
        result = compute_early_transmute_result(_tc_responses(8), _scenario_responses(6))
        assert result["event_type"] == "assessment.transmute_result"
        for key in ("archetype", "x", "y", "confidence", "confidence_reason"):
            assert key in result

    def test_high_confidence_at_conf_high_thresholds(self):
        """8/8 TC (>= CONF_HIGH_TC=6) + 6/10 scenarios (>= CONF_HIGH_SCEN=6) -> high."""
        result = compute_early_transmute_result(_tc_responses(8), _scenario_responses(6))
        assert result["confidence"] == "high"

    def test_medium_confidence_between_low_and_high_bands(self):
        """8 TC (>= CONF_HIGH_TC) but only 3 scenarios: >= MIN_SCENARIOS(3) so
        not low, but < CONF_HIGH_SCEN(6) so not high -> medium."""
        result = compute_early_transmute_result(_tc_responses(8), _scenario_responses(3))
        assert result["confidence"] == "medium"

    def test_low_confidence_with_one_tc_item(self):
        """1 TC answered (< MIN_ITEMS_PER_DIM=2) -> low regardless of scenarios."""
        result = compute_early_transmute_result(_tc_responses(1), _scenario_responses(6))
        assert result["confidence"] == "low"

    def test_low_confidence_with_zero_scenarios(self):
        """0 scenarios (< MIN_SCENARIOS=3) -> low even with full TC."""
        result = compute_early_transmute_result(_tc_responses(8), _scenario_responses(0))
        assert result["confidence"] == "low"

    def test_confidence_reason_is_a_nonempty_string(self):
        result = compute_early_transmute_result(_tc_responses(8), _scenario_responses(6))
        assert isinstance(result["confidence_reason"], str)
        assert len(result["confidence_reason"]) > 0

    def test_confidence_reason_differs_by_band(self):
        """The plain-language reason should not be identical across bands
        (Barnum mitigation -- it must actually communicate the real state)."""
        high = compute_early_transmute_result(_tc_responses(8), _scenario_responses(6))
        low = compute_early_transmute_result(_tc_responses(1), _scenario_responses(0))
        assert high["confidence_reason"] != low["confidence_reason"]

    def test_does_not_reuse_calculate_quadrant_confidence_field(self):
        """_calculate_quadrant's own confidence is scenario-count-only
        (>=3 votes -> high/medium) and lacks a reason -- this function must
        compute an independent confidence, not just forward that value."""
        # 6 scenarios each casting a 1.0 'a'=transmuter vote -> 6 total votes,
        # so _calculate_quadrant's OWN rule would call this "high" too, but
        # verify by using an input where the two rules diverge: 8 TC + exactly
        # 3 scenario votes needs _calculate_quadrant's total_scenario_votes>=3
        # (also high there) but THIS function's own CONF_HIGH_SCEN=6 threshold
        # is not met, so it must independently report "medium".
        result = compute_early_transmute_result(_tc_responses(8), _scenario_responses(3))
        assert result["confidence"] == "medium"

    def test_zero_scenarios_and_zero_tc_still_returns_event_type(self):
        """Even the worst-case empty input must not crash and must still
        carry event_type (mandatory -- drives the chat re-emit path)."""
        result = compute_early_transmute_result({}, {})
        assert result["event_type"] == "assessment.transmute_result"
        assert result["confidence"] == "low"

    def test_archetype_x_y_come_from_calculate_quadrant(self):
        """Sanity: the (x, y, archetype) triple matches what
        _calculate_quadrant computes for the same inputs (this function
        wraps it rather than reimplementing quadrant placement).

        compute_early_transmute_result runs _enrich_scenario_responses
        before _calculate_quadrant (scenario responses built with just
        {"choice": ...} have no quadrant_weight/maslow_level until enriched).
        The "expected" oracle call below must enrich its OWN separate
        scenario_responses dict the same way, or it will compare against an
        un-enriched _calculate_quadrant call that sees zero scenario votes
        and always reports x=0.0/y=0.0 regardless of archetype."""
        from agents.transmutation.question_bank import get_question_bank

        qb = get_question_bank()
        responses = _tc_responses(8)

        dim_scores = _score_likert_by_dimension(responses, qb)
        expected_scenario_responses = _enrich_scenario_responses(_scenario_responses(6), qb)
        expected_quadrant = _calculate_quadrant(dim_scores, expected_scenario_responses, qb)

        result = compute_early_transmute_result(responses, _scenario_responses(6))
        assert result["archetype"] == expected_quadrant["archetype"]
        assert result["x"] == expected_quadrant["x"]
        assert result["y"] == expected_quadrant["y"]
