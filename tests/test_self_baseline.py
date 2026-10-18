# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_self_baseline.py: the user's own token history (tracegauge/self_baseline.py).

A task type is active from MIN_OWN_SESSIONS (10) of the user's own in-scope sessions with usage;
waste is not a filter, zero-usage stubs and out-of-scope sessions do not count; below 10 it is
"building" and nothing is borrowed from the bundled tables.
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
    load_or_compute,
    ordinal,
    own_percentile,
    quartiles,
)

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
# Activation: the threshold edges, what counts and what does not
# ---------------------------------------------------------------------------


def _tokens(n: int) -> list[int]:
    return [100_000 * (i + 1) for i in range(n)]


@pytest.mark.parametrize(
    "n,expected", [(0, "building"), (9, "building"), (10, "self"), (11, "self")]
)
def test_threshold_edges_nine_ten_eleven(tmp_path: Path, n: int, expected: str) -> None:
    db = _make_test_db(tmp_path, {"debug-fix": _waste_free_sessions(_tokens(n))})
    bl = compute_self_baselines(db, _B2).by_type["debug-fix"]
    assert bl.source == expected and bl.own_n == n
    assert bl.sessions_needed == max(0, MIN_OWN_SESSIONS - n)
    if expected == "self":
        assert bl.p25 is not None and bl.median is not None and bl.p75 is not None
    else:
        assert bl.p25 is None and bl.median is None and bl.p75 is None


def test_all_in_scope_sessions_count_whether_or_not_they_have_waste(tmp_path: Path) -> None:
    rows = [
        {"real_tokens": t, "waste_event_count": 3 if i % 2 else 0, "turn_count": 100}
        for i, t in enumerate(_tokens(12))
    ]
    bl = compute_self_baselines(_make_test_db(tmp_path, {"debug-fix": rows}), _B2).by_type[
        "debug-fix"
    ]
    assert bl.source == "self" and bl.own_n == 12  # 6 of them have waste events
    assert [v for _, v in bl.values] == _tokens(12)  # all of them, ascending, none trimmed
    assert (bl.p25, bl.median, bl.p75) == quartiles(_tokens(12))


def test_zero_usage_stubs_do_not_count_toward_the_threshold(tmp_path: Path) -> None:
    stubs = [{"real_tokens": 0, "waste_event_count": 0, "turn_count": 100} for _ in range(10)]
    nine = _make_test_db(
        tmp_path, {"debug-fix": stubs + _waste_free_sessions(_tokens(9))}, db_name="s9.db"
    )
    ten = _make_test_db(
        tmp_path, {"debug-fix": stubs + _waste_free_sessions(_tokens(10))}, db_name="s10.db"
    )
    assert compute_self_baselines(nine, _B2).by_type["debug-fix"].source == "building"
    assert compute_self_baselines(nine, _B2).by_type["debug-fix"].own_n == 9
    assert compute_self_baselines(ten, _B2).by_type["debug-fix"].source == "self"  # control


def test_out_of_scope_sessions_do_not_count(tmp_path: Path) -> None:
    short = [{"real_tokens": 50_000, "waste_event_count": 0, "turn_count": 5} for _ in range(8)]
    db = _make_test_db(tmp_path, {"debug-fix": short + _waste_free_sessions(_tokens(9))})
    bl = compute_self_baselines(db, _B2).by_type["debug-fix"]
    assert bl.own_n == 9 and bl.source == "building"  # the 8 below the 59-turn floor are left out


def test_rows_from_the_old_accounting_are_excluded_and_counted(tmp_path: Path) -> None:
    old = [
        {"real_tokens": 900_000, "waste_event_count": 0, "turn_count": 100, "adapter_version": None}
        for _ in range(4)
    ]
    db = _make_test_db(tmp_path, {"debug-fix": old + _waste_free_sessions(_tokens(10))})
    bl = compute_self_baselines(db, _B2).by_type["debug-fix"]
    assert bl.own_n == 10 and bl.stale_n == 4 and "pre-dedupe" in bl.domain_of_validity


def test_cold_start_unavailable_not_blank(tmp_path: Path) -> None:
    bl = compute_self_baselines(_make_test_db(tmp_path, {"debug-fix": []}), _B2).by_type[
        "debug-fix"
    ]
    assert bl.source == "building" and bl.own_n == 0 and bl.sessions_needed == MIN_OWN_SESSIONS
    assert bl.p25 is None and bl.median is None and bl.p75 is None


def test_nothing_is_borrowed_from_the_bundled_table(tmp_path: Path) -> None:
    db = _make_test_db(tmp_path, {"debug-fix": []})
    assert _B2["types"]["debug-fix"]["p25"]  # control: the bundled table does have a band
    with pytest.raises(TypeError):
        compute_self_baselines(db, _B2, corpus_fallback=True)  # type: ignore[call-arg]
    state = compute_self_baselines(db, _B2)
    assert {tb.source for tb in state.by_type.values()} == {"building"}
    assert all(tb.p25 is None and tb.median is None for tb in state.by_type.values())


def test_building_state_without_a_store_is_all_building() -> None:
    state = building_state(_B2)
    assert set(state.by_type) == set(_B2["scope_gates"])
    for tb in state.by_type.values():
        assert tb.source == "building" and tb.own_n == 0 and tb.p25 is None and tb.values == []
        assert tb.sessions_needed == MIN_OWN_SESSIONS
        assert "0/10" in tb.domain_of_validity


# ---------------------------------------------------------------------------
# Percentile and cache
# ---------------------------------------------------------------------------


def test_own_percentile_is_mid_rank() -> None:
    vals = [10, 20, 30, 40]
    assert own_percentile(vals, 5) == 0 and own_percentile(vals, 100) == 100
    assert own_percentile(vals, 25) == 50  # 2 below, 0 equal
    assert own_percentile(vals, 30) == 62  # 2 below + half of 1 equal = 2.5 / 4 = 62.5 -> 62
    assert own_percentile([7, 7, 7, 7], 7) == 50  # all equal: the middle
    with pytest.raises(ValueError):
        own_percentile([], 1)


def test_ordinals() -> None:
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 50, 83)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "50th", "83rd",
    ]  # fmt: skip


def test_cache_follows_token_changes_not_just_counts(tmp_path: Path) -> None:
    db = _make_test_db(tmp_path, {"debug-fix": _waste_free_sessions(_tokens(10))})
    first = load_or_compute(db, _B2)
    assert first.by_type["debug-fix"].median == quartiles(_tokens(10))[1]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET real_tokens = real_tokens * 2")  # same rows, new tokens
    conn.commit()
    conn.close()
    second = load_or_compute(db, _B2)
    assert second.by_type["debug-fix"].median == quartiles([2 * t for t in _tokens(10)])[1]


def test_cache_round_trip_keeps_the_values(tmp_path: Path) -> None:
    db = _make_test_db(tmp_path, {"debug-fix": _waste_free_sessions(_tokens(10))})
    a = load_or_compute(db, _B2)
    b = load_or_compute(db, _B2)  # served from the cache file
    assert a.by_type["debug-fix"].values == b.by_type["debug-fix"].values
    assert len(b.by_type["debug-fix"].values) == 10


def test_domain_of_validity_states_source(tmp_path: Path) -> None:
    active = compute_self_baselines(
        _make_test_db(tmp_path, {"debug-fix": _waste_free_sessions(_tokens(12))}, db_name="a.db"),
        _B2,
    ).by_type["debug-fix"]
    dov = active.domain_of_validity.lower()
    assert "your own" in dov and "not an absolute" in dov and "12 sessions" in dov
    building = compute_self_baselines(
        _make_test_db(tmp_path, {"debug-fix": []}, db_name="b.db"), _B2
    ).by_type["debug-fix"]
    assert "Building your debug-fix history (0/10" in building.domain_of_validity
    assert "No token verdict until then" in building.domain_of_validity


# ---------------------------------------------------------------------------
# Scope floor (unchanged)
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
    assert bl.scope_floor > 1


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
