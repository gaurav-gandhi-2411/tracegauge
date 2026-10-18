# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_self_baseline.py — Tests for tracegauge/self_baseline.py.

Covers the ten required cases:
  1. Lean-subset lower-half selection (p90-cap + lower-half + quartiles)
  2. Outlier exclusion via p90-cap
  3. min_n gate → 'building' (too few lean sessions)
  4. min_n gate → 'self'   (enough lean sessions)
  5. Cold-start / zero sessions → 'building', None stats
  6. nothing is borrowed from the bundled table; activation at 10 of your own sessions
  7. Anti-trap: lean-subset p75 is below a heavy test session; naive p75 is not
  8. Scope floor self-derived (user p10 below b2_floor; user p10 above b2_floor)
  9. Scope floor MIN_MEANINGFUL_TURNS guard (user p10 < 20 → floor clamped to 20)
 10. domain_of_validity text by source
"""

import sqlite3
from pathlib import Path

import pytest
from tracegauge.adapt import ADAPTER_VERSION, COST_VERSION
from tracegauge.self_baseline import (
    MIN_MEANINGFUL_TURNS,
    MIN_OWN_SESSIONS,
    building_state,
    compute_self_baselines,
)

# ---------------------------------------------------------------------------
# Minimal B2 baselines fixture used throughout
# ---------------------------------------------------------------------------

_B2 = {
    "scope_gates": {
        "debug-fix": {"p10_turns": 59},
        "infra-deploy": {"p10_turns": 63},
    },
    "types": {
        "debug-fix": {
            "available": True,
            "median": 524_989,
            "p25": 353_407,
            "p75": 654_348,
        },
        "infra-deploy": {
            "available": True,
            "median": 698_512,
            "p25": 386_220,
            "p75": 1_003_593,
        },
    },
}

# B2 with a very small p10_turns so scope floor tests can isolate the guards.
_B2_SMALL_FLOOR = {
    "scope_gates": {
        "debug-fix": {"p10_turns": 10},
    },
    "types": {
        "debug-fix": {
            "available": True,
            "median": 524_989,
            "p25": 353_407,
            "p75": 654_348,
        },
    },
}


# ---------------------------------------------------------------------------
# Helper: build a minimal sessions table in a tmp SQLite DB
# ---------------------------------------------------------------------------


def _make_test_db(
    tmp_path: Path,
    sessions_by_type: dict[str, list[dict]],
    db_name: str = "tes.db",
) -> Path:
    """Create a minimal sessions table and populate it.

    sessions_by_type: { task_type: [{"real_tokens": int, "waste_event_count": int,
                                     "turn_count": int, "scope_status": str}, ...] }
    scope_status defaults to 'in_scope' when not provided.
    """
    db_path = tmp_path / db_name
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id        TEXT PRIMARY KEY,
            task_type         TEXT NOT NULL,
            real_tokens       INTEGER NOT NULL,
            waste_event_count INTEGER NOT NULL DEFAULT 0,
            turn_count        INTEGER,
            scope_status      TEXT NOT NULL DEFAULT 'in_scope',
            adapter_version   INTEGER,
            cost_version      INTEGER
        )
        """
    )
    row_id = 0
    for task_type, rows in sessions_by_type.items():
        for row in rows:
            conn.execute(
                "INSERT INTO sessions "
                "(session_id, task_type, real_tokens, waste_event_count, turn_count, scope_status, "
                "adapter_version, cost_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"sess-{task_type}-{row_id:04d}",
                    task_type,
                    row["real_tokens"],
                    row.get("waste_event_count", 0),
                    row.get("turn_count", None),
                    row.get("scope_status", "in_scope"),
                    # rows default to the current adapter; tests of stale rows override it
                    row.get("adapter_version", ADAPTER_VERSION),
                    row.get("cost_version", COST_VERSION),
                ),
            )
            row_id += 1
    conn.commit()
    conn.close()
    return db_path


def _waste_free_sessions(tokens: list[int], turn_count: int = 100) -> list[dict]:
    """Build waste-free session rows with the given token values."""
    return [{"real_tokens": t, "waste_event_count": 0, "turn_count": turn_count} for t in tokens]


# ---------------------------------------------------------------------------
# Test 1: lean-subset lower-half selection
# ---------------------------------------------------------------------------


def test_lean_subset_lower_half(tmp_path: Path) -> None:
    """20 uniform sessions: p90-cap → 18 sessions; lean-subset = lower 9; source='self'."""
    tokens = list(range(100, 2100, 100))  # [100, 200, ..., 2000]
    assert len(tokens) == 20

    db_path = _make_test_db(
        tmp_path,
        {"debug-fix": _waste_free_sessions(tokens)},
    )
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    # After p90-cap: int(20 * 0.90) - 1 = 17 → cap = tokens[17] = 1800
    # Sessions ≤ 1800 → [100..1800] = 18 sessions
    # median(18 sessions) = (900 + 1000) / 2 = 950  (statistics.median, even list)
    # lean subset = sessions ≤ 950 = [100..900] = 9 sessions
    assert bl.waste_free_n == 20
    assert bl.lean_n == 9
    assert bl.source == "self"
    assert bl.sessions_needed == 0

    # median of lean [100..900] = 500
    assert bl.median == 500
    # p25: sorted[max(0, int(9*0.25)-1)] = sorted[1] = 200
    assert bl.p25 == 200
    # p75: sorted[min(int(9*0.75), 8)] = sorted[6] = 700
    assert bl.p75 == 700


# ---------------------------------------------------------------------------
# Test 2: outlier exclusion via p90-cap
# ---------------------------------------------------------------------------


def test_outlier_exclusion_p90_cap(tmp_path: Path) -> None:
    """p90-cap removes high-token outliers; lean-subset p75 is well below overall p75."""
    tokens = list(range(100, 2100, 100))
    assert len(tokens) == 20

    db_path = _make_test_db(
        tmp_path,
        {"debug-fix": _waste_free_sessions(tokens)},
    )
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    # Overall p75 (without lean-subset) would be the 75th percentile of all 20
    # values ≈ 1550 (index 14 of 20 = 1500, or median-based ≈ 1500).
    # With lean-subset, p75 = 700 — much lower.
    assert bl.p75 is not None
    assert bl.p75 < 1_500  # sanity: lean p75 is well below raw overall p75


# ---------------------------------------------------------------------------
# Test 3: min_n gate → 'building' (12 waste-free sessions → lean ≈ 5-6 < 8)
# ---------------------------------------------------------------------------


def test_min_n_gate_building(tmp_path: Path) -> None:
    """9 waste-free sessions of the type: fewer than the 10 of your own that a verdict needs."""
    tokens = list(range(100, 1000, 100))  # [100..900], 9 sessions
    assert len(tokens) == 9

    db_path = _make_test_db(
        tmp_path,
        {"debug-fix": _waste_free_sessions(tokens)},
    )
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    assert bl.source == "building"
    assert bl.waste_free_n == 9
    assert bl.sessions_needed == MIN_OWN_SESSIONS - 9
    assert bl.p25 is None
    assert bl.median is None
    assert bl.p75 is None


# ---------------------------------------------------------------------------
# Test 4: min_n gate → 'self' (18 waste-free sessions → lean ≥ 8)
# ---------------------------------------------------------------------------


def test_min_n_gate_active(tmp_path: Path) -> None:
    """18 waste-free sessions: lean-subset ≥ 8 after p90-cap → source='self'."""
    tokens = list(range(100, 1900, 100))  # [100..1800], 18 sessions
    assert len(tokens) == 18

    db_path = _make_test_db(
        tmp_path,
        {"debug-fix": _waste_free_sessions(tokens)},
    )
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    # p90-cap: idx = max(0, int(18*0.90)-1) = max(0,15) = 15 → cap=tokens[15]=1600
    # sessions ≤ 1600: 16 sessions
    # median(16) = (800+900)/2 = 850
    # lean ≤ 850: [100..800] = 8 sessions
    assert bl.source == "self"
    assert bl.lean_n >= 8
    assert bl.sessions_needed == 0
    assert bl.p25 is not None
    assert bl.median is not None
    assert bl.p75 is not None


# ---------------------------------------------------------------------------
# Test 5: cold-start / zero sessions → 'building', None stats, sessions_needed = min_lean_n
# ---------------------------------------------------------------------------


def test_cold_start_unavailable_not_blank(tmp_path: Path) -> None:
    """Zero sessions for a type → source='building', all stats None, sessions_needed=8."""
    db_path = _make_test_db(tmp_path, {"debug-fix": []})
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    assert bl.source == "building"
    assert bl.p25 is None
    assert bl.median is None
    assert bl.p75 is None
    assert bl.lean_n == 0
    assert bl.sessions_needed == MIN_OWN_SESSIONS


# ---------------------------------------------------------------------------
# Test 6: corpus_fallback opt-in
# ---------------------------------------------------------------------------


def test_nothing_is_borrowed_from_the_bundled_table(tmp_path: Path) -> None:
    """Zero sessions: no bundled statistic is returned and the 'corpus' source does not exist."""
    db_path = _make_test_db(tmp_path, {"debug-fix": []})
    assert _B2["types"]["debug-fix"]["p25"]  # control: the bundled table does have a band
    with pytest.raises(TypeError):
        compute_self_baselines(db_path, _B2, corpus_fallback=True)  # type: ignore[call-arg]
    state = compute_self_baselines(db_path, _B2)
    assert {tb.source for tb in state.by_type.values()} == {"building"}
    assert all(tb.p25 is None and tb.median is None for tb in state.by_type.values())


def test_activation_needs_ten_of_your_own_sessions(tmp_path: Path) -> None:
    """The 10th waste-free session of a type switches it from building to your own baseline."""
    nine = _make_test_db(
        tmp_path, {"debug-fix": _waste_free_sessions(list(range(100, 1000, 100)))}, db_name="n9.db"
    )
    ten = _make_test_db(
        tmp_path, {"debug-fix": _waste_free_sessions(list(range(100, 1100, 100)))}, db_name="n10.db"
    )
    assert compute_self_baselines(nine, _B2).by_type["debug-fix"].source == "building"
    assert compute_self_baselines(ten, _B2).by_type["debug-fix"].source == "self"


def test_building_state_without_a_store_is_all_building() -> None:
    state = building_state(_B2)
    assert set(state.by_type) == set(_B2["scope_gates"])
    for tb in state.by_type.values():
        assert tb.source == "building" and tb.waste_free_n == 0 and tb.p25 is None
        assert tb.sessions_needed == MIN_OWN_SESSIONS
        assert "0/10" in tb.domain_of_validity


# ---------------------------------------------------------------------------
# Test 7: anti-trap — lean-subset prevents heavy sessions from hiding above p75
# ---------------------------------------------------------------------------


def test_anti_trap(tmp_path: Path) -> None:
    """Lean-subset baseline correctly flags a heavy session that naive baseline misses.

    Dataset: 20 sessions spanning 300K–1.5M with most mass in the lower half.
    After p90-cap the lean subset p75 ≤ ~700K.
    A test session at 1_000_000 tokens:
      - With lean-subset baseline:  1M > lean_p75  → above_p75  (anti-trap works)
      - With naive (all-sessions) baseline: 1M ≤ naive_p75       (trap fires)
    """
    tokens = [
        300_000,
        350_000,
        400_000,
        450_000,
        500_000,
        550_000,
        600_000,
        650_000,
        700_000,
        750_000,
        800_000,
        850_000,
        900_000,
        950_000,
        1_000_000,
        1_100_000,
        1_200_000,
        1_300_000,
        1_400_000,
        1_500_000,
    ]
    assert len(tokens) == 20

    db_path = _make_test_db(
        tmp_path,
        {"debug-fix": _waste_free_sessions(tokens)},
    )
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    # Part A: lean-subset baseline correctly flags 1_000_000 as above_p75.
    assert bl.source == "self", f"Expected source='self', got '{bl.source}'"
    assert bl.p75 is not None
    assert bl.p75 < 1_000_000, (
        f"Anti-trap failed: 1_000_000 should be above lean p75 ({bl.p75}) "
        "but it is not — the lean-subset filter is not working."
    )

    # Part B: naive baseline (all waste-free sessions without lean-subset) would
    # NOT flag 1_000_000 as above_p75, demonstrating the lean-subset is load-bearing.
    # Naive p75 = _quartiles(sorted all 20 tokens)[2]
    sorted_all = sorted(tokens)
    # Using the same _quartiles logic: p75_idx = min(int(20*0.75), 19) = 15
    naive_p75_idx = min(int(len(sorted_all) * 0.75), len(sorted_all) - 1)
    naive_p75 = sorted_all[naive_p75_idx]

    assert naive_p75 >= 1_000_000, (
        f"Naive p75 ({naive_p75}) should be >= 1_000_000 so the trap is visible, "
        "but it's not — this dataset doesn't demonstrate the anti-trap effectively."
    )


# ---------------------------------------------------------------------------
# Test 8: scope floor self-derived
# ---------------------------------------------------------------------------


def test_scope_floor_self_derived(tmp_path: Path) -> None:
    """User turn-counts below b2_floor → floor = max(min(user_p10, b2_floor), 20).

    Also verifies: when user_p10 > b2_floor the floor does NOT exceed b2_floor.
    """
    # Give enough waste-free sessions to get source='self'.
    tokens = list(range(100_000, 1_000_000, 50_000))  # 18 sessions

    b2_floor = _B2["scope_gates"]["debug-fix"]["p10_turns"]  # 59

    # --- Scenario A: user p10 below b2_floor ---
    # 15 sessions with turn_counts well below 59.
    turn_counts_low = [25, 28, 30, 32, 35, 38, 40, 42, 45, 48, 50, 52, 55, 57, 59]
    assert len(turn_counts_low) >= 10
    sessions_a = [
        {"real_tokens": t, "waste_event_count": 0, "turn_count": tc}
        for t, tc in zip(tokens, turn_counts_low)
    ]
    db_a = _make_test_db(tmp_path, {"debug-fix": sessions_a}, db_name="a.db")
    state_a = compute_self_baselines(db_a, _B2)
    bl_a = state_a.by_type["debug-fix"]

    # p10 of sorted [25..59] = counts[max(0, int(15*0.10)-1)] = counts[0] = 25
    # effective = max(min(25, 59), 20) = max(25, 20) = 25
    assert bl_a.scope_floor == max(min(25, b2_floor), MIN_MEANINGFUL_TURNS)
    assert bl_a.scope_floor <= b2_floor

    # --- Scenario B: user p10 above b2_floor → floor must NOT exceed b2_floor ---
    turn_counts_high = [70, 75, 80, 85, 90, 95, 100, 105, 110, 115, 120, 125, 130, 135, 140]
    assert len(turn_counts_high) >= 10
    sessions_b = [
        {"real_tokens": t, "waste_event_count": 0, "turn_count": tc}
        for t, tc in zip(tokens, turn_counts_high)
    ]
    db_b = _make_test_db(tmp_path, {"debug-fix": sessions_b}, db_name="b.db")
    state_b = compute_self_baselines(db_b, _B2)
    bl_b = state_b.by_type["debug-fix"]

    # p10 of [70..140] = 70 > b2_floor=59 → effective = min(70, 59) = 59
    assert bl_b.scope_floor == b2_floor
    assert bl_b.scope_floor <= b2_floor


# ---------------------------------------------------------------------------
# Test 9: scope floor MIN_MEANINGFUL_TURNS guard
# ---------------------------------------------------------------------------


def test_scope_floor_min_meaningful_guard(tmp_path: Path) -> None:
    """User p10 turn_count is tiny → floor is clamped to MIN_MEANINGFUL_TURNS (20).

    Uses a custom b2_floor=10 (below MIN_MEANINGFUL_TURNS) with 10 sessions
    having very short turn_counts so user_p10 = 1.  Without the guard the
    effective floor would be max(min(1, 10), ...) = ... → 10, but MIN_MEANINGFUL_TURNS
    raises it to 20.
    """
    b2_small = {
        "scope_gates": {"debug-fix": {"p10_turns": 10}},
        "types": _B2["types"],
    }

    # 15 sessions with turn_counts mostly 1–8 (user p10 will be 1).
    tokens = list(range(100_000, 850_000, 50_000))  # 15 sessions
    turn_counts = [1, 1, 2, 2, 3, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18]
    assert len(turn_counts) == len(tokens) == 15

    sessions = [
        {"real_tokens": t, "waste_event_count": 0, "turn_count": tc}
        for t, tc in zip(tokens, turn_counts)
    ]
    db_path = _make_test_db(tmp_path, {"debug-fix": sessions})
    state = compute_self_baselines(db_path, b2_small)
    bl = state.by_type["debug-fix"]

    # p10 of sorted [1,1,2,2,3,3,4,5,6,7,8,10,12,15,18]:
    # idx = max(0, int(15*0.10)-1) = max(0,0) = 0 → counts[0] = 1
    # effective = max(min(1, 10), MIN_MEANINGFUL_TURNS) = max(1, 20) = 20
    assert bl.scope_floor == MIN_MEANINGFUL_TURNS, (
        f"Expected scope_floor={MIN_MEANINGFUL_TURNS}, got {bl.scope_floor}. "
        "MIN_MEANINGFUL_TURNS guard is not working."
    )
    assert bl.scope_floor > 1  # confirms guard activated (not 1, the raw p10)


# ---------------------------------------------------------------------------
# Test 10: domain_of_validity text by source
# ---------------------------------------------------------------------------


def test_domain_of_validity_states_source(tmp_path: Path) -> None:
    """DOV strings contain the expected phrases for each source variant."""
    # --- 'self' ---
    tokens_self = list(range(100, 2100, 100))  # 20 sessions → source='self'
    db_self = _make_test_db(
        tmp_path,
        {"debug-fix": _waste_free_sessions(tokens_self)},
        db_name="self.db",
    )
    bl_self = compute_self_baselines(db_self, _B2).by_type["debug-fix"]
    assert bl_self.source == "self"
    dov_self = bl_self.domain_of_validity.lower()
    assert "your own" in dov_self
    assert "relative" in dov_self
    assert "not an absolute" in dov_self

    # --- 'building' ---
    db_building = _make_test_db(tmp_path, {"debug-fix": []}, db_name="building.db")
    bl_building = compute_self_baselines(db_building, _B2).by_type["debug-fix"]
    assert bl_building.source == "building"
    dov_building = bl_building.domain_of_validity
    assert "Building your debug-fix baseline (0/10" in dov_building
    assert "No token verdict until then" in dov_building


# ---------------------------------------------------------------------------
# Test 11: OOS sessions excluded from lean subset
# ---------------------------------------------------------------------------


def test_oos_sessions_excluded_from_lean_subset(tmp_path: Path) -> None:
    """OOS sessions (scope_status='out_of_scope') must NOT pollute the lean subset.

    Scenario: 20 in-scope waste-free sessions [100K-2M] + 2 OOS low-token stubs
    (42K at 12 turns, 57K at 14 turns). Without the fix the stubs drag p25 to ~57K.
    With the fix they are excluded and p25 is well above median/3.
    """
    # 20 in-scope sessions
    in_scope = [
        {"real_tokens": t, "waste_event_count": 0, "turn_count": 200, "scope_status": "in_scope"}
        for t in range(100_000, 2_100_000, 100_000)  # [100K..2M]
    ]
    # 2 OOS stubs with low tokens
    oos_stubs = [
        {
            "real_tokens": 42_092,
            "waste_event_count": 0,
            "turn_count": 12,
            "scope_status": "out_of_scope",
        },
        {
            "real_tokens": 57_910,
            "waste_event_count": 0,
            "turn_count": 14,
            "scope_status": "out_of_scope",
        },
    ]
    db_path = _make_test_db(tmp_path, {"debug-fix": in_scope + oos_stubs})
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    assert bl.source == "self", f"Expected 'self', got '{bl.source}'"
    assert bl.p25 is not None
    # OOS stubs are excluded: p25 must be well above their 57K token counts
    assert bl.p25 > 100_000, (
        f"p25={bl.p25:,} is suspiciously low — OOS stubs may not have been excluded"
    )
    # Stability guard: p25 >= median/3
    assert bl.p25 >= bl.median // 3, (
        f"p25={bl.p25:,} < median/3={bl.median // 3:,} — stability guard should have fired"
    )


# ---------------------------------------------------------------------------
# Test 12: Band stability guard
# ---------------------------------------------------------------------------


def test_band_stability_guard(tmp_path: Path) -> None:
    """lean_n >= min_lean_n but p25 < median/3 → source='building', not 'self'.

    Dataset: 4 near-zero outliers (5K) mixed with moderate-to-high sessions spanning
    200K–1.5M (20 sessions total).  After p90-cap the lean subset is:
        [5K, 5K, 5K, 5K, 200K, 300K, 400K, 500K, 600K]  (n=9)
    p25 = 5K, median = 200K, median/3 = 66K → p25 < median/3 → guard fires.
    """
    tokens = [5_000] * 4 + [
        200_000,
        300_000,
        400_000,
        500_000,
        600_000,
        700_000,
        800_000,
        900_000,
        1_000_000,
        1_100_000,
        1_200_000,
        1_300_000,
        1_400_000,
        1_500_000,
        1_600_000,
        1_700_000,
    ]  # 4 + 16 = 20 sessions
    sessions = [
        {"real_tokens": t, "waste_event_count": 0, "turn_count": 200, "scope_status": "in_scope"}
        for t in tokens
    ]
    db_path = _make_test_db(tmp_path, {"debug-fix": sessions})
    state = compute_self_baselines(db_path, _B2)
    bl = state.by_type["debug-fix"]

    # lean subset: [5K,5K,5K,5K,200K,300K,400K,500K,600K] → p25=5K, median=200K
    # p25=5K < median/3=66K → stability guard fires → source = 'building'
    assert bl.source == "building", (
        f"Expected 'building' (unstable band), got '{bl.source}' (p25={bl.p25}, lean_n={bl.lean_n})"
    )
    assert bl.p25 is None
    assert "too wide" in bl.domain_of_validity or "tighten" in bl.domain_of_validity


# ---------------------------------------------------------------------------
# Test 13: zero-token stubs excluded from scope-floor p10 computation
# ---------------------------------------------------------------------------


def test_zero_token_stubs_excluded_from_scope_floor_p10(tmp_path: Path) -> None:
    """Zero-token stubs (turn_count > 0 but real_tokens = 0) must NOT drag down the
    scope-floor p10.

    Scenario: 10 zero-token stubs with turn_counts 1-10, plus 20 substantive sessions
    with turn_counts 60-79.  Without the real_tokens > 0 filter the p10 of the combined
    set (n=30) would be ~3, clamped to MIN_MEANINGFUL_TURNS=20 by the guard — the guard
    fires for the wrong reason.  With the filter the stubs are excluded: p10 of the 20
    substantive sessions = 62, effective floor = min(62, b2_floor=59) = 59 (b2 cap).
    """
    b2_small = {
        "scope_gates": {"debug-fix": {"p10_turns": 59}},
        "types": _B2["types"],
    }

    # 10 zero-token stubs with turn_counts 1-10
    zero_stubs = [
        {"real_tokens": 0, "waste_event_count": 0, "turn_count": tc} for tc in range(1, 11)
    ]

    # 20 substantive sessions with turn_counts 60-79, tokens 200K-600K
    substantive = [
        {"real_tokens": 200_000 + i * 20_000, "waste_event_count": 0, "turn_count": 60 + i}
        for i in range(20)
    ]

    db_path = _make_test_db(tmp_path, {"debug-fix": zero_stubs + substantive})
    state = compute_self_baselines(db_path, b2_small)
    bl = state.by_type["debug-fix"]

    # p10 of substantive turn_counts [60..79] with n=20:
    # idx = max(0, int(20*0.10)-1) = 1 -> sorted[1] = 61
    # effective = max(min(61, 59), 20) = 59 (b2 cap)
    assert bl.scope_floor == 59, (
        f"Expected scope_floor=59 (b2-capped from substantive p10=61), got {bl.scope_floor}. "
        "Zero-token stubs may be polluting the p10 computation."
    )
    # Guard must NOT have fired (floor should be data-derived, not MIN_MEANINGFUL_TURNS=20)
    assert bl.scope_floor != MIN_MEANINGFUL_TURNS, (
        f"scope_floor == MIN_MEANINGFUL_TURNS={MIN_MEANINGFUL_TURNS}: guard fired when it "
        "should not have — zero-token stubs are still in the p10 computation."
    )


def test_inactive_corpus_cell_with_null_scope_gate_does_not_crash(tmp_path: Path) -> None:
    """A corpus cell below the activation minimum ships p10_turns=None; the floor falls back."""
    b2 = {
        "scope_gates": {"feature-build": {"p10_turns": None}},
        "types": {"feature-build": {"available": False, "n": 8}},
    }
    db_path = _make_test_db(tmp_path, {"feature-build": _waste_free_sessions([100, 200])})
    state = compute_self_baselines(db_path, b2)
    bl = state.by_type["feature-build"]
    assert bl.source == "building"
    assert bl.scope_floor == MIN_MEANINGFUL_TURNS
