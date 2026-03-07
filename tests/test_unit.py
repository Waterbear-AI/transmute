import os
import tempfile

import bcrypt
import pytest

from api.auth import _sign_cookie, _verify_cookie
from config import Settings, TransmutationSettings, _load_yaml_config
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
            assert count == 1

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
            assert len(tables) == 11
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
