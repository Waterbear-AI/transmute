#!/usr/bin/env python3
"""Phase fast-forward seeder CLI.

Seeds a user into the database at a specific Transmutation Engine phase
with production-shaped, backdated data. Each phase function satisfies the
real advance_phase() gate by writing backdated rows — no gate-bypass flags
are used or introduced (ADR-3).

Usage:
    python -m scripts.seed_phase --phase assessment --email dev@example.com

Or directly (from repo root with PYTHONPATH=.):
    DB_PATH=transmute.db python scripts/seed_phase.py --phase graduated \\
        --email tester@example.com --archetype transmuter --days-ago 60

Phases seed cumulatively:
    orientation → assessment → profile → education → development →
    reassessment → graduation → graduated → check_in

Options:
    --phase         Target phase (required)
    --email         User email (required)
    --password      User password (default: Seed1234!)
    --archetype     Target archetype: transmuter|absorber|magnifier|extractor|conduit
    --days-ago      Days to backdate practice entries and roadmap (default: 35)
    --entries       Number of practice journal entries to create (default: 10)
    --db            Explicit DB path (overrides DB_PATH env var and config)
    --force         Delete existing user with this email and re-seed
    --with-completed-check-in
                    After seeding to check_in, also save the check-in record and
                    advance back to graduated
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Logging setup (structured banner for CLI use)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("seed_phase")


# ---------------------------------------------------------------------------
# Phase ordering mirror (tools.py PHASE_ORDER)
# ---------------------------------------------------------------------------

PHASE_ORDER = [
    "orientation",
    "assessment",
    "profile",
    "education",
    "development",
    "reassessment",
    "graduation",
    "graduated",
    "check_in",
]

VALID_ARCHETYPES = ["transmuter", "absorber", "magnifier", "extractor", "conduit"]

# Archetype-keyed TC sub-dimension target effective scores (1-5 Likert scale).
# "effective" score is the dimensionally-meaningful value after reverse scoring.
# For reverse-scored questions: raw = 6 - effective (agreement_5 scale).
#
# TC axis mapping (scoring_engine._calculate_quadrant):
#   X (Amplification A): Fulfillment Emission → +X, Absorption Patterns → -X
#   Y (Filtering F): Deprivation Filtering → +Y, Amplification Awareness → +Y
#
# archetype -> {sub_dimension -> effective_score}
_ARCHETYPE_TC_SCORES: dict[str, dict[str, int]] = {
    "transmuter": {
        "Deprivation Filtering": 5,   # +Y
        "Fulfillment Emission": 5,    # +X
        "Amplification Awareness": 5, # +Y
        "Absorption Patterns": 1,     # −X (low absorption = emit-side)
        "Conduit Recognition": 5,
    },
    "absorber": {
        "Deprivation Filtering": 5,   # +Y
        "Fulfillment Emission": 1,    # −X
        "Amplification Awareness": 5, # +Y
        "Absorption Patterns": 5,     # −X (high absorption = absorb-side)
        "Conduit Recognition": 3,
    },
    "magnifier": {
        "Deprivation Filtering": 1,   # −Y
        "Fulfillment Emission": 5,    # +X
        "Amplification Awareness": 1, # −Y
        "Absorption Patterns": 1,     # −X (low absorption = emit-side)
        "Conduit Recognition": 3,
    },
    "extractor": {
        "Deprivation Filtering": 1,   # −Y
        "Fulfillment Emission": 1,    # −X
        "Amplification Awareness": 1, # −Y
        "Absorption Patterns": 5,     # −X
        "Conduit Recognition": 3,
    },
    "conduit": {
        "Deprivation Filtering": 3,
        "Fulfillment Emission": 3,
        "Amplification Awareness": 3,
        "Absorption Patterns": 3,
        "Conduit Recognition": 5,
    },
}

# Canonical 5 education categories (mirrors tools.EDUCATION_CATEGORY_KEYS)
_EDUCATION_CATEGORIES = [
    "what_this_means",
    "your_score",
    "daily_effects",
    "strengths_gaps",
    "external_interaction",
]

# Hardcoded first-question-per-category lookup for all 13 dimensions.
# Built from question_bank comprehension_checks.json; the correct_option
# key is the known-correct answer so seeds achieve 100% score in one pass.
# Format: dim -> {category -> (question_id, correct_option)}
_COMPREHENSION_FIRST_Q: dict[str, dict[str, tuple[str, str]]] = {
    "Cognitive Awareness": {
        "what_this_means": ("cc_cog_cat1_q1", "b"),
        "your_score": ("cc_cog_cat2_q1", "b"),
        "daily_effects": ("cc_cog_cat3_q1", "a"),
        "strengths_gaps": ("cc_cog_cat4_q1", "a"),
        "external_interaction": ("cc_cog_cat5_q1", "b"),
    },
    "Emotional Awareness": {
        "what_this_means": ("cc_ea_cat1_q1", "b"),
        "your_score": ("cc_ea_cat2_q1", "b"),
        "daily_effects": ("cc_ea_cat3_q1", "b"),
        "strengths_gaps": ("cc_ea_cat4_q1", "b"),
        "external_interaction": ("cc_ea_cat5_q1", "a"),
    },
    "Environmental Awareness": {
        "what_this_means": ("cc_env_cat1_q1", "b"),
        "your_score": ("cc_env_cat2_q1", "b"),
        "daily_effects": ("cc_env_cat3_q1", "a"),
        "strengths_gaps": ("cc_env_cat4_q1", "a"),
        "external_interaction": ("cc_env_cat5_q1", "b"),
    },
    "Flow Awareness": {
        "what_this_means": ("cc_fa_cat1_q1", "b"),
        "your_score": ("cc_fa_cat2_q1", "b"),
        "daily_effects": ("cc_fa_cat3_q1", "b"),
        "strengths_gaps": ("cc_fa_cat4_q1", "b"),
        "external_interaction": ("cc_fa_cat5_q1", "b"),
    },
    "Interoceptive Awareness": {
        "what_this_means": ("cc_intero_cat1_q1", "b"),
        "your_score": ("cc_intero_cat2_q1", "b"),
        "daily_effects": ("cc_intero_cat3_q1", "a"),
        "strengths_gaps": ("cc_intero_cat4_q1", "a"),
        "external_interaction": ("cc_intero_cat5_q1", "b"),
    },
    "Meta-Cognitive Awareness": {
        "what_this_means": ("cc_mca_cat1_q1", "a"),
        "your_score": ("cc_mca_cat2_q1", "b"),
        "daily_effects": ("cc_mca_cat3_q1", "b"),
        "strengths_gaps": ("cc_mca_cat4_q1", "b"),
        "external_interaction": ("cc_mca_cat5_q1", "b"),
    },
    "Mindfulness": {
        "what_this_means": ("cc_mind_cat1_q1", "b"),
        "your_score": ("cc_mind_cat2_q1", "b"),
        "daily_effects": ("cc_mind_cat3_q1", "a"),
        "strengths_gaps": ("cc_mind_cat4_q1", "a"),
        "external_interaction": ("cc_mind_cat5_q1", "b"),
    },
    "Physical Awareness": {
        "what_this_means": ("cc_phys_cat1_q1", "b"),
        "your_score": ("cc_phys_cat2_q1", "b"),
        "daily_effects": ("cc_phys_cat3_q1", "b"),
        "strengths_gaps": ("cc_phys_cat4_q1", "a"),
        "external_interaction": ("cc_phys_cat5_q1", "b"),
    },
    "Social Awareness": {
        "what_this_means": ("cc_sa_cat1_q1", "b"),
        "your_score": ("cc_sa_cat2_q1", "a"),
        "daily_effects": ("cc_sa_cat3_q1", "b"),
        "strengths_gaps": ("cc_sa_cat4_q1", "a"),
        "external_interaction": ("cc_sa_cat5_q1", "b"),
    },
    "Spatial Awareness": {
        "what_this_means": ("cc_spat_cat1_q1", "b"),
        "your_score": ("cc_spat_cat2_q1", "b"),
        "daily_effects": ("cc_spat_cat3_q1", "a"),
        "strengths_gaps": ("cc_spat_cat4_q1", "a"),
        "external_interaction": ("cc_spat_cat5_q1", "b"),
    },
    "Systemic Awareness": {
        "what_this_means": ("cc_sys_cat1_q1", "c"),
        "your_score": ("cc_sys_cat2_q1", "b"),
        "daily_effects": ("cc_sys_cat3_q1", "b"),
        "strengths_gaps": ("cc_sys_cat4_q1", "b"),
        "external_interaction": ("cc_sys_cat5_q1", "b"),
    },
    "Temporal Awareness": {
        "what_this_means": ("cc_temp_cat1_q1", "b"),
        "your_score": ("cc_temp_cat2_q1", "b"),
        "daily_effects": ("cc_temp_cat3_q1", "a"),
        "strengths_gaps": ("cc_temp_cat4_q1", "a"),
        "external_interaction": ("cc_temp_cat5_q1", "b"),
    },
    "Transmutation Capacity": {
        "what_this_means": ("cc_tc_cat1_q1", "c"),
        "your_score": ("cc_tc_cat2_q1", "b"),
        "daily_effects": ("cc_tc_cat3_q1", "a"),
        "strengths_gaps": ("cc_tc_cat4_q1", "b"),
        "external_interaction": ("cc_tc_cat5_q1", "b"),
    },
}


# ---------------------------------------------------------------------------
# Seeder functions
# ---------------------------------------------------------------------------


def seed_user(conn, email: str, password: str) -> str:
    """Insert a user row and return the new user_id.

    Uses bcrypt identical to api/auth.py so the seeded account is login-able
    via the real UI.

    Raises:
        ValueError: if the email already exists (call with --force to delete
            the existing record first).
    """
    import bcrypt

    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        raise ValueError(
            f"Email already exists: {email}. Use --force to overwrite."
        )

    user_id = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users (id, name, email, password_hash, current_phase) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, email.split("@")[0], email, password_hash, "orientation"),
    )
    logger.info("seed_user: created user_id=%s email=%s", user_id, email)
    return user_id


def seed_assessment(conn, user_id: str, archetype: str) -> None:
    """Seed assessment_state with archetype-keyed Likert answers.

    Answers every question in the question bank. Each dimension receives ≥60%
    of its questions answered, satisfying the _check_assessment_completion_gate.
    TC sub-dimension scores are set to produce the target archetype; all other
    dimensions use a score of 3 (neutral baseline).

    For reverse_scored questions, the raw DB value is inverted so the effective
    Likert interpretation matches the archetype target.
    """
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    tc_targets = _ARCHETYPE_TC_SCORES[archetype]

    responses: dict[str, Any] = {}
    for dim in qb.get_dimensions():
        for q in qb.get_questions_by_dimension(dim):
            qid = q["id"]
            sd = q.get("sub_dimension", "")
            is_rev = q.get("reverse_scored", False)

            if dim == "Transmutation Capacity" and sd in tc_targets:
                effective = tc_targets[sd]
            else:
                effective = 3  # neutral

            # Reverse-scored questions: agreement_5 uses points=5, so raw = 6 - effective
            raw = (6 - effective) if is_rev else effective
            responses[qid] = {"score": raw}

    state_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO assessment_state "
        "(id, user_id, responses, scenario_responses, created_at) "
        "VALUES (?, ?, ?, '{}', ?)",
        (state_id, user_id, json.dumps(responses), now),
    )
    logger.info(
        "seed_assessment: wrote %d responses for user_id=%s archetype=%s",
        len(responses), user_id, archetype,
    )


def seed_profile(user_id: str) -> str:
    """Generate and save a profile snapshot. Returns the snapshot ID.

    Calls the real tool pipeline (generate_profile_snapshot →
    save_profile_snapshot) so the profile is indistinguishable from a
    production-generated one, including the spider chart.

    Raises:
        RuntimeError: if the tool returns an error (seeder bug, not gate failure).
    """
    from agents.transmutation.tools import generate_profile_snapshot, save_profile_snapshot

    result = generate_profile_snapshot(user_id)
    if "error" in result:
        raise RuntimeError(f"seed_profile: generate_profile_snapshot failed: {result['error']}")

    save_result = save_profile_snapshot(
        user_id,
        interpretation=f"Seeded profile snapshot for development/testing.",
    )
    if "error" in save_result:
        raise RuntimeError(f"seed_profile: save_profile_snapshot failed: {save_result['error']}")

    snapshot_id = save_result.get("snapshot_id", "")
    logger.info("seed_profile: snapshot_id=%s for user_id=%s", snapshot_id, user_id)
    return snapshot_id


def seed_education(user_id: str) -> None:
    """Seed education progress for the 3 weakest dimensions.

    Loads the profile snapshot, identifies the top-3 weakest dimensions
    (by raw score), and records one correct comprehension answer per category
    for each of them — satisfying the 5-categories-each gate. Scores are 100%.

    Raises:
        RuntimeError: if no profile snapshot exists or a comprehension Q is missing.
    """
    from agents.transmutation.tools import record_comprehension_answer, get_user_profile

    profile = get_user_profile(user_id)
    if not profile.get("exists"):
        raise RuntimeError("seed_education: no profile snapshot found — run seed_profile first")

    scores = profile["scores"]
    # Sort dimensions by ascending score to find the 3 weakest
    ranked = sorted(
        scores.items(),
        key=lambda kv: kv[1].get("score", 0) if isinstance(kv[1], dict) else kv[1],
    )
    top3 = [dim for dim, _ in ranked[:3]]

    for dim in top3:
        dim_qs = _COMPREHENSION_FIRST_Q.get(dim)
        if not dim_qs:
            logger.warning("seed_education: no comprehension map for dim=%s, skipping", dim)
            continue
        for cat in _EDUCATION_CATEGORIES:
            entry = dim_qs.get(cat)
            if not entry:
                logger.warning(
                    "seed_education: no question entry for dim=%s cat=%s, skipping", dim, cat
                )
                continue
            qid, correct_option = entry
            result = record_comprehension_answer(user_id, dim, cat, qid, correct_option)
            if "error" in result:
                raise RuntimeError(
                    f"seed_education: record_comprehension_answer failed for "
                    f"dim={dim} cat={cat}: {result['error']}"
                )

    logger.info("seed_education: seeded comprehension for dims=%s user_id=%s", top3, user_id)


def seed_development(conn, user_id: str, entries: int, days_ago: int) -> str:
    """Seed a roadmap and backdated practice journal entries. Returns roadmap_id.

    The roadmap is saved via the real save_roadmap() tool (which validates
    practice linkage). Practice journal entries are inserted directly with
    backdated created_at because log_practice_entry() always uses utcnow() —
    there is no way to backdate via the tool layer.

    The roadmap's created_at is also backdated via a direct UPDATE so the
    30-day gate is satisfied even with fewer than 10 entries.

    Raises:
        RuntimeError: if generate_roadmap or save_roadmap returns an error.
    """
    from agents.transmutation.tools import generate_roadmap, save_roadmap
    from agents.transmutation.question_bank import get_question_bank

    qb = get_question_bank()
    dimensions_index = {d: qb.get_sub_dimensions(d) for d in qb.get_dimensions()}

    # Generate leverage targets for this user's profile
    roadmap_data = generate_roadmap(user_id)
    if "error" in roadmap_data:
        raise RuntimeError(f"seed_development: generate_roadmap failed: {roadmap_data['error']}")

    leverage_targets = roadmap_data.get("leverage_targets", [])
    if not leverage_targets:
        raise RuntimeError("seed_development: no leverage_targets returned from generate_roadmap")

    # Build practice list from leverage targets
    practices = []
    for i, target in enumerate(leverage_targets[:3]):
        dim = target.get("dimension", "")
        sub_dims = dimensions_index.get(dim, [])
        sub_dim = sub_dims[0] if sub_dims else None
        practices.append({
            "practice_id": f"seed-practice-{i + 1}",
            "title": f"Practice {i + 1}: {dim}",
            "dimension": dim,
            "sub_dimension": sub_dim,
            "transmutation_operation": "filtering",
        })

    roadmap = {
        "summary": "Seeded development roadmap for testing.",
        "practices": practices,
    }
    save_result = save_roadmap(user_id, roadmap)
    if "error" in save_result:
        raise RuntimeError(f"seed_development: save_roadmap failed: {save_result['error']}")

    roadmap_id = save_result["roadmap_id"]

    # Backdate the roadmap to satisfy the 30-day time gate
    backdated_ts = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "UPDATE development_roadmap SET created_at = ? WHERE id = ?",
        (backdated_ts, roadmap_id),
    )

    # Insert backdated practice journal entries directly (bypassing log_practice_entry
    # which always stamps utcnow()) to satisfy the 10-entry OR 30-day gate.
    practice = practices[0]  # anchor entries to the first practice
    entry_ids = []
    for i in range(entries):
        entry_ts = (datetime.utcnow() - timedelta(days=days_ago - i)).isoformat()
        entry_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO practice_journal "
            "(id, user_id, practice_id, reflection, self_rating, created_at, "
            "dimension, sub_dimension, transmutation_operation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                user_id,
                practice["practice_id"],
                f"Seeded reflection #{i + 1}.",
                4,
                entry_ts,
                practice["dimension"],
                practice["sub_dimension"],
                practice.get("transmutation_operation", "filtering"),
            ),
        )
        entry_ids.append(entry_id)

    logger.info(
        "seed_development: roadmap_id=%s entries=%d days_ago=%d user_id=%s",
        roadmap_id, entries, days_ago, user_id,
    )
    return roadmap_id


def seed_graduation_readiness(conn, user_id: str) -> None:
    """Insert two backdated profile snapshots to satisfy graduation readiness.

    evaluate_graduation_readiness() checks for ≥2 profile snapshots in the
    reassessment phase with <GRADUATION_STABILITY_MAX_NORMALIZED points of
    normalized movement between them. This function inserts two snapshots
    with the same archetype and minimal score movement (< 5 normalized points
    ≈ 0.1 on the raw 1–5 scale) via direct INSERT.

    The snapshots mirror the _create_profile_snapshot helper in
    tests/test_e2e_lifecycle.py — same archetype key and score shape.

    Note: the current profile snapshot (from seed_profile) remains the latest
    entry. The two new snapshots are timestamped well in the past so they
    represent the "reassessment cycle" pair.
    """
    # Fetch current profile scores and archetype to base the readiness pair on
    from db.database import get_db_session

    with get_db_session() as ro_conn:
        snap = ro_conn.execute(
            "SELECT scores, quadrant_placement FROM profile_snapshots "
            "WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
            (user_id,),
        ).fetchone()

    if not snap:
        raise RuntimeError(
            "seed_graduation_readiness: no profile snapshot found — run seed_profile first"
        )

    base_scores = json.loads(snap["scores"] or "{}")
    placement = json.loads(snap["quadrant_placement"] or "{}")
    archetype = placement.get("archetype") or placement.get("quadrant") or "conduit"

    # Create a slightly-shifted second set of scores (< 5 normalized points movement).
    # Raw shift of 0.05 on the 1–5 scale ≈ 1.25 normalized points — well under the 5.0 threshold.
    shifted_scores = {}
    for dim, data in base_scores.items():
        if isinstance(data, dict):
            shifted_scores[dim] = {
                **data,
                "score": round(data.get("score", 3.0) + 0.05, 4),
            }
        else:
            shifted_scores[dim] = round(float(data) + 0.05, 4)

    now = datetime.utcnow()
    ts1 = (now - timedelta(days=35)).isoformat()  # "first reassessment" snapshot
    ts2 = (now - timedelta(days=5)).isoformat()   # "second reassessment" snapshot

    quadrant_json = json.dumps({"archetype": archetype})

    for ts, scores in [(ts1, base_scores), (ts2, shifted_scores)]:
        snap_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO profile_snapshots "
            "(id, user_id, scores, quadrant_placement, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, user_id, json.dumps(scores), quadrant_json, ts),
        )

    logger.info(
        "seed_graduation_readiness: inserted 2 backdated snapshots for user_id=%s archetype=%s",
        user_id, archetype,
    )


def seed_graduation(user_id: str) -> str:
    """Generate and save a graduation record. Returns the graduation record_id.

    Calls generate_graduation_artifacts() and save_graduation_record() via
    the real tool pipeline.

    Raises:
        RuntimeError: if either tool returns an error.
    """
    from agents.transmutation.tools import generate_graduation_artifacts, save_graduation_record

    artifacts = generate_graduation_artifacts(user_id)
    if "error" in artifacts:
        raise RuntimeError(
            f"seed_graduation: generate_graduation_artifacts failed: {artifacts['error']}"
        )

    narrative = "Seeded graduation narrative: consistent transmutation capacity demonstrated."
    indicators = {
        "stability": True,
        "archetype_sustained": True,
        "practice_count_met": True,
    }

    result = save_graduation_record(user_id, narrative, indicators)
    if "error" in result:
        raise RuntimeError(f"seed_graduation: save_graduation_record failed: {result['error']}")

    record_id = result.get("record_id", "")
    logger.info("seed_graduation: record_id=%s for user_id=%s", record_id, user_id)
    return record_id


def seed_check_in(user_id: str, with_completed_check_in: bool = False) -> None:
    """Seed the check-in phase.

    Requires assessment_state to be populated (seed_assessment writes it) and
    a graduation record to exist (seed_graduation writes it).

    Steps:
        1. generate_check_in_snapshot — scores the existing assessment_state
           data as a check-in (tags the cache with kind=check_in)
        2. save_profile_snapshot — persists the check-in snapshot
        3. get_graduation_record — fetch baseline snapshot_id
        4. generate_comparison_snapshot — compute delta vs. graduation baseline
        5. detect_check_in_regression — deterministic regression verdict
        6. save_check_in_log — persist log row
        7. If with_completed_check_in: advance back to graduated

    Raises:
        RuntimeError: if any tool returns an error.
    """
    from agents.transmutation.tools import (
        generate_check_in_snapshot,
        save_profile_snapshot,
        get_graduation_record,
        generate_comparison_snapshot,
        detect_check_in_regression,
        save_check_in_log,
        advance_phase,
    )

    # 1. Score the check-in
    result = generate_check_in_snapshot(user_id)
    if "error" in result:
        raise RuntimeError(
            f"seed_check_in: generate_check_in_snapshot failed: {result['error']}"
        )

    # 2. Persist the check-in snapshot
    save_result = save_profile_snapshot(
        user_id,
        interpretation="Seeded check-in snapshot for testing.",
    )
    if "error" in save_result:
        raise RuntimeError(
            f"seed_check_in: save_profile_snapshot failed: {save_result['error']}"
        )

    check_in_snapshot_id = save_result.get("snapshot_id", "")

    # 3. Fetch graduation baseline
    grad_record = get_graduation_record(user_id)
    if not grad_record.get("exists"):
        raise RuntimeError("seed_check_in: no graduation record found")

    graduation_snapshot_id = grad_record.get("final_snapshot_id", "")

    # 4. Comparison snapshot (just for completeness; result not stored here)
    if graduation_snapshot_id:
        generate_comparison_snapshot(user_id, graduation_snapshot_id)

    # 5. Detect regression
    regression_result = detect_check_in_regression(user_id)
    regression_detected = regression_result.get("regression_detected", False)

    # 6. Save check-in log
    log_result = save_check_in_log(
        user_id=user_id,
        snapshot_id=check_in_snapshot_id,
        graduation_snapshot_id=graduation_snapshot_id,
        regression_detected=regression_detected,
        re_entered_development=False,
    )
    if "error" in log_result:
        raise RuntimeError(
            f"seed_check_in: save_check_in_log failed: {log_result['error']}"
        )

    logger.info(
        "seed_check_in: check_in seeded for user_id=%s regression_detected=%s",
        user_id, regression_detected,
    )

    if with_completed_check_in:
        adv = advance_phase(user_id, "graduated", reason="seed: completed check-in")
        if "error" in adv:
            raise RuntimeError(
                f"seed_check_in: advance_phase(graduated) failed: {adv['error']}"
            )
        logger.info(
            "seed_check_in: advanced back to graduated for user_id=%s", user_id
        )


# ---------------------------------------------------------------------------
# Force-delete helper
# ---------------------------------------------------------------------------


def _delete_user(conn, user_id: str) -> None:
    """Delete a user and all FK-dependent rows in safe deletion order.

    Deletion order respects foreign key constraints (child tables first).
    All deletes run in a single transaction managed by the caller.
    """
    fk_tables = [
        "check_in_log",
        "graduation_record",
        "practice_journal",
        "roadmap_practices",
        "development_roadmap",
        "education_progress",
        "dimension_assessment_state",
        "moral_ledger",
        "profile_snapshots",
        "assessment_state",
        "safety_log",
    ]
    for table in fk_tables:
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))  # noqa: S608
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    logger.info("_delete_user: deleted user_id=%s and all FK rows", user_id)


# ---------------------------------------------------------------------------
# Main seeder orchestration
# ---------------------------------------------------------------------------


def _phases_up_to(target: str) -> list[str]:
    """Return the ordered list of phases to seed, from assessment to target."""
    try:
        idx = PHASE_ORDER.index(target)
    except ValueError:
        raise ValueError(f"Unknown phase: {target!r}")
    # We always start from assessment (orientation is the initial DB state)
    start = PHASE_ORDER.index("assessment")
    return PHASE_ORDER[start : idx + 1]


def seed_user_to_phase(
    *,
    email: str,
    password: str,
    target_phase: str,
    archetype: str,
    days_ago: int,
    entries: int,
    force: bool,
    with_completed_check_in: bool,
) -> str:
    """Orchestrate full seeding from orientation through target_phase.

    Returns the new user_id.

    Raises:
        ValueError: for invalid phase/archetype or duplicate email (without force).
        RuntimeError: if any gate call fails (seeder bug — not a gate issue).
    """
    from db.database import get_db_session
    from agents.transmutation.tools import advance_phase

    phases_to_seed = _phases_up_to(target_phase)

    # ── Step 1: create the user (or force-delete and re-create) ──────────
    with get_db_session() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing:
            if not force:
                raise ValueError(
                    f"Email already exists: {email!r}. Use --force to overwrite."
                )
            logger.info("seed_user_to_phase: --force: deleting existing user %s", email)
            _delete_user(conn, existing["id"])

        user_id = seed_user(conn, email, password)

    # ── Step 2: seed phases cumulatively ─────────────────────────────────
    if "assessment" in phases_to_seed:
        # orientation → assessment (no gate; just a state transition)
        adv = advance_phase(user_id, "assessment", reason="seed: enter assessment")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(assessment) failed: {adv['error']}")

        with get_db_session() as conn:
            seed_assessment(conn, user_id, archetype)

        adv = advance_phase(user_id, "profile", reason="seed: assessment complete")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(profile) failed: {adv['error']}")

    if "profile" in phases_to_seed:
        seed_profile(user_id)

        adv = advance_phase(user_id, "education", reason="seed: profile saved")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(education) failed: {adv['error']}")

    if "education" in phases_to_seed:
        seed_education(user_id)

        adv = advance_phase(user_id, "development", reason="seed: education complete")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(development) failed: {adv['error']}")

    if "development" in phases_to_seed:
        with get_db_session() as conn:
            seed_development(conn, user_id, entries=entries, days_ago=days_ago)

        adv = advance_phase(user_id, "reassessment", reason="seed: development complete")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(reassessment) failed: {adv['error']}")

    if "reassessment" in phases_to_seed:
        with get_db_session() as conn:
            seed_graduation_readiness(conn, user_id)

        adv = advance_phase(user_id, "graduation", reason="seed: graduation readiness met")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(graduation) failed: {adv['error']}")

    if "graduation" in phases_to_seed:
        seed_graduation(user_id)

        adv = advance_phase(user_id, "graduated", reason="seed: graduation complete")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(graduated) failed: {adv['error']}")

    if "check_in" in phases_to_seed:
        # Re-seed assessment_state so generate_check_in_snapshot has data to score
        with get_db_session() as conn:
            seed_assessment(conn, user_id, archetype)

        adv = advance_phase(user_id, "check_in", reason="seed: entering check-in")
        if "error" in adv:
            raise RuntimeError(f"advance_phase(check_in) failed: {adv['error']}")

        seed_check_in(user_id, with_completed_check_in=with_completed_check_in)

    return user_id


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="seed_phase",
        description=(
            "Seed a Transmutation Engine user to a specific phase with "
            "production-shaped, backdated data. Each phase satisfies real "
            "advance_phase() gates — no bypass flags are used."
        ),
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=[p for p in PHASE_ORDER if p != "orientation"],
        help="Target phase to seed the user to (cumulative).",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="User email address.",
    )
    parser.add_argument(
        "--password",
        default="Seed1234!",
        help="User password (default: Seed1234!).",
    )
    parser.add_argument(
        "--archetype",
        default="transmuter",
        choices=VALID_ARCHETYPES,
        help="Target archetype for assessment scoring (default: transmuter).",
    )
    parser.add_argument(
        "--days-ago",
        type=int,
        default=35,
        metavar="N",
        help=(
            "Days to backdate the roadmap created_at and earliest practice "
            "entry (default: 35). Must be > 30 to satisfy the 30-day gate "
            "when entries < 10."
        ),
    )
    parser.add_argument(
        "--entries",
        type=int,
        default=10,
        metavar="N",
        help="Number of practice journal entries to create (default: 10).",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help=(
            "Explicit path to the SQLite database file. Overrides DB_PATH "
            "env var and config.yaml default."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing user with this email before re-seeding.",
    )
    parser.add_argument(
        "--with-completed-check-in",
        action="store_true",
        dest="with_completed_check_in",
        help=(
            "After seeding to check_in, save the check-in log and advance "
            "back to graduated."
        ),
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    """Validate argument combinations that argparse cannot express."""
    if args.days_ago < 1:
        raise argparse.ArgumentTypeError("--days-ago must be a positive integer.")
    if args.entries < 1:
        raise argparse.ArgumentTypeError("--entries must be a positive integer.")
    if args.with_completed_check_in and args.phase != "check_in":
        raise argparse.ArgumentTypeError(
            "--with-completed-check-in is only valid when --phase check_in."
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0 = success, 1 = failure)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        validate_args(args)
    except argparse.ArgumentTypeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # ── DB_PATH must be set BEFORE any get_settings() call is resolved ───
    # config._settings is a cached singleton; setting the env var here
    # (before any import that triggers get_settings()) ensures the seeder
    # points at the right DB file.
    if args.db:
        os.environ["DB_PATH"] = args.db

    # Now it is safe to import and call DB-touching code.
    from db.database import run_migrations
    from config import get_settings

    settings = get_settings()
    db_path = settings.db_path
    run_migrations(db_path)

    logger.info(
        "seed_phase: db=%s phase=%s email=%s archetype=%s entries=%d days_ago=%d force=%s",
        db_path, args.phase, args.email, args.archetype,
        args.entries, args.days_ago, args.force,
    )

    try:
        user_id = seed_user_to_phase(
            email=args.email,
            password=args.password,
            target_phase=args.phase,
            archetype=args.archetype,
            days_ago=args.days_ago,
            entries=args.entries,
            force=args.force,
            with_completed_check_in=args.with_completed_check_in,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # ── Summary line (stdout, machine-parseable) ──────────────────────────
    print(
        f"seeded user_id={user_id} email={args.email!r} "
        f"phase={args.phase!r} archetype={args.archetype!r}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
