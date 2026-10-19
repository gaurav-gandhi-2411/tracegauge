# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/budget.py — Rolling-window spend tracking + honest self-trend projection.

Design: research/13_coach_alarm_honesty_design.md (reviewed and approved).

The projection is the user's OWN trend over the trailing window, linearly
extrapolated to the window's end, and ALWAYS labeled with its N (sessions,
days observed) and the non-forecast caveat. Never "you will spend $X" — the
message is always framed as "trending toward," never a promise.

**source_mtime, not scored_at (issue #12)**: filters on the session FILE's
own last-write time on disk -- when the real usage happened -- not on
`scored_at` (when `tracegauge score`/`tracegauge scan` happened to run). These coincide
under an immediate-scoring workflow but diverge under batch-scoring: every
session scored in one `tracegauge scan` run gets the SAME `scored_at`, which would
cluster a week of real spend onto one instant or drop it outside the
window entirely. Same fix `tracegauge/cost_period.py`'s `compute_period_cost`
already applies, for the identical reason -- see that module's own
docstring for the full argument. `source_mtime` is a `REAL` epoch float
column (unlike `scored_at`'s ISO-string column), so this module compares
`datetime.timestamp()`, not `.isoformat()`.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from tracegauge.legacy import current_clause, legacy_clause
from tracegauge.web.cost_format import format_unpriced

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10

DEFAULT_WINDOW_DAYS: int = 7
_MIN_DAYS_OBSERVED: float = (
    1.0 / 24
)  # floor at 1 hour — avoids divide-by-near-zero on a single fresh session


@dataclass
class BudgetProjection:
    window_days: int
    session_count: int
    days_observed: float
    total_usd_so_far: float
    projected_usd_for_window: float
    message: str
    # Models missing from the price table across the window's sessions: the dollar
    # figures are then priced-subtotal-only. Empty when everything priced.
    unpriced_models: list[str] = field(default_factory=list)

    @property
    def priced(self) -> bool:
        return not self.unpriced_models


def compute_budget_projection(
    conn: sqlite3.Connection,
    window_days: int = DEFAULT_WINDOW_DAYS,
    _now: datetime | None = None,
) -> BudgetProjection | None:
    """Linear self-trend projection over the trailing window_days.

    Returns None when there are no sessions with cost data in the window —
    silence rather than a fabricated $0 projection (nothing to project).

    LEGACY rows (tracegauge.legacy: scored before usage de-duplication / 1-hour cache pricing) are
    excluded: their dollars are overcounted ~2x, so folding them into a pace would project a
    spend rate that never happened. Use :func:`legacy_rows_excluded_in_window` for their count.
    """
    now = _now if _now is not None else datetime.now(UTC)
    window_start = now - timedelta(days=window_days)

    current_sql, current_params = current_clause(conn)
    rows = conn.execute(
        "SELECT source_mtime, session_cost_usd, cost_unpriced_models FROM sessions "
        f"WHERE session_cost_usd IS NOT NULL AND source_mtime >= ? AND {current_sql} "  # noqa: S608
        "ORDER BY source_mtime ASC",
        (window_start.timestamp(), *current_params),
    ).fetchall()

    if not rows:
        return None

    total_usd = sum(float(r["session_cost_usd"]) for r in rows)
    session_count = len(rows)
    unpriced: set[str] = set()
    for r in rows:
        if r["cost_unpriced_models"]:
            unpriced.update(r["cost_unpriced_models"].split(","))
    unpriced_models = sorted(unpriced)

    first_ts = datetime.fromtimestamp(float(rows[0]["source_mtime"]), tz=UTC)
    days_observed = max((now - first_ts).total_seconds() / 86400.0, _MIN_DAYS_OBSERVED)

    daily_rate = total_usd / days_observed
    projected = daily_rate * window_days

    sessions_str = f"{session_count} session{'s' if session_count != 1 else ''}"
    if unpriced_models and total_usd <= 0:
        # Nothing priced at all: a "$0.00 trend" would be a lie, so no projection is made.
        message = (
            f"Spend so far across {sessions_str} is {format_unpriced(unpriced_models)} -- "
            "no dollar projection is possible until a price is known for these models "
            "(set TRACEGAUGE_PRICE_TABLE or ~/.tes/prices.json)."
        )
        projected = 0.0
    else:
        suffix = f" + {format_unpriced(unpriced_models)}" if unpriced_models else ""
        note = (
            " The figures are a priced subtotal; the unpriced models' spend is NOT included."
            if unpriced_models
            else ""
        )
        message = (
            f"At this pace (~${total_usd:.2f}{suffix} so far across {sessions_str}, "
            f"{days_observed:.1f} of {window_days} days) "
            f"you're trending toward ~${projected:.2f}{suffix} over a {window_days}-day window — "
            f"based on your last {days_observed:.1f} days, not a forecast of future work; "
            f"work volume varies.{note}"
        )

    return BudgetProjection(
        window_days=window_days,
        session_count=session_count,
        days_observed=round(days_observed, 2),
        total_usd_so_far=round(total_usd, 4),
        projected_usd_for_window=round(projected, 4),
        message=message,
        unpriced_models=unpriced_models,
    )


def legacy_rows_excluded_in_window(
    conn: sqlite3.Connection,
    window_days: int = DEFAULT_WINDOW_DAYS,
    _now: datetime | None = None,
) -> int:
    """Legacy rows with cost data inside the window that the projection leaves out."""
    now = _now if _now is not None else datetime.now(UTC)
    legacy_sql, legacy_params = legacy_clause(conn)
    row = conn.execute(
        "SELECT COUNT(*) FROM sessions "
        f"WHERE session_cost_usd IS NOT NULL AND source_mtime >= ? AND {legacy_sql}",  # noqa: S608
        ((now - timedelta(days=window_days)).timestamp(), *legacy_params),
    ).fetchone()
    return int(row[0])


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "BudgetProjection",
    "compute_budget_projection",
    "legacy_rows_excluded_in_window",
]
