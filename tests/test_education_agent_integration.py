"""Integration tests for Education Agent tool registration (BE-002).

Verifies that:
  - present_comprehension_question is registered in the education agent's tools list
  - record_comprehension_answer remains in the tools list
  - The education_prompt instructs the agent to call present_comprehension_question
    (not write questions as markdown)
  - The education_prompt reinforces record_comprehension_answer for user selections
"""

import pytest


class TestEducationAgentToolRegistration:
    """Verify the education agent is configured with the correct tools."""

    def _get_tool_names(self) -> list[str]:
        """Create the education agent and return its tool function names."""
        from agents.transmutation.sub_agents.education import create_education_agent
        agent = create_education_agent(model="")
        # LlmAgent stores tools as a list of callables
        return [t.__name__ for t in agent.tools]

    def test_present_comprehension_question_registered(self):
        """present_comprehension_question must be in the education agent tools."""
        tool_names = self._get_tool_names()
        assert "present_comprehension_question" in tool_names, (
            f"present_comprehension_question not found in tools: {tool_names}"
        )

    def test_record_comprehension_answer_registered(self):
        """record_comprehension_answer must still be in the education agent tools."""
        tool_names = self._get_tool_names()
        assert "record_comprehension_answer" in tool_names, (
            f"record_comprehension_answer not found in tools: {tool_names}"
        )

    def test_present_continue_prompt_registered(self):
        """present_continue_prompt must be in the education agent tools."""
        tool_names = self._get_tool_names()
        assert "present_continue_prompt" in tool_names, (
            f"present_continue_prompt not found in tools: {tool_names}"
        )

    def test_present_education_content_registered(self):
        """present_education_content must be in the education agent tools (BE-001)."""
        tool_names = self._get_tool_names()
        assert "present_education_content" in tool_names, (
            f"present_education_content not found in tools: {tool_names}"
        )

    def test_core_tools_present(self):
        """All eight expected tools are present — no accidental removals."""
        tool_names = self._get_tool_names()
        expected = {
            "get_user_profile",
            "get_education_progress",
            "present_comprehension_question",
            "present_education_content",
            "record_comprehension_answer",
            "present_continue_prompt",
            "advance_phase",
            "flag_safety_concern",
        }
        missing = expected - set(tool_names)
        assert not missing, f"Missing tools in education agent: {missing}"

    def test_no_extra_unexpected_tools(self):
        """The tool count matches expected — no accidental additions."""
        tool_names = self._get_tool_names()
        assert len(tool_names) == 8, (
            f"Expected 8 tools, got {len(tool_names)}: {tool_names}"
        )


class TestEducationPromptInstructions:
    """Verify the education prompt guides correct tool usage."""

    def _get_prompt(self) -> str:
        from agents.transmutation.prompts.education_prompt import EDUCATION_INSTRUCTIONS
        return EDUCATION_INSTRUCTIONS

    def test_prompt_instructs_use_of_present_comprehension_question(self):
        """The prompt must explicitly tell the agent to call present_comprehension_question."""
        prompt = self._get_prompt()
        assert "present_comprehension_question" in prompt, (
            "prompt does not mention present_comprehension_question"
        )

    def test_prompt_forbids_markdown_questions(self):
        """The prompt must instruct the agent NOT to write questions as markdown."""
        prompt = self._get_prompt()
        # The key phrase from the approved spec
        assert "Do NOT write the question or options as markdown text" in prompt, (
            "prompt does not forbid markdown question output"
        )

    def test_prompt_instructs_use_of_record_comprehension_answer(self):
        """The prompt must reinforce calling record_comprehension_answer on user selection."""
        prompt = self._get_prompt()
        assert "record_comprehension_answer" in prompt, (
            "prompt does not mention record_comprehension_answer"
        )

    def test_prompt_mentions_comprehension_answer_message(self):
        """The prompt must tell the agent to wait for the user's selection message."""
        prompt = self._get_prompt()
        assert "comprehension_answer" in prompt, (
            "prompt does not reference the comprehension_answer message type"
        )

    def test_prompt_clarifies_no_score_passthrough(self):
        """The prompt must clarify the agent should not pass a score to the tool."""
        prompt = self._get_prompt()
        assert "NEVER pass a score" in prompt or "only the selected_option" in prompt, (
            "prompt does not clarify score passthrough is forbidden"
        )

    def test_prompt_instructs_use_of_present_continue_prompt(self):
        """The prompt must tell the agent to use present_continue_prompt for continuation."""
        prompt = self._get_prompt()
        assert "present_continue_prompt" in prompt, (
            "prompt does not mention present_continue_prompt"
        )

    def test_prompt_instructs_use_of_present_education_content(self):
        """The prompt must tell the agent to deliver teaching via present_education_content (BE-001)."""
        prompt = self._get_prompt()
        assert "present_education_content" in prompt, (
            "prompt does not mention present_education_content"
        )

    def test_prompt_forbids_markdown_teaching_content(self):
        """The prompt must instruct the agent NOT to also write the explanation as markdown."""
        prompt = self._get_prompt()
        assert "Do NOT also write that explanation as" in prompt, (
            "prompt does not forbid duplicating teaching content as markdown"
        )


class TestToolFunctionImports:
    """Verify the imports in education.py are correct and importable."""

    def test_present_comprehension_question_importable_from_tools(self):
        """The function must be importable from agents.transmutation.tools."""
        from agents.transmutation.tools import present_comprehension_question
        assert callable(present_comprehension_question)

    def test_record_comprehension_answer_importable_from_tools(self):
        """record_comprehension_answer must remain importable."""
        from agents.transmutation.tools import record_comprehension_answer
        assert callable(record_comprehension_answer)

    def test_education_agent_imports_both_tools(self):
        """education.py must import both tools from agents.transmutation.tools."""
        from agents.transmutation.sub_agents import education as edu_module
        import inspect
        source = inspect.getsource(edu_module)
        assert "present_comprehension_question" in source
        assert "record_comprehension_answer" in source

    def test_present_education_content_importable_from_tools(self):
        """present_education_content must be importable from agents.transmutation.tools (BE-001)."""
        from agents.transmutation.tools import present_education_content
        assert callable(present_education_content)

    def test_education_agent_imports_present_education_content(self):
        """education.py must import present_education_content from agents.transmutation.tools."""
        from agents.transmutation.sub_agents import education as edu_module
        import inspect
        source = inspect.getsource(edu_module)
        assert "present_education_content" in source
