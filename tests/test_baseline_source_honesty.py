# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_baseline_source_honesty.py — Verifies that score_session routes the token axis
to the correct reference and that domain_of_validity + baseline_source always declare which
baseline was used.

Cases:
  1. Self-baseline active ('self') → scores against user's lean p25/p75, not B2
  2. Self-baseline building       → token axis UNAVAILABLE, DOV says "Building"
  3. No self-baseline provided    → falls through to B2, baseline_source='b2_corpus'
  4. Session OOS by self-derived floor → out_of_scope / unavailable even when in B2 scope
  5. Session IN scope under self floor but would be OOS under B2 floor → scored correctly
"""


from tracegauge.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    score_session,
)
from tracegauge.self_baseline import SelfBaselineState, TypeBaseline

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
    source: str,
    p25: int | None = None,
    median: int | None = None,
    p75: int | None = None,
    lean_n: int = 10,
    waste_free_n: int = 20,
    sessions_needed: int = 0,
    scope_floor: int = 30,
    dov: str = "",
) -> SelfBaselineState:
    """Build a minimal SelfBaselineState with one TypeBaseline entry."""
    if not dov:
        if source == "self":
            dov = f"Calibrated to YOUR OWN leaner waste-free {task_type} sessions (lean subset: {lean_n} sessions). Relative-to-your-own-baseline; not an absolute efficiency verdict. Baseline: self / scope floor: {scope_floor} turns."
        elif source == "building":
            dov = f"Building your baseline: need {sessions_needed} more waste-free {task_type} sessions (have {lean_n} in lean subset, need 8). Scope floor: {scope_floor} turns."
        else:
            dov = TOKEN_DOMAIN_OF_VALIDITY

    bl = TypeBaseline(
        task_type=task_type,
        source=source,
        p25=p25,
        median=median,
        p75=p75,
        lean_n=lean_n,
        waste_free_n=waste_free_n,
        sessions_needed=sessions_needed,
        scope_floor=scope_floor,
        domain_of_validity=dov,
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


# ---------------------------------------------------------------------------
# Test 1: self-baseline active — scores against user's lean p25/p75, not B2
# ---------------------------------------------------------------------------


def test_self_baseline_active_uses_own_band() -> None:
    """When source='self', band verdict must use TypeBaseline p25/p75, not B2."""
    # Self-baseline: lean band [300K, 400K, 500K]
    # B2 band: [500K, 700K, 900K]
    # Test session: 600K tokens — above_p75 for self (>500K), within_band for B2
    self_bl = _make_self_baseline(
        "feature-build",
        source="self",
        p25=300_000,
        median=400_000,
        p75=500_000,
        scope_floor=30,
    )
    record = _make_record("add new feature to the app", turn_count=50, real_tokens=600_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.band_verdict == "above_p75", (
        f"Expected above_p75 (600K > self p75 500K), got '{result.band_verdict}'"
    )
    assert result.p75 == 500_000
    assert result.scope_status == "in_scope"
    assert "YOUR OWN" in result.token_domain_of_validity
    assert "your own" in result.token_domain_of_validity.lower()


def test_self_baseline_within_band() -> None:
    """Session within self lean band → within_band, interpretation mentions lean band."""
    self_bl = _make_self_baseline(
        "feature-build",
        source="self",
        p25=300_000,
        median=400_000,
        p75=500_000,
        scope_floor=30,
    )
    record = _make_record("add feature", turn_count=50, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.band_verdict == "within_band"
    assert "lean reference band" in result.interpretation


def test_self_baseline_below_p25() -> None:
    """Session below self lean p25 → below_p25."""
    self_bl = _make_self_baseline(
        "feature-build",
        source="self",
        p25=300_000,
        median=400_000,
        p75=500_000,
        scope_floor=30,
    )
    record = _make_record("add feature", turn_count=50, real_tokens=200_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.band_verdict == "below_p25"
    assert result.p25 == 300_000


# ---------------------------------------------------------------------------
# Test 2: self-baseline building — UNAVAILABLE, DOV mentions "Building"
# ---------------------------------------------------------------------------


def test_building_gives_unavailable_with_building_dov() -> None:
    """source='building' → band_verdict='unavailable', baseline_source='building',
    DOV text mentions 'Building'."""
    self_bl = _make_self_baseline(
        "feature-build",
        source="building",
        lean_n=3,
        waste_free_n=3,
        sessions_needed=5,
        scope_floor=30,
    )
    record = _make_record("add feature", turn_count=50, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "building"
    assert result.band_verdict == "unavailable"
    assert result.p25 is None and result.p75 is None
    assert result.interpretation.startswith("building (3/10)")
    assert "borrowed" in result.interpretation
    assert (
        "Building" in result.token_domain_of_validity
        or "building" in result.token_domain_of_validity.lower()
    )


# ---------------------------------------------------------------------------
# Test 3: no self-baseline → falls through to B2, baseline_source='b2_corpus'
# ---------------------------------------------------------------------------


def test_no_self_baseline_uses_b2_corpus() -> None:
    """When self_baseline=None, result uses B2 baselines and baseline_source='b2_corpus'."""
    # Session: 600K tokens, turn_count=200 (above B2 p10_turns=166, so in_scope)
    record = _make_record("add feature", turn_count=200, real_tokens=600_000)
    result = score_session(record, _B2_SIMPLE)  # no self_baseline

    assert result.baseline_source == "b2_corpus"
    # B2 band: p25=500K, p75=900K → 600K is within_band
    assert result.band_verdict == "within_band"
    assert result.p75 == 900_000
    assert TOKEN_DOMAIN_OF_VALIDITY in result.token_domain_of_validity


# ---------------------------------------------------------------------------
# Test 4: self-derived scope floor OOS overrides B2 in-scope
# ---------------------------------------------------------------------------


def test_self_floor_oos_despite_b2_inscope() -> None:
    """Session in_scope under B2 (turn_count=200 > b2_floor=166) but OOS under self
    scope_floor=250 → result is out_of_scope."""
    self_bl = _make_self_baseline(
        "feature-build",
        source="self",
        p25=300_000,
        median=400_000,
        p75=500_000,
        scope_floor=250,  # higher than B2 floor; user has longer typical sessions
    )
    record = _make_record("add feature", turn_count=200, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.scope_status == "out_of_scope"
    assert result.band_verdict == "unavailable"
    assert "self-derived floor" in result.interpretation


# ---------------------------------------------------------------------------
# Test 5: self floor lower than B2 — OOS under B2 becomes in_scope under self
# ---------------------------------------------------------------------------


def test_self_floor_inscope_despite_b2_oos() -> None:
    """Session OOS under B2 (turn_count=50 < b2_floor=166) but in_scope under
    self scope_floor=30 → result is in_scope and scored against self-baseline."""
    self_bl = _make_self_baseline(
        "feature-build",
        source="self",
        p25=150_000,
        median=250_000,
        p75=350_000,
        scope_floor=30,
    )
    record = _make_record("add feature", turn_count=50, real_tokens=200_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "self"
    assert result.scope_status == "in_scope"
    assert result.band_verdict == "within_band"  # 200K is within [150K, 350K]
    assert "YOUR OWN" in result.token_domain_of_validity


# ---------------------------------------------------------------------------
# Test 6: baseline_source='building' applies self scope floor for scope check
# ---------------------------------------------------------------------------


def test_building_applies_self_scope_floor() -> None:
    """source='building' must apply TypeBaseline.scope_floor, not B2 floor, for scope check."""
    # Self scope_floor=30 (lower than B2's 166).  Session turn_count=50.
    # Under B2: out_of_scope (50 < 166).  Under self floor: in_scope (50 >= 30).
    self_bl = _make_self_baseline(
        "feature-build",
        source="building",
        lean_n=4,
        sessions_needed=4,
        scope_floor=30,
    )
    record = _make_record("add feature", turn_count=50, real_tokens=400_000)
    result = score_session(record, _B2_SIMPLE, self_baseline=self_bl)

    assert result.baseline_source == "building"
    # Even though baseline is unavailable, scope gate uses the self-derived floor
    assert result.scope_status == "in_scope", (
        f"Expected in_scope (turn_count=50 >= self floor=30), got '{result.scope_status}'. "
        "The building path may still be using the B2 scope gate."
    )
    assert result.band_verdict == "unavailable"
