# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/self_baseline.py — Per-user, per-type token history from the SQLite store.

A session's token verdict is its place among ALL of the user's own in-scope sessions of the same task
type (zero-usage stubs and rows from the pre-dedupe accounting excluded). It is active once the user
has at least ``MIN_OWN_SESSIONS`` other sessions of that type; below that the reading is
``building (n/10)``. Nothing is borrowed from any bundled table or any other population.

Public API:
    TypeBaseline       — dataclass carrying one task-type's state (counts, quartiles, the values)
    SelfBaselineState  — top-level result keyed by task_type
    compute_self_baselines(db_path, b2_baselines, ...)  -> SelfBaselineState
    load_or_compute(db_path, b2_baselines, ...)         -> SelfBaselineState
    building_state(b2_baselines)                        -> SelfBaselineState
    own_percentile(values, x)                           -> int
"""

import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path

# Never let the computed scope floor fall below this; a handful of very-short
# sessions would otherwise push the floor so low it stops gatekeeping anything.
MIN_MEANINGFUL_TURNS: int = 20

# A task type gets a token verdict only from the user's own sessions: at least this many of their
# own in-scope sessions of the type (shown as "building (n/10)" until then).
MIN_OWN_SESSIONS: int = 10

CACHE_VERSION: int = 3  # bump when the cached layout or the rule changes


@dataclass
class TypeBaseline:
    task_type: str
    source: str  # 'self' | 'building'
    p25: int | None  # quartiles of ALL own in-scope sessions; None when building
    median: int | None
    p75: int | None
    own_n: int  # the user's in-scope sessions of this type with usage (stubs excluded)
    sessions_needed: int  # how many more are needed (0 when active)
    scope_floor: int  # effective turns floor for this type
    domain_of_validity: str
    # Rows of this type excluded because the pre-dedupe adapter produced their real_tokens.
    stale_n: int = 0
    # (session_id, real_tokens) of those sessions, ascending by tokens; used for the percentile.
    values: list[tuple[str, int]] = field(default_factory=list)
    # True only for the quickstart's invented history (tracegauge.quickstart_history); the
    # text then says "synthetic-history" instead of "your". Never set for a real store.
    synthetic: bool = False


@dataclass
class SelfBaselineState:
    by_type: dict[str, TypeBaseline] = field(default_factory=dict)
    total_sessions: int = 0


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _current_adapter_clause(conn: sqlite3.Connection) -> tuple[str, tuple[int, ...]]:
    """SQL fragment (+ params) keeping only rows from the current accounting (not LEGACY, see
    tracegauge.legacy). Rows from the pre-dedupe adapter (adapter_version NULL or older) over-count usage
    ~2.4x, and rows priced before 1-hour cache writes were split out (cost_version NULL or older)
    are under-priced, so mixing either into a self-baseline would silently skew the user's own
    band. A DB that predates the columns has no current rows at all (fails closed)."""
    from tracegauge.legacy import current_clause

    sql, params = current_clause(conn)
    return f" AND {sql}", params


def _percentile(sorted_values: list[int], pct: float) -> int:
    """Return the value at the given percentile from a sorted list (nearest-rank, floor)."""
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty list")
    idx = max(0, int(len(sorted_values) * pct) - 1)
    return sorted_values[idx]


def quartiles(sorted_values: list[int]) -> tuple[int, int, int]:
    """Return (p25, median, p75) from a sorted list (nearest-rank / index-floor)."""
    n = len(sorted_values)
    p25 = sorted_values[max(0, int(n * 0.25) - 1)] if n >= 4 else sorted_values[0]
    median = statistics.median(sorted_values)  # type: ignore[arg-type]
    p75 = sorted_values[min(int(n * 0.75), n - 1)]
    return int(p25), int(median), int(p75)


def own_percentile(values: list[int], x: int) -> int:
    """Mid-rank percentile (0 to 100) of ``x`` among ``values``: below counts 1, equal counts 1/2."""
    if not values:
        raise ValueError("Cannot compute a percentile among no values")
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return round(100 * (below + 0.5 * equal) / len(values))


def _build_domain_of_validity(source: str, task_type: str, own_n: int, scope_floor: int) -> str:
    if source == "self":
        return (
            f"Relative to YOUR OWN {task_type} sessions ({own_n} sessions with usage and at least "
            f"{scope_floor} turns; waste events are not excluded). Not an absolute efficiency "
            "verdict: it says where this session sits in your own history, not whether those "
            f"sessions were efficient. Scope floor: {scope_floor} turns."
        )
    # source == 'building': no verdict is borrowed from any other population.
    return (
        f"Building your {task_type} history ({min(own_n, MIN_OWN_SESSIONS)}/{MIN_OWN_SESSIONS} of "
        "your own sessions of this type). No token verdict until then. "
        f"Scope floor: {scope_floor} turns."
    )


def compute_baseline_cost_band(
    conn: sqlite3.Connection,
    task_type: str,
    scope_floor: int,
    outlier_cap_pct: float = 0.90,
    lean_fraction: float = 0.50,
    min_lean_n: int = 8,
) -> tuple[float, float, float] | None:
    """Return (p25_usd, median_usd, p75_usd) for the lean token-subset sessions that also have cost data.

    The cost framing keeps its own conservative reference (waste-free sessions, p90 outlier cap,
    lower half by tokens); it is not the token verdict. Returns None if fewer than min_lean_n
    sessions have both session_cost_usd populated and real_tokens > 0.
    """
    cur_sql, cur_params = _current_adapter_clause(conn)
    rows = conn.execute(
        "SELECT real_tokens, session_cost_usd FROM sessions "
        "WHERE task_type = ? AND waste_event_count = 0 AND real_tokens > 0 "
        "  AND session_cost_usd IS NOT NULL "
        # A session with unpriced models has a partial (possibly $0) cost; it must not
        # pull the "typical efficient run" cost band down.
        "  AND (cost_unpriced_models IS NULL OR cost_unpriced_models = '') "
        "  AND ("
        "    (turn_count IS NOT NULL AND turn_count >= ?)"
        "    OR (turn_count IS NULL AND scope_status = 'in_scope')"
        "  )" + cur_sql,
        (task_type, scope_floor, *cur_params),
    ).fetchall()

    if not rows:
        return None

    pairs = sorted((int(r[0]), float(r[1])) for r in rows)
    tokens_sorted = [p[0] for p in pairs]

    cap_value = _percentile(tokens_sorted, outlier_cap_pct)
    pairs_capped = [(t, c) for t, c in pairs if t <= cap_value]

    if not pairs_capped:
        return None

    tokens_capped = [p[0] for p in pairs_capped]
    lean_cutoff = statistics.median(tokens_capped)
    lean_costs = [c for t, c in pairs_capped if t <= lean_cutoff]

    if len(lean_costs) < min_lean_n:
        return None

    lean_costs_sorted = sorted(lean_costs)
    n = len(lean_costs_sorted)
    p25_c = lean_costs_sorted[max(0, int(n * 0.25) - 1)]
    median_c = statistics.median(lean_costs_sorted)
    p75_c = lean_costs_sorted[min(int(n * 0.75), n - 1)]

    return float(p25_c), float(median_c), float(p75_c)


def _compute_scope_floor(
    conn: sqlite3.Connection,
    task_type: str,
    b2_floor: int,
    min_meaningful_turns: int,
) -> int:
    """Derive the effective scope floor for one task type.

    Uses the p10 of recorded turn_counts for the type, clamped to
    [min_meaningful_turns, b2_floor].  If fewer than 10 sessions have
    turn_count recorded we fall back to b2_floor directly (insufficient
    data to derive a reliable user-specific p10).
    """
    rows = conn.execute(
        "SELECT turn_count FROM sessions "
        "WHERE task_type = ? AND turn_count > 0 AND real_tokens > 0",
        (task_type,),
    ).fetchall()
    counts = sorted(int(r[0]) for r in rows)

    if len(counts) < 10:
        # Not enough data to derive a reliable user p10; use corpus floor.
        return max(b2_floor, min_meaningful_turns)

    user_p10 = _percentile(counts, 0.10)
    # Never raise the floor above b2_floor — the corpus floor is an upper bound.
    effective = min(user_p10, b2_floor)
    return max(effective, min_meaningful_turns)


def compute_self_baselines(
    db_path: Path | str,
    b2_baselines: dict,
    *,
    min_meaningful_turns: int = MIN_MEANINGFUL_TURNS,
    min_sessions: int = MIN_OWN_SESSIONS,
) -> SelfBaselineState:
    """Compute the per-user, per-type token history from the SQLite store.

    Parameters
    ----------
    db_path:
        Path to the TES SQLite database.
    b2_baselines:
        Loaded cc_baselines.json dict (from load_baselines()); only its task types and turn floors
        are used, never a band.
    min_meaningful_turns:
        Floor on the effective scope floor; prevents near-zero floors on sparse data.
    min_sessions:
        Number of the user's own in-scope sessions of the type from which it is active.

    Returns
    -------
    SelfBaselineState
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total_row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    total_sessions: int = int(total_row[0]) if total_row else 0

    state = SelfBaselineState(total_sessions=total_sessions)
    cur_sql, cur_params = _current_adapter_clause(conn)

    scope_gates: dict = b2_baselines.get("scope_gates", {})

    for task_type in b2_baselines.get("scope_gates", {}):
        # An inactive corpus cell (too few sessions to band) ships p10_turns=None, which
        # .get(key, default) would return as-is; treat it as "no corpus floor".
        b2_floor: int = scope_gates.get(task_type, {}).get("p10_turns") or MIN_MEANINGFUL_TURNS
        scope_floor = _compute_scope_floor(conn, task_type, b2_floor, min_meaningful_turns)

        # ALL of the user's in-scope sessions of the type, waste or not. A zero-usage stub
        # (real_tokens = 0) is not a session to compare with. For rows with a recorded turn count
        # require turn_count >= scope_floor; for legacy rows with NULL turn_count use scope_status.
        rows = conn.execute(
            "SELECT session_id, real_tokens FROM sessions "
            "WHERE task_type = ? AND real_tokens > 0 "
            "  AND ("
            "    (turn_count IS NOT NULL AND turn_count >= ?)"
            "    OR (turn_count IS NULL AND scope_status = 'in_scope')"
            "  )" + cur_sql,
            (task_type, scope_floor, *cur_params),
        ).fetchall()
        total_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE task_type = ? AND real_tokens > 0",
                (task_type,),
            ).fetchone()[0]
        )
        cur_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE task_type = ? AND real_tokens > 0" + cur_sql,
                (task_type, *cur_params),
            ).fetchone()[0]
        )
        stale_n = total_n - cur_n
        values = sorted(((str(r[0]), int(r[1])) for r in rows), key=lambda v: (v[1], v[0]))
        own_n = len(values)

        if own_n < min_sessions:
            state.by_type[task_type] = TypeBaseline(
                task_type=task_type,
                source="building",
                p25=None,
                median=None,
                p75=None,
                own_n=own_n,
                sessions_needed=min_sessions - own_n,
                scope_floor=scope_floor,
                domain_of_validity=_build_domain_of_validity(
                    "building", task_type, own_n, scope_floor
                ),
                stale_n=stale_n,
                values=values,
            )
            continue

        p25, median_val, p75 = quartiles([v for _, v in values])
        state.by_type[task_type] = TypeBaseline(
            task_type=task_type,
            source="self",
            p25=p25,
            median=median_val,
            p75=p75,
            own_n=own_n,
            sessions_needed=0,
            scope_floor=scope_floor,
            domain_of_validity=_build_domain_of_validity("self", task_type, own_n, scope_floor),
            stale_n=stale_n,
            values=values,
        )

    # Say loudly why a type's own history is thin after the usage-dedupe upgrade.
    for tb in state.by_type.values():
        if tb.stale_n:
            tb.domain_of_validity += (
                f" {tb.stale_n} stored {tb.task_type} session(s) were scored by the pre-dedupe "
                "adapter (token counts inflated ~2.4x) and are excluded; run `tracegauge backfill-waste` "
                "to re-score them from their source transcripts."
            )

    conn.close()
    return state


def _fingerprint(conn: sqlite3.Connection, b2_baselines: dict) -> dict[str, int]:
    """Return per-type counts, turn-count counts and token sums; used to detect when to recompute.

    ``<type>:turns`` counts the rows that have a recorded turn count (filling them in with
    ``tracegauge rescore`` makes rows eligible without adding one); ``<type>:tokens`` is the sum of
    their tokens (a rescore can change a row's tokens without changing any count). ``_v`` is the
    cache layout, so a cache from another rule never matches.
    """
    result: dict[str, int] = {"_v": CACHE_VERSION}
    # Counting only current-adapter rows also invalidates a cache built before the upgrade.
    cur_sql, cur_params = _current_adapter_clause(conn)
    for task_type in b2_baselines.get("scope_gates", {}):
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(real_tokens), 0), "
            "COALESCE(SUM(turn_count > 0), 0), COALESCE(SUM(turn_count), 0) FROM sessions "
            "WHERE task_type = ? AND real_tokens > 0" + cur_sql,
            (task_type, *cur_params),
        ).fetchone()
        result[task_type] = int(row[0])
        result[f"{task_type}:tokens"] = int(row[1])
        result[f"{task_type}:turns"] = int(row[2])
        result[f"{task_type}:turnsum"] = int(row[3])
    return result


def _state_to_json(state: SelfBaselineState) -> dict:
    """Serialise SelfBaselineState to a plain dict for JSON caching."""
    return {
        "total_sessions": state.total_sessions,
        "by_type": {
            k: {
                "task_type": v.task_type,
                "source": v.source,
                "p25": v.p25,
                "median": v.median,
                "p75": v.p75,
                "own_n": v.own_n,
                "sessions_needed": v.sessions_needed,
                "scope_floor": v.scope_floor,
                "domain_of_validity": v.domain_of_validity,
                "stale_n": v.stale_n,
                "values": [[sid, tok] for sid, tok in v.values],
            }
            for k, v in state.by_type.items()
        },
    }


def _state_from_json(data: dict) -> SelfBaselineState:
    """Deserialise a SelfBaselineState from a cached JSON dict."""
    by_type: dict[str, TypeBaseline] = {}
    for k, v in data.get("by_type", {}).items():
        by_type[k] = TypeBaseline(
            task_type=v["task_type"],
            source=v["source"],
            p25=v["p25"],
            median=v["median"],
            p75=v["p75"],
            own_n=v["own_n"],
            sessions_needed=v["sessions_needed"],
            scope_floor=v["scope_floor"],
            domain_of_validity=v["domain_of_validity"],
            stale_n=v.get("stale_n", 0),
            values=[(str(sid), int(tok)) for sid, tok in v.get("values", [])],
        )
    return SelfBaselineState(
        by_type=by_type,
        total_sessions=data.get("total_sessions", 0),
    )


def load_or_compute(
    db_path: Path | str,
    b2_baselines: dict,
    *,
    cache_path: Path | str | None = None,
    force_recompute: bool = False,
    **kwargs,
) -> SelfBaselineState:
    """Return SelfBaselineState, using a JSON cache when the per-type fingerprint is unchanged.

    Cache invalidation key: per-type row counts, turn-count counts and token sums (see
    ``_fingerprint``). If they match the stored fingerprint the cached result is returned;
    otherwise recomputed and cached. Pass force_recompute=True to bypass the cache unconditionally.

    Extra keyword arguments are forwarded to compute_self_baselines().
    """
    db_path = Path(db_path)
    if cache_path is None:
        cache_path = db_path.parent / "self_baseline_cache.json"
    cache_path = Path(cache_path)

    if not force_recompute and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            stored_fp: dict[str, int] = cached.get("_fingerprint", {})

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            current_fp = _fingerprint(conn, b2_baselines)
            conn.close()

            if stored_fp == current_fp:
                return _state_from_json(cached)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            pass  # Corrupt or unreadable cache — recompute.

    state = compute_self_baselines(db_path, b2_baselines, **kwargs)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    fp = _fingerprint(conn, b2_baselines)
    conn.close()

    payload = _state_to_json(state)
    payload["_fingerprint"] = fp
    try:
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass  # Cache write failure is non-fatal.

    return state


def building_state(b2_baselines: dict) -> SelfBaselineState:
    """The state of a user with no sessions of their own: every task type is ``building`` (0/10).

    Used where no store is available (for example ``tracegauge score FILE`` with no database), so
    the token verdict is ``unavailable`` rather than borrowed from the bundled tables. The scope
    floor is the bundled p10 turn floor of the type, as for any type with fewer than 10 sessions.
    """
    state = SelfBaselineState()
    for task_type, gate in b2_baselines.get("scope_gates", {}).items():
        floor = max((gate or {}).get("p10_turns") or MIN_MEANINGFUL_TURNS, MIN_MEANINGFUL_TURNS)
        state.by_type[task_type] = TypeBaseline(
            task_type=task_type,
            source="building",
            p25=None,
            median=None,
            p75=None,
            own_n=0,
            sessions_needed=MIN_OWN_SESSIONS,
            scope_floor=floor,
            domain_of_validity=_build_domain_of_validity("building", task_type, 0, floor),
        )
    return state


__all__ = [
    "MIN_MEANINGFUL_TURNS",
    "MIN_OWN_SESSIONS",
    "TypeBaseline",
    "SelfBaselineState",
    "building_state",
    "compute_baseline_cost_band",
    "compute_self_baselines",
    "load_or_compute",
    "ordinal",
    "own_percentile",
    "quartiles",
]
