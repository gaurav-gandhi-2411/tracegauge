# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Direct unit tests for ``_parse_cache_tiers`` and its clamp to ``cache_creation_input_tokens``.

No test imported ``_parse_cache_tiers`` before this file, and ``tests/test_cost_cache_tiers.py``
never has the word "clamp" in it, so the clamp behaviour documented in the function's own
docstring -- "a malformed breakdown can never price more write tokens than the record billed" --
was untested. Every expected value below is written out by hand, not recomputed with the
function's own formula. Synthetic usage dicts only.
"""

from tracegauge.adapt import _parse_cache_tiers


def test_no_cache_creation_object_gives_zero_zero() -> None:
    # No ``cache_creation`` key at all -- a record without the breakdown.
    assert _parse_cache_tiers({}) == (0, 0)
    assert _parse_cache_tiers({"cache_creation_input_tokens": 500}) == (0, 0)


def test_both_tiers_present_summing_to_total() -> None:
    usage = {
        "cache_creation_input_tokens": 100,
        "cache_creation": {"ephemeral_5m_input_tokens": 60, "ephemeral_1h_input_tokens": 40},
    }
    assert _parse_cache_tiers(usage) == (40, 100)


def test_only_one_hour_tier_key_present() -> None:
    usage = {
        "cache_creation_input_tokens": 50,
        "cache_creation": {"ephemeral_1h_input_tokens": 30},
    }
    assert _parse_cache_tiers(usage) == (30, 30)


def test_only_five_minute_tier_key_present() -> None:
    usage = {
        "cache_creation_input_tokens": 45,
        "cache_creation": {"ephemeral_5m_input_tokens": 45},
    }
    assert _parse_cache_tiers(usage) == (0, 45)


def test_one_hour_tokens_larger_than_total_is_clamped() -> None:
    # The 1-hour figure itself exceeds the record's total write tokens.
    usage = {
        "cache_creation_input_tokens": 50,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 80},
    }
    assert _parse_cache_tiers(usage) == (50, 50)


def test_five_plus_hour_larger_than_total_only_known_is_clamped() -> None:
    # 1-hour alone is within the total, but five-minute plus one-hour together overshoot it --
    # only the "known" count should be clamped, not the 1-hour figure.
    usage = {
        "cache_creation_input_tokens": 50,
        "cache_creation": {"ephemeral_5m_input_tokens": 60, "ephemeral_1h_input_tokens": 10},
    }
    assert _parse_cache_tiers(usage) == (10, 50)


def test_negative_tier_values_give_zero_for_that_tier() -> None:
    usage = {
        "cache_creation_input_tokens": 100,
        "cache_creation": {"ephemeral_5m_input_tokens": -20, "ephemeral_1h_input_tokens": 30},
    }
    assert _parse_cache_tiers(usage) == (30, 30)


def test_none_tier_values_treated_as_zero() -> None:
    usage = {
        "cache_creation_input_tokens": 100,
        "cache_creation": {"ephemeral_5m_input_tokens": 40, "ephemeral_1h_input_tokens": None},
    }
    assert _parse_cache_tiers(usage) == (0, 40)


def test_non_dict_cache_creation_gives_zero_zero() -> None:
    usage = {"cache_creation_input_tokens": 50, "cache_creation": "not-a-dict"}
    assert _parse_cache_tiers(usage) == (0, 0)
