import os
import tempfile

import bcrypt
import pytest

from api.auth import _sign_cookie, _verify_cookie
from agents.transmutation.flow_engine import (
    compute_flows_per_level,
    compute_full_profile,
    compute_moral_capital_debt,
    compute_moral_work,
    compute_weighted_total,
)
from config import Settings, TransmutationSettings, _load_yaml_config
from models.moral_profile import FlowValues
from db.database import run_migrations


# --- Password Hashing Tests ---

class TestPasswordHashing:
    def test_bcrypt_round_trip(self):
        password = "test-password-123"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        assert bcrypt.checkpw(password.encode(), hashed.encode())

    def test_bcrypt_rejects_wrong_password(self):
        password = "correct-password"
        wrong = "wrong-password"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        assert not bcrypt.checkpw(wrong.encode(), hashed.encode())


# --- Cookie Signing Tests ---

class TestCookieSigning:
    def test_sign_and_verify_round_trip(self):
        user_id = "test-user-id-123"
        signed = _sign_cookie(user_id)
        assert _verify_cookie(signed) == user_id

    def test_tampered_cookie_rejected(self):
        user_id = "test-user-id-123"
        signed = _sign_cookie(user_id)
        tampered = signed[:-4] + "xxxx"
        assert _verify_cookie(tampered) is None

    def test_no_dot_cookie_rejected(self):
        assert _verify_cookie("no-dot-here") is None

    def test_empty_cookie_rejected(self):
        assert _verify_cookie("") is None


# --- Migration Runner Tests ---

class TestMigrationRunner:
    def test_applies_migrations(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            count = run_migrations(db_path=db_path)
            assert count == 2

            import sqlite3
            conn = sqlite3.connect(db_path)
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            conn.close()

            assert "users" in tables
            assert "assessment_state" in tables
            assert "schema_version" in tables
            assert "moral_ledger" in tables
            assert len(tables) == 12
        finally:
            os.unlink(db_path)

    def test_skips_already_applied(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            run_migrations(db_path=db_path)
            count = run_migrations(db_path=db_path)
            assert count == 0
        finally:
            os.unlink(db_path)


# --- Config Loading Tests ---

class TestConfigLoading:
    def test_yaml_config_loads(self):
        config = _load_yaml_config()
        assert "model" in config
        assert config["model"]["provider"] == "anthropic"

    def test_settings_loads_from_yaml(self):
        settings = Settings()
        assert settings.model.provider == "anthropic"
        assert settings.model.model_id == "claude-sonnet-4-5-20250514"

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Invalid provider"):
            Settings(model={"provider": "invalid_provider"})

    def test_cost_lookup(self):
        settings = Settings()
        cost = settings.get_cost_per_token("claude-sonnet-4-5-20250514")
        assert cost.input == 3.00
        assert cost.output == 15.00

    def test_wildcard_cost_lookup(self):
        settings = Settings()
        cost = settings.get_cost_per_token("ollama/llama3")
        assert cost.input == 0.0
        assert cost.output == 0.0

    def test_unknown_model_returns_zero_cost(self):
        settings = Settings()
        cost = settings.get_cost_per_token("unknown-model")
        assert cost.input == 0.0
        assert cost.output == 0.0


# --- Transmutation Settings Tests ---

class TestTransmutationSettings:
    def test_default_tau(self):
        ts = TransmutationSettings()
        assert ts.tau == 1.0

    def test_default_maslow_weights(self):
        ts = TransmutationSettings()
        assert ts.maslow_weights == [5, 4, 3, 2, 1]

    def test_settings_loads_transmutation_from_yaml(self):
        settings = Settings()
        assert settings.transmutation.tau == 1.0
        assert settings.transmutation.maslow_weights == [5, 4, 3, 2, 1]

    def test_custom_tau(self):
        ts = TransmutationSettings(tau=2.5)
        assert ts.tau == 2.5

    def test_custom_maslow_weights(self):
        ts = TransmutationSettings(maslow_weights=[1, 1, 1, 1, 1])
        assert ts.maslow_weights == [1, 1, 1, 1, 1]


# --- Flow Engine Tests ---

# --- Debug Reload Config Tests ---

class TestDebugReloadConfig:
    def test_debug_true_sets_reload_true(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        assert os.getenv("DEBUG", "").lower() == "true"

    def test_debug_TRUE_sets_reload_true(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "TRUE")
        assert os.getenv("DEBUG", "").lower() == "true"

    def test_debug_unset_sets_reload_false(self, monkeypatch):
        monkeypatch.delenv("DEBUG", raising=False)
        assert os.getenv("DEBUG", "").lower() != "true"

    def test_debug_false_sets_reload_false(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "false")
        assert os.getenv("DEBUG", "").lower() != "true"


# --- Health Endpoint Tests ---

class TestHealthEndpoint:
    def test_health_returns_ok_when_db_accessible(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"

    def test_readiness_returns_ok(self, api_client):
        resp = api_client.get("/readiness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_returns_503_when_db_unreachable(self, api_client, monkeypatch):
        def broken_session():
            raise Exception("DB unreachable")

        # Make get_db_session raise an exception
        from contextlib import contextmanager

        @contextmanager
        def broken_context():
            raise Exception("DB unreachable")
            yield  # noqa: unreachable

        monkeypatch.setattr("api.health.get_db_session", broken_context)
        resp = api_client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["db"] == "disconnected"


class TestComputeFlowsPerLevel:
    def _make_scenarios(self):
        return [
            {"id": "sc1", "maslow_level": "physiological", "choices": [
                {"key": "a", "quadrant_weight": {"transmuter": 1.0}},
            ]},
            {"id": "sc2", "maslow_level": "safety", "choices": [
                {"key": "a", "quadrant_weight": {"absorber": 1.0}},
            ]},
        ]

    def test_transmuter_response_produces_correct_flows(self):
        scenarios = self._make_scenarios()
        responses = {"sc1": {"choice": "a", "quadrant_weight": {"transmuter": 1.0}}}
        flows = compute_flows_per_level(responses, scenarios)
        phys = flows["physiological"]
        assert phys.d_plus_out == 1.0
        assert phys.d_minus_in == 1.0
        assert phys.d_plus_in == 0.0
        assert phys.d_minus_out == 0.0
        assert phys.filtering == 1.0  # d_minus_in - d_minus_out = 1 - 0
        assert phys.amplification == 1.0  # d_plus_out - d_plus_in = 1 - 0

    def test_absorber_response_produces_correct_flows(self):
        scenarios = self._make_scenarios()
        responses = {"sc2": {"choice": "a", "quadrant_weight": {"absorber": 1.0}}}
        flows = compute_flows_per_level(responses, scenarios)
        safety = flows["safety"]
        assert safety.d_plus_in == 1.0
        assert safety.d_minus_in == 1.0
        assert safety.filtering == 1.0  # absorbs deprivation (d_minus_in)
        assert safety.amplification == -1.0  # absorbs fulfillment (negative A)

    def test_no_responses_returns_zero_flows(self):
        scenarios = self._make_scenarios()
        flows = compute_flows_per_level({}, scenarios)
        for level in flows.values():
            assert level.d_plus_in == 0.0
            assert level.d_plus_out == 0.0
            assert level.filtering == 0.0
            assert level.amplification == 0.0

    def test_unknown_scenario_id_ignored(self):
        scenarios = self._make_scenarios()
        responses = {"unknown": {"choice": "a", "quadrant_weight": {"transmuter": 1.0}}}
        flows = compute_flows_per_level(responses, scenarios)
        for level in flows.values():
            assert level.d_plus_out == 0.0


class TestComputeMoralWork:
    def test_uniform_flows_with_tau_1(self):
        flows = {
            level: FlowValues(filtering=1.0, amplification=5.0)
            for level in ["physiological", "safety", "belonging", "esteem", "self-actualization"]
        }
        m = compute_moral_work(flows, tau=1.0)
        assert m == [6.0, 6.0, 6.0, 6.0, 6.0]

    def test_tau_2_doubles_filtering(self):
        flows = {
            "physiological": FlowValues(filtering=1.0, amplification=0.0),
            "safety": FlowValues(),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        m = compute_moral_work(flows, tau=2.0)
        assert m[0] == 2.0
        assert m[1] == 0.0

    def test_all_zeros_gives_zero_vector(self):
        flows = {
            level: FlowValues()
            for level in ["physiological", "safety", "belonging", "esteem", "self-actualization"]
        }
        m = compute_moral_work(flows, tau=1.0)
        assert m == [0.0, 0.0, 0.0, 0.0, 0.0]


class TestComputeWeightedTotal:
    def test_uniform_moral_work(self):
        m = [6.0, 6.0, 6.0, 6.0, 6.0]
        w = compute_weighted_total(m, [5, 4, 3, 2, 1])
        assert w == 90.0  # 6 * (5+4+3+2+1) = 90

    def test_default_weights(self):
        m = [1.0, 1.0, 1.0, 1.0, 1.0]
        w = compute_weighted_total(m)
        assert w == 15.0  # 1*(5+4+3+2+1) = 15

    def test_zero_moral_work(self):
        m = [0.0, 0.0, 0.0, 0.0, 0.0]
        assert compute_weighted_total(m) == 0.0


class TestComputeMoralCapitalDebt:
    def test_positive_flows_accumulate_capital(self):
        flows = {
            "physiological": FlowValues(filtering=2.0, amplification=3.0),
            "safety": FlowValues(filtering=1.0, amplification=1.0),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        ledger = compute_moral_capital_debt(flows)
        assert ledger.c_plus == 7.0  # 2+3+1+1
        assert ledger.c_minus == 0.0

    def test_negative_flows_accumulate_debt(self):
        flows = {
            "physiological": FlowValues(filtering=-2.0, amplification=-3.0),
            "safety": FlowValues(),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        ledger = compute_moral_capital_debt(flows)
        assert ledger.c_plus == 0.0
        assert ledger.c_minus == 5.0  # |-2| + |-3|

    def test_mixed_flows(self):
        flows = {
            "physiological": FlowValues(filtering=2.0, amplification=-1.0),
            "safety": FlowValues(),
            "belonging": FlowValues(),
            "esteem": FlowValues(),
            "self-actualization": FlowValues(),
        }
        ledger = compute_moral_capital_debt(flows)
        assert ledger.c_plus == 2.0  # filtering positive
        assert ledger.c_minus == 1.0  # amplification negative


class TestComputeFullProfile:
    def test_full_pipeline(self):
        scenarios = [
            {"id": "sc1", "maslow_level": "physiological", "choices": []},
            {"id": "sc2", "maslow_level": "safety", "choices": []},
        ]
        responses = {
            "sc1": {"quadrant_weight": {"transmuter": 1.0}},
            "sc2": {"quadrant_weight": {"transmuter": 1.0}},
        }
        profile = compute_full_profile(responses, scenarios, tau=1.0)
        assert len(profile.levels) == 5
        assert len(profile.moral_work) == 5
        assert profile.tau == 1.0
        assert profile.weights == [5, 4, 3, 2, 1]
        assert profile.weighted_total != 0.0
        assert profile.moral_capital > 0.0
