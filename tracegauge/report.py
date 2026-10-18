# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/report.py — Output formatter for ThreeAxisResult.

format_human(result) -> str   Human-readable three-axis report with caveats inline.
format_json(result) -> str    Full JSON serialization including domain-of-validity.
"""

import dataclasses
import json
import textwrap
from typing import Any

from tracegauge.json_out import SCHEMA_VERSION as JSON_SCHEMA_VERSION
from tracegauge.score import ThreeAxisResult
from tracegauge.takeaway import BREAKDOWN_NOTE, format_cost_breakdown_lines
from tracegauge.web.cost_format import (
    format_cost_display,
    format_cost_usd,
    format_cost_vs_baseline,
    format_price_provenance,
    format_unpriced,
)

_WIDTH = 76
_BORDER = "═" * _WIDTH
_DIVIDER_WIDTH = _WIDTH


def _section_divider(title: str) -> str:
    """Build e.g. '── TOKEN ECONOMY ─────...' at _WIDTH chars."""
    prefix = f"── {title} "
    fill = "─" * max(0, _WIDTH - len(prefix))
    return prefix + fill


def _wrap(text: str, indent: int = 2) -> str:
    pad = " " * indent
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=pad,
        subsequent_indent=pad,
    )


def _format_turns(turns: list[int]) -> str:
    """Format [14,15,18,19] -> '14→15, 18→19'."""
    pairs = []
    for i in range(0, len(turns) - 1, 2):
        pairs.append(f"{turns[i]}→{turns[i + 1]}")
    return ", ".join(pairs) if pairs else str(turns)


_CAUSE_LABELS = (
    ("TTL_GAP", "TTL gap (idle past the cache lifetime)"),
    ("MODEL_SWITCH", "model switch"),
    ("COMPACTION", "compaction"),
    ("UNEXPLAINED", "unexplained (no cause claimed)"),
)
_TOP_EVENTS_IN_TEXT = 3


def _usd_or_unpriced(usd: float | None) -> str:
    return "unpriced" if usd is None else format_cost_usd(usd)


def format_cache_churn_lines(churn: dict[str, Any] | None) -> list[str]:
    """The CACHE CHURN section (empty list = print nothing).

    Nothing is printed when the analysis was unavailable or found no event: the section exists to
    point at money, and "0 events" is not worth a block in every report (the full object, zeros
    included, is in `score --json`).
    """
    if not churn or churn.get("status") != "ok" or not churn.get("events"):
        return []
    scope = churn["by_scope"]
    lines = ["", _section_divider("CACHE CHURN")]
    floor = "" if churn["priced"] else " (priced part; some models unpriced)"
    lines.append(
        _wrap(
            f"{churn['events']} event(s) where cached context was re-written instead of read: "
            f"{format_cost_usd(churn['churn_usd'])} above what reading it would have cost{floor}. "
            f"Main chain {format_cost_usd(scope['main']['churn_usd'])} "
            f"({scope['main']['events']}), subagents "
            f"{format_cost_usd(scope['subagent']['churn_usd'])} ({scope['subagent']['events']})."
        )
    )
    for key, label in _CAUSE_LABELS:
        b = churn["by_cause"][key]
        if b["events"]:
            unpriced = f", {b['unpriced_events']} unpriced" if b["unpriced_events"] else ""
            lines.append(
                f"    {label:<40} {b['events']:>4}  {format_cost_usd(b['churn_usd']):>10}{unpriced}"
            )
    lines.append(
        _wrap(
            f"Plausibly avoidable (a judgement): {format_cost_usd(churn['avoidable_usd'])}; "
            f"not avoidable by a setting (idle over an hour, compaction): "
            f"{format_cost_usd(churn['unavoidable_usd'])}."
        )
    )
    for ev in churn["events_listed"][:_TOP_EVENTS_IN_TEXT]:
        if ev["gap_s"] is None:
            gap = ""
        elif ev["ttl_s"] is None:
            gap = f", gap {ev['gap_s']:,.0f}s, cache lifetime unknown"
        else:
            gap = f", gap {ev['gap_s']:,.0f}s vs {ev['ttl_s']:,.0f}s TTL"
        where = "main" if ev["scope"] == "main" else f"subagent {ev['chain']}"
        lines.append(
            _wrap(
                f"[{where}] turn {ev['previous_turn']['turn_index']}→"
                f"{ev['current_turn']['turn_index']}: {ev['cause'].lower().replace('_', ' ')}"
                f"{gap}, {ev['churn_tokens']:,} tokens re-written, "
                f"{_usd_or_unpriced(ev['churn_usd'])}",
                indent=4,
            )
        )
    for adv in churn["advice"]:
        lines.append(
            _wrap(
                f"Priced for this session: setting {adv['setting']}=1h "
                f"(env {adv['env_var']}) would save about {format_cost_usd(adv['net_usd'])} net "
                f"({format_cost_usd(adv['saved_usd'])} of avoided re-writes minus "
                f"{format_cost_usd(adv['premium_usd'])} higher write rate on {adv['scope']} "
                f"chains). Assumes {adv['events_avoided']} gap(s) of 5 min to 1 h become reads; "
                f"other sessions may lose money on it."
            )
        )
    lines.append(_wrap("Cause = a rule on transcript markers and timestamps, not a verified fact."))
    lines.append("")
    return lines


def format_human(
    result: ThreeAxisResult,
    baseline_cost_band: tuple[float, float, float] | None = None,
) -> str:
    """Format a ThreeAxisResult as a human-readable multi-section report.

    Parameters
    ----------
    result:
        The scored session result.
    baseline_cost_band:
        Optional (p25_usd, median_usd, p75_usd) for relative cost framing.
        When None the cost section shows the dollar amount with a dashboard note.
    """
    lines: list[str] = []

    # Header
    lines.append(_BORDER)
    lines.append("  TRACEGAUGE")
    lines.append(f"  Session:   {result.session_id}")
    lines.append(f"  Task type: {result.task_type}")
    lines.append(_BORDER)
    lines.append("")

    # ── TOKEN ECONOMY ──
    lines.append(_section_divider("TOKEN ECONOMY"))
    lines.append(f"  Real tokens: {result.real_tokens:,}")
    if result.band_verdict == "unavailable":
        lines.append("  Verdict:     UNAVAILABLE")
        lines.append(_wrap(result.interpretation))
    else:
        lines.append(f"  Verdict:     {result.band_verdict}")
        if result.p25 is not None:
            lines.append(
                f"  Baseline:    {result.task_type} — "
                f"p25: {result.p25:,} / median: {result.median:,} / p75: {result.p75:,}"
            )
        lines.append(_wrap(result.interpretation))
    lines.append("")
    lines.append(_wrap(result.token_domain_of_validity))
    lines.append("")

    # ── TRAJECTORY QUALITY ──
    lines.append(_section_divider("TRAJECTORY QUALITY"))
    if result.judge_verdict is not None:
        lines.append(f"  Verdict:     {result.judge_verdict} (score: {result.judge_score})")
        if result.judge_reasoning:
            lines.append(_wrap(f"Reasoning:   {result.judge_reasoning}", indent=2))
    else:
        lines.append("  Verdict:     UNAVAILABLE (no local judge configured)")
        lines.append("  To enable:   ollama pull qwen3:30b-a3b  (~18GB VRAM required)")
        lines.append("               Token and waste axes still run fully without the judge.")
    lines.append("")
    lines.append(_wrap(result.trajectory_domain_of_validity))
    lines.append("")

    # ── DETERMINISTIC WASTE ──
    lines.append(_section_divider("DETERMINISTIC WASTE"))
    if result.waste_event_count == 0:
        lines.append("  Events:  0 detected")
    else:
        lines.append(f"  Events:  {result.waste_event_count} event(s)")
        for idx, ev in enumerate(result.waste_events, 1):
            det = ev.get("detector", "?")
            ev_turns = ev.get("turns", [])
            evi = ev.get("evidence", {})
            turns_str = _format_turns(ev_turns)
            cost_usd = ev.get("wasted_cost_usd")
            cost_str = f"  ~${cost_usd:.3f}" if cost_usd is not None and cost_usd > 0 else ""
            if det == "REPEATED-FAILED-RETRY":
                rc = ev.get("repeat_count", "?")
                snip = evi.get("error_snippet", "")[:60]
                lines.append(f"  [{idx}] {det} — {rc} retries, turns: {turns_str}{cost_str}")
                lines.append(f"      Evidence: {snip!r}")
            elif det == "REDUNDANT-READ":
                path_label = evi.get("path", "?")
                gap = evi.get("gap", "?")
                snip = evi.get("content_snippet", "")[:60]
                lines.append(
                    f"  [{idx}] {det} (PATH-{path_label}) — gap={gap}, turns: {turns_str}{cost_str}"
                )
                lines.append(f"      Evidence: {snip!r}")
    lines.append("")
    lines.append(_wrap(result.waste_domain_of_validity))

    # ── CACHE CHURN ── (informational; absent when there is nothing to show)
    lines.extend(format_cache_churn_lines(result.cache_churn))

    # ── COST ANNOTATION ──
    # Cost is an annotation on the token axis. Not a score. Not a composite.
    lines.append(_section_divider("COST ANNOTATION"))
    if result.session_cost_usd is None:
        lines.append("  Cost: not computed (run backfill_cost() or re-score the session)")
    else:
        # Load prices for provenance line — lazy import to avoid circular at module level.
        try:
            from tracegauge.cost import load_price_table  # noqa: PLC0415

            _prices = load_price_table()
            provenance_line = format_price_provenance(_prices, result.cost_models)
        except Exception:
            provenance_line = (
                "Prices: see ~/.tes/prices.json or bundled tracegauge/data/prices.json"
            )

        cost_str = format_cost_display(result.session_cost_usd, result.unpriced_models)
        # A partial/absent total is not comparable with a fully-priced baseline.
        vs_str = (
            "not comparable: includes unpriced turns"
            if result.unpriced_models
            else format_cost_vs_baseline(result.session_cost_usd, baseline_cost_band)
        )

        if baseline_cost_band is not None or result.unpriced_models:
            lines.append(f"  Cost:  {cost_str}  ({vs_str})")
        else:
            lines.append(f"  Cost:  {cost_str}  (no baseline cost comparison yet)")
        lines.append(
            textwrap.fill(
                provenance_line,
                width=_WIDTH,
                initial_indent=" " * 9,
                subsequent_indent=" " * 11,
            )
        )
        if result.unpriced_models:
            lines.append(
                f"         [{format_unpriced(result.unpriced_models)}: these models are not in the "
                "price table, so their turns are EXCLUDED from the figure above — the true "
                "cost is higher. Add them via TRACEGAUGE_PRICE_TABLE or ~/.tes/prices.json.]"
            )
        if result.subagent_count:
            # With unpriced models in play the subagent figure is a priced-part subtotal.
            sub_cost = format_cost_usd(result.subagent_cost_usd) + (
                " (priced part)" if result.unpriced_models else ""
            )
            lines.append(
                f"         Includes {result.subagent_count} subagent transcript(s): "
                f"{result.subagent_tokens:,} tokens, {sub_cost} "
                "(counted here, not in the TOKEN ECONOMY verdict above)."
            )
            lines.extend(
                format_subagent_lines(
                    result.subagents, result.session_cost_usd, bool(result.unpriced_models)
                )
            )
        if result.cost_approximate:
            lines.append(
                "         [APPROXIMATE: some turns had an unresolvable model and are "
                "EXCLUDED from this total (never priced at a guessed/default rate) — "
                "see cost_domain_of_validity]"
            )
        for warning in result.cost_server_tool_warnings:
            lines.append(f"         [NOT PRICED: {warning}]")
        for caveat in result.cost_pricing_caveats:
            lines.append(f"         [FLOOR: {caveat}]")

    lines.append("")
    lines.append(
        _wrap(
            "Cost is not a score. It annotates the token axis at API-equivalent rates. "
            "Flat-plan users: marginal cost differs; token consumption is the honest metric."
        )
    )

    # COST BREAKDOWN: informational dollars per bucket; never a finding (see tracegauge.takeaway).
    if result.cost_breakdown:
        bd = result.cost_breakdown
        lines.append("")
        lines.append(_section_divider("COST BREAKDOWN"))
        lines.append(_wrap(f"({bd['label']})"))
        lines.extend(format_cost_breakdown_lines(bd))
        if bd["note"]:
            lines.append(_wrap(f"[{bd['note']}]"))
        lines.append(_wrap(BREAKDOWN_NOTE))

    # LEVER: only when an absolute finding fires (waste above its threshold, or output >= 40%).
    if result.lever_hint:
        lines.append("")
        lines.append(_section_divider("LEVER"))
        lines.append(_wrap(result.lever_hint))
    lines.append(_BORDER)

    return "\n".join(lines)


_MAX_SUBAGENT_TYPE_LINES = 6  # beyond this the tail collapses into "N more"


def _fmt_sub_usd(usd: float | None, is_floor: bool) -> str:
    """'$X.XX' / '>= $X.XX' for a lower bound / 'unpriced' when nothing was priced."""
    if usd is None:
        return "unpriced"
    # A real but sub-cent price must not read as "$0.00" (that looks like free).
    amount = "<$0.01" if 0 < usd < 0.005 else format_cost_usd(usd)
    return f"{'>= ' if is_floor else ''}{amount}"


def format_subagent_lines(
    sub: dict[str, Any], session_usd: float | None = None, partial: bool = False
) -> list[str]:
    """Share-of-session line plus one line per subagent type (largest dollars first).

    ``partial`` marks a session total that excludes unpriced turns (then a priced-part share).
    """
    by_type: list[dict[str, Any]] = sub.get("by_type") or []
    if not by_type:
        return []
    out: list[str] = []
    share = sub.get("share_of_session_usd")
    if share is not None and session_usd is not None:
        out.append(
            f"         subagents: {share * 100:.0f}% of this session's "
            f"{format_cost_usd(session_usd)}{' (priced part)' if partial else ''}; "
            "per-type tokens are real tokens, as above"
        )
    for row in by_type[:_MAX_SUBAGENT_TYPE_LINES]:
        row_share = row["share_of_session_usd"]
        share_txt = f", {row_share * 100:.0f}% of session" if row_share is not None else ""
        out.append(
            f"           {row['type']}: {row['files']} file(s), {row['real_tokens']:,} tokens, "
            f"{_fmt_sub_usd(row['usd'], row['usd_is_floor'])}{share_txt}"
        )
    if len(by_type) > _MAX_SUBAGENT_TYPE_LINES:
        out.append(f"           {len(by_type) - _MAX_SUBAGENT_TYPE_LINES} more type(s)")
    return out


def format_json(result: ThreeAxisResult, legacy_rows_excluded: int = 0) -> str:
    """Serialize the full ThreeAxisResult to indented JSON.

    `command` and `legacy_rows_excluded` follow the envelope every other --json document has.
    `legacy_rows_excluded` is the store's pre-0.15 row count (0 when no store is open); a single
    score reads none of them, so it is context, not an adjustment.
    """
    d: dict[str, Any] = {
        "schema_version": JSON_SCHEMA_VERSION,
        "command": "score",
        "legacy_rows_excluded": legacy_rows_excluded,
        **dataclasses.asdict(result),
    }
    return json.dumps(d, indent=2, ensure_ascii=False)
