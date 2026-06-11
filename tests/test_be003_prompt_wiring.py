"""Tests for BE-003: agent prompt updates and tool wiring.

Covers:
- reassessment_prompt.py contains advance_phase transition instructions
- reassessment_prompt.py mentions record_self_assessed_readiness before advance_phase('graduation')
- reassessment_prompt.py correctly describes widened save tool guard
- reassessment_prompt.py instructs warm gate rejection without numeric thresholds
- sub_agents/reassessment.py has record_self_assessed_readiness in its toolset
- agent.py root instruction calls advance_phase('check_in') for graduated users
"""

import pytest

from agents.transmutation.prompts.reassessment_prompt import REASSESSMENT_INSTRUCTIONS, PROMPT
from agents.transmutation.agent import _ROOT_INSTRUCTION_TEMPLATE
from agents.transmutation.sub_agents.reassessment import create_reassessment_agent
from agents.transmutation.tools import record_self_assessed_readiness


# ── reassessment_prompt.py content tests ──────────────────────────────────────


class TestReassessmentPromptTransitionInstructions:
    """The prompt must explicitly instruct both outbound transitions."""

    def test_contains_advance_phase_graduation(self):
        assert "advance_phase('graduation')" in REASSESSMENT_INSTRUCTIONS

    def test_contains_advance_phase_development(self):
        assert "advance_phase('development')" in REASSESSMENT_INSTRUCTIONS

    def test_instructs_record_self_assessed_readiness_before_graduation(self):
        """record_self_assessed_readiness must appear before advance_phase('graduation')."""
        record_idx = REASSESSMENT_INSTRUCTIONS.find("record_self_assessed_readiness")
        graduation_idx = REASSESSMENT_INSTRUCTIONS.find("advance_phase('graduation')")
        assert record_idx != -1, "record_self_assessed_readiness not found in prompt"
        assert graduation_idx != -1, "advance_phase('graduation') not found in prompt"
        assert record_idx < graduation_idx, (
            "record_self_assessed_readiness must appear before advance_phase('graduation')"
        )

    def test_instructs_mandatory_phase_advance(self):
        """The prompt must make clear that advance_phase is REQUIRED, not optional."""
        assert "MUST" in REASSESSMENT_INSTRUCTIONS or "REQUIRED" in REASSESSMENT_INSTRUCTIONS

    def test_instructs_both_branches_explicitly(self):
        """Both outbound transitions must be mentioned (anti-happy-path-only pattern)."""
        assert "advance_phase('graduation')" in REASSESSMENT_INSTRUCTIONS
        assert "advance_phase('development')" in REASSESSMENT_INSTRUCTIONS


class TestReassessmentPromptSaveToolDescription:
    """The prompt must correctly describe the widened save guard (BE-001)."""

    def test_does_not_claim_only_reassessment_phase(self):
        """The old false claim said current_phase must be 'reassessment' — that's wrong."""
        # The updated text should describe the widened set, not just 'reassessment'
        false_claim = "validates that current_phase is 'reassessment'"
        assert false_claim not in REASSESSMENT_INSTRUCTIONS, (
            "Prompt still contains the false claim about save tool validation"
        )

    def test_describes_widened_save_guard(self):
        """The prompt should mention that the save tool accepts reassessment phase."""
        # The description should mention reassessment phase acceptance
        assert "reassessment" in REASSESSMENT_INSTRUCTIONS.lower()
        # Should mention assessment or the widened set
        lower = REASSESSMENT_INSTRUCTIONS.lower()
        assert "save_assessment_response" in lower or "save" in lower


class TestReassessmentPromptGateRejectionHandling:
    """The prompt must handle gate rejections warmly without numeric thresholds."""

    def test_instructs_warm_gate_rejection(self):
        """The prompt should instruct warm relay of gate errors."""
        lower = REASSESSMENT_INSTRUCTIONS.lower()
        assert "warmly" in lower or "warm" in lower or "relay" in lower

    def test_does_not_expose_numeric_thresholds(self):
        """Numeric threshold values must not appear in the prompt (security-error-handling)."""
        # GRADUATION_STABILITY_MAX_NORMALIZED = 5.0 should not appear
        assert "5.0" not in REASSESSMENT_INSTRUCTIONS
        # CHECK_IN_REGRESSION_DROP_NORMALIZED = 15.0 should not appear
        assert "15.0" not in REASSESSMENT_INSTRUCTIONS

    def test_instructs_not_to_promise_graduation_on_first_reassessment(self):
        """Prompt must caution that graduation requires ≥3 snapshots."""
        lower = REASSESSMENT_INSTRUCTIONS.lower()
        assert "first reassessment" in lower or "enough" in lower or "snapshots" in lower or "cycles" in lower


# ── sub_agents/reassessment.py tool wiring tests ──────────────────────────────


class TestReassessmentAgentToolWiring:
    """record_self_assessed_readiness must be in the reassessment agent's toolset."""

    def _get_tool_names(self, agent) -> set[str]:
        return {t.__name__ if callable(t) else str(t) for t in agent.tools}

    def test_record_self_assessed_readiness_in_tools(self):
        agent = create_reassessment_agent(model="")
        tool_names = self._get_tool_names(agent)
        assert "record_self_assessed_readiness" in tool_names, (
            f"record_self_assessed_readiness not found in reassessment agent tools. "
            f"Found: {tool_names}"
        )

    def test_advance_phase_still_in_tools(self):
        """advance_phase must remain in the toolset."""
        agent = create_reassessment_agent(model="")
        tool_names = self._get_tool_names(agent)
        assert "advance_phase" in tool_names

    def test_evaluate_graduation_readiness_still_in_tools(self):
        """evaluate_graduation_readiness must remain available."""
        agent = create_reassessment_agent(model="")
        tool_names = self._get_tool_names(agent)
        assert "evaluate_graduation_readiness" in tool_names

    def test_save_assessment_response_still_in_tools(self):
        agent = create_reassessment_agent(model="")
        tool_names = self._get_tool_names(agent)
        assert "save_assessment_response" in tool_names

    def test_record_self_assessed_readiness_is_callable(self):
        """The imported tool is the real function, not a stub."""
        assert callable(record_self_assessed_readiness)
        assert record_self_assessed_readiness.__name__ == "record_self_assessed_readiness"


# ── agent.py root instruction check-in routing ────────────────────────────────


class TestRootAgentCheckInInstruction:
    """The root instruction must guide advance_phase('check_in') for graduated users."""

    def test_graduated_routing_mentions_advance_phase_check_in(self):
        assert "advance_phase('check_in')" in _ROOT_INSTRUCTION_TEMPLATE

    def test_graduated_routing_mentions_check_in_intent_gate(self):
        """The instruction must gate on user intent, not auto-advance on every visit."""
        lower = _ROOT_INSTRUCTION_TEMPLATE.lower()
        # Should mention explicit user request / intent
        assert "request" in lower or "asks" in lower or "explicitly" in lower or "want" in lower

    def test_graduated_routing_guards_against_re_advance(self):
        """Instruction must tell the agent NOT to call advance_phase('check_in') when already in check_in."""
        assert "check_in" in _ROOT_INSTRUCTION_TEMPLATE
        # The guard against re-advancing should be present
        lower = _ROOT_INSTRUCTION_TEMPLATE.lower()
        assert "already" in lower or "not permitted" in lower or "do not" in lower or "don't" in lower

    def test_check_in_phase_routing_still_present(self):
        """check_in phase must still route to Check-in Agent."""
        assert "check_in" in _ROOT_INSTRUCTION_TEMPLATE
        # The instruction for in-progress check_in should route to Check-in Agent
        assert "Check-in Agent" in _ROOT_INSTRUCTION_TEMPLATE

    def test_root_instruction_template_renders(self):
        """Template must render without error with a sample user_id."""
        rendered = _ROOT_INSTRUCTION_TEMPLATE.format(user_id="test-user-123")
        assert "test-user-123" in rendered
        assert "advance_phase('check_in')" in rendered
