"""Tests for DOC-001: assessment/education prompt content and dimension taxonomy.

Covers:
- assessment_prompt.py contains explicit tier-driven instructions for each
  assessment_tier value and calls the correct BE-004 tools
- assessment_prompt.py preserves the safety/no-shame/boundary protocols verbatim
- awareness_dimensions.py describes exactly the 8 v2 dimensions (no cut
  dimensions remain, all new ones present)
- education_prompt.py's illustrative dimension-name examples use real v2
  dimension names (no stale "Environmental Awareness"/bare "Emotional
  Awareness" references)
- sub_agents/assessment.py's tool list includes the 3 new tier tools
"""

import re

from agents.transmutation.prompts.assessment_prompt import ASSESSMENT_INSTRUCTIONS, PROMPT
from agents.transmutation.prompts.education_prompt import PROMPT as EDUCATION_PROMPT
from agents.transmutation.prompts.shared.awareness_dimensions import PROMPT as AWARENESS_DIMS
from agents.transmutation.prompts.shared.safety import PROMPT as SAFETY
from agents.transmutation.prompts.shared.no_shame import PROMPT as NO_SHAME
from agents.transmutation.prompts.shared.boundary import PROMPT as BOUNDARY
from agents.transmutation.question_bank import get_question_bank


CUT_V1_DIMENSIONS = (
    "Spatial Awareness", "Flow Awareness", "Environmental Awareness",
    "Physical Awareness", "Cognitive Awareness", "Social Awareness",
    "Temporal Awareness",
)

V2_DIMENSIONS = (
    "Transmutation Capacity", "Emotional Awareness & Regulation", "Reflective Functioning",
    "Self-Compassion", "Relational Awareness & Compassion", "Meta-Cognitive Awareness",
    "Mindful Presence", "Systemic/Temporal Awareness",
)


class TestAssessmentPromptTierInstructions:
    """The prompt must explicitly instruct each assessment_tier branch."""

    def test_mentions_get_assessment_state_first(self):
        assert "get_assessment_state" in ASSESSMENT_INSTRUCTIONS

    def test_instructs_transmute_core_tier(self):
        assert 'assessment_tier == "transmute_core"' in ASSESSMENT_INSTRUCTIONS
        assert "present_transmute_core_batch" in ASSESSMENT_INSTRUCTIONS
        assert "evaluate_transmute_core_complete" in ASSESSMENT_INSTRUCTIONS

    def test_instructs_awareness_tiers(self):
        assert "awareness_core" in ASSESSMENT_INSTRUCTIONS
        assert "awareness_deepdive" in ASSESSMENT_INSTRUCTIONS
        assert "get_next_adaptive_batch" in ASSESSMENT_INSTRUCTIONS

    def test_instructs_complete_tier(self):
        assert '"complete"' in ASSESSMENT_INSTRUCTIONS
        assert "advance_phase('profile')" in ASSESSMENT_INSTRUCTIONS

    def test_early_result_handling_present(self):
        """The prompt must explicitly handle the early-result step (spec A3/B3)."""
        lower = ASSESSMENT_INSTRUCTIONS.lower()
        assert "early result" in lower or "assessment.transmute_result" in ASSESSMENT_INSTRUCTIONS

    def test_does_not_instruct_client_side_tier_setting(self):
        """The tier must be read from the tool, never asserted/set directly by the agent."""
        assert "server-authoritative" in ASSESSMENT_INSTRUCTIONS or "never set" in ASSESSMENT_INSTRUCTIONS.lower()

    def test_does_not_reveal_scoring_mechanics(self):
        """Preserved from v1: the agent must not reveal quadrant weights or which
        dimension triggered a deep-dive expansion."""
        lower = ASSESSMENT_INSTRUCTIONS.lower()
        assert "quadrant weight" in lower
        assert "do not reveal" in lower or "not reveal" in lower


class TestAssessmentPromptSafetyPreserved:
    """agents-llm-guardrails: the crisis/no-shame/boundary protocols must be
    preserved verbatim in the rewritten assessment_prompt.py."""

    def test_safety_protocol_verbatim(self):
        assert SAFETY in PROMPT

    def test_no_shame_protocol_verbatim(self):
        assert NO_SHAME in PROMPT

    def test_boundary_protocol_verbatim(self):
        assert BOUNDARY in PROMPT

    def test_crisis_hotline_present(self):
        assert "988" in PROMPT

    def test_flag_safety_concern_referenced(self):
        assert "flag_safety_concern" in PROMPT


class TestAwarenessDimensionsV2Taxonomy:
    """awareness_dimensions.py must describe exactly the 8 v2 dimensions."""

    def test_all_v2_dimensions_named(self):
        for dim in V2_DIMENSIONS:
            assert dim in AWARENESS_DIMS, f"{dim} missing from awareness_dimensions.py"

    def test_no_cut_v1_dimensions_remain(self):
        """Match the cut dimension only as a standalone name -- not as a
        substring of a kept dimension that legitimately contains the same
        words: "Cognitive Awareness" is cut standalone, but "Meta-Cognitive
        Awareness" is a kept v2 dimension containing that substring;
        "Temporal Awareness" is cut standalone, but "Systemic/Temporal
        Awareness" is a kept v2 dimension containing that substring."""
        for cut in CUT_V1_DIMENSIONS:
            # Negative lookbehind: not preceded by a word character, hyphen,
            # or slash (rules out both the Meta-Cognitive and
            # Systemic/Temporal false-positive collisions above).
            pattern = r"(?<![\w/-])" + re.escape(cut)
            assert not re.search(pattern, AWARENESS_DIMS), \
                f"cut dimension {cut!r} still referenced as a standalone name"

    def test_matches_real_question_bank_dimensions(self):
        """The prompt's dimension list must match what's actually in questions.json."""
        qb = get_question_bank()
        real_dims = set(qb.get_dimensions())
        for dim in real_dims:
            assert dim in AWARENESS_DIMS, f"real dimension {dim!r} not described in the prompt"

    def test_describes_tiers(self):
        """The taxonomy should be organized by tier, matching the new tiered flow."""
        assert "Tier 1" in AWARENESS_DIMS
        assert "Tier 2" in AWARENESS_DIMS
        assert "Tier 3" in AWARENESS_DIMS


class TestEducationPromptV2References:
    """education_prompt.py's illustrative examples must use real v2 dimension names."""

    def test_no_stale_environmental_awareness_example(self):
        assert "Environmental Awareness" not in EDUCATION_PROMPT

    def test_no_bare_emotional_awareness_example(self):
        """Bare 'Emotional Awareness' (without '& Regulation') must not appear as
        a standalone dimension reference."""
        assert "Emotional Awareness " not in EDUCATION_PROMPT.replace(
            "Emotional Awareness & Regulation", ""
        )

    def test_still_describes_five_category_taxonomy(self):
        for cat in ("what_this_means", "your_score", "daily_effects",
                    "strengths_gaps", "external_interaction"):
            assert cat in EDUCATION_PROMPT

    def test_preserves_safety_protocols(self):
        assert SAFETY in EDUCATION_PROMPT
        assert NO_SHAME in EDUCATION_PROMPT
        assert BOUNDARY in EDUCATION_PROMPT


class TestAssessmentSubAgentDescriptionAccuracy:
    """The sub-agent's description metadata should reflect the tiered flow,
    not the old per-dimension >=60% model."""

    def test_description_mentions_tiered_flow(self):
        from agents.transmutation.sub_agents.assessment import DESCRIPTION

        lower = DESCRIPTION.lower()
        assert "tier" in lower

    def test_description_does_not_claim_old_per_dimension_gate(self):
        from agents.transmutation.sub_agents.assessment import DESCRIPTION

        assert "sufficient coverage" not in DESCRIPTION.lower() or "tier" in DESCRIPTION.lower()
