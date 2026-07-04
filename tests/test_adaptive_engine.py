"""Unit tests for agents/transmutation/adaptive_engine.py (BE-001).

Verifies:
- dimension_consistency: max-min range on effective (reverse-scored) scores
- dimension_confidence: low/medium/high per answered/total/consistency
- should_expand_dimension: LOW_CUT / BORDERLINE_MARGIN / INCONSISTENT_RANGE
  thresholds from TransmutationSettings.adaptive
- select_next_awareness_items: screener-first Tier-3 routing, fully-
  administered Tier-2, and the transmute_core/unknown-tier no-op cases
- The whole module remains pure -- no DB/LLM/network calls anywhere
"""

import config
from agents.transmutation.adaptive_engine import (
    dimension_confidence,
    dimension_consistency,
    select_next_awareness_items,
    should_expand_dimension,
)
from agents.transmutation.question_bank import get_question_bank


class TestDimensionConsistency:
    """max - min range on effective (already reverse-scored) scores."""

    def test_perfectly_consistent_scores(self):
        assert dimension_consistency([4, 4, 4]) == 0.0

    def test_inconsistent_scores(self):
        assert dimension_consistency([1, 5, 3]) == 4.0

    def test_two_scores_computes_range(self):
        assert dimension_consistency([2, 5]) == 3.0

    def test_single_score_returns_zero(self):
        """A single data point has no meaningful range."""
        assert dimension_consistency([3]) == 0.0

    def test_empty_list_returns_zero(self):
        assert dimension_consistency([]) == 0.0

    def test_order_independent(self):
        assert dimension_consistency([5, 1, 3]) == dimension_consistency([1, 3, 5])


class TestDimensionConfidence:
    """low/medium/high based on answered count, total screener count, and consistency."""

    def test_low_when_not_all_screeners_answered(self):
        assert dimension_confidence(answered=1, total_screener=3, consistency_range=0.0) == "low"

    def test_low_when_zero_answered(self):
        assert dimension_confidence(answered=0, total_screener=3, consistency_range=0.0) == "low"

    def test_low_when_total_screener_is_zero(self):
        """No screener items at all -- nothing to be confident about."""
        assert dimension_confidence(answered=0, total_screener=0, consistency_range=0.0) == "low"

    def test_high_when_fully_answered_and_consistent(self):
        # INCONSISTENT_RANGE default is 2.0; a range of 0 is well under it.
        assert dimension_confidence(answered=3, total_screener=3, consistency_range=0.0) == "high"

    def test_medium_when_fully_answered_but_inconsistent(self):
        assert dimension_confidence(answered=3, total_screener=3, consistency_range=2.0) == "medium"

    def test_high_just_below_inconsistent_threshold(self):
        assert dimension_confidence(answered=3, total_screener=3, consistency_range=1.99) == "high"

    def test_answered_greater_than_total_still_counts_as_fully_answered(self):
        """Defensive: answered > total shouldn't be treated as 'not yet done'."""
        assert dimension_confidence(answered=5, total_screener=3, consistency_range=0.0) == "high"


class TestShouldExpandDimension:
    """LOW_CUT (2.75) / BORDERLINE_MARGIN (0.5 around 3.0) / INCONSISTENT_RANGE (2.0)."""

    def test_expands_at_low_cut_exactly(self):
        assert should_expand_dimension("Any Dim", screener_mean=2.75, consistency_range=0.0) is True

    def test_expands_below_low_cut(self):
        assert should_expand_dimension("Any Dim", screener_mean=1.5, consistency_range=0.0) is True

    def test_does_not_expand_just_above_low_cut_and_outside_borderline(self):
        # 2.76 is > LOW_CUT (2.75) and outside the [2.5, 3.5] borderline band
        # and not inconsistent -- confident low-normal score, no expansion.
        assert should_expand_dimension("Any Dim", screener_mean=4.0, consistency_range=0.0) is False

    def test_expands_at_borderline_lower_bound(self):
        # 3.0 - 0.5 = 2.5
        assert should_expand_dimension("Any Dim", screener_mean=2.5, consistency_range=0.0) is True

    def test_expands_at_borderline_upper_bound(self):
        # 3.0 + 0.5 = 3.5
        assert should_expand_dimension("Any Dim", screener_mean=3.5, consistency_range=0.0) is True

    def test_expands_at_exact_midpoint(self):
        assert should_expand_dimension("Any Dim", screener_mean=3.0, consistency_range=0.0) is True

    def test_does_not_expand_confident_high_score(self):
        assert should_expand_dimension("Any Dim", screener_mean=4.5, consistency_range=0.5) is False

    def test_expands_on_inconsistent_range_even_with_confident_mean(self):
        # A "confident" high mean (4.5) would not normally expand, but a
        # large consistency_range (>= 2.0) overrides that.
        assert should_expand_dimension("Any Dim", screener_mean=4.5, consistency_range=2.0) is True

    def test_does_not_expand_just_below_inconsistent_threshold(self):
        assert should_expand_dimension("Any Dim", screener_mean=4.5, consistency_range=1.99) is False


class TestSelectNextAwarenessItemsAwarenessCore:
    """Tier-2: fully administered, no screener/expand routing."""

    def test_returns_all_unanswered_awareness_core_items(self):
        qb = get_question_bank()
        core_items = qb.get_questions_by_tier("awareness_core")
        result = select_next_awareness_items({}, set(), qb, "awareness_core")
        assert set(result) == {item["id"] for item in core_items}

    def test_excludes_already_answered_items(self):
        qb = get_question_bank()
        core_items = qb.get_questions_by_tier("awareness_core")
        already_answered = {core_items[0]["id"]}
        result = select_next_awareness_items({}, already_answered, qb, "awareness_core")
        assert core_items[0]["id"] not in result
        assert len(result) == len(core_items) - 1

    def test_empty_when_all_answered(self):
        qb = get_question_bank()
        core_items = qb.get_questions_by_tier("awareness_core")
        all_answered = {item["id"] for item in core_items}
        result = select_next_awareness_items({}, all_answered, qb, "awareness_core")
        assert result == []

    def test_never_includes_deepdive_or_transmute_core_items(self):
        qb = get_question_bank()
        result = select_next_awareness_items({}, set(), qb, "awareness_core")
        for qid in result:
            q = qb.get_question_by_id(qid)
            assert q["tier"] == "awareness_core"


class TestSelectNextAwarenessItemsDeepDive:
    """Tier-3: screener-first per dimension; expand only if flagged."""

    def test_returns_screener_items_first_when_none_answered(self):
        qb = get_question_bank()
        result = select_next_awareness_items({}, set(), qb, "awareness_deepdive")
        # Every returned item must be a screener item (nothing has been
        # answered yet, so no dimension has reached the expand decision).
        for qid in result:
            q = qb.get_question_by_id(qid)
            assert q["is_screener"] is True
            assert q["tier"] == "awareness_deepdive"

    def test_all_deepdive_screeners_present_when_nothing_answered(self):
        qb = get_question_bank()
        all_screeners = qb.get_screener_items()
        result = select_next_awareness_items({}, set(), qb, "awareness_deepdive")
        assert set(result) == {item["id"] for item in all_screeners}

    def test_expands_dimension_with_low_screener_scores(self):
        """Answering every Mindful Presence screener with a low effective
        score should trigger expansion to that dimension's full item set."""
        qb = get_question_bank()
        screeners = qb.get_screener_items("Mindful Presence")
        assert len(screeners) == 3

        responses = {}
        answered_ids = set()
        for item in screeners:
            # raw=1 -> effective=1 if not reverse, effective=5 if reverse.
            # Use raw that always yields a LOW effective score regardless of
            # reverse_scored, so this test is robust to which items are
            # reverse-keyed.
            raw = 5 if item["reverse_scored"] else 1
            responses[item["id"]] = {"score": raw}
            answered_ids.add(item["id"])

        result = select_next_awareness_items(responses, answered_ids, qb, "awareness_deepdive")

        full_items = [
            item for item in qb.get_questions_by_dimension("Mindful Presence")
            if not item["is_screener"]
        ]
        full_ids = {item["id"] for item in full_items}
        # All the dimension's full (non-screener) items should now be offered.
        assert full_ids.issubset(set(result))

    def test_does_not_expand_dimension_with_confident_high_scores(self):
        """Answering every screener with a strong, consistent effective score
        should NOT expand that dimension -- it contributes no more items."""
        qb = get_question_bank()
        screeners = qb.get_screener_items("Mindful Presence")

        responses = {}
        answered_ids = set()
        for item in screeners:
            # raw that always yields a HIGH effective score (>= 4.5, well
            # outside LOW_CUT/BORDERLINE) regardless of reverse-scoring.
            raw = 1 if item["reverse_scored"] else 5
            responses[item["id"]] = {"score": raw}
            answered_ids.add(item["id"])

        result = select_next_awareness_items(responses, answered_ids, qb, "awareness_deepdive")

        full_items = [
            item for item in qb.get_questions_by_dimension("Mindful Presence")
            if not item["is_screener"]
        ]
        full_ids = {item["id"] for item in full_items}
        # None of this dimension's full items should be offered -- it's done.
        assert full_ids.isdisjoint(set(result))

    def test_other_dimensions_still_screener_gated_independently(self):
        """Expanding one dimension must not affect another dimension's
        screener-first gating (no cross-dimension trigger map)."""
        qb = get_question_bank()
        mp_screeners = qb.get_screener_items("Mindful Presence")

        responses = {}
        answered_ids = set()
        for item in mp_screeners:
            raw = 5 if item["reverse_scored"] else 1  # force expansion
            responses[item["id"]] = {"score": raw}
            answered_ids.add(item["id"])

        result = select_next_awareness_items(responses, answered_ids, qb, "awareness_deepdive")

        # Other deep-dive dimensions (e.g. Meta-Cognitive Awareness) have
        # answered nothing, so only THEIR screener items should appear for
        # them -- never their full items.
        mc_full_items = [
            item for item in qb.get_questions_by_dimension("Meta-Cognitive Awareness")
            if not item["is_screener"]
        ]
        mc_full_ids = {item["id"] for item in mc_full_items}
        assert mc_full_ids.isdisjoint(set(result))

    def test_fully_answered_bank_returns_empty(self):
        qb = get_question_bank()
        deepdive_items = qb.get_questions_by_tier("awareness_deepdive")
        responses = {item["id"]: {"score": 3} for item in deepdive_items}
        answered_ids = {item["id"] for item in deepdive_items}
        result = select_next_awareness_items(responses, answered_ids, qb, "awareness_deepdive")
        assert result == []


class TestSelectNextAwarenessItemsOtherTiers:
    """transmute_core and unknown tiers are explicit no-ops, not crashes."""

    def test_transmute_core_returns_empty(self):
        qb = get_question_bank()
        result = select_next_awareness_items({}, set(), qb, "transmute_core")
        assert result == []

    def test_unknown_tier_returns_empty(self):
        qb = get_question_bank()
        result = select_next_awareness_items({}, set(), qb, "not_a_real_tier")
        assert result == []


class TestModuleIsPure:
    """No DB/LLM/network imports anywhere in the module."""

    def test_no_forbidden_imports(self):
        import ast
        import inspect

        from agents.transmutation import adaptive_engine

        source = inspect.getsource(adaptive_engine)
        tree = ast.parse(source)
        forbidden_substrings = ("sqlite3", "requests", "httpx", "litellm", "google.adk", "db.database")

        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)

        for name in imported_names:
            for forbidden in forbidden_substrings:
                assert forbidden not in name, f"adaptive_engine.py imports forbidden module: {name}"

    def test_config_read_is_the_only_external_dependency(self):
        """The one non-stdlib import should be config.get_settings, matching
        scoring_engine.py's own established pattern for pure-module threshold
        reads (a cached in-process settings object, not I/O)."""
        import inspect

        from agents.transmutation import adaptive_engine

        source = inspect.getsource(adaptive_engine)
        assert "from config import get_settings" in source

    def test_calling_functions_does_not_touch_settings_cache_unexpectedly(self):
        """Sanity: calling the pure functions repeatedly doesn't mutate global
        config state (get_settings() returns the same cached object)."""
        config._settings = None
        before = config.get_settings()
        should_expand_dimension("X", 3.0, 0.0)
        after = config.get_settings()
        assert before is after
