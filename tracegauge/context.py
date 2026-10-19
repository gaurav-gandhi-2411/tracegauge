# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Multi-developer context for a session: where its tokens per turn fall among other developers'.

This is CONTEXT, not a verdict. The token verdict comes from the user's own sessions only
(tracegauge.self_baseline). The context line says "this session is at about the Nth percentile of
M developers' sessions of the same task type", labelled with the population, the source, the dates
and the ODC-BY notice, and it is shown only for task types whose cell passed publication
(scripts/build_multidev_bands.py).
"""

import json
from functools import lru_cache
from typing import Any

from tracegauge.baselines import MULTIDEV_BASELINES_PATH
from tracegauge.self_baseline import ordinal as _ordinal

SCHEMA = 2  # the table layout this module reads (grid of percentiles of tokens per turn)
NOTICE = "Contains information from SALT-NLP/SWE-chat, made available under the ODC Attribution License 1.0."


@lru_cache(maxsize=1)
def load_context_table() -> dict[str, Any] | None:
    """The shipped table, or None when it is missing, unreadable or of another layout."""
    try:
        table = json.loads(MULTIDEV_BASELINES_PATH.read_text(encoding="utf-8"))
        if table["schema"] == SCHEMA and table["types"] and table["population"]:
            return table
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _percentile(grid: dict[str, int], value: float) -> tuple[int | None, str]:
    """(percentile, range) of ``value`` on a 5..95 grid, linear between points."""
    pts = sorted((int(p), float(v)) for p, v in grid.items())
    if value < pts[0][1]:
        return None, "below_p5"
    if value > pts[-1][1]:
        return None, "above_p95"
    ps = [p for p, v in pts if v == value]
    if ps:  # on the grid, or on a flat run of it: the middle of the run
        return round((ps[0] + ps[-1]) / 2), "within"
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        if v0 < value < v1:
            return round(p0 + (p1 - p0) * (value - v0) / (v1 - v0)), "within"
    return None, "within"  # unreachable for a sorted grid; never raises


def session_context(
    task_type: str,
    real_tokens: int,
    turn_count: int,
    table: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The context object for one session, or None when it is not published for this session.

    None for a task type whose cell was not published (too few developers, unstable, or excluded),
    for a session with no tokens or no recorded turn count, and when the table is absent.
    """
    table = table if table is not None else load_context_table()
    if table is None or real_tokens <= 0 or turn_count <= 0:
        return None
    cell = table["types"].get(task_type)
    if not cell or not cell.get("available"):
        return None
    value = real_tokens / turn_count
    pct, where = _percentile(cell["grid"], value)
    pop = table["population"]
    return {
        "unit": table["unit"]["name"],
        "value": round(value),
        "percentile": pct,
        "range": where,
        "task_type": task_type,
        "developers": cell["developers"],
        "sessions": cell["n"],
        "source": pop["source"],
        "dataset_version": pop["dataset_version"],
        "date_range": pop["date_range"],
        "attribution": NOTICE,
        "note": "context, not a verdict",
    }


def context_line(ctx: dict[str, Any]) -> str:
    """One paragraph of text for a context object (no 'above/below band' wording)."""
    if ctx["range"] == "below_p5":
        where = "below the 5th percentile of"
    elif ctx["range"] == "above_p95":
        where = "above the 95th percentile of"
    else:
        where = f"about the {_ordinal(ctx['percentile'])} percentile of"
    dates = ctx["date_range"] or ["?", "?"]
    return (
        f"Context: {ctx['value']:,} tokens per turn is {where} {ctx['developers']} developers' "
        f"{ctx['task_type']} sessions ({ctx['sessions']} sessions; SWE-chat {ctx['dataset_version']}, "
        f"public repositories, {dates[0]} to {dates[1]}; each developer weighted equally). "
        f"Not a verdict. {ctx['attribution']}"
    )


def context_for_result(result: Any) -> dict[str, Any] | None:
    """Context for a ThreeAxisResult (uses its task type, real tokens and recorded turn count)."""
    return session_context(result.task_type, result.real_tokens, getattr(result, "turn_count", 0))
