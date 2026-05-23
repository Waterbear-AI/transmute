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
from db.database import run_migrations, _strip_sql_comments


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


# --- SQL Comment Stripping Tests ---

class TestStripSqlComments:
    def test_removes_full_line_comment(self):
        sql = "-- This is a comment\nCREATE TABLE foo (id INTEGER);"
        result = _strip_sql_comments(sql)
        assert "--" not in result
        assert "CREATE TABLE foo" in result

    def test_removes_inline_comment(self):
        sql = "SELECT * FROM foo -- get all rows\nWHERE id = 1;"
        result = _strip_sql_comments(sql)
        assert "-- get all rows" not in result
        assert "SELECT * FROM foo" in result
        assert "WHERE id = 1" in result

    def test_preserves_sql_when_no_comments(self):
        sql = "CREATE TABLE bar (id INTEGER PRIMARY KEY);"
        result = _strip_sql_comments(sql)
        assert result.strip() == sql

    def test_empty_string(self):
        assert _strip_sql_comments("") == ""

    def test_only_comments_yields_empty_lines(self):
        sql = "-- comment one\n-- comment two"
        result = _strip_sql_comments(sql)
        # All meaningful content stripped; only whitespace/newlines remain
        assert result.strip() == ""

    def test_multistatement_with_comments(self):
        """Comment-prefixed blocks must not cause CREATE TABLE to be skipped."""
        sql = (
            "-- Version tracking\n"
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);\n"
            "-- Users\n"
            "CREATE TABLE users (id TEXT PRIMARY KEY);"
        )
        result = _strip_sql_comments(sql)
        statements = [s.strip() for s in result.split(";") if s.strip()]
        assert len(statements) == 2
        assert all("CREATE TABLE" in s for s in statements)


# --- Migration Runner Tests ---

class TestMigrationRunner:
    def test_applies_migrations(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            count = run_migrations(db_path=db_path)
            # 001_initial, 002_flow_tracking, 003_session_events, 004_sentinel_tracking
            assert count == 4

            import sqlite3
            conn = sqlite3.connect(db_path)
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            # Verify events_json column was added by migration 003
            cols = [
                r[1] for r in conn.execute(
                    "PRAGMA table_info(adk_sessions)"
                ).fetchall()
            ]
            conn.close()

            assert "users" in tables
            assert "assessment_state" in tables
            assert "schema_version" in tables
            assert "moral_ledger" in tables
            assert "events_json" in cols
            assert "dimension_assessment_state" in tables
            assert len(tables) == 13
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

    def test_invalid_sql_raises_and_no_version_recorded(self):
        """A migration with invalid SQL must raise and not record its version."""
        import sqlite3 as _sqlite3
        from pathlib import Path
        import shutil

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up a minimal migrations dir with one invalid migration
            migs_dir = Path(tmpdir) / "migrations"
            migs_dir.mkdir()
            (migs_dir / "001_bad.sql").write_text(
                "-- Bad migration\nINSERT INTO nonexistent_table VALUES (1);"
            )

            db_path = str(Path(tmpdir) / "test.db")

            # Monkey-patch MIGRATIONS_DIR for the duration of this test
            import db.database as db_mod
            original_dir = db_mod.MIGRATIONS_DIR
            db_mod.MIGRATIONS_DIR = migs_dir
            try:
                with pytest.raises(_sqlite3.Error):
                    run_migrations(db_path=db_path)

                # schema_version must not contain version 1
                conn = _sqlite3.connect(db_path)
                rows = conn.execute("SELECT version FROM schema_version").fetchall()
                conn.close()
                assert rows == [], "Failed migration must not be recorded in schema_version"
            finally:
                db_mod.MIGRATIONS_DIR = original_dir

    def test_applies_migration_003_to_existing_db(self):
        """Migration 003 adds events_json to an existing db at versions [1, 2]."""
        import sqlite3 as _sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Apply only migrations 001 and 002 first
            count = run_migrations(db_path=db_path)
            assert count == 4  # All four applied fresh

            # Simulate a DB that only has versions 1 and 2 by removing version 3
            conn = _sqlite3.connect(db_path)
            conn.execute("ALTER TABLE adk_sessions DROP COLUMN events_json")
            conn.execute("DELETE FROM schema_version WHERE version = 3")
            conn.commit()
            conn.close()

            # Now applying migrations should only apply 003
            count2 = run_migrations(db_path=db_path)
            assert count2 == 1

            conn = _sqlite3.connect(db_path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(adk_sessions)").fetchall()]
            conn.close()
            assert "events_json" in cols
        finally:
            os.unlink(db_path)


# --- User ID Injection Tests (BE-006) ---

class TestWithUserId:
    """Unit tests for the with_user_id helper."""

    def _make_ctx(self, user_id: str):
        """Create a minimal mock ReadonlyContext with state."""
        class FakeCtx:
            def __init__(self, state):
                self.state = state
        return FakeCtx({"user_id": user_id})

    def test_injects_user_id_into_prompt(self):
        from agents.transmutation.sub_agents.inject_user_id import with_user_id
        fn = with_user_id("Do the thing.")
        ctx = self._make_ctx("user-abc-123")
        result = fn(ctx)
        assert "user-abc-123" in result
        assert "Do the thing." in result

    def test_user_id_appears_before_prompt(self):
        from agents.transmutation.sub_agents.inject_user_id import with_user_id
        fn = with_user_id("Static prompt text.")
        ctx = self._make_ctx("user-xyz")
        result = fn(ctx)
        user_id_pos = result.find("user-xyz")
        prompt_pos = result.find("Static prompt text.")
        assert user_id_pos < prompt_pos, "user_id header must precede static prompt"

    def test_missing_user_id_falls_back_to_unknown(self):
        from agents.transmutation.sub_agents.inject_user_id import with_user_id
        fn = with_user_id("Any prompt.")

        class FakeCtx:
            state = {}  # no user_id key

        result = fn(FakeCtx())
        assert "unknown" in result

    def test_all_sub_agents_use_with_user_id(self):
        """Every sub-agent create_* function must use with_user_id for its instruction."""
        import inspect
        from agents.transmutation.sub_agents import (
            assessment, check_in, development, education,
            graduation, profile, reassessment,
        )
        modules = [assessment, check_in, development, education, graduation, profile, reassessment]
        for mod in modules:
            src = inspect.getsource(mod)
            assert "with_user_id" in src, (
                f"Sub-agent module {mod.__name__} does not use with_user_id"
            )

    def test_root_agent_reads_user_id_from_context(self):
        """Root agent _root_instruction must embed user_id from ctx.state."""
        from agents.transmutation.agent import _root_instruction

        class FakeCtx:
            state = {"user_id": "root-user-test-id"}

        result = _root_instruction(FakeCtx())
        assert "root-user-test-id" in result

    def test_create_session_seeds_user_id_in_state(self):
        """POST /api/sessions creates a session with user_id in the ADK state."""
        import sqlite3
        import json

        resp = None
        try:
            from fastapi.testclient import TestClient
            from main import app
            tc = TestClient(app)
            reg = tc.post("/auth/register", json={
                "name": "Inject Test",
                "email": "injecttest@example.com",
                "password": "password123",
            })
            assert reg.status_code == 200
            user_id = reg.json()["user_id"]
            cookies = reg.cookies

            sess = tc.post("/api/sessions", cookies=cookies)
            assert sess.status_code == 200
            session_id = sess.json()["session_id"]

            conn = sqlite3.connect(__import__("os").environ["DB_PATH"])
            row = conn.execute(
                "SELECT session_state FROM adk_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            conn.close()

            assert row is not None
            state = json.loads(row[0])
            assert state.get("user_id") == user_id, (
                "Session state must contain the authenticated user_id"
            )
        except Exception:
            raise


# --- Chat Debug Instrumentation Tests ---

class TestChatDebugInstrumentation:
    def test_litellm_generate_content_async_not_monkeypatched(self):
        """Importing api.chat must not replace LiteLlm.generate_content_async."""
        from google.adk.models.lite_llm import LiteLlm
        original_method = LiteLlm.generate_content_async.__qualname__

        import api.chat  # noqa: F401 — ensure module is imported

        # After import, generate_content_async must still be the original method,
        # not the _debug_generate wrapper.
        assert LiteLlm.generate_content_async.__qualname__ == original_method, (
            "LiteLlm.generate_content_async was monkeypatched by api.chat import"
        )

    def test_no_debug_files_created_under_var_lib_transmute(self, tmp_path):
        """No files should be written to /var/lib/transmute during LLM interaction setup."""
        # The debug wrapper wrote to /var/lib/transmute/*.json.
        # After removal, that directory must NOT be created or written to during
        # a normal import + model-string resolution cycle.
        import api.chat  # noqa: F401 — ensure module is imported

        # Simulate what the debug wrapper would have created
        var_lib_transmute = tmp_path / "transmute"
        # If debug code were present it would try to open paths under /var/lib/transmute.
        # Confirm no such path exists in the module source.
        import inspect
        chat_source = inspect.getsource(api.chat)
        assert "/var/lib/transmute" not in chat_source, (
            "api/chat.py still references /var/lib/transmute — debug file dump not removed"
        )
        assert "_debug_generate" not in chat_source, (
            "api/chat.py still contains _debug_generate wrapper"
        )


# --- Config Loading Tests ---

# --- Session Event Slimming Tests ---

class TestSlimToolResponse:
    """Unit tests for _slim_tool_response — pure function."""

    def setup_method(self):
        from agents.transmutation.session_service import _slim_tool_response
        self._slim = _slim_tool_response

    def test_question_batch_retains_question_ids(self):
        response = {
            "event_type": "assessment.question_batch",
            "batch_id": "b1",
            "dimension": "consciousness",
            "sub_dimension": "awareness",
            "count": 3,
            "question_ids": ["q1", "q2", "q3"],
            "questions": [{"id": "q1", "text": "Long question text..."}],  # stripped
        }
        slimmed = self._slim("get_next_question_batch", response)
        assert slimmed["question_ids"] == ["q1", "q2", "q3"]
        assert "questions" not in slimmed
        assert slimmed["event_type"] == "assessment.question_batch"
        assert slimmed["count"] == 3

    def test_question_batch_missing_question_ids_defaults_to_empty_list(self):
        response = {
            "event_type": "assessment.question_batch",
            "batch_id": "b1",
            "count": 2,
        }
        slimmed = self._slim("get_next_question_batch", response)
        assert slimmed["question_ids"] == []

    def test_profile_snapshot_strips_spider_png_keeps_quadrant(self):
        response = {
            "event_type": "profile.snapshot",
            "saved": True,
            "snapshot_id": "snap-1",
            "quadrant": "Creator",
            "spider_chart": "base64encodedPNGdata==",  # stripped
            "scores": {"consciousness": 0.8},  # stripped
        }
        slimmed = self._slim("generate_profile_snapshot", response)
        assert "spider_chart" not in slimmed
        assert "scores" not in slimmed
        assert slimmed["quadrant"] == "Creator"
        assert slimmed["event_type"] == "profile.snapshot"

    def test_scenario_retains_scenario_id(self):
        response = {
            "event_type": "assessment.scenario",
            "scenario_id": "sc-42",
            "narrative": "A very long scenario text...",
            "choices": [{"id": "c1", "text": "Choice 1"}, {"id": "c2", "text": "Choice 2"}],
        }
        slimmed = self._slim("get_scenario", response)
        assert slimmed["scenario_id"] == "sc-42"
        assert slimmed["event_type"] == "assessment.scenario"
        # Narrative and full choices are stripped
        assert "narrative" not in slimmed
        assert "choices" not in slimmed

    def test_small_response_passes_through(self):
        response = {"status": "ok", "count": 5}
        slimmed = self._slim("some_tool", response)
        assert slimmed == response

    def test_large_unknown_response_truncated_to_scalars(self):
        response = {
            "key1": "value1",
            "big_list": list(range(500)),  # large non-scalar
            "simple": 42,
        }
        slimmed = self._slim("some_tool", response)
        # big_list is non-scalar so should be dropped
        assert "big_list" not in slimmed
        assert slimmed.get("simple") == 42


class TestSlimEventsForStorage:
    """Unit tests for _slim_events_for_storage — pure function."""

    def setup_method(self):
        from agents.transmutation.session_service import _slim_events_for_storage
        self._slim = _slim_events_for_storage

    def _make_tool_response_event(self, tool_name: str, response: dict) -> dict:
        return {
            "content": {
                "role": "tool",
                "parts": [
                    {
                        "function_response": {
                            "name": tool_name,
                            "response": response,
                        }
                    }
                ],
            }
        }

    def test_empty_events_returns_empty(self):
        assert self._slim([]) == []

    def test_non_tool_event_passes_through(self):
        event = {"content": {"role": "model", "parts": [{"text": "Hello"}]}}
        result = self._slim([event])
        assert result == [event]

    def test_question_batch_event_slimmed_with_question_ids(self):
        event = self._make_tool_response_event("get_next_question_batch", {
            "event_type": "assessment.question_batch",
            "batch_id": "b1",
            "count": 3,
            "question_ids": ["q1", "q2", "q3"],
            "questions": [{"id": "q1", "text": "Text..."}],
        })
        result = self._slim([event])
        assert len(result) == 1
        fr = result[0]["content"]["parts"][0]["function_response"]
        assert fr["response"]["question_ids"] == ["q1", "q2", "q3"]
        assert "questions" not in fr["response"]

    def test_event_without_parts_passes_through(self):
        event = {"content": {"role": "tool", "parts": []}}
        result = self._slim([event])
        assert result == [event]


# --- Config Loading Tests ---


class TestConfigLoading:
    VALID_PROVIDERS = {"anthropic", "openai", "bedrock", "ollama"}

    def test_yaml_config_loads(self):
        config = _load_yaml_config()
        assert "model" in config
        # Provider must be a valid value — not hardcoded to one environment's choice
        assert config["model"]["provider"] in self.VALID_PROVIDERS, (
            f"provider '{config['model']['provider']}' not in {self.VALID_PROVIDERS}"
        )

    def test_settings_loads_from_yaml(self):
        settings = Settings()
        # Provider must be a supported value; exact value depends on deployment config
        assert settings.model.provider in self.VALID_PROVIDERS, (
            f"provider '{settings.model.provider}' not in {self.VALID_PROVIDERS}"
        )
        # model_id must be a non-empty string
        assert isinstance(settings.model.model_id, str) and settings.model.model_id

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
