"""Pure deterministic adaptive-routing engine for the Tier-2/3 awareness flow.

Decides which awareness items to present next given the tiered, screener-first
item bank (see data/questions.json v2 / question_bank.py): Tier-2 "awareness
core" dimensions are always fully administered, while Tier-3 "deep-dive"
dimensions show only their screener items first and expand to the full item
set only when routing flags the dimension as needing more signal (low score,
borderline score, or inconsistent screener answers).

This module is PURE: no DB access, no LLM calls, no network I/O, no side
effects. It only reads response data and QuestionBank item metadata passed in
by the caller (mirroring scoring_engine.py's own use of `config.get_settings()`
for threshold values -- a cached in-process settings read, not I/O).
"""

from typing import Any, Literal

from config import get_settings

DimensionConfidence = Literal["low", "medium", "high"]


def _effective_score(question: dict[str, Any], raw_score: int, qb) -> int:
    """Apply reverse-scoring the same way scoring_engine._score_likert_by_dimension does.

    reverse_scored items invert on the scale_type's point count:
    effective = (points + 1) - raw.
    """
    if not question.get("reverse_scored"):
        return raw_score
    scale_type = question.get("scale_type", "agreement_5")
    points = qb.scale_types.get(scale_type, {}).get("points", 5)
    return (points + 1) - raw_score


def _answered_effective_scores(
    items: list[dict[str, Any]],
    responses: dict[str, Any],
    qb,
) -> list[int]:
    """Effective (reverse-scoring-applied) scores for the subset of `items`
    that have a non-null answer in `responses`.

    Mirrors scoring_engine's N/A handling: a response present with
    `{"score": None}` (explicit N/A / skip) is not counted as answered.
    """
    scores = []
    for item in items:
        resp = responses.get(item["id"])
        if resp is None:
            continue
        raw_score = resp.get("score")
        if raw_score is None:
            continue
        scores.append(_effective_score(item, raw_score, qb))
    return scores


def dimension_consistency(screener_item_scores: list[int]) -> float:
    """Consistency range for a dimension's answered screener items.

    Range = max - min, computed on already-effective (reverse-scored) 1-5
    scores. A range of 0 means every screener item in the dimension agreed;
    a large range means the person answered inconsistently within the same
    construct, which is itself a signal to expand and get more data.

    Returns 0.0 for fewer than 2 scores (no meaningful range with 0-1 points).
    """
    if len(screener_item_scores) < 2:
        return 0.0
    return float(max(screener_item_scores) - min(screener_item_scores))


def dimension_confidence(
    answered: int,
    total_screener: int,
    consistency_range: float,
) -> DimensionConfidence:
    """Confidence level for a deep-dive dimension's current screener data.

    "low"    -- haven't answered every screener item yet, so the mean/range
                are based on partial data.
    "high"   -- every screener item answered AND the answers were consistent
                (range strictly below TransmutationSettings.adaptive.INCONSISTENT_RANGE).
    "medium" -- every screener item answered, but the answers were
                inconsistent (range at/above INCONSISTENT_RANGE) -- there IS
                a full screener read, just a noisy one.
    """
    if total_screener <= 0 or answered < total_screener:
        return "low"

    inconsistent_range = get_settings().transmutation.adaptive.INCONSISTENT_RANGE
    if consistency_range >= inconsistent_range:
        return "medium"
    return "high"


def should_expand_dimension(
    dimension: str,
    screener_mean: float,
    consistency_range: float,
) -> bool:
    """Whether a Tier-3 deep-dive dimension should expand from screener-only
    to its full item set.

    Expands (returns True) if ANY of:
      - screener_mean <= LOW_CUT: the person is scoring low on this
        construct -- worth a closer look.
      - abs(screener_mean - 3.0) <= BORDERLINE_MARGIN: the person is near
        the scale midpoint, i.e. genuinely ambiguous rather than clearly
        strong or weak.
      - consistency_range >= INCONSISTENT_RANGE: the person answered the
        screener items inconsistently, so the screener mean alone can't be
        trusted -- more items are needed to resolve the signal.

    `dimension` is accepted (rather than inferring thresholds implicitly)
    so future per-dimension threshold overrides have an obvious extension
    point; v1 uses the same global thresholds for every dimension.
    """
    adaptive = get_settings().transmutation.adaptive

    if screener_mean <= adaptive.LOW_CUT:
        return True
    if abs(screener_mean - 3.0) <= adaptive.BORDERLINE_MARGIN:
        return True
    if consistency_range >= adaptive.INCONSISTENT_RANGE:
        return True
    return False


def _unanswered_ids(items: list[dict[str, Any]], answered_ids: set[str]) -> list[str]:
    return [item["id"] for item in items if item["id"] not in answered_ids]


def _select_awareness_core_items(
    answered_ids: set[str],
    qb,
) -> list[str]:
    """Tier-2: every awareness-core item is fully administered, no routing."""
    items = qb.get_questions_by_tier("awareness_core")
    return _unanswered_ids(items, answered_ids)


def _select_deepdive_items_for_dimension(
    dimension: str,
    responses: dict[str, Any],
    answered_ids: set[str],
    qb,
) -> list[str]:
    """Screener-first selection for one Tier-3 deep-dive dimension.

    Returns unanswered screener items if the screener isn't fully answered
    yet. Once the screener is fully answered, checks should_expand_dimension
    and returns the unanswered non-screener (full) items only if flagged;
    otherwise the dimension is done and contributes no more items.
    """
    all_items = qb.get_questions_by_dimension(dimension)
    screener_items = [item for item in all_items if item.get("is_screener")]

    unanswered_screeners = _unanswered_ids(screener_items, answered_ids)
    if unanswered_screeners:
        return unanswered_screeners

    effective_scores = _answered_effective_scores(screener_items, responses, qb)
    if not effective_scores:
        # No screener items exist for this dimension at all -- nothing to
        # gate on, so there is nothing more this dimension can offer.
        return []

    screener_mean = sum(effective_scores) / len(effective_scores)
    consistency_range = dimension_consistency(effective_scores)

    if not should_expand_dimension(dimension, screener_mean, consistency_range):
        return []

    full_items = [item for item in all_items if not item.get("is_screener")]
    return _unanswered_ids(full_items, answered_ids)


def _select_deepdive_items(
    responses: dict[str, Any],
    answered_ids: set[str],
    qb,
) -> list[str]:
    """Tier-3: screener-first per dimension; expand only flagged dimensions.

    Each deep-dive dimension is routed independently (self-referential --
    a dimension's own screener answers decide whether IT expands; there is
    no cross-dimension trigger map).
    """
    items: list[str] = []
    for dimension in qb.get_dimensions():
        deepdive_items = qb.get_questions_by_dimension(dimension)
        if not any(item.get("tier") == "awareness_deepdive" for item in deepdive_items):
            continue
        items.extend(
            _select_deepdive_items_for_dimension(dimension, responses, answered_ids, qb)
        )
    return items


def select_next_awareness_items(
    responses: dict[str, Any],
    answered_ids: set[str],
    qb,
    tier: str,
) -> list[str]:
    """Return the next batch of awareness-item IDs to present for `tier`.

    tier="awareness_core"   -> every unanswered Tier-2 item (fully administered).
    tier="awareness_deepdive" -> screener-first per Tier-3 dimension; a
        dimension's full items are only included once its screener is fully
        answered AND should_expand_dimension flags it.
    tier="transmute_core"   -> [] (Tier-1 items are served by the
        present_transmute_core_batch tool, not this adaptive router).
    Any other/unknown tier -> [] (no items to route; explicit rather than
        raising, since an unrecognized tier is a caller bug the tool layer
        should surface as a status error, not a crash inside the pure core).
    """
    if tier == "awareness_core":
        return _select_awareness_core_items(answered_ids, qb)
    if tier == "awareness_deepdive":
        return _select_deepdive_items(responses, answered_ids, qb)
    return []
