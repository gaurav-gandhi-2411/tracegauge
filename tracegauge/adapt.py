# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/adapt.py — Claude Code session JSONL adapter.

Self-contained implementation (no scripts/ or src/ import) so the installed wheel
works without repo access. This is a direct port of the frozen adapter logic from
scripts/adapters/claudecode_adapter.py.

Public API:
    adapt_session(session_path: Path) -> dict
"""

import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

from tracegauge._digest import SessionDigest, TurnDigest
from tracegauge.discovery import SUBAGENTS_DIRNAME, is_subagent_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SNIPPET_MAX_CHARS: int = 300
_TASK_DESC_MAX_CHARS: int = 800

# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Provider API keys
    (r"gsk_[A-Za-z0-9]{20,}", "groq_key"),
    (r"sk-ant-[A-Za-z0-9\-_]{20,}", "anthropic_key"),
    (r"sk-or-[A-Za-z0-9\-_]{20,}", "openrouter_key"),
    (r"sk-[A-Za-z0-9]{40,}", "openai_style_key"),
    (r"ghp_[A-Za-z0-9]{20,}", "github_pat"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "github_fine_grained_pat"),
    (r"ghs_[A-Za-z0-9]{20,}", "github_actions_token"),
    (r"hf_[A-Za-z0-9]{20,}", "huggingface_token"),
    (r"AIzaSy[A-Za-z0-9\-_]{30,}", "google_api_key"),
    (r"AKIA[A-Z0-9]{16}", "aws_access_key_id"),
    (r"wandb_v1_[A-Za-z0-9_]{20,}", "wandb_api_key"),
    (r"xoxb-[A-Za-z0-9\-]{20,}", "slack_bot_token"),
    (r"xoxp-[A-Za-z0-9\-]{20,}", "slack_user_token"),
    (r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}", "slack_webhook"),
    # Generic assignments: KEY=<long-random-value>
    # Excludes: placeholders, code attribute-access values (settings.key, self.key),
    # and visual separators (strings of = chars).
    (
        r"(?i)(?:API_KEY|SECRET_KEY|PRIVATE_KEY|ACCESS_TOKEN|AUTH_TOKEN|DB_PASSWORD|ANON_KEY|SERVICE_ROLE_KEY)"
        r"\s*=\s*(?!NOT_SET|your_|<|REDACTED|\*{3}|\.{3}|none|null|placeholder|change.me|example"
        r"|=+|[A-Za-z_][A-Za-z0-9_]*\.)"
        r"[A-Za-z0-9\-_+=/.@#$%]{16,}",
        "generic_key_assignment",
    ),
    # Database URLs with embedded credentials
    (r"(?:postgresql|mysql|mongodb)://[^:]+:[^@\s'\"\\]{6,}@[^\s'\"\\]+", "database_url"),
    # JWT session tokens (only in value position after "token":)
    (r'"token"\s*:\s*"eyJ[A-Za-z0-9+/=._\-]{20,}"', "jwt_session_token"),
    # Private key blocks
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private_key_header"),
]

# Compile once at module load time for efficiency.
_COMPILED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in _SECRET_PATTERNS
]

# Module-level counter so callers can observe how many redactions occurred.
redaction_count: int = 0


def _redact_secrets(text: str) -> str:
    """Replace any secret-looking values in text with ``<SECRET_REDACTED>``."""
    global redaction_count
    result = text
    for compiled, label in _COMPILED_PATTERNS:
        new_result, n = compiled.subn("<SECRET_REDACTED>", result)
        if n > 0:
            redaction_count += n
            print(
                f"[adapter] WARNING: redacted {n} occurrence(s) of pattern '{label}'",
                file=sys.stderr,
            )
            result = new_result
    return result


# ---------------------------------------------------------------------------
# JSONL parsing helpers
# ---------------------------------------------------------------------------


def _is_main_chain(msg: dict[str, Any]) -> bool:
    """Return True if the message belongs to the main (non-sidechain) conversation."""
    return not msg.get("isSidechain", False)


def _extract_tool_names_from_content(content: list[dict[str, Any]]) -> list[str]:
    """Return the names of all tool_use blocks in an assistant content list."""
    return [
        str(block["name"])
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name")
    ]


def _extract_edit_operations_from_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull Edit/Write/MultiEdit/NotebookEdit operations out of this
    assistant message's tool_use blocks -- the ONE place in the adapter that
    reads tool_use.input, since TurnDigest deliberately never carries it
    (compact-representation design, see _digest.py). Computed in the SAME
    single pass over the source JSONL that builds the rest of the record,
    because this is the only opportunity to read it before the
    file may later disappear. Returns plain dicts (not EditOperation
    instances) so this module stays free of a tracegauge.impact import cycle risk;
    tracegauge.impact.extract_edit_operations does the actual parsing.
    """
    from tracegauge.impact import extract_edit_operations

    ops: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        tool_input = block.get("input")
        if not name or not isinstance(tool_input, dict):
            continue
        for op in extract_edit_operations(str(name), tool_input):
            ops.append(
                {
                    "path": op.path,
                    "additions": op.additions,
                    "deletions": op.deletions,
                    "tool": op.tool,
                    "prior_content_unknown": op.prior_content_unknown,
                    "untested_tool_shape": op.untested_tool_shape,
                }
            )
    return ops


def _extract_text_snippet(content: list[dict[str, Any]]) -> str:
    """Concatenate all text blocks (skipping thinking) and truncate to snippet max."""
    parts: list[str] = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts)[:_SNIPPET_MAX_CHARS]


def _extract_tool_result_snippet(content: list[dict[str, Any]]) -> str:
    """Concatenate content fields from all tool_result blocks and truncate."""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        sub = block.get("content", "")
        if isinstance(sub, str):
            parts.append(sub)
        elif isinstance(sub, list):
            for item in sub:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
    return "".join(parts)[:_SNIPPET_MAX_CHARS]


def _int_or_zero(value: Any) -> int:
    """A usage count as an int. A JSON ``null`` (seen in real transcripts written through some
    API proxies), a boolean, a non-numeric string or a container counts as 0 instead of
    aborting the whole session."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _sanitize_record(obj: Any) -> dict[str, Any] | None:
    """Keep a parsed JSONL line only if it is a JSON object, and make ``message`` and
    ``message.usage`` dicts so every later ``.get`` is safe. Returns ``None`` for a scalar,
    list or null line."""
    if not isinstance(obj, dict):
        return None
    message = obj.get("message")
    if "message" in obj and not isinstance(message, dict):
        obj["message"] = {}
    elif isinstance(message, dict):
        if "usage" in message and not isinstance(message["usage"], dict):
            message["usage"] = {}
        if "model" in message and not isinstance(message["model"], str):
            message["model"] = ""
    return obj


def _parse_usage(usage: dict[str, Any]) -> tuple[int, int, int, int]:
    """Return (input_tokens, cache_creation, cache_read, output_tokens) from a usage dict."""
    return (
        _int_or_zero(usage.get("input_tokens", 0)),
        _int_or_zero(usage.get("cache_creation_input_tokens", 0)),
        _int_or_zero(usage.get("cache_read_input_tokens", 0)),
        _int_or_zero(usage.get("output_tokens", 0)),
    )


def _parse_cache_tiers(usage: dict[str, Any]) -> tuple[int, int]:
    """Return ``(cache_creation_1h, cache_creation_tier_known)`` from a usage dict.

    ``usage.cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`` is a
    subset breakdown of ``cache_creation_input_tokens``: ``1h`` tokens are billed at the
    1-hour write rate (2x input), ``5m`` at 1.25x. ``known`` is how many of the record's
    cache-write tokens the breakdown accounts for (all of them on real data: 5m + 1h ==
    cache_creation_input_tokens held on 100% of the records checked, see CHANGELOG); a record
    without the breakdown reports ``(0, 0)`` so its writes stay of *unspecified* tier and
    tracegauge.cost prices them at the 5-minute rate (the documented conservative default). Both
    values are clamped to ``cache_creation_input_tokens`` so a malformed breakdown can never
    price more write tokens than the record billed.
    """
    total = _int_or_zero(usage.get("cache_creation_input_tokens", 0))
    raw = usage.get("cache_creation")
    if not isinstance(raw, dict) or not (
        "ephemeral_5m_input_tokens" in raw or "ephemeral_1h_input_tokens" in raw
    ):
        return 0, 0
    five = max(0, _int_or_zero(raw.get("ephemeral_5m_input_tokens")))
    hour = max(0, _int_or_zero(raw.get("ephemeral_1h_input_tokens")))
    return min(hour, total), min(five + hour, total)


def _parse_server_tool_use(usage: dict[str, Any]) -> dict[str, int] | None:
    """Extract ``usage.server_tool_use`` counts (e.g. ``{"web_search_requests": 2}``)
    from a raw Claude API usage dict, when present and non-empty.

    Added 0.10.2 (S1 fix): before this, these counts were silently dropped —
    ``_parse_usage`` above reads only 4 token-count fields, and nothing in
    this module looked at ``server_tool_use`` at all, so a session with
    server-side tool calls (e.g. web search, billed at $10/1,000 searches on
    top of token costs) was priced as if those calls never happened, with
    zero warning. This function only detects and surfaces the counts —
    tracegauge.cost prices web_search_requests ($10/1,000 searches) and warns about
    the kinds it cannot price (see tracegauge/cost.py's _server_tool_warning).

    Returns None when the field is absent, not a dict, or every count in it
    is zero/falsy -- distinguishing "no server-side tool usage" from "usage
    present but nothing billable" isn't needed downstream, so both collapse
    to None (nothing to warn about).
    """
    raw = usage.get("server_tool_use")
    if not isinstance(raw, dict):
        return None
    counts = {str(k): int(v) for k, v in raw.items() if isinstance(v, int | float) and v}
    return counts or None


def _parse_billing_modifiers(usage: dict[str, Any]) -> tuple[str, str]:
    """Return ``(speed, inference_geo)`` normalised to what changes the bill.

    ``usage.speed == "fast"`` -> ``"fast"``; ``usage.inference_geo == "us"`` -> ``"us"``.
    Everything else ("standard", "global", "not_available", null, absent, or a value the
    pricing page does not know) normalises to ``""`` -- tracegauge.cost prices it at the standard
    rate, and scripts/check_usage_fields_priced.py is what flags an unrecognised value.
    """
    speed = "fast" if usage.get("speed") == "fast" else ""
    geo = "us" if usage.get("inference_geo") == "us" else ""
    return speed, geo


def _message_dedup_key(msg: dict[str, Any]) -> str:
    """Key identifying the API response an assistant record belongs to.

    Claude Code writes one assistant record per content block (thinking / text /
    tool_use), each repeating the response's ``usage``; streaming partials of one response
    can also differ in ``output_tokens``. Records of one response share ``message.id``.
    Falls back to ``requestId`` then ``uuid`` (unique, so never merged) when absent.
    """
    message = msg.get("message")
    mid = message.get("id") if isinstance(message, dict) else None
    return str(mid or msg.get("requestId") or msg.get("uuid") or id(msg))


# Bumped whenever the token accounting of ``adapt_session`` changes in a way that moves
# stored ``real_tokens``/cost. Rows persisted by tracegauge.store carry it so a self-baseline can
# tell rows produced by an older (pre-dedupe) adapter from current ones.
#   1 = one usage per assistant record (over-counts: Claude Code writes one record per
#       content block of an API response, each repeating the response's usage)
#   2 = each distinct message.id counted once (per-field max over its records)
ADAPTER_VERSION: int = 2

# Bumped whenever the *cost* computed from a record changes without moving real_tokens, so
# stored session_cost_usd rows can be recognised as stale and re-scored by `tracegauge backfill-waste`.
# Kept apart from ADAPTER_VERSION on purpose: that one gates self_baseline/alarm_baseline and the
# shipped baseline provenance, and real_tokens did not change here.
#   1 = every cache write priced at the 5-minute rate
#   2 = cache writes split by usage.cache_creation into 5-minute and 1-hour (2x input) tiers
#   3 = web search ($10 per 1,000 searches), fast mode (usage.speed == "fast") and US-only
#       inference (usage.inference_geo == "us", 1.1x on 4.6+ models) priced; a row that carries
#       none of them costs the same under 2 and 3
COST_VERSION: int = 3


def _has_tool_use(content: list[Any]) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def _collapse_main_chain_usage(
    messages: list[dict[str, Any]],
) -> tuple[dict[int, tuple[list[int], dict[str, int], tuple[str, str]]], dict[str, int]]:
    """Resolve each API response's usage ONCE across the main-chain records it spans.

    Returns ``(carriers, stats)``. ``carriers`` maps the index (into ``messages``) of ONE
    record per response -- the *carrier* -- to ``([input, cache_creation, cache_read,
    output, cache_creation_1h, tier_known], server_tool_use, (speed, inference_geo))``: the
    per-field maximum over all records sharing the
    response's ``_message_dedup_key`` (on real data every record of a response repeats an
    identical usage, so max == first == last; max is also the safe choice for streaming
    partials whose ``output_tokens`` grow). Every other record of the response is a
    zero-usage turn. ``speed`` / ``inference_geo`` are properties of the response, identical
    on every record of it; the value of the last usage-carrying record is taken.

    Why the turn structure is NOT merged: waste detectors, proof-turn indices, the
    ``turn_count`` scope gates and ``edit_operations`` all work on one turn per record, and
    ``tracegauge.attribution`` charges a flagged turn the tokens it carries. So only the usage is
    deduped. The carrier is the LAST record holding a ``tool_use`` block (else the last
    record): redundant-read / retry waste is detected on tool-calling turns and must be
    charged the response's tokens, not zero.
    """
    groups: dict[str, dict[str, Any]] = {}
    usage_records = 0
    for i, msg in enumerate(messages):
        if not _is_main_chain(msg) or msg.get("type") != "assistant":
            continue
        message = msg.get("message", {})
        content = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            continue  # the main loop skips these records too
        usage = message.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        if usage:
            usage_records += 1
        g = groups.setdefault(
            _message_dedup_key(msg),
            {
                "counts": [0] * 6,
                "stu": {},
                "mods": ("", ""),
                "last": i,
                "tool": None,
                "has_usage": False,
            },
        )
        # counts = [input, cache_creation, cache_read, output, cache_creation_1h, tier_known]
        row = [*_parse_usage(usage), *_parse_cache_tiers(usage)]
        g["counts"] = [max(a, b) for a, b in zip(g["counts"], row, strict=True)]
        stu = _parse_server_tool_use(usage) or {}
        g["stu"] = {k: max(g["stu"].get(k, 0), stu.get(k, 0)) for k in {*g["stu"], *stu}}
        if usage:
            g["mods"] = _parse_billing_modifiers(usage)
        g["last"] = i
        g["has_usage"] = g["has_usage"] or bool(usage)
        if _has_tool_use(content):
            g["tool"] = i
    carriers = {
        (g["tool"] if g["tool"] is not None else g["last"]): (g["counts"], g["stu"], g["mods"])
        for g in groups.values()
    }
    distinct = sum(1 for g in groups.values() if g["has_usage"])
    stats = {
        "usage_records": usage_records,
        "usage_records_deduped": distinct,
        "duplicate_usage_records": usage_records - distinct,
    }
    return carriers, stats


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file, skipping blank/malformed lines (a live file may end mid-write)."""
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


UNKNOWN_AGENT_TYPE = "unknown"
_AGENT_TYPE_MAX_LEN = 64  # bound a hostile/garbled meta file; real names are far shorter


def read_agent_type(agent_file: Path) -> str:
    """Subagent type from the sibling ``agent-<id>.meta.json``, or ``"unknown"``.

    Tolerates a missing file, bad JSON, a non-dict payload and a non-string or empty
    ``agentType``. Control characters are stripped and the result is capped at 64 chars.
    """
    meta = agent_file.with_suffix(".meta.json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return UNKNOWN_AGENT_TYPE
    if not isinstance(data, dict):
        return UNKNOWN_AGENT_TYPE
    raw = data.get("agentType")
    if not isinstance(raw, str):
        return UNKNOWN_AGENT_TYPE
    cleaned = "".join(ch for ch in raw if ch.isprintable()).strip()[:_AGENT_TYPE_MAX_LEN]
    return cleaned or UNKNOWN_AGENT_TYPE


def collect_subagent_usage(session_path: Path) -> dict[str, Any] | None:
    """Roll up the usage of a session's subagent transcripts, or None if it has none.

    Layout (verified on real Claude Code data): the parent ``<project>/<sid>.jsonl`` has a
    sibling directory ``<project>/<sid>/subagents/agent-<agentId>.jsonl``; the parent's own
    transcript records the launch (``toolUseResult.agentId``) but NOT the subagent's token
    usage, so adding the subagent files to the parent does not double count.

    Every record in a subagent file has ``isSidechain: true``, which is why the main-chain
    adapter reads them as empty. Here each API response is counted once (records sharing a
    ``message.id`` are merged by taking the per-field maximum, see ``_message_dedup_key``).

    Returns ``{"file_count", "message_count", "real_tokens", "billed_tokens", "by_type",
    "turns"}``; ``by_type`` maps each subagent type (``agentType`` of the sibling meta file,
    ``"unknown"`` when absent) to ``{file_count, message_count, real_tokens, billed_tokens}``.
    ``turns`` are one TurnDigest-shaped dict per (subagent file, model, speed, inference_geo)
    -- cost and attribution are linear in tokens, so aggregating loses nothing, but fast mode
    and the US-only multiplier are per-request properties that scale the whole request, so a
    request is never summed with one that has a different (speed, geo): each turn carries one
    uniform ``speed`` / ``inference_geo``. A subagent file with no fast or US request yields
    exactly the per-(file, model) turns it always did.
    """
    if is_subagent_path(session_path):
        return None
    sub_dir = session_path.with_suffix("") / SUBAGENTS_DIRNAME
    if not sub_dir.is_dir():
        return None

    turns: list[dict[str, Any]] = []
    file_count = 0
    message_count = 0
    by_type: dict[str, dict[str, int]] = {}
    for agent_file in sorted(sub_dir.glob("agent-*.jsonl")):
        try:
            records = _read_jsonl(agent_file)
        except OSError:
            continue
        # message key -> (model, [input, cache_creation, cache_read, output, cache_creation_1h,
        # tier_known], server_tool_use, (speed, inference_geo))
        per_message: dict[str, tuple[str, list[int], dict[str, int], tuple[str, str]]] = {}
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            counts = [*_parse_usage(usage), *_parse_cache_tiers(usage)]
            stu = _parse_server_tool_use(usage) or {}
            mods = _parse_billing_modifiers(usage)
            key = _message_dedup_key(rec)
            if key in per_message:
                model, prev, prev_stu, _prev_mods = per_message[key]
                counts = [max(a, b) for a, b in zip(prev, counts, strict=True)]
                stu = {k: max(prev_stu.get(k, 0), stu.get(k, 0)) for k in {*prev_stu, *stu}}
                per_message[key] = (model or str(message.get("model", "")), counts, stu, mods)
            else:
                per_message[key] = (str(message.get("model", "")), counts, stu, mods)

        # Aggregation key: (model, speed, inference_geo) -- see the docstring.
        by_model: dict[tuple[str, str, str], list[int]] = {}
        stu_by_model: dict[tuple[str, str, str], dict[str, int]] = {}
        requests_by_model: dict[tuple[str, str, str], int] = {}
        for model, counts, stu, (speed, geo) in per_message.values():
            if not any(counts[:4]):
                continue  # zero-usage records (e.g. synthetic client-side messages)
            agg = (model, speed, geo)
            acc = by_model.setdefault(agg, [0] * 6)
            for i, c in enumerate(counts):
                acc[i] += c
            message_count += 1
            requests_by_model[agg] = requests_by_model.get(agg, 0) + 1
            tot = stu_by_model.setdefault(agg, {})
            for k, v in stu.items():
                tot[k] = tot.get(k, 0) + v
        if not by_model:
            continue
        file_count += 1
        agent_type = read_agent_type(agent_file)
        tstats = by_type.setdefault(
            agent_type,
            {"file_count": 0, "message_count": 0, "real_tokens": 0, "billed_tokens": 0},
        )
        tstats["file_count"] += 1
        for agg, (inp, cache_cr, cache_rd, out, cache_1h, tier_known) in sorted(by_model.items()):
            model, speed, geo = agg
            turns.append(
                {
                    "role": "ai",
                    "tool_names": [],
                    "content_snippet": "",
                    "token_count_input": inp + cache_cr + cache_rd,
                    "token_count_output": out,
                    "cache_read": cache_rd,
                    "h2_duplicate": False,
                    "cache_creation": cache_cr,
                    "model": model,
                    "server_tool_use": stu_by_model.get(agg) or None,
                    "request_count": requests_by_model[agg],
                    "cache_creation_1h": cache_1h,
                    "cache_creation_tier_known": tier_known,
                    "speed": speed,
                    "inference_geo": geo,
                    "agent_type": agent_type,
                }
            )
            tstats["message_count"] += requests_by_model[agg]
            tstats["billed_tokens"] += inp + cache_cr + cache_rd + out
            tstats["real_tokens"] += inp + cache_cr + out

    if not turns:
        return None
    billed = sum(t["token_count_input"] + t["token_count_output"] for t in turns)
    real = sum(t["token_count_input"] - t["cache_read"] + t["token_count_output"] for t in turns)
    return {
        "file_count": file_count,
        "message_count": message_count,
        "real_tokens": real,
        "billed_tokens": billed,
        "by_type": by_type,
        "turns": turns,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def adapt_session(session_path: Path) -> dict[str, Any]:
    """Convert a single Claude Code session JSONL file to a layer1_outputs record.

    This is a direct port of scripts/adapters/claudecode_adapter.adapt_session.
    Secret redaction is ON by default.

    Parameters
    ----------
    session_path:
        Path to the Claude Code session JSONL file.

    Returns
    -------
    dict
        A record matching the layer1_outputs.jsonl schema, with a populated
        ``digest`` field (SessionDigest serialised via dataclasses.asdict).
    """
    session_id: str = session_path.stem

    raw_lines: list[str] = session_path.read_text(encoding="utf-8").split("\n")
    messages: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = _sanitize_record(json.loads(line))
        except (json.JSONDecodeError, RecursionError):
            record = None
            print(
                f"[adapter] WARNING: skipping malformed JSON line in {session_path.name}",
                file=sys.stderr,
            )
        else:
            if record is None:
                print(
                    f"[adapter] WARNING: skipping a line that is not a JSON object in {session_path.name}",
                    file=sys.stderr,
                )
        if record is not None:
            messages.append(record)

    # --- Session-level accumulation ------------------------------------------
    turns: list[TurnDigest] = []
    turn_index: int = 0
    task_description: str = "N/A"
    task_description_set: bool = False

    # Running sums over assistant messages for session-level stats
    sum_input: int = 0
    sum_cache_creation: int = 0
    sum_cache_read: int = 0
    sum_output: int = 0

    # Edit operations, accumulated across the whole session -- a
    # sibling to `digest`, not a TurnDigest field (see
    # _extract_edit_operations_from_content's docstring for why).
    edit_operations: list[dict[str, Any]] = []

    usage_carriers, usage_stats = _collapse_main_chain_usage(messages)

    for msg_idx, msg in enumerate(messages):
        if not _is_main_chain(msg):
            continue

        msg_type: str = msg.get("type", "")

        if msg_type == "assistant":
            message: dict[str, Any] = msg.get("message", {})
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            # Usage is charged once per API response, on its carrier record (see
            # _collapse_main_chain_usage); sibling content-block records get zero usage.
            counts, stu, (speed, geo) = usage_carriers.get(msg_idx, ([0] * 6, {}, ("", "")))
            inp, cache_cr, cache_rd, out, cache_1h, tier_known = counts
            server_tool_use = stu or None
            model_str: str = message.get("model", "")

            sum_input += inp
            sum_cache_creation += cache_cr
            sum_cache_read += cache_rd
            sum_output += out

            tool_names: list[str] = _extract_tool_names_from_content(content)
            snippet: str = _redact_secrets(_extract_text_snippet(content))
            edit_operations.extend(_extract_edit_operations_from_content(content))

            # input_tokens in TurnDigest = all tokens billed for this call
            turn_input: int = inp + cache_cr + cache_rd
            turn_output: int = out

            turns.append(
                TurnDigest(
                    turn_index=turn_index,
                    role="ai",
                    tool_names=tool_names,
                    content_snippet=snippet,
                    token_count_input=turn_input,
                    token_count_output=turn_output,
                    cache_read=cache_rd,
                    h2_duplicate=False,
                    cache_creation=cache_cr,
                    model=model_str,
                    server_tool_use=server_tool_use,
                    cache_creation_1h=cache_1h,
                    cache_creation_tier_known=tier_known,
                    speed=speed,
                    inference_geo=geo,
                )
            )
            turn_index += 1

        elif msg_type == "user":
            message = msg.get("message", {})
            if message.get("role") != "user":
                continue

            content = message.get("content", "")

            if isinstance(content, str):
                stripped: str = content.strip()
                if not stripped:
                    continue
                # Skip system-reminder injections at the very start before any AI turn
                if not task_description_set and stripped.startswith("<system-reminder>"):
                    continue

                snippet = _redact_secrets(stripped[:_SNIPPET_MAX_CHARS])

                if not task_description_set:
                    task_description = _redact_secrets(stripped[:_TASK_DESC_MAX_CHARS])
                    task_description_set = True

                turns.append(
                    TurnDigest(
                        turn_index=turn_index,
                        role="user",
                        tool_names=[],
                        content_snippet=snippet,
                        token_count_input=0,
                        token_count_output=0,
                        cache_read=0,
                        h2_duplicate=False,
                    )
                )
                turn_index += 1

            elif isinstance(content, list):
                has_tool_result: bool = any(
                    isinstance(item, dict) and item.get("type") == "tool_result" for item in content
                )
                if not has_tool_result:
                    continue

                snippet = _redact_secrets(_extract_tool_result_snippet(content))
                if not snippet.strip():
                    continue

                turns.append(
                    TurnDigest(
                        turn_index=turn_index,
                        role="tool",
                        tool_names=[],
                        content_snippet=snippet,
                        token_count_input=0,
                        token_count_output=0,
                        cache_read=0,
                        h2_duplicate=False,
                    )
                )
                turn_index += 1

    # --- Session-level derived metrics ---------------------------------------
    total_billed: int = sum_input + sum_cache_creation + sum_cache_read
    total_tokens: int = total_billed + sum_output
    cache_hit_rate: float = sum_cache_read / max(1, total_billed)
    turn_count: int = len(turns)

    # Subagent spend: kept OUT of `turns` (waste detectors, judge text and the verdict's
    # real_tokens stay main-chain only) and carried on the digest for cost/attribution.
    subagent = collect_subagent_usage(session_path)
    subagent_turns: list[TurnDigest] = []
    if subagent is not None:
        for i, t in enumerate(subagent["turns"]):
            subagent_turns.append(TurnDigest(turn_index=turn_count + i, **t))

    digest = SessionDigest(
        session_id=session_id,
        domain="unknown",
        resolved=False,
        total_tokens=total_tokens,
        turn_count=turn_count,
        h2_duplicate_count=0,
        cache_hit_rate=cache_hit_rate,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description=task_description,
        turns=turns,
        subagent_turns=subagent_turns,
        subagent_count=subagent["file_count"] if subagent else 0,
        subagent_files_by_type=(
            {k: v["file_count"] for k, v in subagent["by_type"].items()} if subagent else {}
        ),
        usage_records=usage_stats["usage_records"],
        usage_records_deduped=usage_stats["usage_records_deduped"],
    )

    return {
        "session_id": session_id,
        "domain_id": "unknown",
        "test_outcome": False,
        "total_tokens": total_tokens,
        "turn_count": turn_count,
        "h2_duplicate_count": 0,
        "cache_hit_rate": cache_hit_rate,
        "p25_token_ratio": 1.0,
        "labeler_model": "not_applicable",
        "scaffold": "claude_code",
        "output_tokens_available": True,
        "digest": dataclasses.asdict(digest),
        "token_economy_available": False,
        "domain_inferred": "fallback_unknown",
        "edit_operations": edit_operations,
        "adapter_version": ADAPTER_VERSION,
        "cost_version": COST_VERSION,
        "usage_dedupe": usage_stats,
        "subagent_usage": (
            {k: v for k, v in subagent.items() if k != "turns"}
            if subagent
            else {"file_count": 0, "message_count": 0, "real_tokens": 0, "billed_tokens": 0}
        ),
    }


__all__ = ["ADAPTER_VERSION", "COST_VERSION", "adapt_session", "collect_subagent_usage"]
