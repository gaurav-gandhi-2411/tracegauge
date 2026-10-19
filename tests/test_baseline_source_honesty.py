# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_baseline_source_honesty.py — Verifies that score_session routes the token axis
to the correct reference and that domain_of_validity + baseline_source always declare which
baseline was used.

Cases:
  1. Self-baseline active ('self') → scores against the user's own percentile and p25/p75, not B2
  2. Self-baseline building       → token axis UNAVAILABLE, DOV says "Building"
  3. No self-baseline provided    → falls through to B2, baseline_source='b2_corpus'
  4. Session OOS by self-derived floor → out_of_scope / unavailable even when in B2 scope
  5. Session IN scope under self floor but would be OOS under B2 floor → scored correctly
"""


from tracegauge.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    score_session,
)
from tracegauge.self_baseline import (
    MIN_OWN_SESSIONS,
    SelfBaselineState,
    TypeBaseline,
    quartiles,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    task_description: str,
    turn_count: int,
    real_tokens: int,
    session_id: str = "test-sess-0001",
) -> dict:
    """Minimal record accepted by score_session."""
    ai_turns = [
        {
            "role": "ai",
            "turn_index": 0,
            # real_tokens = input - cache_read + output; set cache_read=0 for simplicity
            "token_count_input": real_tokens,
            "token_count_output": 0,
            "cache_read": 0,
        }
    ]
    return {
        "session_id": session_id,
        "turn_count": turn_count,
        "digest": {
            "task_description": task_description,  # no keywords -> feature-build
            "turns": ai_turns,
        },
    }


def _make_self_baseline(
    task_type: str,
    values: list[int],
    scope_floor: int = 30,
) -> SelfBaselineState:
    """A SelfBaselineState whose one task type holds exactly these own-session token counts."""
    vals = sorted(values)
    pairs = [(f"hist-{i:03d}", v) for i, v in enumerate(vals)]
    active = len(vals) >= MIN_OWN_SESSIONS
    p25, med, p75 = quartiles(vals) if active else (None, None, None)
    bl = TypeBaseline(
        task_type=task_type,
        source="self" if active else "building",
        p25=p25,
        median=med,
        p75=p75,
        own_n=len(vals),
        sessions_needed=max(0, MIN_OWN_SESSIONS - len(vals)),
        scope_floor=scope_floor,
        domain_of_validity="Relative to YOUR OWN sessions of this type." if active else "Building.",
        values=pairs,
    )
    state = SelfBaselineState(total_sessions=100)
    state.by_type[task_type] = bl
    return state


_B2_SIMPLE = {
    "scope_gates": {
        "feature-build": {"p10_turns": 166},
    },
    "types": {
        "feature-build": {
            "available": True,
            "median": 700_000,
            "p25": 500_000,
            "p75": 900_000,
        },
    },
}

# Twenty own sessions from 100K to 2M tokens: p25 = 500K, median = 1.05M, p75 = 1.6M.
_OWN = [100_000 * i for i in range(1, 21)]


# ---------------------------------------------------------------------------
# Test 1: with 10 or more own sessions the verdict is the session's place among them
# ---------------------------------------------------------------------------


def test_own_history_gives_a_percentile_and_a_band_label() -> None:
    self_bl = _make_self_baseline("feature-build", _OWN)
    record = _make_record("add new feature to the app", turn_count=50, real_tokens=1_650_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.band_verdict == "above_p75"  # above the own p75 (1.6M), not the bundled 900K
    assert result.own_sessions == 20 and result.own_percentile == 80
    assert "about the 80th percentile of your 20 feature-build sessions" in result.interpretation
    assert result.p75 == 1_600_000 and result.scope_status == "in_scope"
    assert "YOUR OWN" in result.token_domain_of_validity


def test_within_and_below_labels_follow_the_own_quartiles() -> None:
    self_bl = _make_self_baseline("feature-build", _OWN)
    mid = score_session(
        _make_record("add feature", turn_count=50, real_tokens=1_050_000),
        _B2_SIMPLE,
        self_baseline=self_bl,
    )
    low = score_session(
        _make_record("add feature", turn_count=50, real_tokens=250_000),
        _B2_SIMPLE,
        self_baseline=self_bl,
    )
    assert mid.band_verdict == "within_band" and mid.own_percentile == 50
    assert low.band_verdict == "below_p25" and low.p25 == 500_000 and low.own_percentile == 10


# ---------------------------------------------------------------------------
# Test 2: below 10 own sessions the reading is "building (n/10)" and nothing is borrowed
# ---------------------------------------------------------------------------


def test_threshold_edges_nine_ten_eleven_own_sessions() -> None:
    record = _make_record("add feature", turn_count=50, real_tokens=400_000)
    nine = score_session(
        record, _B2_SIMPLE, self_baseline=_make_self_baseline("feature-build", _OWN[:9])
    )
    ten = score_session(
        record, _B2_SIMPLE, self_baseline=_make_self_baseline("feature-build", _OWN[:10])
    )
    eleven = score_session(
        record, _B2_SIMPLE, self_baseline=_make_self_baseline("feature-build", _OWN[:11])
    )
    assert nine.band_verdict == "unavailable" and nine.interpretation.startswith("building (9/10)")
    assert nine.own_percentile is None and nine.p25 is None and nine.baseline_source == "building"
    assert ten.band_verdict != "unavailable" and ten.own_sessions == 10
    assert ten.baseline_source == "self" and ten.own_percentile is not None
    assert eleven.band_verdict != "unavailable" and eleven.own_sessions == 11
    assert "borrowed" in nine.interpretation


def test_a_session_already_in_the_store_is_not_compared_with_itself() -> None:
    # Ten stored sessions, one of which is the session being scored again: only 9 others.
    values = _OWN[:10]
    self_bl = _make_self_baseline("feature-build", values)
    record = _make_record("add feature", turn_count=50, real_tokens=values[3])
    record["session_id"] = "hist-003"
    again = score_session(record, _B2_SIMPLE, self_baseline=self_bl)
    assert again.band_verdict == "unavailable" and again.own_sessions == 9
    assert again.interpretation.startswith("building (9/10)")


def test_building_gives_unavailable_with_building_dov() -> None:
    self_bl = _make_self_baseline("feature-build", _OWN[:3])
    record = _make_record("add feature", turn_count=50, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "building"
    assert result.band_verdict == "unavailable"
    assert result.p25 is None and result.p75 is None
    assert result.interpretation.startswith("building (3/10)")
    assert "borrowed" in result.interpretation
    assert "building" in result.token_domain_of_validity.lower()


# ---------------------------------------------------------------------------
# Test 3: no self-baseline state passed (library use) -> the raw table result, as before
# ---------------------------------------------------------------------------


def test_no_self_baseline_uses_b2_corpus() -> None:
    """When self_baseline=None, result uses B2 baselines and baseline_source='b2_corpus'."""
    record = _make_record("add feature", turn_count=200, real_tokens=600_000)
    result = score_session(record, _B2_SIMPLE)  # no self_baseline

    assert result.baseline_source == "b2_corpus"
    assert result.band_verdict == "within_band"
    assert result.p75 == 900_000
    assert TOKEN_DOMAIN_OF_VALIDITY in result.token_domain_of_validity


# ---------------------------------------------------------------------------
# Test 4/5/6: the user's own scope floor decides in or out of scope, active or building
# ---------------------------------------------------------------------------


def test_self_floor_oos_despite_b2_inscope() -> None:
    """In scope under B2 (200 > 166) but out of scope under the user's own floor of 250."""
    self_bl = _make_self_baseline("feature-build", _OWN, scope_floor=250)
    record = _make_record("add feature", turn_count=200, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.scope_status == "out_of_scope"
    assert result.band_verdict == "unavailable"
    assert "floor is 250 turns" in result.interpretation


def test_self_floor_inscope_despite_b2_oos() -> None:
    """Out of scope under B2 (50 < 166) but in scope under the user's own floor of 30."""
    self_bl = _make_self_baseline("feature-build", _OWN, scope_floor=30)
    record = _make_record("add feature", turn_count=50, real_tokens=1_050_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.scope_status == "in_scope"
    assert result.band_verdict == "within_band"


def test_building_applies_self_scope_floor() -> None:
    self_bl = _make_self_baseline("feature-build", _OWN[:4], scope_floor=30)
    record = _make_record("add feature", turn_count=50, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "building"
    assert result.scope_status == "in_scope", "the building path must use the own scope floor"
    assert result.band_verdict == "unavailable"
