# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/cost_period.py — Period-scoped spend report: total, session count, per-project breakdown.

`tracegauge cost --week` / `--month` / `--since <date>`.
A REPORT of real spend already incurred in a period -- distinct from
`tracegauge budget`'s rolling self-trend PROJECTION (budget.py answers "where is my
pace heading"; this module answers "what did I actually spend").

**source_mtime vs scored_at -- deliberate, and NOT the same choice
budget.py made:**

This module filters on `source_mtime` -- the session FILE's own last-write
time on disk, i.e. when the real Claude Code usage happened -- not
`scored_at` (when the `tracegauge score`/`tracegauge scan` command that computed the cost
happened to run). These are the same instant only under an immediate-scoring
workflow (score right after each session ends). Under a batch-scoring
workflow (run `tracegauge scan` once over a week's worth of sessions), every one of
those sessions gets the SAME `scored_at` timestamp -- a `scored_at`-based
period report would cluster a week of real spend onto one instant, or drop
it outside the requested window entirely, silently misattributing spend
across period boundaries. `source_mtime` reflects when the money was
actually spent, independent of when the user got around to running the
scorer.

**Historical note, now resolved (issue #12):** `tracegauge/budget.py`'s rolling-
window projection used to filter on `scored_at`, not `source_mtime`, for
exactly the reason this module avoids it -- flagged here when this module
was written (scoped to the new `tracegauge cost` command only, not fixed
alongside an unrelated feature) and fixed as its own dedicated follow-up.
`compute_budget_projection` now filters/orders on `source_mtime`, matching
this module's own convention.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracegauge.legacy import current_clause, legacy_clause

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10

DEFAULT_WEEK_DAYS: int = 7
DEFAULT_MONTH_DAYS: int = 30


@dataclass
class ProjectCostBreakdown:
    project_label: str
    total_usd: float
    session_count: int
    # Models whose turns are missing from total_usd for sessions in this project.
    unpriced_models: list[str] = field(default_factory=list)


@dataclass
class SubagentTypePeriod:
    """One subagent type aggregated over the recorded sessions of a period."""

    type: str
    files: int = 0
    requests: int = 0
    tokens: int = 0  # billed tokens
    usd: float | None = None  # None until some session priced this type
    usd_is_floor: bool = False
    share_of_total_usd: float | None = None  # of the recorded sessions' spend


@dataclass
class PeriodSubagents:
    """Subagent share of a period's spend, over sessions that recorded a per-type split.

    ``sessions_not_recorded`` rows (scored before 0.17.0, subagent_by_type_json NULL) are in
    NEITHER the numerators nor the denominators here: ``sessions_recorded`` says what the
    figures cover. Re-scoring recovers the rest.
    """

    sessions_recorded: int = 0
    sessions_not_recorded: int = 0
    usd: float = 0.0  # subagent spend (priced part) of the recorded sessions
    usd_is_floor: bool = False
    recorded_total_usd: float = 0.0  # their total session spend (subagents included)
    share_of_total_usd: float | None = None
    # Real-token units (input - cache_read + output), the unit of the stored subagent_tokens.
    subagent_real_tokens: int = 0
    recorded_real_tokens: int = 0  # main-chain real_tokens + subagent real tokens
    share_of_total_tokens: float | None = None
    by_type: list[SubagentTypePeriod] = field(default_factory=list)


@dataclass
class PeriodCostReport:
    period_label: str
    period_start: datetime
    period_end: datetime
    total_usd: float
    session_count: int
    sessions_missing_cost: int
    by_project: list[ProjectCostBreakdown]
    # Unpriced coverage -- what fraction of this period's sessions
    # and tokens are priced, and which models caused the gap. token_total
    # includes ALL sessions in the window regardless of pricing (a
    # denominator that only counted priced sessions would make 100%
    # coverage trivially true by construction). unpriced_models is
    # aggregated only from rows that persisted it at score time -- legacy rows scored before that column existed
    # contribute to the coverage gap but can't name their own model;
    # unpriced_models_incomplete flags that distinction.
    token_total: int = 0
    token_priced: int = 0
    unpriced_models: list[str] = field(default_factory=list)
    unpriced_models_incomplete: bool = False
    # Sessions that are NOT fully priced: an unpriced model among their turns, or no cost stored.
    # token_priced counts only fully priced sessions, so a session with even one unpriced turn
    # moves ALL its tokens to the unpriced side (an upper bound on the unpriced tokens; the
    # store keeps no per-model token split).
    sessions_unpriced: int = 0
    # LEGACY rows (tracegauge.legacy) in the period are NOT in any figure above: their dollars come from
    # the pre-0.15 accounting (usage counted ~2x). They are reported on their own line, never
    # summed into total_usd. legacy_total_usd sums only those with a stored cost.
    legacy_rows_excluded: int = 0
    legacy_total_usd: float = 0.0
    subagents: PeriodSubagents = field(default_factory=PeriodSubagents)

    @property
    def priced(self) -> bool:
        """True iff no session in the window has turns excluded for lack of a price."""
        return not self.unpriced_models

    @property
    def tokens_unpriced(self) -> int:
        return self.token_total - self.token_priced

    @property
    def session_coverage_pct(self) -> float | None:
        total = self.session_count + self.sessions_missing_cost
        return 100.0 * (total - self.sessions_unpriced) / total if total else None

    @property
    def token_coverage_pct(self) -> float | None:
        return 100.0 * self.token_priced / self.token_total if self.token_total else None


def _project_label_from_source_path(source_path: str) -> str:
    """Same derivation as cli.py's `_project_label` (parent directory name,
    last 40 chars) -- reimplemented here rather than imported, to keep this
    module free of a dependency on the CLI layer. Both must agree; enforced
    by tests/test_cost_period.py's cross-check against cli._project_label.
    """
    name = Path(source_path).parent.name
    return name[-40:] if len(name) > 40 else name


def _period_subagents(rows: list[sqlite3.Row]) -> PeriodSubagents:
    """Aggregate the per-type subagent split over the period's rows (see PeriodSubagents)."""
    out = PeriodSubagents()
    types: dict[str, SubagentTypePeriod] = {}
    for r in rows:
        if r["session_cost_usd"] is None:
            continue  # no cost at all: already reported as sessions_missing_cost
        raw = r["subagent_by_type_json"]
        if raw is None:
            out.sessions_not_recorded += 1
            continue
        try:
            entries = [
                (
                    str(e["type"]),
                    int(e["files"]),
                    int(e["requests"]),
                    int(e["tokens"]),
                    None if e["usd"] is None else float(e["usd"]),
                    bool(e["usd_is_floor"]),
                )
                for e in json.loads(raw)
            ]
        except (ValueError, TypeError, KeyError):
            out.sessions_not_recorded += 1  # unreadable counts as not recorded, never as zero
            continue
        out.sessions_recorded += 1
        out.recorded_total_usd += float(r["session_cost_usd"])
        out.usd += float(r["subagent_cost_usd"] or 0.0)
        out.subagent_real_tokens += int(r["subagent_tokens"] or 0)
        out.recorded_real_tokens += int(r["real_tokens"] or 0) + int(r["subagent_tokens"] or 0)
        for e_type, e_files, e_requests, e_tokens, e_usd, e_floor in entries:
            t = types.setdefault(e_type, SubagentTypePeriod(type=e_type))
            t.files += e_files
            t.requests += e_requests
            t.tokens += e_tokens
            if e_usd is not None:
                t.usd = (t.usd or 0.0) + e_usd
            if e_floor:
                t.usd_is_floor = True
                out.usd_is_floor = True
    for t in types.values():
        if out.recorded_total_usd > 0 and t.usd is not None:
            t.share_of_total_usd = t.usd / out.recorded_total_usd
    out.by_type = sorted(types.values(), key=lambda t: (-(t.usd or 0.0), t.type))
    if out.recorded_total_usd > 0:
        out.share_of_total_usd = out.usd / out.recorded_total_usd
    if out.recorded_real_tokens > 0:
        out.share_of_total_tokens = out.subagent_real_tokens / out.recorded_real_tokens
    return out


def compute_period_cost(
    conn: sqlite3.Connection,
    period_start: datetime,
    period_end: datetime,
    period_label: str = "",
) -> PeriodCostReport:
    """Total spend, session count, and per-project breakdown for
    ``[period_start, period_end)`` by ``source_mtime`` -- see module
    docstring for why ``source_mtime``, not ``scored_at``.

    Boundary is INCLUSIVE of ``period_start``, EXCLUSIVE of ``period_end``
    (cross-boundary case) -- a session sitting exactly on a boundary
    instant belongs to exactly one of two adjacent periods run back-to-back,
    never both, never neither.

    Sessions with ``session_cost_usd IS NULL`` (never scored for cost, or
    scored before the cost axis existed in this DB) are EXCLUDED from
    ``total_usd``/``by_project`` but counted separately in
    ``sessions_missing_cost`` -- silently omitting them from the total
    without reporting the omission would make an incomplete total look
    complete (a data fetch must fail closed, never
    silently drop coverage).

    An EMPTY period (zero sessions of any kind) still returns a
    valid, ordinary report with ``total_usd=0.0``, ``session_count=0`` --
    never ``None`` and never an exception; "checked, found nothing" always
    looks different from a raised error, and this function never raises on
    a normal (even empty) query.

    A SINGLE matching session is handled with no special case at
    all -- ``by_project`` just has one entry with ``session_count=1``.

    LEGACY rows (tracegauge.legacy) are excluded from every figure above and counted in
    ``legacy_rows_excluded`` / ``legacy_total_usd`` so the caller can show them on a clearly
    separate line; ``total_usd`` (and so the ROI built on it) is the corrected spend only.
    """
    current_sql, current_params = current_clause(conn)
    rows = conn.execute(
        "SELECT source_path, session_cost_usd, real_tokens, cost_unpriced_models, "
        "subagent_cost_usd, subagent_tokens, subagent_by_type_json "
        f"FROM sessions WHERE source_mtime >= ? AND source_mtime < ? AND {current_sql}",  # noqa: S608
        (period_start.timestamp(), period_end.timestamp(), *current_params),
    ).fetchall()
    legacy_sql, legacy_params = legacy_clause(conn)
    legacy_n, legacy_usd = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(session_cost_usd), 0.0) FROM sessions "
        f"WHERE source_mtime >= ? AND source_mtime < ? AND {legacy_sql}",  # noqa: S608
        (period_start.timestamp(), period_end.timestamp(), *legacy_params),
    ).fetchone()

    priced = [r for r in rows if r["session_cost_usd"] is not None]
    missing = len(rows) - len(priced)

    token_total = sum(r["real_tokens"] or 0 for r in rows)
    # A session with ANY unpriced model is not fully priced, even though its stored cost is a
    # non-NULL priced-turns subtotal (or 0.0): counting it as priced made coverage read 100%.
    fully_priced = [r for r in priced if not r["cost_unpriced_models"]]
    token_priced = sum(r["real_tokens"] or 0 for r in fully_priced)

    missing_rows = [r for r in rows if r["session_cost_usd"] is None]
    # A session whose model is missing from the price table is stored with
    # session_cost_usd = 0.0 (or a priced-turns-only subtotal), NOT NULL, so it used to look
    # fully priced. Name its models from EVERY row, not just the NULL-cost ones.
    named_models: set[str] = set()
    for r in rows:
        raw = r["cost_unpriced_models"]
        if raw:
            named_models.update(raw.split(","))
    # A missing-cost row with no cost_unpriced_models value is either a
    # legacy row (scored before that column existed) or a session where
    # cost was never computed at all (e.g. --no-judge scoring that also
    # skipped cost) -- either way, its model can't be named, and that
    # matters: silently showing a "complete" unpriced_models list when some
    # of the gap has no attributable model would misstate coverage.
    unpriced_models_incomplete = any(not r["cost_unpriced_models"] for r in missing_rows)

    totals_by_project: dict[str, list[float]] = {}
    unpriced_by_project: dict[str, set[str]] = {}
    for r in priced:
        label = _project_label_from_source_path(r["source_path"])
        totals_by_project.setdefault(label, []).append(float(r["session_cost_usd"]))
        if r["cost_unpriced_models"]:
            unpriced_by_project.setdefault(label, set()).update(
                r["cost_unpriced_models"].split(",")
            )

    by_project = sorted(
        (
            ProjectCostBreakdown(
                project_label=label,
                total_usd=sum(costs),
                session_count=len(costs),
                unpriced_models=sorted(unpriced_by_project.get(label, ())),
            )
            for label, costs in totals_by_project.items()
        ),
        key=lambda b: b.total_usd,
        reverse=True,
    )

    return PeriodCostReport(
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        total_usd=sum(c.total_usd for c in by_project),
        session_count=len(priced),
        sessions_missing_cost=missing,
        by_project=by_project,
        token_total=token_total,
        token_priced=token_priced,
        unpriced_models=sorted(named_models),
        unpriced_models_incomplete=unpriced_models_incomplete,
        sessions_unpriced=len(rows) - len(fully_priced),
        legacy_rows_excluded=int(legacy_n),
        legacy_total_usd=float(legacy_usd),
        subagents=_period_subagents(rows),
    )


def resolve_period(
    *,
    week: bool = False,
    month: bool = False,
    since: str | None = None,
    _now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Resolves ``--week``/``--month``/``--since`` into (period_start,
    period_end, period_label). Exactly one of week/month/since must be
    truthy -- the CLI's mutually-exclusive argparse group enforces this
    before calling here; this function itself raises ValueError if that
    invariant is somehow violated, rather than silently picking one.

    ``--week``/``--month`` are ROLLING N-day windows ending at ``_now``
    (default 7/30 days) -- matching ``budget.py``'s own
    ``DEFAULT_WINDOW_DAYS`` rolling-window precedent, deliberately NOT a
    calendar week/month. A calendar-aligned window needs a user timezone and
    a first-day-of-week convention this tool has no basis to guess, and
    would silently disagree with `tracegauge budget`'s own meaning of "window" for
    the same English words. ``--since <YYYY-MM-DD>`` is an explicit lower
    bound with no upper bound other than ``_now``.
    """
    now = _now if _now is not None else datetime.now(UTC)
    if since is not None:
        try:
            start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"--since must be YYYY-MM-DD, got {since!r}") from exc
        return start, now, f"since {since}"
    if month:
        return now - timedelta(days=DEFAULT_MONTH_DAYS), now, f"last {DEFAULT_MONTH_DAYS} days"
    if week:
        return now - timedelta(days=DEFAULT_WEEK_DAYS), now, f"last {DEFAULT_WEEK_DAYS} days"
    raise ValueError("resolve_period: exactly one of week/month/since must be given")


__all__ = [
    "DEFAULT_MONTH_DAYS",
    "DEFAULT_WEEK_DAYS",
    "PeriodCostReport",
    "PeriodSubagents",
    "ProjectCostBreakdown",
    "SubagentTypePeriod",
    "compute_period_cost",
    "resolve_period",
]
