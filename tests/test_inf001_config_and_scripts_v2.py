"""Tests for INF-001: v2 configuration keys, spider_chart labels, and the
seed_phase.py comprehension-answer lookup that replaced the hardcoded
_COMPREHENSION_FIRST_Q map.

Verifies:
- config.py/config.yaml expose MIN_ITEMS_PER_DIM, MIN_SCENARIOS, CONF_HIGH_TC,
  CONF_HIGH_SCEN, and the nested adaptive.{LOW_CUT,BORDERLINE_MARGIN,
  INCONSISTENT_RANGE} submodel with the spec-mandated defaults
- spider_chart.DIMENSION_LABELS covers every v2 dimension; TRANSMUTARIAN_DIMENSIONS
  reflects the v2 taxonomy (Transmutation Capacity only -- Flow was cut,
  Systemic folded into the non-transmutarian Systemic/Temporal deep-dive dim)
- scripts.seed_phase._first_comprehension_answer derives (id, correct_option)
  from QuestionBank/comprehension_checks.json instead of a hardcoded map, and
  returns None (not a KeyError/crash) for a dimension/category with no content
- seed_assessment's _ARCHETYPE_TC_SCORES no longer references the cut
  Conduit Recognition sub-dimension
"""

import config
from agents.transmutation.question_bank import get_question_bank
from agents.transmutation.spider_chart import DIMENSION_LABELS, TRANSMUTARIAN_DIMENSIONS
from scripts.seed_phase import _ARCHETYPE_TC_SCORES, _first_comprehension_answer


class TestTransmutationSettingsV2Keys:
    """New config.py/config.yaml keys for tiered-assessment thresholds."""

    def _settings(self):
        config._settings = None
        return config.get_settings()

    def test_min_items_per_dim_default(self):
        assert self._settings().transmutation.MIN_ITEMS_PER_DIM == 2

    def test_min_scenarios_default(self):
        assert self._settings().transmutation.MIN_SCENARIOS == 3

    def test_conf_high_tc_default(self):
        assert self._settings().transmutation.CONF_HIGH_TC == 6

    def test_conf_high_scen_default(self):
        assert self._settings().transmutation.CONF_HIGH_SCEN == 6

    def test_adaptive_submodel_defaults(self):
        adaptive = self._settings().transmutation.adaptive
        assert adaptive.LOW_CUT == 2.75
        assert adaptive.BORDERLINE_MARGIN == 0.5
        assert adaptive.INCONSISTENT_RANGE == 2.0

    def test_adaptive_is_its_own_pydantic_model(self):
        """adaptive is a nested submodel, not a plain dict -- attribute access works."""
        adaptive = self._settings().transmutation.adaptive
        assert hasattr(adaptive, "LOW_CUT")
        assert isinstance(adaptive.LOW_CUT, float)


class TestSpiderChartV2Labels:
    """DIMENSION_LABELS and TRANSMUTARIAN_DIMENSIONS for the v2 dimension set."""

    def test_every_v2_dimension_has_a_label(self):
        qb = get_question_bank()
        for dim in qb.get_dimensions():
            assert dim in DIMENSION_LABELS, f"{dim} missing from spider_chart.DIMENSION_LABELS"

    def test_no_stale_v1_dimension_labels(self):
        """Cut/renamed v1 dimension names should not linger in the label map."""
        for cut in ("Flow Awareness", "Spatial Awareness", "Environmental Awareness",
                    "Physical Awareness", "Cognitive Awareness", "Emotional Awareness",
                    "Social Awareness", "Systemic Awareness", "Temporal Awareness",
                    "Interoceptive Awareness"):
            assert cut not in DIMENSION_LABELS, f"stale v1 label {cut!r} should be removed"

    def test_transmutarian_dimensions_is_transmutation_capacity_only(self):
        """Flow was cut; Systemic folded into the non-transmutarian deep-dive dim."""
        assert TRANSMUTARIAN_DIMENSIONS == ("Transmutation Capacity",)

    def test_transmutarian_dimensions_are_all_real_dimensions(self):
        qb = get_question_bank()
        real_dims = set(qb.get_dimensions())
        for dim in TRANSMUTARIAN_DIMENSIONS:
            assert dim in real_dims


class TestFirstComprehensionAnswer:
    """Dynamic replacement for the old hardcoded _COMPREHENSION_FIRST_Q map."""

    def test_returns_id_and_correct_option_for_existing_content(self):
        qb = get_question_bank()
        result = _first_comprehension_answer(qb, "Transmutation Capacity", "what_this_means")
        assert result is not None
        qid, correct_option = result
        assert qid == "cc_tc_cat1_q1"
        assert correct_option == "c"

    def test_returns_none_for_dimension_with_no_content(self):
        """A v2-only dimension with no comprehension_checks.json entry yet
        returns None instead of raising -- comprehension_checks.json
        regeneration is a separate task; this must not crash the seeder."""
        qb = get_question_bank()
        result = _first_comprehension_answer(qb, "Self-Compassion", "what_this_means")
        assert result is None

    def test_returns_none_for_unknown_dimension(self):
        qb = get_question_bank()
        result = _first_comprehension_answer(qb, "Nonexistent Dimension", "what_this_means")
        assert result is None

    def test_matches_the_underlying_comprehension_question(self):
        """The returned id/correct_option must be internally consistent with
        the actual question dict the question bank holds."""
        qb = get_question_bank()
        qid, correct_option = _first_comprehension_answer(qb, "Meta-Cognitive Awareness", "your_score")
        q = qb.get_comprehension_question_by_id(qid)
        assert q is not None
        assert q["correct_option"] == correct_option


class TestArchetypeTcScoresV2:
    """_ARCHETYPE_TC_SCORES must match the v2 TC sub-dimension set (4, not 5)."""

    def test_conduit_recognition_removed(self):
        for archetype, sub_dims in _ARCHETYPE_TC_SCORES.items():
            assert "Conduit Recognition" not in sub_dims, (
                f"{archetype} still references cut sub-dimension Conduit Recognition"
            )

    def test_all_archetypes_cover_the_v2_tc_sub_dimensions(self):
        qb = get_question_bank()
        tc_sub_dims = set(qb.get_sub_dimensions("Transmutation Capacity"))
        assert tc_sub_dims == {
            "Deprivation Filtering", "Fulfillment Emission",
            "Amplification Awareness", "Absorption Patterns",
        }
        for archetype, sub_dims in _ARCHETYPE_TC_SCORES.items():
            assert set(sub_dims.keys()) == tc_sub_dims, (
                f"{archetype} sub-dimension keys {set(sub_dims.keys())} != "
                f"real v2 TC sub-dimensions {tc_sub_dims}"
            )
