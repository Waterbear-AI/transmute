"""Scripted mock LLM for cost-free harness testing.

MockLlm implements BaseLlm and replays a scenario script instead of calling
an LLM API. This is the sole mock in the system — every tool, scoring
function, SSE pipeline, and DB write downstream runs unmodified production
code.

STEP SEMANTICS (important):
    One scenario step is consumed per MODEL INVOCATION, NOT per user message.
    ADK re-invokes the model after every tool result, so a turn like
    "call tool → say text" requires TWO steps in the script:
        Step 1: {"call": "some_tool", "args": {...}}
        Step 2: {"say": "Here is what I found..."}

STATE SCOPE:
    Per-agent step queues advance globally for the server process (module-
    singleton agent). State is shared across concurrent sessions. This is
    intentional for single-developer harness use: restart the server to reset.

SCENARIO FILE FORMAT (JSON):
    {
      "default_say": "I'm still here — please continue.",
      "transmutation_engine": [
        {"say": "Hello! Let me check your state."},
        {"call": "get_assessment_state", "args": {"user_id": "PLACEHOLDER"},
         "args_from": {"user_id": "user_message.user_id"}}
      ],
      "education_agent": [
        {"call": "present_comprehension_question",
         "args": {},
         "args_from": {"user_id": "user_message.user_id",
                       "question_ids": "tool_response.question_ids[*]"}},
        {"say": "Great work on that question!"}
      ]
    }

ARGS_FROM SOURCES:
    - tool_response.<dotted.path>   — most recent function_response in history
    - user_message.<dotted.path>    — most recent user content parsed as JSON
    Path elements: dict keys + [*] for list mapping (returns list of values).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, AsyncGenerator, TYPE_CHECKING

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

if TYPE_CHECKING:
    from google.adk.models.llm_request import LlmRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent inference — maps distinctive tool names to agent names
# ---------------------------------------------------------------------------

#: Maps a unique tool name (owned by exactly one sub-agent) → agent name.
_AGENT_TOOL_MARKERS: dict[str, str] = {
    # Education agent
    "present_comprehension_question": "education_agent",
    "record_comprehension_answer": "education_agent",
    "get_education_progress": "education_agent",
    "present_continue_prompt": "education_agent",
    # Development agent
    "generate_roadmap": "development_agent",
    "save_roadmap": "development_agent",
    "log_practice_entry": "development_agent",
    "get_practice_history": "development_agent",
    "rank_gaps": "development_agent",
    "update_roadmap": "development_agent",
    "check_roadmap_targets_gaps": "development_agent",
    "get_development_roadmap": "development_agent",
    # Check-in agent (also has get_graduation_record as a marker)
    "detect_check_in_regression": "check_in_agent",
    "save_check_in_log": "check_in_agent",
    "generate_check_in_snapshot": "check_in_agent",
    "get_graduation_record": "check_in_agent",
    # Reassessment agent
    "evaluate_graduation_readiness": "reassessment_agent",
    "select_reassessment_targets": "reassessment_agent",
    "generate_reassessment_snapshot": "reassessment_agent",
    "select_sentinel_questions": "reassessment_agent",
    # Graduation agent
    "generate_graduation_artifacts": "graduation_agent",
    "save_graduation_record": "graduation_agent",
    # Assessment agent
    "score_responses": "assessment_agent",
    "generate_profile_snapshot": "profile_agent",
}

# Root fallback when no marker matches
_ROOT_AGENT = "transmutation_engine"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScenarioError(Exception):
    """Raised for malformed or missing scenario files at load time."""


# ---------------------------------------------------------------------------
# ScenarioScript
# ---------------------------------------------------------------------------

class ScenarioScript:
    """Loads and serves scripted steps from a JSON scenario file.

    Per-agent step queues advance independently. Exhausted queues fall back
    to ``default_say`` — the script never raises after load time.

    Scenarios may reference dynamic runtime values via ``args_from``; see
    :func:`_extract_args` for the supported path syntax.
    """

    def __init__(
        self,
        *,
        default_say: str,
        steps_by_agent: dict[str, deque[dict[str, Any]]],
        source_path: str,
    ) -> None:
        self._default_say = default_say
        # Per-agent step queues (mutable; consumed as steps are served)
        self._queues: dict[str, deque[dict[str, Any]]] = steps_by_agent
        self._source_path = source_path

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "ScenarioScript":
        """Load and validate a scenario JSON file.

        Args:
            path: File system path to the scenario JSON file.

        Returns:
            Validated ``ScenarioScript`` instance.

        Raises:
            ScenarioError: If the file is missing, not valid JSON, or
                structurally invalid (missing ``default_say``, non-list
                agent steps, or malformed step dicts).
        """
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            raise ScenarioError(f"Scenario file not found: {path}")
        except json.JSONDecodeError as exc:
            raise ScenarioError(
                f"Scenario file is not valid JSON: {path} — {exc}"
            )

        if not isinstance(raw, dict):
            raise ScenarioError(
                f"Scenario file must be a JSON object at the top level: {path}"
            )

        if "default_say" not in raw:
            raise ScenarioError(
                f"Scenario file missing required key 'default_say': {path}"
            )
        default_say = raw["default_say"]
        if not isinstance(default_say, str):
            raise ScenarioError(
                f"'default_say' must be a string: {path}"
            )

        # Build per-agent queues from all keys except reserved ones
        reserved = {"default_say"}
        steps_by_agent: dict[str, deque[dict[str, Any]]] = {}
        for agent_name, steps in raw.items():
            if agent_name in reserved:
                continue
            if not isinstance(steps, list):
                raise ScenarioError(
                    f"Agent '{agent_name}' steps must be a list: {path}"
                )
            validated: deque[dict[str, Any]] = deque()
            for idx, step in enumerate(steps):
                cls._validate_step(step, agent_name, idx, path)
                validated.append(step)
            steps_by_agent[agent_name] = validated

        logger.info(
            "ScenarioScript loaded from %s — agents: %s",
            path,
            list(steps_by_agent.keys()),
        )
        return cls(
            default_say=default_say,
            steps_by_agent=steps_by_agent,
            source_path=path,
        )

    @staticmethod
    def _validate_step(
        step: Any,
        agent_name: str,
        idx: int,
        path: str,
    ) -> None:
        """Validate a single step dict; raise ScenarioError if invalid."""
        if not isinstance(step, dict):
            raise ScenarioError(
                f"Scenario {path!r}: agent '{agent_name}', step {idx} must be"
                f" a dict, got {type(step).__name__}"
            )
        valid_kinds = {"say", "call", "transfer"}
        kind_keys = valid_kinds & step.keys()
        if not kind_keys:
            raise ScenarioError(
                f"Scenario {path!r}: agent '{agent_name}', step {idx} must"
                f" have one of {sorted(valid_kinds)} — got keys: {list(step.keys())}"
            )
        if len(kind_keys) > 1:
            raise ScenarioError(
                f"Scenario {path!r}: agent '{agent_name}', step {idx} has"
                f" multiple kind keys: {sorted(kind_keys)} — only one allowed"
            )
        (kind,) = kind_keys
        if kind == "say" and not isinstance(step["say"], str):
            raise ScenarioError(
                f"Scenario {path!r}: agent '{agent_name}', step {idx} 'say'"
                f" must be a string"
            )
        if kind == "transfer" and not isinstance(step["transfer"], str):
            raise ScenarioError(
                f"Scenario {path!r}: agent '{agent_name}', step {idx}"
                f" 'transfer' must be a string agent name"
            )
        if kind == "call":
            if not isinstance(step.get("call"), str):
                raise ScenarioError(
                    f"Scenario {path!r}: agent '{agent_name}', step {idx}"
                    f" 'call' must be a string tool name"
                )

    # ------------------------------------------------------------------
    # Step access
    # ------------------------------------------------------------------

    def next_step(
        self,
        agent_name: str,
        llm_request: "LlmRequest",
    ) -> dict[str, Any]:
        """Return and consume the next step for ``agent_name``.

        Falls back to ``{"say": default_say}`` when the queue for
        ``agent_name`` is exhausted or absent.

        Args:
            agent_name: Name of the currently active agent.
            llm_request: The LLM request (passed to _extract_args).

        Returns:
            A step dict with ``say``, ``call``/``args``/``args_from``,
            or ``transfer`` key.
        """
        queue = self._queues.get(agent_name)
        if not queue:
            logger.debug(
                "ScenarioScript: agent '%s' steps exhausted — using default_say",
                agent_name,
            )
            return {"say": self._default_say}

        step = queue.popleft()
        logger.debug(
            "ScenarioScript: agent '%s' serving step: %s", agent_name, step
        )
        return step


# ---------------------------------------------------------------------------
# _extract_args — dotted-path dynamic argument resolution
# ---------------------------------------------------------------------------

def _extract_args(
    args_from: dict[str, str],
    llm_request: "LlmRequest",
) -> dict[str, Any]:
    """Resolve dynamic values from the LLM request history.

    Supported path prefixes:
        ``tool_response.<path>``   — most recent function_response part in
                                     ``llm_request.contents``
        ``user_message.<path>``    — most recent user content, parsed as JSON

    Path elements:
        - Simple dict key:  ``results.data``
        - List mapping:     ``question_ids[*]``  (returns a list)

    Unresolvable paths log a warning and are silently omitted (returning
    whatever static args the step already has — never crash the stream).

    Args:
        args_from: Mapping from arg name → source path string.
        llm_request: Active LLM request to extract values from.

    Returns:
        Dict of resolved arg name → value (may be partial if some paths
        failed to resolve).
    """
    resolved: dict[str, Any] = {}

    # Cache the two sources lazily
    _tool_response: Any = _UNSET
    _user_message: Any = _UNSET

    for arg_name, path_str in args_from.items():
        try:
            if path_str.startswith("tool_response."):
                if _tool_response is _UNSET:
                    _tool_response = _find_last_tool_response(llm_request)
                root = _tool_response
                sub_path = path_str[len("tool_response."):]
            elif path_str.startswith("user_message."):
                if _user_message is _UNSET:
                    _user_message = _find_last_user_message_json(llm_request)
                root = _user_message
                sub_path = path_str[len("user_message."):]
            else:
                logger.warning(
                    "_extract_args: unknown path prefix in %r — skipping",
                    path_str,
                )
                continue

            value = _traverse_path(root, sub_path, path_str)
            resolved[arg_name] = value
        except Exception:
            logger.warning(
                "_extract_args: failed to resolve path %r for arg %r — skipping",
                path_str,
                arg_name,
                exc_info=True,
            )

    return resolved


# Sentinel for lazy initialisation inside _extract_args
_UNSET = object()


def _find_last_tool_response(llm_request: "LlmRequest") -> Any:
    """Return the response dict from the most recent function_response part."""
    from google.adk.models.llm_request import LlmRequest  # local import avoids circularity
    for content in reversed(llm_request.contents):
        if content.parts:
            for part in reversed(content.parts):
                if part.function_response is not None:
                    resp = part.function_response.response
                    if isinstance(resp, str):
                        # Attempt JSON parse for string responses
                        try:
                            return json.loads(resp)
                        except json.JSONDecodeError:
                            return resp
                    return resp
    return {}


def _find_last_user_message_json(llm_request: "LlmRequest") -> Any:
    """Return the most recent user content parsed as JSON (best-effort)."""
    for content in reversed(llm_request.contents):
        if content.role == "user" and content.parts:
            for part in reversed(content.parts):
                if part.text:
                    try:
                        return json.loads(part.text)
                    except json.JSONDecodeError:
                        continue
    return {}


def _traverse_path(root: Any, path: str, full_path: str) -> Any:
    """Walk a dotted/bracketed path on ``root``.

    Supports:
        - ``some.nested.key``       — dict key traversal
        - ``list_field[*]``         — map over a list, collecting each element

    Args:
        root: The root value to traverse.
        path: Dotted path string (without the ``tool_response.`` prefix).
        full_path: Original full path (used in error messages).

    Returns:
        The value at the path.

    Raises:
        KeyError, TypeError, ValueError: For missing keys or type mismatches
        (caller logs and skips).
    """
    if not path:
        return root

    current = root
    # Split on dots but keep [*] attached to the preceding key
    parts = path.split(".")
    for part in parts:
        if part.endswith("[*]"):
            key = part[:-3]
            if key:
                if not isinstance(current, dict):
                    raise TypeError(
                        f"Expected dict at {key!r} in path {full_path!r},"
                        f" got {type(current).__name__}"
                    )
                current = current[key]
            if not isinstance(current, list):
                raise TypeError(
                    f"Expected list at [*] in path {full_path!r},"
                    f" got {type(current).__name__}"
                )
            # Return the whole list (identity map — no sub-path)
            return current
        else:
            if not isinstance(current, dict):
                raise TypeError(
                    f"Expected dict at {part!r} in path {full_path!r},"
                    f" got {type(current).__name__}"
                )
            current = current[part]
    return current


# ---------------------------------------------------------------------------
# MockLlm
# ---------------------------------------------------------------------------

class MockLlm(BaseLlm):
    """Deterministic scripted model for harness testing.

    Replays a :class:`ScenarioScript` instead of making API calls. Every
    downstream component (tools, SSE, cost recording, DB) runs unmodified.

    Usage::

        script = ScenarioScript.load("tests/harness/scenarios/education.json")
        mock = MockLlm(scenario=script)
        agent = create_transmutation_agent(model=mock)

    STATE: Per-agent queues in the scenario script advance globally across
    sessions for the lifetime of the process. Restart the server to reset.
    """

    model: str = "mock/scripted"

    # scenario is stored as a PrivateAttr to avoid pydantic serialisation
    # (ScenarioScript is not a pydantic model and need not be).
    _scenario: ScenarioScript = PrivateAttr()

    def __init__(self, *, scenario: ScenarioScript, model: str = "mock/scripted", **data: Any) -> None:
        super().__init__(model=model, **data)
        self._scenario = scenario

    # ------------------------------------------------------------------
    # Agent inference
    # ------------------------------------------------------------------

    def infer_agent(self, llm_request: "LlmRequest") -> str:
        """Identify the active agent from tool declarations in the request.

        ADK does not pass the agent name to the model. We identify it by
        looking for distinctive tool names that are registered exclusively
        to one sub-agent. The first match wins; if no marker is found,
        falls back to the root agent (``transmutation_engine``).

        Args:
            llm_request: The incoming LLM request.

        Returns:
            Agent name string matching the scenario script's section keys.
        """
        tool_names: set[str] = set()
        if llm_request.config and llm_request.config.tools:
            for tool_obj in llm_request.config.tools:
                if hasattr(tool_obj, "function_declarations") and tool_obj.function_declarations:
                    for decl in tool_obj.function_declarations:
                        if decl.name:
                            tool_names.add(decl.name)

        for tool_name in tool_names:
            agent = _AGENT_TOOL_MARKERS.get(tool_name)
            if agent:
                return agent

        return _ROOT_AGENT

    # ------------------------------------------------------------------
    # Content generation
    # ------------------------------------------------------------------

    async def generate_content_async(
        self,
        llm_request: "LlmRequest",
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Replay the next script step for the inferred active agent.

        When ``stream=True`` a single complete (non-partial) response is
        yielded — this mirrors how complete final chunks flow in the
        existing SSE pipeline and is sufficient for harness testing.

        Args:
            llm_request: The LLM request from the ADK runner.
            stream: Whether streaming mode is requested.

        Yields:
            Exactly one :class:`LlmResponse`.
        """
        agent_name = self.infer_agent(llm_request)
        step = self._scenario.next_step(agent_name, llm_request)

        logger.debug(
            "MockLlm: agent=%s step_kind=%s",
            agent_name,
            next(k for k in ("say", "call", "transfer") if k in step),
        )

        response = self._build_response(step, llm_request)
        yield response

    def _build_response(
        self,
        step: dict[str, Any],
        llm_request: "LlmRequest",
    ) -> LlmResponse:
        """Translate a script step dict into an :class:`LlmResponse`."""
        if "say" in step:
            text = step["say"]
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=text)],
                ),
                partial=False,
                usage_metadata=_make_usage(input_text="", output_text=text),
            )

        if "transfer" in step:
            target_agent = step["transfer"]
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="transfer_to_agent",
                                args={"agent_name": target_agent},
                            )
                        )
                    ],
                ),
                partial=False,
                usage_metadata=_make_usage(input_text="", output_text=target_agent),
            )

        if "call" in step:
            tool_name: str = step["call"]
            static_args: dict[str, Any] = dict(step.get("args") or {})
            args_from: dict[str, str] = dict(step.get("args_from") or {})

            if args_from:
                extracted = _extract_args(args_from, llm_request)
                # Static args win on key conflict (per spec)
                merged = {**extracted, **static_args}
            else:
                merged = static_args

            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=tool_name,
                                args=merged,
                            )
                        )
                    ],
                ),
                partial=False,
                usage_metadata=_make_usage(input_text="", output_text=tool_name),
            )

        # Defensive fallback — should never reach here post-validation
        logger.warning("MockLlm: unrecognised step keys %s — falling back to empty say", list(step.keys()))
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="")],
            ),
            partial=False,
            usage_metadata=_make_usage(input_text="", output_text=""),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usage(
    *,
    input_text: str,
    output_text: str,
) -> types.GenerateContentResponseUsageMetadata:
    """Return deterministic usage metadata based on approximate token counts.

    Uses a simple 4-chars-per-token heuristic (adequate for harness
    purposes — the cost pipeline is exercised at $0.00 regardless).
    """
    input_tokens = max(1, len(input_text) // 4)
    output_tokens = max(1, len(output_text) // 4)
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=input_tokens,
        candidates_token_count=output_tokens,
        total_token_count=input_tokens + output_tokens,
    )
