# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_projection_labeled.py — Budget projection is a labeled self-trend, never a forecast.

Covers: silence when no sessions in window, presence of the non-forecast caveat
and window/N, and that no false-certainty phrasing ("you will spend") ever appears.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracegauge.adapt import ADAPTER_VERSION, COST_VERSION
from tracegauge.budget import compute_budget_projection
from tracegauge.store import open_db

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    source_mtime_dt: datetime,
    cost_usd: float | None,
    scored_at_dt: datetime | None = None,
) -> None:
    """Insert a minimal-but-valid session row. ``source_mtime_dt`` is what
    ``compute_budget_projection`` now filters/orders on (issue #12);
    ``scored_at_dt`` defaults to the same instant but can be set
    independently to simulate a batch-scoring workflow, where every session
    in one ``tracegauge scan`` run shares the same ``scored_at`` regardless of when
    the real usage happened."""
    scored_at_dt = scored_at_dt if scored_at_dt is not None else source_mtime_dt
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, task_type, source_path, source_mtime, source_hash, scored_at,
            axes_scored, real_tokens, scope_status, baseline_available,
            p25, p75, median, band_verdict, interpretation, token_domain_of_validity,
            baseline_source, judge_verdict, judge_score, judge_reasoning,
            trajectory_domain_of_validity, judge_source_hash,
            waste_event_count, waste_events, waste_domain_of_validity,
            turn_count, session_cost_usd, cost_approximate, cost_domain_of_validity,
            adapter_version, cost_version
        ) VALUES (
            ?, 'infra-deploy', '/fake/path.jsonl', ?, 'hash', ?,
            '["token"]', 1000, 'in_scope', 1,
            NULL, NULL, NULL, 'within_band', '', '',
            'self', NULL, NULL, NULL,
            '', NULL,
            0, '[]', '',
            30, ?, 0, '', ?, ?
        )
        """,
        (
            session_id,
            source_mtime_dt.timestamp(),
            scored_at_dt.isoformat(),
            cost_usd,
            ADAPTER_VERSION,
            COST_VERSION,
        ),
    )
    conn.commit()


def _now() -> datetime:
    return datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Silence when there's nothing to project
# ---------------------------------------------------------------------------


def test_none_when_no_sessions_in_window(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    assert compute_budget_projection(conn, window_days=7, _now=_now()) is None


def test_none_when_sessions_exist_but_outside_window(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    old_ts = _now() - timedelta(days=30)
    _insert_session(conn, "old-session", old_ts, 5.0)
    assert compute_budget_projection(conn, window_days=7, _now=_now()) is None


# ---------------------------------------------------------------------------
# Honest labeling when a projection IS produced
# ---------------------------------------------------------------------------


def test_projection_carries_n_window_and_nonforecast_caveat(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    _insert_session(conn, "s1", now - timedelta(days=3), 4.0)
    _insert_session(conn, "s2", now - timedelta(days=1), 6.0)

    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    assert projection.session_count == 2
    assert projection.window_days == 7
    assert "2 sessions" in projection.message
    assert "based on your last" in projection.message
    assert "not a forecast of future work" in projection.message
    assert "trending toward" in projection.message


def test_projection_never_states_false_certainty(tmp_path: Path) -> None:
    """Regex guard: the message must never claim a bare future spend as certain fact."""
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    _insert_session(conn, "s1", now - timedelta(hours=6), 10.0)

    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    # Must never say "you will spend $X" or "you'll spend $X" — a false-certainty forecast.
    assert not re.search(r"you('ll| will) spend", projection.message, re.IGNORECASE)
    # "trending toward" (labeled estimate) must accompany every dollar figure claim.
    assert "trending toward" in projection.message


# ---------------------------------------------------------------------------
# Issue #12: filters by source_mtime, not scored_at (batch-scoring workflow)
# ---------------------------------------------------------------------------


def test_filters_by_source_mtime_not_scored_at(tmp_path: Path) -> None:
    """Real usage spread across the window, but all scored in one batch run
    (same scored_at) -- the projection must reflect WHEN THE USAGE HAPPENED
    (source_mtime), not when the batch scan happened to run (scored_at).
    Mirrors tests/test_cost_period.py's own test_filters_by_source_mtime_not_scored_at."""
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    batch_scored_at = now  # every session below scored in one batch, right now

    # Real usage: one session per day for the last 3 days -- INSIDE the 7-day window.
    _insert_session(conn, "s1", now - timedelta(days=3), 4.0, scored_at_dt=batch_scored_at)
    _insert_session(conn, "s2", now - timedelta(days=2), 4.0, scored_at_dt=batch_scored_at)
    _insert_session(conn, "s3", now - timedelta(days=1), 4.0, scored_at_dt=batch_scored_at)
    # Real usage from 30 days ago -- OUTSIDE the 7-day window, even though it
    # shares the exact same scored_at as the in-window sessions above. A
    # scored_at-based filter would incorrectly include this session (all 4
    # rows share one scored_at, so a scored_at>=window_start filter can't
    # distinguish them); a source_mtime-based filter correctly excludes it.
    _insert_session(conn, "old", now - timedelta(days=30), 999.0, scored_at_dt=batch_scored_at)

    projection = compute_budget_projection(conn, window_days=7, _now=now)

    assert projection is not None
    assert projection.session_count == 3  # NOT 4 -- "old" is correctly excluded
    assert projection.total_usd_so_far == 12.0  # NOT 1011.0
    # days_observed reflects the REAL span of usage (s1 at day-3 to now),
    # not "0 days" (which a scored_at-based days_observed would compute,
    # since every row shares the identical batch scored_at instant).
    assert projection.days_observed > 2.9


def test_projection_handles_single_very_recent_session_without_crashing(tmp_path: Path) -> None:
    """days_observed floor (1 hour) prevents a divide-by-near-zero blowup."""
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    _insert_session(conn, "s1", now - timedelta(seconds=5), 2.0)

    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    assert projection.projected_usd_for_window >= 0
    assert projection.days_observed > 0
