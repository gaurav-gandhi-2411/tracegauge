# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/plan.py — Plan-cost configuration and ROI computation.

"Is my subscription worth it at API-equivalent prices?" -- the ROI
side of `tracegauge cost --roi`. Two deliberate design choices, both driven by
the feature's honesty requirements:

**A plan HISTORY, not a single static plan.** A user's plan can change
mid-window (upgraded from Pro to Max, added a second seat) -- a single
static `monthly_cost_usd` would either misprice the whole window at the
wrong rate or require the user to re-run the report per sub-window by
hand. `load_plan_config` returns an ordered list of `PlanPeriod` entries,
each with its own `effective_from` date; `prorated_plan_cost` walks the
window day by day, always pricing each day at whichever plan was actually
in effect that day, and correctly handles a plan change landing inside the
window.

**A partial-month window is prorated by day, not by calendar month.**
Matches this project's own established rolling-window convention
(`tracegauge/cost_period.py`, `tracegauge/budget.py`): `--week`/`--month` are rolling
N-day windows, not calendar-aligned, so plan cost must scale the same way
-- `monthly_cost_usd / 30` per day, summed over the window's actual day
count, never a flat monthly figure regardless of window length.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from tracegauge._env import env_value

DAYS_PER_MONTH: float = 30.0  # same convention as DEFAULT_MONTH_DAYS elsewhere


@dataclass
class PlanPeriod:
    name: str
    monthly_cost_usd: float
    effective_from: date


@dataclass
class ROIResult:
    api_equivalent_usd: float
    plan_cost_usd: float
    plan_names: list[str]  # every plan active at any point in the window
    multiple: float


def resolve_plan_config_path(explicit_path: str | Path | None = None) -> Path:
    """explicit arg -> TRACEGAUGE_PLAN_PATH -> ~/.tes/plan.json.

    Mirrors tracegauge.store.resolve_db_path's own resolution order, and
    tracegauge.cost's TRACEGAUGE_PRICE_TABLE / ~/.tes/prices.json precedent -- JSON, not
    YAML, matching this project's only existing user-editable config
    convention rather than introducing a new file format and a new
    dependency for one small feature.
    """
    if explicit_path is not None:
        return Path(explicit_path)
    if env_val := env_value("PLAN_PATH"):
        return Path(env_val)
    return Path.home() / ".tes" / "plan.json"


def load_plan_config(path: str | Path | None = None) -> list[PlanPeriod]:
    """Load and validate the plan history. Returns [] if no config file
    exists -- never raises for "not configured," since that's the normal,
    expected state for a user who hasn't set up `--roi` yet. Raises
    ValueError for a config file that exists but is malformed -- a
    present-but-broken config should fail loud, not silently report no ROI.

    Sorted by effective_from, ascending.
    """
    resolved = resolve_plan_config_path(path)
    if not resolved.exists():
        return []

    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{resolved}: not valid JSON ({exc})") from exc

    if not raw or "plans" not in raw:
        raise ValueError(f"{resolved}: missing top-level 'plans' key")

    plans: list[PlanPeriod] = []
    for i, entry in enumerate(raw["plans"]):
        try:
            name = str(entry["name"])
            monthly_cost = float(entry["monthly_cost_usd"])
            effective_from = _parse_date(str(entry["effective_from"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"{resolved}: plans[{i}] is invalid ({exc})") from exc
        if monthly_cost < 0:
            raise ValueError(f"{resolved}: plans[{i}].monthly_cost_usd must be >= 0")
        plans.append(
            PlanPeriod(name=name, monthly_cost_usd=monthly_cost, effective_from=effective_from)
        )

    plans.sort(key=lambda p: p.effective_from)
    return plans


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"effective_from must be YYYY-MM-DD, got {s!r}") from exc


def prorated_plan_cost(
    plans: list[PlanPeriod],
    window_start: datetime,
    window_end: datetime,
) -> tuple[float, list[str]]:
    """Exact proration of plan cost across [window_start, window_end), by
    segment overlap -- NOT calendar-date counting. A `--week` window is
    exactly 7.0 elapsed days between two arbitrary instants (`now` has a
    real time-of-day component); counting whole calendar dates instead of
    elapsed time would inflate a 7-day window to 8 days' worth of cost
    whenever window_start and window_end don't both land on midnight,
    which is every real invocation. Each plan period is treated as a
    segment [effective_from, next_plan.effective_from or +inf); the
    overlap of each segment with the window is priced at
    monthly_cost_usd / 30 per elapsed day.

    Returns (total_cost_usd, plan_names_active_at_any_point_in_window). A
    span of the window before every plan's effective_from contributes $0,
    not an error -- correctly handles a window starting before the user's
    first plan.json entry.
    """
    if not plans:
        return 0.0, []

    tz = window_start.tzinfo
    ordered = sorted(plans, key=lambda p: p.effective_from)
    total = 0.0
    names: set[str] = set()

    for i, plan in enumerate(ordered):
        seg_start = datetime.combine(plan.effective_from, datetime.min.time(), tzinfo=tz)
        seg_end = (
            datetime.combine(ordered[i + 1].effective_from, datetime.min.time(), tzinfo=tz)
            if i + 1 < len(ordered)
            else None  # last plan is open-ended
        )
        overlap_start = max(seg_start, window_start)
        overlap_end = min(seg_end, window_end) if seg_end is not None else window_end
        if overlap_start >= overlap_end:
            continue
        elapsed_days = (overlap_end - overlap_start).total_seconds() / 86400.0
        total += plan.monthly_cost_usd / DAYS_PER_MONTH * elapsed_days
        names.add(plan.name)

    return total, sorted(names)


def compute_roi(
    api_equivalent_usd: float,
    priced_session_count: int,
    plans: list[PlanPeriod],
    window_start: datetime,
    window_end: datetime,
) -> ROIResult | None:
    """Refuses to compute a ratio the data can't support -- returns
    None, never a misleading number, when:
      - no plan is configured at all (nothing to compare against), or
      - the window has zero priced sessions (a 0/N ratio looks like a real
        signal but measures nothing).
    """
    if not plans:
        return None
    if priced_session_count == 0:
        return None

    plan_cost, names = prorated_plan_cost(plans, window_start, window_end)
    if plan_cost <= 0:
        # every day in the window predates the user's first plan.yaml entry
        return None

    return ROIResult(
        api_equivalent_usd=api_equivalent_usd,
        plan_cost_usd=plan_cost,
        plan_names=names,
        multiple=api_equivalent_usd / plan_cost,
    )


__all__ = [
    "DAYS_PER_MONTH",
    "PlanPeriod",
    "ROIResult",
    "resolve_plan_config_path",
    "load_plan_config",
    "prorated_plan_cost",
    "compute_roi",
]
