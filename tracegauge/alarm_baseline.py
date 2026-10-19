# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/alarm_baseline.py -- the comparison distribution for the live alarm.

The alarm used to compare a live session against ``self_baseline``'s band, which is built from
the LEAN half of a user's waste-free sessions over all time: its "p75" sits near the user's own
median, so the alarm fired on most sessions (80% of a real user's last 50, retrospectively) and
kept mixing sessions from earlier model generations. This module builds the threshold the alarm
now uses (study and numbers: docs/ALARM.md):

* a plain percentile of the user's OWN sessions (not the lean half),
* from RECENT sessions only (``window_days``),
* from the SAME MODEL ERA (the model that did most of a session's token work, e.g.
  ``claude-sonnet-5`` vs ``claude-sonnet-5-5``), because token scale moves between generations,
* ignoring rows from the pre-dedupe adapter (their real_tokens are inflated ~2.4x).

A fallback chain with an explicit minimum sample size picks the first tier that has enough data:

    recent_era_type  recent sessions, same era, same task type
    recent_era       recent sessions, same era, any task type
    recent_type      recent sessions, any era, same task type
    recent           recent sessions, any era, any task type
    shipped          the bundled reference band (upper 95% bound of its p75)
    disabled         no tier qualifies: the alarm stays silent and says why

Pure functions over ``PoolRow`` lists, so the study and the tests exercise exactly this code.
"""

import re
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Chosen from data (docs/ALARM.md): p85 fired on 8/50 (chronological) and 7/50 (leave-one-out)
# of a real user's last 50 sessions, under the 20% target, while still flagging 5/5 and 4/5 of
# the top-decile sessions by tokens. p80 also met the target (9/50 both) but sits on the 20% line
# by construction; p75 did not (12/50 and 11/50).
DEFAULT_PERCENTILE: float = 0.85
# A 7-day window left 4 of 49 sessions with no usable history (chronological, min_n 10); 14, 30,
# 60 days and unbounded gave identical results on that one month of data. 30 days is a round
# window that is not data-starved and still forgets a model generation that ended a month ago.
DEFAULT_WINDOW_DAYS: int = 30
# Same floor as the shipped-baseline builder (a cell is active with >= 10 sessions). A p80 of ten
# values is the 9th smallest: coarse, and the docs say so.
DEFAULT_MIN_N: int = 10

TIER_RECENT_ERA_TYPE = "recent_era_type"
TIER_RECENT_ERA = "recent_era"
TIER_RECENT_TYPE = "recent_type"
TIER_RECENT = "recent"
TIER_SHIPPED = "shipped"
TIER_DISABLED = "disabled"

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

_MODEL_DATE_SUFFIX = re.compile(r"-\d{8}$")
_MODEL_CONTEXT_SUFFIX = re.compile(r"\[[^\]]*\]$")
_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class PoolRow:
    """One finished session of the user: just what the alarm baseline needs."""

    session_id: str
    task_type: str
    era: str  # normalized dominant model; "" when unknown
    real_tokens: int
    mtime: float  # source transcript mtime = when the session last ran


@dataclass
class AlarmThreshold:
    """The threshold the alarm compares against, with its provenance."""

    status: str  # STATUS_ACTIVE | STATUS_DISABLED
    tier: str  # TIER_*
    threshold_tokens: int | None
    n: int  # sessions behind the threshold (0 when disabled)
    percentile: float  # fraction, e.g. 0.8; for TIER_SHIPPED the reference band's upper p75 bound
    window_days: int
    era: str  # the live session's era ("" when unknown)
    task_type: str
    reason: str  # one line: where the threshold comes from, or why the alarm is disabled

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_model(model: str | None) -> str:
    """Model id without a date or context-window suffix: ``claude-sonnet-5-5-20260901[1m]`` ->
    ``claude-sonnet-5-5``. Empty/synthetic ids map to ``""`` (era unknown)."""
    if not isinstance(model, str) or not model or model.startswith("<"):
        return ""
    name = _MODEL_CONTEXT_SUFFIX.sub("", model.strip())
    return _MODEL_DATE_SUFFIX.sub("", name)


def dominant_model(turns: Iterable[Mapping[str, Any]]) -> str:
    """The normalized model that produced most of the session's real tokens.

    Real tokens per turn = input - cache_read + output (the measure the bands use). Ties break
    to the lexicographically smaller id so the answer is deterministic. ``""`` when no turn names
    a model.
    """
    weight: dict[str, int] = {}
    for t in turns:
        model = normalize_model(t.get("model"))
        if not model:
            continue
        real = (
            int(t.get("token_count_input", 0) or 0)
            - int(t.get("cache_read", 0) or 0)
            + int(t.get("token_count_output", 0) or 0)
        )
        weight[model] = weight.get(model, 0) + max(real, 0)
    if not weight:
        return ""
    return min(weight, key=lambda m: (-weight[m], m))


def percentile_at_index(sorted_values: Sequence[int], fraction: float) -> int:
    """Element at floor(n * fraction) of an ascending list (same rule as the baseline builder)."""
    if not sorted_values:
        raise ValueError("percentile of an empty list")
    idx = max(0, min(int(len(sorted_values) * fraction), len(sorted_values) - 1))
    return sorted_values[idx]


def load_pool_rows(db_path: Path | str) -> list[PoolRow]:
    """Current-adapter sessions with tokens from the store, read-only.

    LEGACY rows (tracegauge.legacy: adapter_version or cost_version NULL/older) are excluded: pre-dedupe
    real_tokens are inflated ~2.4x. A store that predates the version columns
    contributes nothing (fails closed); one that predates ``dominant_model`` contributes rows of
    unknown era (they can still serve the era-agnostic tiers). A missing store is an empty pool.
    """
    from tracegauge.legacy import current_clause  # noqa: PLC0415 -- keeps this module import-light

    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        era_col = "dominant_model" if "dominant_model" in cols else "NULL"
        current_sql, current_params = current_clause(conn)  # "0" (no rows) if columns missing
        rows = conn.execute(
            f"SELECT session_id, task_type, {era_col}, real_tokens, source_mtime "  # noqa: S608
            f"FROM sessions WHERE real_tokens > 0 AND {current_sql}",
            current_params,
        ).fetchall()
    finally:
        conn.close()
    return [
        PoolRow(str(r[0]), str(r[1]), normalize_model(r[2]), int(r[3]), float(r[4])) for r in rows
    ]


def _shipped_threshold(shipped_types: Mapping[str, Any] | None, task_type: str) -> int | None:
    cell = (shipped_types or {}).get(task_type) or {}
    if not cell.get("available"):
        return None
    ci = (cell.get("ci95") or {}).get("p75")
    bound = ci[1] if isinstance(ci, list) and len(ci) == 2 else cell.get("p75")
    return int(bound) if bound else None


def resolve_threshold(
    rows: Sequence[PoolRow],
    *,
    task_type: str,
    era: str,
    now: float,
    percentile: float = DEFAULT_PERCENTILE,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_n: int = DEFAULT_MIN_N,
    exclude_session_id: str | None = None,
    shipped_types: Mapping[str, Any] | None = None,
) -> AlarmThreshold:
    """Walk the fallback chain and return the first tier with at least ``min_n`` sessions.

    Only sessions that ended within ``window_days`` before ``now`` count, so a retrospective
    caller passes the scored session's own start/end time as ``now`` and sees what was known then.
    An unknown ``era`` ("") skips the era tiers.
    """
    cutoff = now - window_days * _SECONDS_PER_DAY
    recent = [
        r
        for r in rows
        if cutoff <= r.mtime <= now and r.real_tokens > 0 and r.session_id != exclude_session_id
    ]
    same_type = [r for r in recent if r.task_type == task_type]
    tiers: list[tuple[str, list[PoolRow], str]] = []
    if era:
        same_era = [r for r in recent if r.era == era]
        era_type = [r for r in same_era if r.task_type == task_type]
        tiers.append(
            (TIER_RECENT_ERA_TYPE, era_type, f"your last {window_days} days of {era} {task_type}")
        )
        tiers.append((TIER_RECENT_ERA, same_era, f"your last {window_days} days of {era} sessions"))
    tiers.append(
        (TIER_RECENT_TYPE, same_type, f"your last {window_days} days of {task_type} sessions")
    )
    tiers.append((TIER_RECENT, recent, f"your last {window_days} days of sessions"))

    for tier, pool, label in tiers:
        if len(pool) >= min_n:
            value = percentile_at_index(sorted(r.real_tokens for r in pool), percentile)
            return AlarmThreshold(
                status=STATUS_ACTIVE,
                tier=tier,
                threshold_tokens=value,
                n=len(pool),
                percentile=percentile,
                window_days=window_days,
                era=era,
                task_type=task_type,
                reason=f"p{round(percentile * 100)} of {label} (n={len(pool)}): {value:,} tokens",
            )

    shipped = _shipped_threshold(shipped_types, task_type)
    if shipped is not None:
        return AlarmThreshold(
            status=STATUS_ACTIVE,
            tier=TIER_SHIPPED,
            threshold_tokens=shipped,
            n=int((shipped_types or {}).get(task_type, {}).get("n", 0)),
            reason=(
                f"fewer than {min_n} recent sessions of yours (have {len(recent)}); using the "
                f"bundled {task_type} reference band, upper 95% bound of its p75: {shipped:,} "
                "tokens (not your own history)"
            ),
            percentile=0.75,
            window_days=window_days,
            era=era,
            task_type=task_type,
        )
    return AlarmThreshold(
        status=STATUS_DISABLED,
        tier=TIER_DISABLED,
        threshold_tokens=None,
        n=0,
        reason=(
            f"alarm disabled: needs at least {min_n} of your sessions from the last {window_days} "
            f"days (have {len(recent)}, {len(same_type)} of type {task_type}) and the bundled "
            f"reference has no usable {task_type} band"
        ),
        percentile=percentile,
        window_days=window_days,
        era=era,
        task_type=task_type,
    )


def compute_alarm_threshold(
    db_path: Path | str,
    *,
    task_type: str,
    era: str,
    session_id: str | None,
    baselines: Mapping[str, Any] | None = None,
    percentile: float = DEFAULT_PERCENTILE,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_n: int = DEFAULT_MIN_N,
    now: float | None = None,
) -> AlarmThreshold:
    """Load the store's pool and resolve the threshold for one live session (read-only)."""
    return resolve_threshold(
        load_pool_rows(db_path),
        task_type=task_type,
        era=normalize_model(era),
        now=time.time() if now is None else now,
        percentile=percentile,
        window_days=window_days,
        min_n=min_n,
        exclude_session_id=session_id,
        shipped_types=(baselines or {}).get("types"),
    )


__all__ = [
    "DEFAULT_MIN_N",
    "DEFAULT_PERCENTILE",
    "DEFAULT_WINDOW_DAYS",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "TIER_DISABLED",
    "TIER_RECENT",
    "TIER_RECENT_ERA",
    "TIER_RECENT_ERA_TYPE",
    "TIER_RECENT_TYPE",
    "TIER_SHIPPED",
    "AlarmThreshold",
    "PoolRow",
    "compute_alarm_threshold",
    "dominant_model",
    "load_pool_rows",
    "normalize_model",
    "percentile_at_index",
    "resolve_threshold",
]
