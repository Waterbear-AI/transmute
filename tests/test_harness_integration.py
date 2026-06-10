"""Integration tests for mock mode wiring.

Verifies:
- GET /api/health returns mock_mode: false in normal operation
- GET /api/health returns mock_mode: true when TRANSMUTE_MOCK_SCENARIO is set
- _build_model() returns a str when env var is absent
- _build_model() returns a MockLlm when env var is set and scenario file is valid
- record_llm_call records 'mock/scripted' model_id in mock mode
- 'mock/*' cost wildcard resolves to $0.00 via config.yaml
- SSE event flow in mock mode emits session.cost at $0.00 and records mock/scripted in llm_calls

Markers: integration
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_minimal_scenario(path: str) -> None:
    """Write a minimal valid scenario file to path."""
    with open(path, "w") as fh:
        json.dump(
            {
                "default_say": "done.",
                "transmutation_engine": [{"say": "Hello!"}],
            },
            fh,
        )


# ---------------------------------------------------------------------------
# _build_model() unit tests
# ---------------------------------------------------------------------------

class TestBuildModel:
    """Unit tests for the _build_model factory in api/chat.py.

    We call _build_model() directly with env vars patched rather than
    reloading the module (which would re-run module-level code and mutate
    MOCK_MODE globally, polluting subsequent tests).
    """

    def test_returns_string_when_env_var_absent(self, monkeypatch):
        monkeypatch.delenv("TRANSMUTE_MOCK_SCENARIO", raising=False)
        import api.chat as chat_mod
        result = chat_mod._build_model()
        assert isinstance(result, str)

    def test_returns_mock_llm_when_env_var_set(self, monkeypatch, tmp_path):
        scenario_path = str(tmp_path / "scenario.json")
        _write_minimal_scenario(scenario_path)
        monkeypatch.setenv("TRANSMUTE_MOCK_SCENARIO", scenario_path)

        import api.chat as chat_mod
        from agents.transmutation.mock_llm import MockLlm
        result = chat_mod._build_model()
        assert isinstance(result, MockLlm)
        assert result.model == "mock/scripted"

    def test_startup_fails_for_missing_scenario_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRANSMUTE_MOCK_SCENARIO", str(tmp_path / "nonexistent.json"))

        import api.chat as chat_mod
        from agents.transmutation.mock_llm import ScenarioError
        with pytest.raises(ScenarioError, match="not found"):
            chat_mod._build_model()


# ---------------------------------------------------------------------------
# GET /api/health — mock_mode field
# ---------------------------------------------------------------------------

class TestHealthMockModeField:
    """Verify /api/health includes mock_mode: false in normal operation."""

    def test_health_includes_mock_mode_false_by_default(self, api_client):
        """Health should include mock_mode: false when no scenario env var is set."""
        # conftest sets up the test DB; MOCK_MODE in api.chat is False at test time
        resp = api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "mock_mode" in data
        # In normal test operation (no TRANSMUTE_MOCK_SCENARIO set), expect False
        assert data["mock_mode"] is False

    def test_health_mock_mode_true_when_monkeypatched(self, api_client, monkeypatch):
        """When MOCK_MODE is set to True, health returns mock_mode: true."""
        import api.chat as chat_mod
        monkeypatch.setattr(chat_mod, "MOCK_MODE", True)

        resp = api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mock_mode"] is True

    def test_health_still_returns_db_status(self, api_client):
        """Ensure existing health fields are preserved after mock_mode addition."""
        resp = api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"
        assert "mock_mode" in data


# ---------------------------------------------------------------------------
# MOCK_MODEL_ID constant and record_llm_call model_id in mock mode
# ---------------------------------------------------------------------------

class TestMockModelId:
    def test_mock_model_id_constant(self):
        """_MOCK_MODEL_ID should be 'mock/scripted'."""
        import api.chat as chat_mod
        assert chat_mod._MOCK_MODEL_ID == "mock/scripted"

    def test_mock_mode_false_by_default_in_test_env(self):
        """MOCK_MODE should be False in the standard test environment."""
        import api.chat as chat_mod
        # conftest loads without TRANSMUTE_MOCK_SCENARIO set
        assert chat_mod.MOCK_MODE is False


# ---------------------------------------------------------------------------
# config.yaml — mock/* cost entry
# ---------------------------------------------------------------------------

class TestMockCostConfig:
    def test_mock_wildcard_resolves_to_zero_cost(self):
        """get_cost_per_token('mock/scripted') should return 0.00 input/output."""
        from config import get_settings
        settings = get_settings()
        cost = settings.get_cost_per_token("mock/scripted")
        assert cost.input == 0.00
        assert cost.output == 0.00

    def test_mock_any_model_resolves_to_zero(self):
        """Any mock/* model ID should resolve to $0.00 via wildcard."""
        from config import get_settings
        settings = get_settings()
        cost = settings.get_cost_per_token("mock/anything-at-all")
        assert cost.input == 0.00
        assert cost.output == 0.00


# ---------------------------------------------------------------------------
# SSE event flow in mock mode — session.cost + llm_calls table
# ---------------------------------------------------------------------------

def _parse_sse_events(raw_text: str) -> list[dict]:
    """Parse raw SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data: list[str] = []
    for line in raw_text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_event:
            data_str = "\n".join(current_data)
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = data_str
            events.append({"event": current_event, "data": data})
            current_event = None
            current_data = []
    return events


def _make_synthetic_event(*, prompt_tokens: int = 10, candidates_tokens: int = 5):
    """Build a synthetic ADK Event that looks like a final text response.

    We construct the Event with real pydantic models so that
    ``event.usage_metadata``, ``event.is_final_response()``, and
    ``event.author`` all behave as the production code expects.
    """
    from google.adk.events import Event
    from google.genai import types as genai_types

    usage = genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidates_tokens,
        total_token_count=prompt_tokens + candidates_tokens,
    )
    content = genai_types.Content(
        parts=[genai_types.Part(text="Hello from mock!")],
        role="model",
    )
    return Event(
        author="transmutation_engine",
        usage_metadata=usage,
        content=content,
        # partial=None (default) so is_final_response() returns True
    )


class TestMockSseFlow:
    """Integration test: SSE event flow in mock mode.

    Verifies that when MOCK_MODE=True:
    - POST /api/chat/{session_id} streams a ``session.cost`` SSE event
      with ``estimated_cost_usd == 0.0``
    - The ``llm_calls`` DB table gets a row with ``model_id = 'mock/scripted'``
      and ``cost_usd == 0.0``

    The ADK runner is monkeypatched so no real LLM calls are made.
    """

    @pytest.mark.anyio
    async def test_mock_sse_emits_zero_cost_and_records_mock_model(
        self, authenticated_client, monkeypatch
    ):
        """session.cost SSE event reports $0.00; llm_calls has mock/scripted."""
        import api.chat as chat_mod

        # --- arrange --------------------------------------------------------
        # Force mock mode so recorded_model_id = _MOCK_MODEL_ID
        monkeypatch.setattr(chat_mod, "MOCK_MODE", True)

        # Build one synthetic ADK Event with real usage metadata
        synthetic_event = _make_synthetic_event(prompt_tokens=10, candidates_tokens=5)

        # Patch the runner so run_async yields our synthetic event
        async def _fake_run_async(**kwargs) -> AsyncGenerator:
            yield synthetic_event

        mock_runner = MagicMock()
        mock_runner.run_async = _fake_run_async
        monkeypatch.setattr(chat_mod, "_runner", mock_runner)

        # Create a session for the authenticated user
        sess_resp = authenticated_client.post("/api/sessions")
        assert sess_resp.status_code == 200
        session_id = sess_resp.json()["session_id"]

        # --- act ------------------------------------------------------------
        resp = authenticated_client.post(
            f"/api/chat/{session_id}",
            json={"message": "hi"},
        )
        assert resp.status_code == 200

        raw = resp.text
        events = _parse_sse_events(raw)

        # --- assert: SSE session.cost event ---------------------------------
        cost_events = [e for e in events if e["event"] == "session.cost"]
        assert len(cost_events) == 1, (
            f"Expected exactly one session.cost event; got {len(cost_events)}. "
            f"All events: {[e['event'] for e in events]}"
        )
        cost_data = cost_events[0]["data"]
        assert cost_data["estimated_cost_usd"] == 0.0, (
            f"expected $0.00 in mock mode, got {cost_data['estimated_cost_usd']}"
        )
        assert cost_data["session_cost_usd"] == 0.0, (
            f"expected session_cost_usd $0.00 in mock mode, got {cost_data['session_cost_usd']}"
        )

        # --- assert: llm_calls DB row has mock/scripted model_id + $0.00 ---
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT model_id, cost_usd, input_tokens, output_tokens "
            "FROM llm_calls WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        conn.close()

        assert len(rows) == 1, (
            f"Expected exactly 1 llm_calls row, got {len(rows)}"
        )
        row = rows[0]
        assert row["model_id"] == "mock/scripted", (
            f"Expected model_id='mock/scripted', got '{row['model_id']}'"
        )
        assert row["cost_usd"] == 0.0, (
            f"Expected cost_usd=0.0, got {row['cost_usd']}"
        )
        assert row["input_tokens"] == 10
        assert row["output_tokens"] == 5
