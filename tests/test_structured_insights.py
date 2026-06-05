"""Integration tests for structured_insights column in profile_snapshots.

Validates:
- Migration adds the column (schema check).
- save_profile_snapshot with structured_insights persists the data.
- save_profile_snapshot without structured_insights (legacy call) works fine.
- GET /api/results returns structured_insights in the response.
- Corrupt structured_insights in DB → API returns null (no 500).
"""

import json
import os
import sqlite3

import pytest

from agents.transmutation.tools import (
    generate_profile_snapshot,
    save_profile_snapshot,
    _profile_cache,
)


def _insert_user(conn, user_id, email=None):
    """Insert a minimal user row."""
    import bcrypt

    if email is None:
        email = f"{user_id}@example.com"
    hashed = bcrypt.hashpw(b"password", bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users (id, name, email, password_hash, current_phase) VALUES (?, ?, ?, ?, ?)",
        (user_id, "Test User", email, hashed, "assessment"),
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


def _build_likert_responses():
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    responses = {}
    for dim in qb.get_dimensions():
        for q in qb.get_questions_by_dimension(dim):
            responses[q["id"]] = {"score": 3}
    return responses


def _build_scenario_responses(archetype="transmuter"):
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


def _make_structured_insights():
    """Return a representative structured_insights payload."""
    return {
        "strengths": [
            {
                "dimension": "Meta-Cognitive Awareness",
                "level": "Strong",
                "score": 3.38,
                "note": "You show exceptional ability to monitor your own thinking.",
            }
        ],
        "growth_areas": [
            {
                "dimension": "Emotional Awareness",
                "level": "Developing",
                "score": 2.79,
                "note": "Recognising emotional triggers earlier could unlock new patterns.",
            }
        ],
        "cross_dimensional_insights": [
            "You see downstream effects but miss upstream triggers.",
            "Strong temporal awareness compensates for lower emotional regulation.",
        ],
    }


class TestMigrationSchema:
    """Verify the migration adds the expected column."""

    def test_structured_insights_column_exists(self):
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        # PRAGMA table_info returns one row per column
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(profile_snapshots)"
            ).fetchall()
        }
        conn.close()
        assert "structured_insights" in columns, (
            "structured_insights column missing from profile_snapshots"
        )

    def test_structured_insights_column_is_nullable(self):
        """Column must accept NULL (no NOT NULL constraint)."""
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "PRAGMA table_info(profile_snapshots)"
        ).fetchall()
        conn.close()
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
        col = next((r for r in rows if r[1] == "structured_insights"), None)
        assert col is not None
        notnull = col[3]
        assert notnull == 0, "structured_insights must be nullable (notnull=0)"


class TestStructuredInsightsPersistence:
    """Verify save_profile_snapshot persists structured_insights correctly."""

    def test_save_with_structured_insights_persists_json(self):
        user_id = "si-persist-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        insights = _make_structured_insights()
        result = save_profile_snapshot(user_id, "Test teaser", structured_insights=insights)

        assert result.get("saved") is True
        snapshot_id = result["snapshot_id"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT structured_insights FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["structured_insights"] is not None
        persisted = json.loads(row["structured_insights"])
        assert persisted["strengths"][0]["dimension"] == "Meta-Cognitive Awareness"
        assert len(persisted["cross_dimensional_insights"]) == 2

    def test_save_without_structured_insights_stores_null(self):
        """Legacy call (no structured_insights arg) must store NULL, not crash."""
        user_id = "si-legacy-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        # Positional call — original signature, must remain source-compatible
        result = save_profile_snapshot(user_id, "Legacy interpretation")
        assert result.get("saved") is True
        snapshot_id = result["snapshot_id"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT structured_insights FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["structured_insights"] is None

    def test_save_result_includes_structured_insights_dict(self):
        """The returned payload must contain structured_insights as a dict, not a JSON string."""
        user_id = "si-return-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        insights = _make_structured_insights()
        result = save_profile_snapshot(user_id, "Return test", structured_insights=insights)

        assert "structured_insights" in result
        returned_insights = result["structured_insights"]
        assert isinstance(returned_insights, dict)
        assert "strengths" in returned_insights
        assert "growth_areas" in returned_insights
        assert "cross_dimensional_insights" in returned_insights

    def test_save_with_none_structured_insights_stores_null(self):
        """Explicitly passing None must store NULL."""
        user_id = "si-none-user"
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        result = save_profile_snapshot(user_id, "None test", structured_insights=None)
        assert result.get("saved") is True
        snapshot_id = result["snapshot_id"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT structured_insights FROM profile_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        conn.close()
        assert row["structured_insights"] is None


class TestStructuredInsightsAPIContract:
    """Verify GET /api/results returns structured_insights correctly."""

    def test_api_returns_structured_insights(self, authenticated_client):
        user_id = authenticated_client.user_id
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        insights = _make_structured_insights()
        save_profile_snapshot(user_id, "API test teaser", structured_insights=insights)

        resp = authenticated_client.get(f"/api/results/{user_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert "latest_profile" in data
        profile = data["latest_profile"]
        assert profile is not None
        assert "structured_insights" in profile
        si = profile["structured_insights"]
        assert si is not None
        assert isinstance(si, dict)
        assert si["strengths"][0]["dimension"] == "Meta-Cognitive Awareness"

    def test_api_returns_null_for_legacy_snapshot(self, authenticated_client):
        """Legacy snapshot (NULL column) → API returns structured_insights: null, no 500."""
        user_id = authenticated_client.user_id
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        save_profile_snapshot(user_id, "Legacy call — no structured insights")

        resp = authenticated_client.get(f"/api/results/{user_id}")
        assert resp.status_code == 200
        data = resp.json()
        profile = data.get("latest_profile")
        assert profile is not None
        # structured_insights should be null (not missing key)
        assert "structured_insights" in profile
        assert profile["structured_insights"] is None

    def test_api_returns_null_for_corrupt_structured_insights(self, authenticated_client):
        """Corrupt JSON in DB → API returns 200 with structured_insights: null."""
        user_id = authenticated_client.user_id
        db_path = os.environ["DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_assessment(conn, user_id, _build_likert_responses(), _build_scenario_responses())
        conn.close()

        generate_profile_snapshot(user_id)
        result = save_profile_snapshot(user_id, "Corrupt test")
        snapshot_id = result["snapshot_id"]

        # Manually corrupt the column
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE profile_snapshots SET structured_insights = ? WHERE id = ?",
            ("not-valid-json{{", snapshot_id),
        )
        conn.commit()
        conn.close()

        resp = authenticated_client.get(f"/api/results/{user_id}")
        assert resp.status_code == 200
        data = resp.json()
        profile = data.get("latest_profile")
        assert profile is not None
        assert profile["structured_insights"] is None
