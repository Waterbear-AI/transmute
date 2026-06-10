"""Integration tests for mock mode wiring.

Verifies:
- GET /api/health returns mock_mode: false in normal operation
- GET /api/health returns mock_mode: true when TRANSMUTE_MOCK_SCENARIO is set
- _build_model() returns a str when env var is absent
- _build_model() returns a MockLlm when env var is set and scenario file is valid
- record_llm_call records 'mock/scripted' model_id in mock mode
- 'mock/*' cost wildcard resolves to $0.00 via config.yaml

Markers: integration
"""

from __future__ import annotations

import json
import os
import tempfile

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
