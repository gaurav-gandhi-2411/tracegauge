# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_coach_grounded.py — Every coach recommendation is grounded in MEASURED data.

Covers: silence below the N-gate (MIN_N_FOR_HABIT), H1/H2/H3 each computed from
real stored/computed data (never a fabricated percentage), ranking by measured
$ impact, and a source-level guard against a hardcoded savings claim slipping in.
"""

import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tracegauge.coach import CAVEAT_SUFFIX, MIN_N_FOR_HABIT, get_habits
from tracegauge.cost import load_price_table
from tracegauge.self_baseline import SelfBaselineState, TypeBaseline
from tracegauge.store import open_db

_PRICES = load_price_table()


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    task_type: str = "infra-deploy",
    source_path: str = "",
    cost_usd: float | None = 1.0,
    waste_events: list[dict] | None = None,
    band_verdict: str = "within_band",
) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, task_type, source_path, source_mtime, source_hash, scored_at,
            axes_scored, real_tokens, scope_status, baseline_available,
            p25, p75, median, band_verdict, interpretation, token_domain_of_validity,
            baseline_source, judge_verdict, judge_score, judge_reasoning,
            trajectory_domain_of_validity, judge_source_hash,
            waste_event_count, waste_events, waste_domain_of_validity,
            turn_count, session_cost_usd, cost_approximate, cost_domain_of_validity
        ) VALUES (
            ?, ?, ?, 0.0, 'hash', '2026-07-01T00:00:00+00:00',
            '["token"]', 1000, 'in_scope', 1,
            NULL, NULL, NULL, ?, '', '',
            'self', NULL, NULL, NULL,
            '', NULL,
            ?, ?, '',
            30, ?, 0, ''
        )
        """,
        (
            session_id,
            task_type,
            source_path,
            band_verdict,
            len(waste_events or []),
            json.dumps(waste_events or []),
            cost_usd,
        ),
    )
    conn.commit()


def _active_baseline(task_type: str = "infra-deploy") -> SelfBaselineState:
    tb = TypeBaseline(
        task_type=task_type,
        source="self",
        p25=100,
        median=200,
        p75=300,
        own_n=15,
        sessions_needed=0,
        scope_floor=20,
        domain_of_validity="",
    )
    return SelfBaselineState(by_type={task_type: tb}, total_sessions=30)


def _building_baseline(task_type: str = "infra-deploy") -> SelfBaselineState:
    tb = TypeBaseline(
        task_type=task_type,
        source="building",
        p25=None,
        median=None,
        p75=None,
        own_n=2,
        sessions_needed=6,
        scope_floor=20,
        domain_of_validity="",
    )
    return SelfBaselineState(by_type={task_type: tb}, total_sessions=2)


# ---------------------------------------------------------------------------
# Silence over a made-up tip
# ---------------------------------------------------------------------------


def test_no_habits_on_empty_store(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    assert get_habits(conn, SelfBaselineState(), _PRICES) == []


def test_h2_silent_below_n_gate(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    # 3 sessions with waste, 2 without — total 5 sessions but n_with_waste=3 < MIN_N_FOR_HABIT.
    for i in range(3):
        _insert_session(conn, f"waste-{i}", waste_events=[{"wasted_cost_usd": 0.5}])
    for i in range(2):
        _insert_session(conn, f"clean-{i}", waste_events=[])
    habits = get_habits(conn, SelfBaselineState(), _PRICES)
    assert not any(h.habit_id == "H2" for h in habits)


def test_h2_fires_at_n_gate(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"waste-{i}", waste_events=[{"wasted_cost_usd": 0.5}])
    for i in range(2):
        _insert_session(conn, f"clean-{i}", waste_events=[])

    habits = get_habits(conn, SelfBaselineState(), _PRICES)
    h2 = next((h for h in habits if h.habit_id == "H2"), None)
    assert h2 is not None
    assert h2.measured_n == MIN_N_FOR_HABIT
    assert f"{MIN_N_FOR_HABIT} of your last {MIN_N_FOR_HABIT + 2} sessions" in h2.message
    assert CAVEAT_SUFFIX in h2.message
    # The $ figure in the message is the measured sum, not a hardcoded literal.
    assert f"${MIN_N_FOR_HABIT * 0.5:.2f}" in h2.message


def test_h3_silent_when_baseline_still_building(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"above-{i}", band_verdict="above_p75", cost_usd=10.0)
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"in-band-{i}", band_verdict="within_band", cost_usd=1.0)

    habits = get_habits(conn, _building_baseline(), _PRICES)
    assert not any(h.habit_id == "H3" for h in habits)


def test_h3_fires_once_baseline_is_active_and_n_gate_clears(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"above-{i}", band_verdict="above_p75", cost_usd=10.0)
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"in-band-{i}", band_verdict="within_band", cost_usd=1.0)

    habits = get_habits(conn, _active_baseline(), _PRICES)
    h3 = next((h for h in habits if h.habit_id == "H3"), None)
    assert h3 is not None
    assert h3.measured_n == MIN_N_FOR_HABIT
    assert "ABOVE your own baseline band" in h3.message
    assert CAVEAT_SUFFIX in h3.message
    assert "$10.00" in h3.message
    assert "$1.00" in h3.message


def test_h3_silent_when_above_group_below_n_gate(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    for i in range(2):  # below MIN_N_FOR_HABIT
        _insert_session(conn, f"above-{i}", band_verdict="above_p75", cost_usd=10.0)
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"in-band-{i}", band_verdict="within_band", cost_usd=1.0)

    habits = get_habits(conn, _active_baseline(), _PRICES)
    assert not any(h.habit_id == "H3" for h in habits)


# ---------------------------------------------------------------------------
# H1 — high context re-send ratio (computed on demand via re-adapted attribution)
# ---------------------------------------------------------------------------


def _fake_record(turns: list[dict], session_id: str) -> dict:
    return {
        "session_id": session_id,
        "digest": {
            "session_id": session_id,
            "domain": "unknown",
            "resolved": False,
            "total_tokens": sum(t["token_count_input"] + t["token_count_output"] for t in turns),
            "turn_count": len(turns),
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.0,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "synthetic",
            "turns": turns,
        },
    }


def _ai_turn(idx: int, cache_read: int, cache_creation: int = 0, output: int = 100) -> dict:
    total_input = cache_read + cache_creation + 100  # some fresh input too
    return {
        "turn_index": idx,
        "role": "ai",
        "tool_names": [],
        "content_snippet": "x",
        "token_count_input": total_input,
        "token_count_output": output,
        "cache_read": cache_read,
        "h2_duplicate": False,
        "cache_creation": cache_creation,
        "model": "claude-sonnet-4-6",
    }


def test_h1_fires_when_high_resend_group_costs_more(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")

    # High-resend sessions: cache_read dominates (>60% of billed tokens), costlier.
    high_turns = [_ai_turn(0, cache_read=9000, cache_creation=0, output=100)]
    # Low-resend (mostly fresh/growth) sessions: cheaper.
    low_turns = [_ai_turn(0, cache_read=100, cache_creation=100, output=100)]

    records: dict[str, dict] = {}
    for i in range(MIN_N_FOR_HABIT):
        sid = f"high-{i}"
        path = str(tmp_path / f"{sid}.jsonl")
        Path(path).write_text("{}", encoding="utf-8")
        _insert_session(conn, sid, source_path=path, cost_usd=5.0)
        records[path] = _fake_record(high_turns, sid)
    for i in range(MIN_N_FOR_HABIT):
        sid = f"low-{i}"
        path = str(tmp_path / f"{sid}.jsonl")
        Path(path).write_text("{}", encoding="utf-8")
        _insert_session(conn, sid, source_path=path, cost_usd=1.0)
        records[path] = _fake_record(low_turns, sid)

    def fake_adapt(path: Path) -> dict:
        return records[str(path)]

    with patch("tracegauge.coach.adapt_session", side_effect=fake_adapt):
        habits = get_habits(conn, SelfBaselineState(), _PRICES)

    h1 = next((h for h in habits if h.habit_id == "H1"), None)
    assert h1 is not None
    assert h1.measured_n == MIN_N_FOR_HABIT
    assert ">60% of billed tokens" in h1.message
    assert "$5.00" in h1.message
    assert "$1.00" in h1.message
    assert CAVEAT_SUFFIX in h1.message


# ---------------------------------------------------------------------------
# Ranking + no hardcoded savings claim
# ---------------------------------------------------------------------------


def test_habits_ranked_by_measured_impact_descending(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    # H2 with a small total impact.
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"waste-{i}", waste_events=[{"wasted_cost_usd": 0.10}])
    for i in range(2):
        _insert_session(conn, f"clean-{i}", waste_events=[])
    # H3 with a much larger total impact.
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"above-{i}", band_verdict="above_p75", cost_usd=100.0)
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"in-band-{i}", band_verdict="within_band", cost_usd=1.0)

    habits = get_habits(conn, _active_baseline(), _PRICES, top_n=5)
    assert len(habits) >= 2
    assert habits[0].impact_usd >= habits[1].impact_usd
    assert habits[0].habit_id == "H3"  # the larger measured impact ranks first


def test_top_n_truncation(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"waste-{i}", waste_events=[{"wasted_cost_usd": 1.0}])
    for i in range(2):
        _insert_session(conn, f"clean-{i}", waste_events=[])
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"above-{i}", band_verdict="above_p75", cost_usd=100.0)
    for i in range(MIN_N_FOR_HABIT):
        _insert_session(conn, f"in-band-{i}", band_verdict="within_band", cost_usd=1.0)

    habits = get_habits(conn, _active_baseline(), _PRICES, top_n=1)
    assert len(habits) == 1


def test_no_hardcoded_savings_percentage_in_source() -> None:
    """Guard against a fabricated universal savings claim (e.g. 'save 40%') in coach.py.

    Every percentage/dollar figure in a habit message must come from an f-string
    interpolation of a computed value, never a bare literal claim like 'save 40%'.
    """
    src = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("tracegauge", "coach.py")
        .read_text(encoding="utf-8")
    )
    assert not re.search(r"save[s]?\s*~?\d+%", src, re.IGNORECASE)
    assert not re.search(r"\d+%\s*(less|savings|cheaper)", src, re.IGNORECASE)
