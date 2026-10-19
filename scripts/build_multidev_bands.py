# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Build tracegauge/data/multidev_bands.json: developer-clustered CONTEXT percentiles from SWE-chat.

The table is context, not a verdict: it lets ``score`` say "this session is at the Nth percentile of
M developers' sessions of the same task type". Input is a LOCAL, gitignored per-session extract (one
JSON object per line; see scripts/extract_swechat_sessions.py): numbers and a hashed developer id,
no text and no paths. Output is a percentile table only: per task type a grid of percentiles of
tokens per turn, the p25/median/p75 with bootstrap intervals, how many developers and sessions stand
behind them, and how much the band moves when one developer is left out. No developer id and no
per-developer value is written.

Method (all choices fixed here, not tuned on the output):
  * unit: ``real_tokens / turn_count`` (tokens per turn). It was chosen before the table existed by
    comparing between-developer dispersion of tokens/session, tokens/turn and tokens/tool-call
    (docs/MULTIDEV_REFERENCE.md); tokens/turn was the most stable on both dataset versions;
  * population: sessions with an attributed developer and no partial-session marker;
  * clustering: every developer carries total weight 1 inside a task type, spread equally over
    that developer's sessions, so a prolific developer cannot set the band;
  * quantile: the first value whose cumulative weight reaches q of the total;
  * interval: percentile bootstrap that resamples DEVELOPERS (B=2000, random.Random(42));
  * publication: a cell is published only if it has at least MIN_DEVELOPERS developers,
    MIN_SESSIONS sessions and the largest relative move of p25/median/p75 over every
    leave-one-developer-out refit is at most MAX_LODO_SHIFT. An unpublished cell carries no values;
  * task types in CONTEXT_EXCLUDED_TYPES are never published (the maintainer's decision: not a
    coherent comparison group).
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "tracegauge" / "data" / "multidev_bands.json"

SEED = 42  # house rule: every stochastic step is seeded
BOOTSTRAP_RESAMPLES = 2000
MIN_DEVELOPERS = (
    5  # the maintainer's cell-size rule: no cell backed by fewer developers is published
)
MIN_SESSIONS = 10  # same activation floor as the one-developer table
MAX_LODO_SHIFT = 0.30  # a published band moves at most 30% when any one developer is dropped
TASK_TYPES = ("ml-eval", "debug-fix", "infra-deploy", "research-recon", "feature-build")
CONTEXT_EXCLUDED_TYPES = ("feature-build",)
QUANTILES = (("p25", 0.25), ("median", 0.5), ("p75", 0.75))
GRID = tuple(range(5, 100, 5))  # percentiles 5, 10, ..., 95
SCHEMA_VERSION = 2
UNIT = "real_tokens_per_turn"
ATTRIBUTION = (
    "Contains information from SALT-NLP/SWE-chat (https://huggingface.co/datasets/SALT-NLP/SWE-chat), "
    "which is made available under the Open Data Commons Attribution License 1.0."
)
PARTIAL_API_RATIO = 0.8  # fewer than 80% of the recorded API responses present = truncated


def is_partial(row: dict[str, Any]) -> str | None:
    """Reason a session is a partial record of its conversation, or None.

    A transcript that starts with a ``summary`` record continues an earlier one (its beginning is
    missing); a session with no usage has nothing to measure; a transcript with clearly fewer API
    responses than the dataset recorded for the session is truncated.
    """
    if row.get("first_type") == "summary":
        return "starts_with_summary"
    if int(row.get("real_tokens") or 0) <= 0:
        return "no_usage"
    api = int(row.get("ds_api_calls") or 0)
    if api > 0 and int(row.get("usage_deduped") or 0) < PARTIAL_API_RATIO * api:
        return "fewer_responses_than_recorded"
    return None


def unit_value(row: dict[str, Any]) -> float:
    """Tokens per turn of one session."""
    return int(row["real_tokens"]) / max(1, int(row.get("turn_count") or 0))


def weighted_quantile(groups: dict[str, list[float]], q: float) -> float:
    """Developer-equal-weight quantile: first value whose cumulative weight reaches q."""
    pairs = sorted((v, 1.0 / len(vs)) for vs in groups.values() for v in vs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total - 1e-12:
            return v
    return pairs[-1][0]


def bootstrap_ci(groups: dict[str, list[float]], q: float) -> list[int]:
    rng = random.Random(SEED)  # noqa: S311 - statistical resampling, not cryptography
    devs = sorted(groups, key=lambda d: sorted(groups[d]))  # order must not depend on the pseudonym
    stats: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        picked = {f"{i}": groups[devs[rng.randrange(len(devs))]] for i in range(len(devs))}
        stats.append(weighted_quantile(picked, q))
    stats.sort()
    return [round(stats[int(len(stats) * 0.025)]), round(stats[int(len(stats) * 0.975) - 1])]


def max_leave_one_out_shift(
    groups: dict[str, list[float]], base: dict[str, float]
) -> dict[str, float]:
    shift = {name: 0.0 for name, _ in QUANTILES}
    if len(groups) < 2:
        return shift
    for dev in sorted(groups):
        rest = {k: v for k, v in groups.items() if k != dev}
        for name, q in QUANTILES:
            moved = abs(weighted_quantile(rest, q) - base[name]) / max(base[name], 1e-9)
            shift[name] = max(shift[name], moved)
    return shift


def build_cell(task_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The published cell for the sessions of one task type (values only when publishable)."""
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        groups[r["dev"]].append(unit_value(r))
    n, k = len(rows), len(groups)
    cell: dict[str, Any] = {"available": False, "n": n, "developers": k}
    if task_type in CONTEXT_EXCLUDED_TYPES:
        cell["inactive_reason"] = "context_not_published_for_type"
        return cell
    if k < MIN_DEVELOPERS or n < MIN_SESSIONS:
        cell["inactive_reason"] = "too_few_developers_or_sessions"
        return cell
    base = {name: weighted_quantile(groups, q) for name, q in QUANTILES}
    shift = max_leave_one_out_shift(groups, base)
    cell["leave_one_developer_out_max_shift"] = {name: round(v, 3) for name, v in shift.items()}
    if max(shift.values()) > MAX_LODO_SHIFT:
        # An unstable band is not published: only the reason and the shifts that caused it.
        cell["inactive_reason"] = "unstable_when_one_developer_is_left_out"
        return cell
    cell.update({name: round(v) for name, v in base.items()})
    cell["ci95"] = {name: bootstrap_ci(groups, q) for name, q in QUANTILES}
    grid = [round(weighted_quantile(groups, p / 100)) for p in GRID]
    cell["grid"] = {str(p): v for p, v in zip(GRID, sorted(grid))}  # monotone after rounding
    cell["available"] = True
    return cell


def build_table(
    rows: list[dict[str, Any]], *, reference_id: str, dataset_version: str, source: str
) -> dict[str, Any]:
    excluded = {"partial": 0, "unattributed": 0}
    reasons: dict[str, int] = defaultdict(int)
    kept: list[dict[str, Any]] = []
    for r in rows:
        why = is_partial(r)
        if why:
            excluded["partial"] += 1
            reasons[why] += 1
        elif not r.get("dev"):
            excluded["unattributed"] += 1
        else:
            kept.append(r)
    types = {t: build_cell(t, [r for r in kept if r["task_type"] == t]) for t in TASK_TYPES}
    # the dataset has rows with no creation date (pandas writes "NaT"); only real dates count
    dates = sorted(str(r["created"]) for r in kept if str(r.get("created") or "")[:2] == "20")
    return {
        "schema": SCHEMA_VERSION,
        "reference_id": reference_id,
        "label": f"{len({r['dev'] for r in kept})} developers ({dataset_version})",
        "attribution": ATTRIBUTION,
        "unit": {
            "name": UNIT,
            "definition": "real_tokens / turn_count; real_tokens = sum over AI turns of "
            "(token_count_input - cache_read + token_count_output)",
        },
        "population": {
            "source": source,
            "dataset_version": dataset_version,
            "developers": len({r["dev"] for r in kept}),
            "sessions": len(kept),
            "date_range": [dates[0], dates[-1]] if dates else None,
            "excluded_sessions": {**excluded, "partial_by_reason": dict(sorted(reasons.items()))},
        },
        "method": {
            "clustering": "each developer carries total weight 1 within a task type",
            "task_type_rule": "tracegauge.classify.classify_session (the rule users are scored with)",
            "interval": f"percentile bootstrap over developers, B={BOOTSTRAP_RESAMPLES}, seed {SEED}",
            "min_developers": MIN_DEVELOPERS,
            "min_sessions": MIN_SESSIONS,
            "max_leave_one_developer_out_shift": MAX_LODO_SHIFT,
            "context_excluded_types": list(CONTEXT_EXCLUDED_TYPES),
        },
        "types": types,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the developer-clustered context percentiles.")
    ap.add_argument("--extract", type=Path, required=True, help="local per-session extract (JSONL)")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--reference-id", default="swechat-v1")
    ap.add_argument("--dataset-version", default="v1")
    ap.add_argument(
        "--source",
        default="SALT-NLP/SWE-chat, Claude Code sessions, main (non-subagent) transcripts",
    )
    args = ap.parse_args(argv)
    rows = [
        json.loads(line) for line in args.extract.read_text(encoding="utf-8").splitlines() if line
    ]
    table = build_table(
        rows,
        reference_id=args.reference_id,
        dataset_version=args.dataset_version,
        source=args.source,
    )
    # bytes, so the file is identical on every OS (no CRLF translation)
    text = json.dumps(table, indent=2, sort_keys=True) + "\n"
    args.out.write_bytes(text.encode("utf-8"))
    pop = table["population"]
    print(f"wrote {args.out} ({pop['sessions']} sessions, {pop['developers']} developers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
