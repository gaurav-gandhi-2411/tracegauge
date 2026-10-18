# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/_digest.py — Shared digest dataclasses used by tracegauge.adapt and tracegauge.judge.

Self-contained (no src/ or scripts/ imports). These are a direct copy of the
dataclasses in src/token_efficiency/trace_digest.py, kept here so the installed
wheel does not depend on the repo's src/ tree.

These are internal to the tracegauge package — not part of the public API.
"""

from dataclasses import dataclass, field


@dataclass
class TurnDigest:
    """Compact representation of one conversation turn."""

    turn_index: int
    role: str  # "ai" | "user" | "tool" | "system"
    tool_names: list[str]  # names of tools called in this turn
    content_snippet: str  # first 300 chars of content_text, stripped
    token_count_input: int
    token_count_output: int
    cache_read: int
    h2_duplicate: bool  # True if annotation flagged this turn as llm_h2_duplicate_message
    cache_creation: int = 0  # cache_creation_input_tokens for this turn (cost use only)
    model: str = ""  # model string for this turn, e.g. "claude-sonnet-4-6"
    # Added 0.10.2 (S1 fix): raw usage.server_tool_use counts from the Claude
    # API response (e.g. {"web_search_requests": 2}), when present and
    # non-empty. None when the field was absent/empty on this turn's raw
    # usage dict -- distinct from an empty dict, though tracegauge.adapt never
    # actually produces an empty dict (see adapt.py's _parse_usage). Cost
    # computation (tracegauge.cost) prices web_search_requests from it and warns that
    # the kinds it cannot price (code execution) are not reflected in total_usd.
    server_tool_use: dict[str, int] | None = None
    # API requests this turn stands for: 1 for a real turn, >1 for an aggregated subagent
    # turn (tracegauge.adapt.collect_subagent_usage). tracegauge.cost needs it because tiered models
    # bill per request, which an aggregate no longer shows.
    request_count: int = 1
    # Cache-write tier split (usage.cache_creation.ephemeral_{5m,1h}_input_tokens, a subset
    # breakdown of cache_creation). cache_creation_1h = tokens written with the 1-hour TTL
    # (billed 2x input); cache_creation_tier_known = tokens of cache_creation covered by a
    # transcript record that carried the breakdown. The rest (cache_creation -
    # cache_creation_tier_known) is of unspecified tier and is priced at the 5-minute rate,
    # the documented conservative default. Both 0 on records stored before this field existed.
    cache_creation_1h: int = 0
    cache_creation_tier_known: int = 0
    # Per-request billing modifiers from usage.speed / usage.inference_geo, normalised by
    # tracegauge.adapt._parse_billing_modifiers: speed is "fast" or "" (standard/absent), inference_geo
    # is "us" or "" (global/not_available/absent). They scale the WHOLE request, so a main-chain
    # turn (one request) carries its own and an aggregated subagent turn is only ever built
    # from requests sharing the same pair (tracegauge.adapt.collect_subagent_usage). "" on records
    # stored before these fields existed, which therefore price exactly as before.
    speed: str = ""
    inference_geo: str = ""
    # Subagent type (``agentType`` of the sibling agent-<id>.meta.json) on aggregated subagent
    # turns; "" on main-chain turns and on records stored before this field existed (reported
    # as "unknown"). See tracegauge.adapt.read_agent_type.
    agent_type: str = ""


@dataclass
class SessionDigest:
    """Human- and judge-consumable digest of a full session."""

    session_id: str
    domain: str
    resolved: bool
    total_tokens: int
    turn_count: int
    h2_duplicate_count: int
    cache_hit_rate: float
    p25_token_ratio: float
    output_tokens_available: bool  # True when per-turn output tokens are recorded
    task_description: str  # first user turn content, first 800 chars
    turns: list[TurnDigest]  # all turns, ordered by turn_index
    # Subagent usage rolled up into this (parent) session: one AI-role TurnDigest per
    # (subagent file, model). Deliberately NOT part of `turns` -- waste detection, the judge
    # text and the real_tokens verdict stay main-chain; tracegauge.cost and tracegauge.attribution add
    # these on top. Empty for sessions without subagents and for records stored before
    # this field existed.
    subagent_turns: list[TurnDigest] = field(default_factory=list)
    subagent_count: int = 0  # number of subagent transcripts that contributed usage
    # Transcripts per subagent type (sums to subagent_count); {} on records stored before this
    # field existed. Lets tracegauge.cost report files per type without file identities.
    subagent_files_by_type: dict[str, int] = field(default_factory=dict)
    # Usage de-duplication (adapter_version 2): Claude Code writes one assistant record per
    # content block of an API response, each repeating the response's usage; tracegauge.adapt counts
    # each distinct message.id once. usage_records = main-chain assistant records that carried
    # a usage dict; usage_records_deduped = distinct responses among them (0/0 on digests
    # stored before this field existed). duplicates = usage_records - usage_records_deduped.
    usage_records: int = 0
    usage_records_deduped: int = 0


def reconstruct_digest(d: dict) -> SessionDigest:
    """Reconstruct a SessionDigest from the plain dict stored in adapted records.

    Handles records generated before output_tokens_available was added by
    defaulting the field to False when absent (safe: swe_agent sessions lack it).
    """
    turns = [TurnDigest(**t) for t in d["turns"]]
    subagent_turns = [TurnDigest(**t) for t in d.get("subagent_turns", [])]
    fields = {k: v for k, v in d.items() if k not in ("turns", "subagent_turns")}
    fields.setdefault("output_tokens_available", False)
    return SessionDigest(**fields, turns=turns, subagent_turns=subagent_turns)


def digest_to_text(digest: SessionDigest) -> str:
    """Render a SessionDigest as judge-readable text (show_stats=False mode).

    Omits formula-derived stats so the judge anchors on agent behaviour
    rather than token math.
    """
    header_summary = (
        f"Domain: {digest.domain} | Resolved: {digest.resolved} | "
        f"Turns: {digest.turn_count} | "
        f"Output Tokens: {'available' if digest.output_tokens_available else 'unavailable (swe_agent)'}"
    )
    lines: list[str] = [f"=== SESSION {digest.session_id} ===", header_summary]
    lines += ["", f"TASK: {digest.task_description}", "", "TRAJECTORY:"]

    for turn in digest.turns:
        if turn.role == "system":
            continue
        tool_str: str = ", ".join(turn.tool_names) if turn.tool_names else "none"
        # ENV_RESULT is the display label for openhands environment-response turns (role="tool").
        role_upper: str = "ENV_RESULT" if turn.role == "tool" else turn.role.upper()
        lines.append(
            f"[T{turn.turn_index}] {role_upper} — tools: {tool_str} — "
            f"in: {turn.token_count_input} / out: {turn.token_count_output}"
        )
        lines.append(f"  {turn.content_snippet}")

    return "\n".join(lines)


__all__ = ["TurnDigest", "SessionDigest", "reconstruct_digest", "digest_to_text"]
