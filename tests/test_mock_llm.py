"""Unit tests for MockLlm and ScenarioScript.

These tests verify:
- ScenarioScript loads valid JSON and raises ScenarioError for malformed files
- ScenarioScript.next_step advances queues per-agent and falls back to default_say
- _extract_args resolves tool_response and user_message paths
- MockLlm.infer_agent identifies sub-agents by tool markers
- MockLlm.generate_content_async replays say/call/transfer steps with usage_metadata
"""

from __future__ import annotations

import json
import os
import asyncio
from collections import deque

import pytest
from google.genai import types

from agents.transmutation.mock_llm import (
    MockLlm,
    ScenarioError,
    ScenarioScript,
    _extract_args,
    _make_usage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_scenario(tmp_path: str, data: dict) -> str:
    """Write a scenario dict as JSON to a temp file; return the path."""
    path = os.path.join(tmp_path, "scenario.json")
    with open(path, "w") as fh:
        json.dump(data, fh)
    return path


def _make_llm_request(
    *,
    tool_names: list[str] | None = None,
    contents: list | None = None,
):
    """Build a minimal LlmRequest for testing."""
    from google.adk.models.llm_request import LlmRequest

    req = LlmRequest()

    if tool_names:
        declarations = [
            types.FunctionDeclaration(name=name, description="test")
            for name in tool_names
        ]
        if req.config.tools is None:
            req.config.tools = []
        req.config.tools.append(types.Tool(function_declarations=declarations))

    if contents:
        req.contents = contents

    return req


# ---------------------------------------------------------------------------
# ScenarioScript — loading
# ---------------------------------------------------------------------------

class TestScenarioScriptLoad:
    def test_loads_valid_scenario(self, tmp_path):
        path = _write_scenario(
            str(tmp_path),
            {
                "default_say": "I'm done.",
                "transmutation_engine": [
                    {"say": "Hello!"},
                    {"call": "get_assessment_state", "args": {"user_id": "u1"}},
                ],
                "education_agent": [
                    {"transfer": "education_agent"},
                ],
            },
        )
        script = ScenarioScript.load(path)
        assert script is not None

    def test_missing_file_raises_scenario_error(self, tmp_path):
        path = os.path.join(str(tmp_path), "nonexistent.json")
        with pytest.raises(ScenarioError, match="not found"):
            ScenarioScript.load(path)

    def test_invalid_json_raises_scenario_error(self, tmp_path):
        path = os.path.join(str(tmp_path), "bad.json")
        with open(path, "w") as fh:
            fh.write("{not valid json")
        with pytest.raises(ScenarioError, match="not valid JSON"):
            ScenarioScript.load(path)

    def test_missing_default_say_raises_scenario_error(self, tmp_path):
        path = _write_scenario(str(tmp_path), {"transmutation_engine": []})
        with pytest.raises(ScenarioError, match="default_say"):
            ScenarioScript.load(path)

    def test_non_dict_top_level_raises_scenario_error(self, tmp_path):
        path = os.path.join(str(tmp_path), "array.json")
        with open(path, "w") as fh:
            json.dump([1, 2, 3], fh)
        with pytest.raises(ScenarioError, match="JSON object"):
            ScenarioScript.load(path)

    def test_non_list_agent_steps_raises_scenario_error(self, tmp_path):
        path = _write_scenario(
            str(tmp_path),
            {"default_say": "done", "transmutation_engine": "not-a-list"},
        )
        with pytest.raises(ScenarioError, match="must be a list"):
            ScenarioScript.load(path)

    def test_step_without_kind_key_raises_scenario_error(self, tmp_path):
        path = _write_scenario(
            str(tmp_path),
            {
                "default_say": "done",
                "transmutation_engine": [{"unknown_key": "value"}],
            },
        )
        with pytest.raises(ScenarioError, match="step 0 must have one of"):
            ScenarioScript.load(path)

    def test_step_with_multiple_kind_keys_raises_scenario_error(self, tmp_path):
        path = _write_scenario(
            str(tmp_path),
            {
                "default_say": "done",
                "transmutation_engine": [{"say": "hi", "call": "tool"}],
            },
        )
        with pytest.raises(ScenarioError, match="multiple kind keys"):
            ScenarioScript.load(path)

    def test_say_step_non_string_raises_scenario_error(self, tmp_path):
        path = _write_scenario(
            str(tmp_path),
            {
                "default_say": "done",
                "transmutation_engine": [{"say": 123}],
            },
        )
        with pytest.raises(ScenarioError, match="'say' must be a string"):
            ScenarioScript.load(path)

    def test_call_step_non_string_tool_raises_scenario_error(self, tmp_path):
        path = _write_scenario(
            str(tmp_path),
            {
                "default_say": "done",
                "transmutation_engine": [{"call": 42, "args": {}}],
            },
        )
        with pytest.raises(ScenarioError, match="'call' must be a string"):
            ScenarioScript.load(path)


# ---------------------------------------------------------------------------
# ScenarioScript — next_step and queue management
# ---------------------------------------------------------------------------

class TestScenarioScriptNextStep:
    def _make_script(self, steps_by_agent: dict, default_say: str = "I'm done.") -> ScenarioScript:
        return ScenarioScript(
            default_say=default_say,
            steps_by_agent={k: deque(v) for k, v in steps_by_agent.items()},
            source_path="test",
        )

    def test_serves_steps_in_order(self):
        req = _make_llm_request()
        script = self._make_script({
            "transmutation_engine": [{"say": "first"}, {"say": "second"}]
        })
        step1 = script.next_step("transmutation_engine", req)
        step2 = script.next_step("transmutation_engine", req)
        assert step1 == {"say": "first"}
        assert step2 == {"say": "second"}

    def test_returns_default_say_when_exhausted(self):
        req = _make_llm_request()
        script = self._make_script({"transmutation_engine": [{"say": "only"}]})
        script.next_step("transmutation_engine", req)  # consume the only step
        fallback = script.next_step("transmutation_engine", req)
        assert fallback == {"say": "I'm done."}

    def test_returns_default_say_for_unknown_agent(self):
        req = _make_llm_request()
        script = self._make_script({"transmutation_engine": [{"say": "x"}]})
        fallback = script.next_step("education_agent", req)
        assert fallback == {"say": "I'm done."}

    def test_independent_queues_per_agent(self):
        req = _make_llm_request()
        script = self._make_script({
            "transmutation_engine": [{"say": "root-step-1"}, {"say": "root-step-2"}],
            "education_agent": [{"say": "edu-step-1"}],
        })
        # Advance education queue
        edu1 = script.next_step("education_agent", req)
        # Root queue should be unaffected
        root1 = script.next_step("transmutation_engine", req)
        root2 = script.next_step("transmutation_engine", req)
        assert edu1 == {"say": "edu-step-1"}
        assert root1 == {"say": "root-step-1"}
        assert root2 == {"say": "root-step-2"}


# ---------------------------------------------------------------------------
# _extract_args
# ---------------------------------------------------------------------------

class TestExtractArgs:
    def _make_contents_with_tool_response(self, response_dict: dict) -> list:
        return [
            types.Content(
                role="tool",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="some_tool",
                            response=response_dict,
                        )
                    )
                ],
            )
        ]

    def _make_contents_with_user_message(self, text: str) -> list:
        return [
            types.Content(
                role="user",
                parts=[types.Part(text=text)],
            )
        ]

    def test_resolves_tool_response_simple_path(self):
        contents = self._make_contents_with_tool_response(
            {"question_ids": ["q1", "q2", "q3"]}
        )
        req = _make_llm_request(contents=contents)
        result = _extract_args(
            {"ids": "tool_response.question_ids"}, req
        )
        assert result == {"ids": ["q1", "q2", "q3"]}

    def test_resolves_tool_response_nested_path(self):
        contents = self._make_contents_with_tool_response(
            {"data": {"user_id": "abc-123"}}
        )
        req = _make_llm_request(contents=contents)
        result = _extract_args({"user_id": "tool_response.data.user_id"}, req)
        assert result == {"user_id": "abc-123"}

    def test_resolves_tool_response_list_map(self):
        contents = self._make_contents_with_tool_response(
            {"items": ["a", "b", "c"]}
        )
        req = _make_llm_request(contents=contents)
        result = _extract_args({"vals": "tool_response.items[*]"}, req)
        assert result == {"vals": ["a", "b", "c"]}

    def test_resolves_user_message_path(self):
        payload = json.dumps({"question_id": "q42", "selected_option": 2})
        contents = self._make_contents_with_user_message(payload)
        req = _make_llm_request(contents=contents)
        result = _extract_args(
            {"question_id": "user_message.question_id"}, req
        )
        assert result == {"question_id": "q42"}

    def test_unresolvable_path_returns_empty_without_crash(self):
        req = _make_llm_request()
        result = _extract_args({"x": "tool_response.missing.key"}, req)
        # Should not raise; should omit the key
        assert "x" not in result or result.get("x") is None

    def test_unknown_prefix_skipped(self):
        req = _make_llm_request()
        result = _extract_args({"x": "unknown_source.something"}, req)
        assert result == {}

    def test_empty_args_from_returns_empty(self):
        req = _make_llm_request()
        assert _extract_args({}, req) == {}


# ---------------------------------------------------------------------------
# MockLlm.infer_agent
# ---------------------------------------------------------------------------

class TestMockLlmInferAgent:
    def _make_mock(self) -> MockLlm:
        from collections import deque
        script = ScenarioScript(
            default_say="done",
            steps_by_agent={},
            source_path="test",
        )
        return MockLlm(scenario=script)

    def test_identifies_education_agent(self):
        mock = self._make_mock()
        req = _make_llm_request(tool_names=["present_comprehension_question"])
        assert mock.infer_agent(req) == "education_agent"

    def test_identifies_development_agent(self):
        mock = self._make_mock()
        req = _make_llm_request(tool_names=["generate_roadmap", "save_roadmap"])
        assert mock.infer_agent(req) == "development_agent"

    def test_identifies_check_in_agent(self):
        mock = self._make_mock()
        req = _make_llm_request(tool_names=["detect_check_in_regression"])
        assert mock.infer_agent(req) == "check_in_agent"

    def test_identifies_reassessment_agent(self):
        mock = self._make_mock()
        req = _make_llm_request(tool_names=["evaluate_graduation_readiness"])
        assert mock.infer_agent(req) == "reassessment_agent"

    def test_falls_back_to_root_for_unknown_tools(self):
        mock = self._make_mock()
        req = _make_llm_request(tool_names=["get_assessment_state", "advance_phase"])
        # These tools are shared — no unique marker → root
        assert mock.infer_agent(req) == "transmutation_engine"

    def test_falls_back_to_root_for_no_tools(self):
        mock = self._make_mock()
        req = _make_llm_request()
        assert mock.infer_agent(req) == "transmutation_engine"


# ---------------------------------------------------------------------------
# MockLlm.generate_content_async
# ---------------------------------------------------------------------------

class TestMockLlmGenerateContentAsync:
    def _make_mock_with_steps(
        self,
        steps: list[dict],
        agent: str = "transmutation_engine",
        default_say: str = "done",
    ) -> MockLlm:
        script = ScenarioScript(
            default_say=default_say,
            steps_by_agent={agent: deque(steps)},
            source_path="test",
        )
        return MockLlm(scenario=script)

    @pytest.mark.anyio
    async def test_say_step_yields_text_response(self):
        mock = self._make_mock_with_steps([{"say": "Hello there!"}])
        req = _make_llm_request()  # no special tools → root agent

        responses = []
        async for resp in mock.generate_content_async(req):
            responses.append(resp)

        assert len(responses) == 1
        resp = responses[0]
        assert resp.content is not None
        assert resp.content.parts[0].text == "Hello there!"
        assert resp.partial is False

    @pytest.mark.anyio
    async def test_say_step_has_usage_metadata(self):
        mock = self._make_mock_with_steps([{"say": "Hi"}])
        req = _make_llm_request()

        async for resp in mock.generate_content_async(req):
            assert resp.usage_metadata is not None
            assert resp.usage_metadata.candidates_token_count is not None
            assert resp.usage_metadata.candidates_token_count >= 1

    @pytest.mark.anyio
    async def test_transfer_step_yields_transfer_to_agent_call(self):
        mock = self._make_mock_with_steps([{"transfer": "education_agent"}])
        req = _make_llm_request()

        async for resp in mock.generate_content_async(req):
            fc = resp.content.parts[0].function_call
            assert fc is not None
            assert fc.name == "transfer_to_agent"
            assert fc.args["agent_name"] == "education_agent"

    @pytest.mark.anyio
    async def test_call_step_with_static_args(self):
        mock = self._make_mock_with_steps([
            {"call": "get_assessment_state", "args": {"user_id": "u42"}}
        ])
        req = _make_llm_request()

        async for resp in mock.generate_content_async(req):
            fc = resp.content.parts[0].function_call
            assert fc.name == "get_assessment_state"
            assert fc.args["user_id"] == "u42"

    @pytest.mark.anyio
    async def test_call_step_with_args_from_tool_response(self):
        # Scenario: call present_comprehension_question with question_ids from
        # the most recent tool response (simulating get_next_question_batch output)
        tool_resp_content = types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="get_next_question_batch",
                        response={"question_ids": ["q1", "q2", "q3"]},
                    )
                )
            ],
        )
        mock = self._make_mock_with_steps(
            [
                {
                    "call": "present_comprehension_question",
                    "args": {"user_id": "static_user"},
                    "args_from": {"question_ids": "tool_response.question_ids[*]"},
                }
            ],
            agent="education_agent",
        )
        req = _make_llm_request(
            tool_names=["present_comprehension_question"],
            contents=[tool_resp_content],
        )

        async for resp in mock.generate_content_async(req):
            fc = resp.content.parts[0].function_call
            assert fc.name == "present_comprehension_question"
            assert fc.args["question_ids"] == ["q1", "q2", "q3"]
            # Static arg wins on conflict — user_id was set statically
            assert fc.args["user_id"] == "static_user"

    @pytest.mark.anyio
    async def test_call_step_with_args_from_user_message(self):
        user_msg = types.Content(
            role="user",
            parts=[types.Part(text=json.dumps({"question_id": "q99", "selected_option": 3}))],
        )
        mock = self._make_mock_with_steps(
            [
                {
                    "call": "record_comprehension_answer",
                    "args": {"user_id": "u1"},
                    "args_from": {
                        "question_id": "user_message.question_id",
                        "selected_option": "user_message.selected_option",
                    },
                }
            ],
            agent="education_agent",
        )
        req = _make_llm_request(
            tool_names=["record_comprehension_answer"],
            contents=[user_msg],
        )

        async for resp in mock.generate_content_async(req):
            fc = resp.content.parts[0].function_call
            assert fc.name == "record_comprehension_answer"
            assert fc.args["question_id"] == "q99"
            assert fc.args["selected_option"] == 3
            assert fc.args["user_id"] == "u1"

    @pytest.mark.anyio
    async def test_exhausted_script_uses_default_say(self):
        mock = self._make_mock_with_steps(
            [{"say": "only step"}], default_say="I have nothing more to say."
        )
        req = _make_llm_request()
        # Consume the only step
        async for _ in mock.generate_content_async(req):
            pass
        # Second call should fall back to default_say
        async for resp in mock.generate_content_async(req):
            assert resp.content.parts[0].text == "I have nothing more to say."

    @pytest.mark.anyio
    async def test_stream_true_still_yields_one_response(self):
        mock = self._make_mock_with_steps([{"say": "streamed"}])
        req = _make_llm_request()

        responses = []
        async for resp in mock.generate_content_async(req, stream=True):
            responses.append(resp)

        assert len(responses) == 1
        assert responses[0].content.parts[0].text == "streamed"

    @pytest.mark.anyio
    async def test_model_id_is_mock_scripted(self):
        script = ScenarioScript(
            default_say="d",
            steps_by_agent={},
            source_path="test",
        )
        mock = MockLlm(scenario=script)
        assert mock.model == "mock/scripted"


# ---------------------------------------------------------------------------
# _make_usage helper
# ---------------------------------------------------------------------------

class TestMakeUsage:
    def test_returns_usage_metadata(self):
        meta = _make_usage(input_text="hello world", output_text="response here")
        assert meta.prompt_token_count >= 1
        assert meta.candidates_token_count >= 1
        assert meta.total_token_count == meta.prompt_token_count + meta.candidates_token_count

    def test_minimum_one_token_for_empty_strings(self):
        meta = _make_usage(input_text="", output_text="")
        assert meta.prompt_token_count == 1
        assert meta.candidates_token_count == 1


# ---------------------------------------------------------------------------
# Shipped scenario files
# ---------------------------------------------------------------------------

_SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "harness", "scenarios")
_SCENARIO_FILES = sorted(
    f for f in os.listdir(_SCENARIOS_DIR) if f.endswith(".json")
)


class TestShippedScenarios:
    """Every scenario file shipped under tests/harness/scenarios must load.

    Parametrized over the directory listing so newly added scenario files
    are covered automatically — a malformed shipped scenario fails CI
    instead of failing server startup for whoever runs it.
    """

    def test_scenarios_exist(self):
        assert _SCENARIO_FILES, "no scenario files found in tests/harness/scenarios"

    @pytest.mark.parametrize("filename", _SCENARIO_FILES)
    def test_shipped_scenario_loads(self, filename):
        script = ScenarioScript.load(os.path.join(_SCENARIOS_DIR, filename))
        assert isinstance(script, ScenarioScript)

    @pytest.mark.parametrize("filename", _SCENARIO_FILES)
    def test_shipped_scenario_agents_are_known(self, filename):
        from agents.transmutation.mock_llm import _AGENT_TOOL_MARKERS, _ROOT_AGENT

        with open(os.path.join(_SCENARIOS_DIR, filename), encoding="utf-8") as fh:
            raw = json.load(fh)
        known = set(_AGENT_TOOL_MARKERS.values()) | {_ROOT_AGENT}
        scenario_agents = {k for k in raw if k != "default_say"}
        unknown = scenario_agents - known
        assert not unknown, (
            f"{filename} scripts unknown agent(s) {sorted(unknown)} — "
            f"steps for these would never be served (valid: {sorted(known)})"
        )
