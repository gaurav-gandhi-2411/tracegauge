# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/codex_adapt.py — Codex CLI rollout JSONL adapter (groundwork, NOT wired in).

``adapt_codex_session(path)`` returns the same "layer 1 record" dict as
``tracegauge.adapt.adapt_session`` does for Claude Code, so ``reconstruct_digest`` and
``tracegauge.cost.compute_session_cost`` work on it.

It is deliberately NOT called from the CLI, ``tracegauge.discovery`` or any other module: how a
path is routed to an adapter needs an agreed design first (docs/ADAPTERS.md, section (a)). The
format notes it is built on (openai/codex @ 2f761ae, with file:line) are in docs/ADAPTERS.md,
"Codex CLI". It was written from that source and hand-made synthetic files, then checked against
real 0.162.1 / 0.165.0 rollouts (synthetic tasks, local model); what those files settled and what
is still UNVERIFIED is listed there.

Public API:
    adapt_codex_session(path: Path) -> dict
    CodexCompressedSessionError
"""

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from tracegauge._digest import SessionDigest, TurnDigest
from tracegauge.adapt import (
    _SNIPPET_MAX_CHARS,
    _TASK_DESC_MAX_CHARS,
    COST_VERSION,
    _int_or_zero,
    _redact_secrets,
)

# Bumped whenever the token accounting of ``adapt_codex_session`` changes. Independent of
# tracegauge.adapt.ADAPTER_VERSION: the two adapters evolve separately.
#   1 = per-call ``last_token_usage`` from each ``token_count`` event whose cumulative total
#       changed; ``input_tokens`` split into fresh / cached / cache-write (see _split_usage)
#   2 = a token_count that follows a tool output is charged to the ai turn that made the call
#       (real 0.162.1 / 0.165.0 order), not to a new empty turn; session totals unchanged
CODEX_ADAPTER_VERSION: int = 2

# User messages Codex injects before the real task. <environment_context> is seen in real
# 0.162.1 / 0.165.0 rollouts; the other two are UNVERIFIED (from memory of the format). They are
# skipped for task_description only while no real user message has been seen, like the
# system-reminder skip in tracegauge.adapt.
_INJECTED_CONTEXT_PREFIXES: tuple[str, ...] = (
    "<environment_context>",
    "<user_instructions>",
    "# AGENTS.md",
)

_OUTPUT_ITEM_TYPES: frozenset[str] = frozenset({"function_call_output", "custom_tool_call_output"})


class CodexCompressedSessionError(ValueError):
    """Raised for a ``*.jsonl.zst`` rollout.

    Codex's compression worker (off by default, files at least 7 days old) writes zstd files.
    Decompressing them needs a zstd library, which tracegauge does not depend on, so the adapter
    refuses instead of returning an empty or partial record. Decompress the file first
    (``zstd -d``) and pass the resulting ``.jsonl``.
    """


def _warn(message: str) -> None:
    print(f"[adapter] WARNING: {message}", file=sys.stderr)


def _obj(value: Any) -> dict[str, Any]:
    """``value`` if it is a JSON object, else an empty dict (so every later ``.get`` is safe)."""
    return value if isinstance(value, dict) else {}


def _split_usage(usage: dict[str, Any]) -> tuple[int, int, int, int]:
    """Return (fresh_input, cache_read, cache_write, output) from one TokenUsage object.

    Codex's ``input_tokens`` INCLUDES the cached part: the source computes non-cached input as
    ``input_tokens - cached_input_tokens`` (codex-rs/protocol/src/protocol.rs:2550-2552) and the
    Responses API parser reads ``cached_tokens`` and ``cache_write_tokens`` out of
    ``input_tokens_details`` (codex-rs/codex-api/src/sse/responses.rs:130-134, test at :846-867
    where input 100 = cached 40 + write 60 and total 110 = input + output). So fresh input is
    ``input - cached - write``, clamped at 0 for a malformed record, and the digest's
    ``token_count_input`` (fresh + read + write) equals ``input_tokens`` for a well-formed one.
    If a malformed record has ``input_tokens`` below ``cached + write`` the turn is made consistent
    the conservative way: the digest's input becomes ``cached + write`` (``fresh`` is 0), so
    cache_read and cache_creation never exceed ``token_count_input`` and no cached token is
    dropped from the bill. Huge values are not capped.
    ``output_tokens`` already includes reasoning tokens (``reasoning_output_tokens`` is a subset,
    shown as "output=X (reasoning Y)" at protocol.rs:2622-2631), so it is never added again.
    """
    inp = max(0, _int_or_zero(usage.get("input_tokens")))
    read = max(0, _int_or_zero(usage.get("cached_input_tokens")))
    write = max(0, _int_or_zero(usage.get("cache_write_input_tokens")))
    out = max(0, _int_or_zero(usage.get("output_tokens")))
    return max(0, inp - read - write), read, write, out


def _text_of_content(content: Any) -> str:
    """Concatenate the text of a message ``content`` list (``input_text`` / ``output_text``)."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        item = _obj(item)
        if item.get("type") in ("input_text", "output_text") and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def _output_text(output: Any) -> str:
    """Text of a ``*_output`` item: a plain string, or a list of content items on the wire."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "".join(
            str(_obj(i).get("text", "")) for i in output if isinstance(_obj(i).get("text"), str)
        )
    return (
        _output_text(output["content"]) if isinstance(output, dict) and "content" in output else ""
    )


def _tool_name(payload: dict[str, Any]) -> str | None:
    """Tool name of a call item, or ``None`` if it is not a tool call."""
    kind = payload.get("type")
    if kind in ("function_call", "custom_tool_call"):
        name = payload.get("name")
        return name if isinstance(name, str) and name else None
    if kind == "local_shell_call":
        return "local_shell"
    if kind == "web_search_call":
        return "web_search"
    return None


def adapt_codex_session(path: Path) -> dict[str, Any]:
    """Convert one Codex CLI rollout ``.jsonl`` file to a layer-1 record.

    Not wired into the CLI or discovery (see the module docstring).

    Usage is read from ``event_msg`` / ``token_count`` events: ``info.last_token_usage`` is the
    usage of one model call and is charged to one ``ai`` turn; ``info.total_token_usage`` is
    cumulative and is only used to recognise a repeated event (unchanged total => ignored). The
    two are never added together. The model of a turn is the latest ``turn_context.model``,
    copied as written. A blank, invalid or non-object line is skipped with a warning.

    Raises
    ------
    CodexCompressedSessionError
        If ``path`` is a ``.zst`` file.
    UnicodeDecodeError
        If the file is not valid UTF-8 (same contract as ``adapt_session``).
    """
    if path.suffix == ".zst":
        raise CodexCompressedSessionError(
            f"{path.name} is zstd-compressed; tracegauge has no zstd dependency. "
            "Decompress it (zstd -d) and pass the .jsonl file."
        )
    session_id: str = path.stem

    turns: list[TurnDigest] = []
    task_description = "N/A"
    task_description_set = False
    model = ""  # latest turn_context.model; "" before any turn_context (priced as unknown)
    forked = False

    # Assistant content seen since the last model-call boundary; attached to the next ai turn.
    pending_text: list[str] = []
    pending_tools: list[str] = []
    # Index of the ai turn that a tool output flushed before its token_count arrived. Real
    # rollouts (0.162.1, 0.165.0) write call -> call output -> token_count, so that call's usage
    # belongs on this turn, not on a new empty one.
    unclaimed: list[int] = []

    # Cumulative total_tokens only grows within one counter (append_last_usage,
    # protocol.rs:2431). An event whose total equals the baseline (the total of the last counted
    # event) is a repeat and is ignored. A total BELOW the baseline is treated as a counter reset
    # (restart, compaction) and counted, with the baseline moved down: dropping it would silently
    # lose every later call, while a replay miscounted as a reset is over-counted but caveated.
    baseline: int | None = None
    prev_key: tuple[int, int, int] | None = None  # fallback key when total_tokens is unusable
    decreasing_total_events = 0  # events counted as a reset because the total went DOWN
    models_seen: list[str] = []
    usage_records = 0  # token_count events that carried usage
    usage_records_deduped = 0  # distinct cumulative totals among them
    sum_input = sum_read = sum_write = sum_output = 0

    def add_turn(
        role: str, snippet: str, tools: list[str], usage: tuple[int, int, int, int]
    ) -> None:
        fresh, read, write, out = usage
        turns.append(
            TurnDigest(
                turn_index=len(turns),
                role=role,
                tool_names=tools,
                content_snippet=_redact_secrets(snippet[:_SNIPPET_MAX_CHARS]),
                token_count_input=fresh + read + write,
                token_count_output=out,
                cache_read=read,
                h2_duplicate=False,
                cache_creation=write,
                model=model if role == "ai" else "",
                # Codex does not say how long a cache write lives: tier unknown, so the cost
                # module prices the write at its floor (5-minute) rate.
                cache_creation_1h=0,
                cache_creation_tier_known=0,
            )
        )

    def flush_pending() -> None:
        """Emit assistant content that no token_count claimed as a zero-usage ai turn."""
        if pending_text or pending_tools:
            add_turn("ai", "".join(pending_text), list(pending_tools), (0, 0, 0, 0))
            # The latest flushed call: if an earlier call never got its token_count, the next
            # one still belongs to the call just before it.
            unclaimed[:] = [len(turns) - 1]
            pending_text.clear()
            pending_tools.clear()

    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            _warn(f"skipping malformed JSON line in {path.name}")
            continue
        if not isinstance(raw, dict):
            _warn(f"skipping a line that is not a JSON object in {path.name}")
            continue

        kind = raw.get("type")
        payload = _obj(raw.get("payload"))

        if kind == "session_meta":
            forked = forked or bool(payload.get("forked_from_id"))
        elif kind == "turn_context":
            if isinstance(payload.get("model"), str):
                model = payload["model"]
                if model not in models_seen:
                    models_seen.append(model)
        elif kind == "response_item":
            ptype = payload.get("type")
            if ptype == "message":
                role = payload.get("role")
                text = _text_of_content(payload.get("content"))
                if role == "assistant":
                    pending_text.append(text)
                elif role == "user" and text.strip():
                    stripped = text.strip()
                    if not task_description_set and stripped.startswith(_INJECTED_CONTEXT_PREFIXES):
                        continue
                    flush_pending()
                    unclaimed.clear()  # a user message starts a new model call
                    if not task_description_set:
                        task_description = _redact_secrets(stripped[:_TASK_DESC_MAX_CHARS])
                        task_description_set = True
                    add_turn("user", stripped, [], (0, 0, 0, 0))
            elif ptype in _OUTPUT_ITEM_TYPES:
                text = _output_text(payload.get("output"))
                if text.strip():
                    flush_pending()
                    add_turn("tool", text, [], (0, 0, 0, 0))
            else:
                name = _tool_name(payload)
                if name is not None:
                    pending_tools.append(name)
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = _obj(payload.get("info"))
            total = _obj(info.get("total_token_usage"))
            last = _obj(info.get("last_token_usage"))
            if not total or not last:
                continue  # rate-limit-only event (info is null): no usage
            usage_records += 1
            raw_total = total.get("total_tokens")
            if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                prev_key = None
                if baseline is not None and raw_total == baseline:
                    continue  # repeated cumulative total: already counted
                if baseline is not None and raw_total < baseline:
                    decreasing_total_events += 1
                    _warn(f"cumulative total went down in {path.name}; counted as a counter reset")
                baseline = raw_total
            else:
                # No usable total: compare (total, input, output) with the previous event only.
                _warn(f"token_count without a numeric total_tokens in {path.name}")
                key = (
                    _int_or_zero(raw_total),
                    _int_or_zero(total.get("input_tokens")),
                    _int_or_zero(total.get("output_tokens")),
                )
                baseline = None
                if key == prev_key:
                    continue
                prev_key = key
            usage_records_deduped += 1
            usage = _split_usage(last)
            sum_input += usage[0]
            sum_read += usage[1]
            sum_write += usage[2]
            sum_output += usage[3]
            if not (pending_text or pending_tools) and unclaimed:
                fresh, read, write, out = usage
                t = turns[unclaimed[0]]
                t.token_count_input, t.token_count_output = fresh + read + write, out
                t.cache_read, t.cache_creation = read, write
            else:
                add_turn("ai", "".join(pending_text), list(pending_tools), usage)
            unclaimed.clear()
            pending_text.clear()
            pending_tools.clear()

    flush_pending()

    caveats: list[str] = []
    if usage_records == 0:
        # Fail closed on money (invariant 8). Every ai turn here is zero-token, which the cost
        # module would price as a confident $0 whenever the model is in the price table. So the
        # model is blanked on the ai turns: tracegauge.cost cannot resolve "" and reports the
        # session as approximate ("empty model string -- cost unknown"). The real ids are kept
        # in ``models_seen`` and the caveat. ``usage_available`` says the same thing explicitly.
        for t in turns:
            t.model = ""
        caveats.append(
            "no token_count events: usage and cost unknown, not zero "
            f"(models seen: {', '.join(models_seen) or 'none'}; model blanked on ai turns)"
        )
        _warn(f"no token_count events in {path.name}; usage is unknown, not zero")
    if decreasing_total_events:
        caveats.append(
            "cumulative total went down and was counted as a counter reset; usage may be "
            "over-counted if it was a replay rather than a reset"
        )
    if forked:
        # UNVERIFIED (third-party parser): a fork replays the parent's usage into the child file.
        caveats.append("forked session: replayed parent usage may be double counted")
        _warn(f"{path.name} is a forked session; replayed parent usage may be double counted")

    total_billed = sum_input + sum_read + sum_write
    total_tokens = total_billed + sum_output
    digest = SessionDigest(
        session_id=session_id,
        domain="unknown",
        resolved=False,
        total_tokens=total_tokens,
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=sum_read / max(1, total_billed),
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description=task_description,
        turns=turns,
        usage_records=usage_records,
        usage_records_deduped=usage_records_deduped,
    )
    return {
        "session_id": session_id,
        "domain_id": "unknown",
        "test_outcome": False,
        "total_tokens": total_tokens,
        "turn_count": len(turns),
        "h2_duplicate_count": 0,
        "cache_hit_rate": digest.cache_hit_rate,
        "p25_token_ratio": 1.0,
        "labeler_model": "not_applicable",
        "scaffold": "codex",
        "output_tokens_available": True,
        "digest": dataclasses.asdict(digest),
        "token_economy_available": False,
        "domain_inferred": "fallback_unknown",
        "edit_operations": [],  # apply_patch extraction is not implemented yet
        "adapter_version": CODEX_ADAPTER_VERSION,
        "cost_version": COST_VERSION,
        "usage_dedupe": {
            "usage_records": usage_records,
            "usage_records_deduped": usage_records_deduped,
            "duplicate_usage_records": usage_records - usage_records_deduped,
            "decreasing_total_events": decreasing_total_events,
        },
        "subagent_usage": {
            "file_count": 0,
            "message_count": 0,
            "real_tokens": 0,
            "billed_tokens": 0,
        },
        "usage_available": usage_records > 0,
        "models_seen": models_seen,
        "usage_caveats": caveats,
    }


__all__ = ["CODEX_ADAPTER_VERSION", "CodexCompressedSessionError", "adapt_codex_session"]
