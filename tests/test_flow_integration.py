"""Integration tests for flow data persistence and comparison snapshot deltas.

Validates the Service+DB boundary: generate_profile_snapshot() and
save_profile_snapshot() correctly persist flow_data JSON and moral_ledger entries.
Also validates generate_comparison_snapshot() returns accurate flow deltas.
"""

import json
import os
import sqlite3

import pytest

from agents.transmutation.tools import (
    generate_comparison_snapshot,
    generate_profile_snapshot,
    save_profile_snapshot,
    _profile_cache,
)
from models.moral_profile import MoralProfile


def _insert_user(conn, user_id="test-user-1"):
    """Insert a minimal user row."""
    import bcrypt

    hashed = bcrypt.hashpw(b"password", bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
        (user_id, "Test User", "test@example.com", hashed, "assessment"),
    )
    conn.commit()


def _insert_assessment(conn, user_id, responses, scenario_responses):
    """Insert assessment_state with given responses."""
    import uuid

    conn.execute(
        "INSERT INTO assessment_state (id, user_id, responses, scenario_responses) VALUES (?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            user_id,
            json.dumps(responses),
            json.dumps(scenario_responses),
        ),
    )
    conn.commit()


def _build_scenario_responses(archetype="transmuter"):
    """Build scenario responses covering all 5 Maslow levels.

    Args:
        archetype: Which archetype weight to assign. Default "transmuter".
            transmuter → D+out=1, D-in=1 → F=1, A=1 → M=2 per level.
            absorber → D+in=1, D-in=1 → F=1, A=-1 → M=0 per level (tau=1).
    """
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    scenarios = qb.get_all_scenarios()

    responses = {}
    for sc in scenarios:
        sc_id = sc["id"]
        choices = sc.get("choices", [])
        if not choices:
            continue
        responses[sc_id] = {
            "choice": choices[0]["key"],
            "quadrant_weight": {archetype: 1.0},
        }
    return responses


def _build_likert_responses():
    """Build minimal Likert responses so scoring doesn't fail."""
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    responses = {}
    for dim in qb.get_dimensions():
        for q in qb.get_questions_by_dimension(dim):
            responses[q["id"]] = {"score": 3}
    return responses


class TestFlowDataPersistence:
    """Verify flow_data JSON is correctly persisted in profile_snapshots."""

    def test_generate_and_save_persists_flow_data(self):
        user_id = "flow-test-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        likert = _build_likert_responses()
        scenarios = _build_scenario_responses()
        _insert_assessment(conn, user_id, likert, scenarios)
        conn.close()

        # Generate profile (populates _profile_cache)
        gen_result = generate_profile_snapshot(user_id)
        assert "error" not in gen_result
        assert "flow_data" in gen_result

        # Save profile (persists to DB)
        save_result = save_profile_snapshot(user_id, "Test interpretation")
        assert save_result.get("saved") is True
        snapshot_id = save_result["snapshot_id"]

        # Verify flow_data column in profile_snapshots
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT flow_data FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["flow_data"] is not None

        # Parse and validate flow_data structure
        flow = MoralProfile.model_validate_json(row["flow_data"])
        assert len(flow.levels) == 5
        assert len(flow.moral_work) == 5
        assert flow.tau == 1.0
        assert flow.weights == [5, 4, 3, 2, 1]
        assert isinstance(flow.weighted_total, float)
        assert isinstance(flow.moral_capital, float)
        assert isinstance(flow.moral_debt, float)

    def test_flow_data_contains_correct_level_names(self):
        user_id = "flow-levels-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        save_result = save_profile_snapshot(user_id, "Level names test")
        snapshot_id = save_result["snapshot_id"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT flow_data FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()

        flow = MoralProfile.model_validate_json(row["flow_data"])
        level_names = [lf.level for lf in flow.levels]
        assert level_names == [
            "physiological", "safety", "belonging", "esteem", "self-actualization",
        ]

    def test_transmuter_responses_produce_positive_flows(self):
        """All-transmuter responses should yield positive filtering and amplification."""
        user_id = "flow-positive-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        gen_result = generate_profile_snapshot(user_id)
        flow_data = gen_result["flow_data"]

        # Transmuter: D+out and D-in are positive → F>0, A>0
        for level_flow in flow_data["levels"]:
            flows = level_flow["flows"]
            # Levels with scenarios should have positive flows
            if flows["d_minus_in"] > 0:
                assert flows["filtering"] > 0
            if flows["d_plus_out"] > 0:
                assert flows["amplification"] > 0

        assert flow_data["moral_capital"] > 0
        assert flow_data["weighted_total"] > 0

        # Clean up cache
        _profile_cache.pop(user_id, None)


class TestMoralLedgerPersistence:
    """Verify moral_ledger entries are correctly created."""

    def test_moral_ledger_entry_created(self):
        user_id = "ledger-test-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        save_result = save_profile_snapshot(user_id, "Ledger test")
        snapshot_id = save_result["snapshot_id"]

        # Verify moral_ledger entry
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ledger = conn.execute(
            "SELECT * FROM moral_ledger WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()

        assert ledger is not None
        assert ledger["user_id"] == user_id
        assert ledger["snapshot_id"] == snapshot_id
        assert isinstance(ledger["c_plus"], float)
        assert isinstance(ledger["c_minus"], float)
        # Transmuter responses should yield positive capital
        assert ledger["c_plus"] > 0

    def test_moral_ledger_links_to_correct_snapshot(self):
        user_id = "ledger-link-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        save_result = save_profile_snapshot(user_id, "Link test")
        snapshot_id = save_result["snapshot_id"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Verify snapshot exists
        snapshot = conn.execute(
            "SELECT id FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        assert snapshot is not None

        # Verify ledger references it
        ledger = conn.execute(
            "SELECT snapshot_id FROM moral_ledger WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()

        assert ledger["snapshot_id"] == snapshot_id


class TestFlowDataEdgeCases:
    """Edge cases for flow data persistence."""

    def test_zero_scenario_responses_produces_zero_flows(self):
        """No scenario responses → all flows zero, but flow_data still persisted."""
        user_id = "zero-flow-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        # Likert only, no scenarios
        _insert_assessment(conn, user_id, _build_likert_responses(), {})
        conn.close()

        gen_result = generate_profile_snapshot(user_id)
        assert "flow_data" in gen_result

        flow_data = gen_result["flow_data"]
        # All moral_work should be zero
        assert all(m == 0.0 for m in flow_data["moral_work"])
        assert flow_data["weighted_total"] == 0.0
        assert flow_data["moral_capital"] == 0.0
        assert flow_data["moral_debt"] == 0.0

        # Save and verify it persists even with zero values
        save_result = save_profile_snapshot(user_id, "Zero flows")
        assert save_result.get("saved") is True

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT flow_data FROM profile_snapshots WHERE id = ?",
            (save_result["snapshot_id"],),
        ).fetchone()
        conn.close()

        assert row["flow_data"] is not None
        flow = MoralProfile.model_validate_json(row["flow_data"])
        assert flow.weighted_total == 0.0

    def test_no_likert_responses_still_produces_flow_data(self):
        """Scenario-only assessment still generates flow data."""
        user_id = "scenario-only-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, {}, _build_scenario_responses())
        conn.close()

        gen_result = generate_profile_snapshot(user_id)
        assert "flow_data" in gen_result
        # Flow data should be non-zero from scenarios
        assert gen_result["flow_data"]["weighted_total"] != 0.0

        # Clean up cache
        _profile_cache.pop(user_id, None)

    def test_moral_ledger_values_match_flow_data(self):
        """C+ and C- in moral_ledger match moral_capital/debt in flow_data."""
        user_id = "ledger-match-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        save_result = save_profile_snapshot(user_id, "Match test")
        snapshot_id = save_result["snapshot_id"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT flow_data FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        ledger = conn.execute(
            "SELECT c_plus, c_minus FROM moral_ledger WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()

        flow = MoralProfile.model_validate_json(row["flow_data"])
        assert ledger["c_plus"] == flow.moral_capital
        assert ledger["c_minus"] == flow.moral_debt


def _create_snapshot(user_id, archetype="transmuter", interpretation="test"):
    """Helper: create assessment → generate → save → return snapshot_id."""
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Delete previous assessment so generate picks up new one
    conn.execute("DELETE FROM assessment_state WHERE user_id = ?", (user_id,))
    conn.commit()

    _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses(archetype))
    conn.close()

    generate_profile_snapshot(user_id)
    result = save_profile_snapshot(user_id, interpretation)
    return result["snapshot_id"]


class TestComparisonSnapshotDeltas:
    """Verify generate_comparison_snapshot() returns accurate flow deltas."""

    def test_comparison_includes_flow_deltas(self):
        """Two snapshots with different archetypes produce non-zero flow deltas."""
        user_id = "comparison-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        conn.close()

        # First snapshot: transmuter (F>0, A>0)
        first_id = _create_snapshot(user_id, archetype="transmuter", interpretation="First")

        # Second snapshot: absorber (F>0, A<0 — different moral work)
        second_id = _create_snapshot(user_id, archetype="absorber", interpretation="Second")

        # Compare second (current) against first (previous)
        comparison = generate_comparison_snapshot(user_id, first_id)
        assert "error" not in comparison
        assert "flow_deltas" in comparison

        flow_deltas = comparison["flow_deltas"]

        # Verify structure
        assert "moral_work" in flow_deltas
        assert "weighted_total" in flow_deltas
        assert "moral_capital" in flow_deltas
        assert "moral_debt" in flow_deltas

        # Verify moral_work delta structure
        mw = flow_deltas["moral_work"]
        assert "previous" in mw
        assert "current" in mw
        assert "delta" in mw
        assert len(mw["delta"]) == 5

        # Transmuter vs absorber should produce different M vectors → non-zero deltas
        assert any(d != 0 for d in mw["delta"])

        # weighted_total should differ
        assert flow_deltas["weighted_total"]["delta"] != 0

    def test_comparison_delta_values_are_correct(self):
        """Verify delta = current - previous for all flow metrics."""
        user_id = "delta-math-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        conn.close()

        first_id = _create_snapshot(user_id, archetype="transmuter")
        second_id = _create_snapshot(user_id, archetype="absorber")

        comparison = generate_comparison_snapshot(user_id, first_id)
        flow_deltas = comparison["flow_deltas"]

        # Verify delta = current - previous for weighted_total
        wt = flow_deltas["weighted_total"]
        assert wt["delta"] == round(wt["current"] - wt["previous"], 4)

        # Verify delta = current - previous for moral_capital
        mc = flow_deltas["moral_capital"]
        assert mc["delta"] == round(mc["current"] - mc["previous"], 4)

        # Verify delta = current - previous for moral_debt
        md = flow_deltas["moral_debt"]
        assert md["delta"] == round(md["current"] - md["previous"], 4)

        # Verify moral_work vector deltas
        mw = flow_deltas["moral_work"]
        for i, d in enumerate(mw["delta"]):
            assert d == round(mw["current"][i] - mw["previous"][i], 4)

    def test_identical_snapshots_produce_zero_deltas(self):
        """Comparing two identical-archetype snapshots yields zero flow deltas."""
        user_id = "identical-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        conn.close()

        first_id = _create_snapshot(user_id, archetype="transmuter")
        second_id = _create_snapshot(user_id, archetype="transmuter")

        comparison = generate_comparison_snapshot(user_id, first_id)
        assert "flow_deltas" in comparison

        flow_deltas = comparison["flow_deltas"]

        # All moral_work deltas should be zero
        assert all(d == 0 for d in flow_deltas["moral_work"]["delta"])
        assert flow_deltas["weighted_total"]["delta"] == 0
        assert flow_deltas["moral_capital"]["delta"] == 0
        assert flow_deltas["moral_debt"]["delta"] == 0

    def test_comparison_without_flow_data_omits_flow_deltas(self):
        """Snapshots without flow_data should not include flow_deltas key."""
        user_id = "no-flow-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        # Manually insert snapshots without flow_data
        import uuid
        from datetime import datetime

        snap1_id = str(uuid.uuid4())
        snap2_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        for snap_id in [snap1_id, snap2_id]:
            conn.execute(
                """INSERT INTO profile_snapshots
                   (id, user_id, scores, quadrant_placement, interpretation, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (snap_id, user_id, "{}", "{}", "test", now),
            )
        conn.commit()
        conn.close()

        comparison = generate_comparison_snapshot(user_id, snap1_id)
        assert "error" not in comparison
        assert "flow_deltas" not in comparison
