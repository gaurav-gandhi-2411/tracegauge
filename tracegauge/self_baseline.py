# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/self_baseline.py — Per-user, per-type lean-subset self-baselines from the SQLite store.

Public API:
    TypeBaseline       — dataclass carrying one task-type's baseline state
    SelfBaselineState  — top-level result keyed by task_type
    compute_self_baselines(db_path, b2_baselines, ...)  -> SelfBaselineState
    load_or_compute(db_path, b2_baselines, ...)         -> SelfBaselineState
"""

import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from tracegauge.score import TOKEN_DOMAIN_OF_VALIDITY

# Never let the computed scope floor fall below this; a handful of very-short
# sessions would otherwise push the floor so low it stops gatekeeping anything.
MIN_MEANINGFUL_TURNS: int = 20


@dataclass
class TypeBaseline:
    task_type: str
    source: str  # 'self' | 'building' | 'corpus'
    p25: int | None  # None when building
    median: int | None  # None when building
    p75: int | None  # None when building
    lean_n: int  # size of lean subset (0 when building/corpus)
    waste_free_n: int  # waste-free sessions with real_tokens > 0
    sessions_needed: int  # how many more lean-subset sessions needed (0 when active)
    scope_floor: int  # effective turns floor for this type
    domain_of_validity: str
    # Rows of this type excluded because the pre-dedupe adapter produced their real_tokens.
    stale_n: int = 0


@dataclass
class SelfBaselineState:
    by_type: dict[str, TypeBaseline] = field(default_factory=dict)
    total_sessions: int = 0


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
    """Return the value at the given percentile from a sorted list (nearest-rank, floor).

    Uses idx = int(n * pct) - 1 so that the top (1 - pct) fraction of values
    is above the cap rather than at or above it.  For n=20, pct=0.90, this gives
    idx=17 which keeps the lower 18 values (the top 2 are treated as outliers).
    """
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty list")
    idx = max(0, int(len(sorted_values) * pct) - 1)
    return sorted_values[idx]


def _quartiles(sorted_values: list[int]) -> tuple[int, int, int]:
    """Return (p25, median, p75) from a sorted list.

    Uses the nearest-rank / index-floor method consistent with _percentile.
    """
    n = len(sorted_values)
    p25 = sorted_values[max(0, int(n * 0.25) - 1)] if n >= 4 else sorted_values[0]
    median = statistics.median(sorted_values)  # type: ignore[arg-type]
    p75_idx = min(int(n * 0.75), n - 1)
    p75 = sorted_values[p75_idx]
    return int(p25), int(median), int(p75)


def _build_domain_of_validity(
    source: str,
    task_type: str,
    lean_n: int,
    sessions_needed: int,
    min_lean_n: int,
    scope_floor: int,
) -> str:
    if source == "self":
        return (
            f"Calibrated to YOUR OWN leaner waste-free {task_type} sessions "
            f"(lean subset: {lean_n} sessions). "
            "Relative-to-your-own-baseline; not an absolute efficiency verdict. "
            f"Baseline: self / scope floor: {scope_floor} turns."
        )
    if source == "building":
        return (
            f"Building your baseline: need {sessions_needed} more waste-free {task_type} "
            f"sessions (have {lean_n} in lean subset, need {min_lean_n}). "
            f"Scope floor: {scope_floor} turns."
        )
    # source == 'corpus'
    return TOKEN_DOMAIN_OF_VALIDITY


def compute_baseline_cost_band(
    conn: sqlite3.Connection,
    task_type: str,
    scope_floor: int,
    outlier_cap_pct: float = 0.90,
    lean_fraction: float = 0.50,
    min_lean_n: int = 8,
) -> tuple[float, float, float] | None:
    """Return (p25_usd, median_usd, p75_usd) for the lean token-subset sessions that also have cost data.

    Uses the same lean-subset selection as compute_self_baselines (sorted by real_tokens,
    p90 outlier cap, lower half), but returns cost quartiles instead of token quartiles.
    Returns None if fewer than min_lean_n sessions have both session_cost_usd populated
    and real_tokens > 0.
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
    min_lean_n: int = 8,
    outlier_cap_pct: float = 0.90,
    lean_fraction: float = 0.50,
    min_meaningful_turns: int = MIN_MEANINGFUL_TURNS,
    corpus_fallback: bool = False,
) -> SelfBaselineState:
    """Compute per-user, per-type lean-subset self-baselines from the SQLite store.

    Parameters
    ----------
    db_path:
        Path to the TES SQLite database.
    b2_baselines:
        Loaded cc_baselines.json dict (from load_baselines()).
    min_lean_n:
        Minimum lean-subset size needed before switching from 'building' to 'self'.
    outlier_cap_pct:
        Percentile cap for outlier exclusion (default p90 → drop top 10%).
    lean_fraction:
        Lower fraction of the outlier-excluded set that defines the lean subset
        (default 0.50 → lower half).
    min_meaningful_turns:
        Floor on the effective scope floor; prevents near-zero floors on sparse data.
    corpus_fallback:
        When True and a type is still 'building', use corpus (B2) baselines instead.

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
    types_info: dict = b2_baselines.get("types", {})

    for task_type in b2_baselines.get("scope_gates", {}):
        # An inactive corpus cell (too few sessions to band) ships p10_turns=None, which
        # .get(key, default) would return as-is; treat it as "no corpus floor".
        b2_floor: int = scope_gates.get(task_type, {}).get("p10_turns") or MIN_MEANINGFUL_TURNS

        # --- Step 0: compute scope_floor first — needed for the lean-subset query ---
        scope_floor = _compute_scope_floor(conn, task_type, b2_floor, min_meaningful_turns)

        # --- Step 1: fetch waste-free sessions, excluding OOS sessions ---
        # For sessions with turn_count populated: require turn_count >= scope_floor.
        # For legacy rows where turn_count IS NULL: fall back to scope_status='in_scope'.
        rows = conn.execute(
            "SELECT real_tokens FROM sessions "
            "WHERE task_type = ? AND waste_event_count = 0 AND real_tokens > 0 "
            "  AND ("
            "    (turn_count IS NOT NULL AND turn_count >= ?)"
            "    OR (turn_count IS NULL AND scope_status = 'in_scope')"
            "  )" + cur_sql,
            (task_type, scope_floor, *cur_params),
        ).fetchall()
        total_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE task_type = ? AND waste_event_count = 0 "
                "AND real_tokens > 0",
                (task_type,),
            ).fetchone()[0]
        )
        cur_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE task_type = ? AND waste_event_count = 0 "
                "AND real_tokens > 0" + cur_sql,
                (task_type, *cur_params),
            ).fetchone()[0]
        )
        stale_n = total_n - cur_n
        tokens: list[int] = sorted(int(r[0]) for r in rows)
        waste_free_n = len(tokens)

        # --- Step 2: p90 outlier cap ---
        if tokens:
            cap_value = _percentile(tokens, outlier_cap_pct)
            tokens_capped = [t for t in tokens if t <= cap_value]
        else:
            tokens_capped = []

        # --- Step 3: lean subset = lower half of outlier-excluded set ---
        if tokens_capped:
            lean_cutoff = statistics.median(tokens_capped)
            lean_subset = [t for t in tokens_capped if t <= lean_cutoff]
        else:
            lean_subset = []

        lean_n = len(lean_subset)

        if lean_n < min_lean_n:
            sessions_needed = min_lean_n - lean_n

            if corpus_fallback and types_info.get(task_type, {}).get("available"):
                ti = types_info[task_type]
                source = "corpus"
                p25 = ti.get("p25")
                median = ti.get("median")
                p75 = ti.get("p75")
            else:
                source = "building"
                p25 = None
                median = None
                p75 = None

            dov = _build_domain_of_validity(
                source, task_type, lean_n, sessions_needed, min_lean_n, scope_floor
            )
            state.by_type[task_type] = TypeBaseline(
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
                stale_n=stale_n,
            )
        else:
            source = "self"
            sessions_needed = 0
            p25, median_val, p75 = _quartiles(lean_subset)

            # --- Stability guard ---
            # Reject bands where the lower tail is implausibly far from the median —
            # p25 < median/3 means the lean subset contains outlier-low sessions that
            # make "within_band" nearly vacuous.
            if median_val > 0 and p25 < median_val // 3:
                source = "building"
                sessions_needed = min_lean_n
                dov = _build_domain_of_validity(
                    source, task_type, lean_n, sessions_needed, min_lean_n, scope_floor
                )
                # Override DOV to mention instability specifically
                dov = (
                    f"Band too wide to be trustworthy for {task_type} "
                    f"(p25={p25:,} < median/3={median_val // 3:,}): "
                    f"need more sessions to tighten the reference band. "
                    f"Scope floor: {scope_floor} turns."
                )
                state.by_type[task_type] = TypeBaseline(
                    task_type=task_type,
                    source=source,
                    p25=None,
                    median=None,
                    p75=None,
                    lean_n=lean_n,
                    waste_free_n=waste_free_n,
                    sessions_needed=sessions_needed,
                    scope_floor=scope_floor,
                    domain_of_validity=dov,
                    stale_n=stale_n,
                )
                continue  # skip the normal 'self' path below

            dov = _build_domain_of_validity(
                source, task_type, lean_n, sessions_needed, min_lean_n, scope_floor
            )
            state.by_type[task_type] = TypeBaseline(
                task_type=task_type,
                source=source,
                p25=p25,
                median=median_val,
                p75=p75,
                lean_n=lean_n,
                waste_free_n=waste_free_n,
                sessions_needed=0,
                scope_floor=scope_floor,
                domain_of_validity=dov,
                stale_n=stale_n,
            )

    # Say loudly why a type's own band is thin/absent after the usage-dedupe upgrade.
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
    """Return per-type waste-free session counts; used to detect when to recompute.

    ``<type>:turns`` counts the same rows that have a recorded turn count: filling in turn counts
    on older rows (``tracegauge rescore``) makes them eligible for the baseline without adding a
    row, so the cache has to notice it. Caches written before this key existed never match, which
    recomputes them once.
    """
    result: dict[str, int] = {}
    # Counting only current-adapter rows also invalidates a cache built before the upgrade.
    cur_sql, cur_params = _current_adapter_clause(conn)
    for task_type in b2_baselines.get("scope_gates", {}):
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions "
            "WHERE task_type = ? AND waste_event_count = 0 AND real_tokens > 0" + cur_sql,
            (task_type, *cur_params),
        ).fetchone()
        result[task_type] = int(row[0]) if row else 0
        turns = conn.execute(
            "SELECT COUNT(*) FROM sessions "
            "WHERE task_type = ? AND waste_event_count = 0 AND real_tokens > 0 "
            "AND turn_count > 0" + cur_sql,
            (task_type, *cur_params),
        ).fetchone()
        result[f"{task_type}:turns"] = int(turns[0]) if turns else 0
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
                "lean_n": v.lean_n,
                "waste_free_n": v.waste_free_n,
                "sessions_needed": v.sessions_needed,
                "scope_floor": v.scope_floor,
                "domain_of_validity": v.domain_of_validity,
                "stale_n": v.stale_n,
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
            lean_n=v["lean_n"],
            waste_free_n=v["waste_free_n"],
            sessions_needed=v["sessions_needed"],
            scope_floor=v["scope_floor"],
            domain_of_validity=v["domain_of_validity"],
            stale_n=v.get("stale_n", 0),
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
    """Return SelfBaselineState, using a JSON cache when the waste-free counts are unchanged.

    Cache invalidation key: per-type waste-free session counts. If counts match
    the stored fingerprint the cached result is returned; otherwise recomputed and
    cached. Pass force_recompute=True to bypass the cache unconditionally.

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
        except (json.JSONDecodeError, KeyError, OSError):
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


__all__ = [
    "MIN_MEANINGFUL_TURNS",
    "TypeBaseline",
    "SelfBaselineState",
    "compute_baseline_cost_band",
    "compute_self_baselines",
    "load_or_compute",
]
