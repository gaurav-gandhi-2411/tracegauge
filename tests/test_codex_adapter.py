# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Codex CLI adapter (groundwork, not wired into the CLI).

Fixtures under tests/fixtures/codex/ are hand-written and synthetic, in the record shapes of
openai/codex @ 2f761ae (see docs/ADAPTERS.md). Expected totals are derived by hand in each test.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from tracegauge._digest import reconstruct_digest
from tracegauge.codex_adapt import (
    CODEX_ADAPTER_VERSION,
    CodexCompressedSessionError,
    adapt_codex_session,
)
from tracegauge.cost import compute_session_cost, load_price_table

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def _prices() -> dict[str, Any]:
    """Bundled table plus two synthetic models (the bundled table has no OpenAI models)."""
    prices = copy.deepcopy(load_price_table())
    prices["models"]["gpt-synth-a"] = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 4.0}
    prices["models"]["gpt-synth-b"] = {"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 8.0}
    return prices


def _ai(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in record["digest"]["turns"] if t["role"] == "ai"]


def _cost(record: dict[str, Any]) -> Any:
    return compute_session_cost(reconstruct_digest(record["digest"]), _prices())


def test_happy_path_totals_hand_computed() -> None:
    record = adapt_codex_session(FIXTURES / "normal.jsonl")
    # Call 1: input 1000 = fresh 300 + cached 600 + cache-write 100 (input INCLUDES the cached and
    # written parts), output 50. Call 2: input 1500 = fresh 500 + cached 1000, output 80.
    ai = _ai(record)
    assert [(t["token_count_input"], t["cache_read"], t["cache_creation"]) for t in ai] == [
        (1000, 600, 100),
        (1500, 1000, 0),
    ]
    assert [t["token_count_output"] for t in ai] == [50, 80]  # reasoning is inside output
    assert record["total_tokens"] == 1000 + 50 + 1500 + 80 == 2630  # == final cumulative total
    assert record["digest"]["cache_hit_rate"] == pytest.approx(1600 / 2500)
    assert record["scaffold"] == "codex" and record["labeler_model"] == "not_applicable"
    assert record["adapter_version"] == CODEX_ADAPTER_VERSION and record["usage_available"]
    assert all(t["cache_creation_tier_known"] == 0 for t in ai)
    # Cost at 1.0 / 4.0 USD per Mtok, read x0.1, 5-minute write x1.25 (unknown tier => floor):
    # call 1: 300 + 600*0.1 + 100*1.25 + 50*4 = 685; call 2: 500 + 1000*0.1 + 80*4 = 920.
    cost = _cost(record)
    assert cost.total_usd == pytest.approx((685 + 920) / 1e6)
    assert not cost.approximate and cost.unpriced_models == []


def test_turn_structure_and_snippets() -> None:
    record = adapt_codex_session(FIXTURES / "normal.jsonl")
    turns = record["digest"]["turns"]
    assert [t["role"] for t in turns] == ["user", "ai", "tool", "ai"]
    assert turns[1]["tool_names"] == ["shell"] and turns[1]["content_snippet"] == "Listing now."
    assert turns[2]["content_snippet"] == "a.txt b.txt"
    assert record["digest"]["task_description"] == "List the files and summarise them"
    assert record["turn_count"] == 4


def test_duplicate_cumulative_events_counted_once() -> None:
    record = adapt_codex_session(FIXTURES / "duplicate_cumulative.jsonl")
    assert record["total_tokens"] == 2630  # same as normal.jsonl despite 2 repeated events
    assert len(_ai(record)) == 2
    assert record["usage_dedupe"] == {
        "usage_records": 4,
        "usage_records_deduped": 2,
        "duplicate_usage_records": 2,
    }
    assert _cost(record).total_usd == pytest.approx(1605 / 1e6)


def test_model_switch_priced_per_turn() -> None:
    record = adapt_codex_session(FIXTURES / "model_switch.jsonl")
    assert [t["model"] for t in _ai(record)] == ["gpt-synth-a", "gpt-synth-b"]
    # Call 2: input 800 = fresh 500 + cached 300, output 100, at 2.0 / 8.0 per Mtok:
    # 500*2 + 300*2*0.1 + 100*8 = 1860; call 1 is 685 as in the happy path.
    assert _cost(record).total_usd == pytest.approx((685 + 1860) / 1e6)


def test_malformed_lines_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    record = adapt_codex_session(FIXTURES / "malformed.jsonl")
    # invalid JSON, a truncated line, [1,2,3], "a string" and null are skipped; blank is silent.
    assert capsys.readouterr().err.count("skipping") == 5
    assert record["total_tokens"] == 1050
    assert [t["role"] for t in record["digest"]["turns"]] == ["user", "ai"]


def test_unknown_model_is_unpriced_not_zero() -> None:
    record = adapt_codex_session(FIXTURES / "unknown_model.jsonl")
    assert _ai(record)[0]["model"] == "gpt-mystery-9"  # copied as written
    cost = _cost(record)
    assert cost.unpriced_models == ["gpt-mystery-9"]
    assert cost.approximate and cost.turn_costs[0].priced is False


def test_session_without_token_count_is_marked_unknown_not_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = adapt_codex_session(FIXTURES / "no_token_count.jsonl")
    assert record["usage_available"] is False
    assert record["usage_caveats"] and "unknown, not zero" in record["usage_caveats"][0]
    assert record["usage_dedupe"]["usage_records"] == 0
    assert "no token_count events" in capsys.readouterr().err
    # Turn structure survives, with tool names; the zero-usage ai turns say nothing about money.
    assert [t["role"] for t in record["digest"]["turns"]] == ["user", "ai", "tool", "ai"]
    assert record["digest"]["turns"][1]["tool_names"] == ["shell"]


def test_zst_file_raises_documented_error(tmp_path: Path) -> None:
    p = tmp_path / "rollout-x.jsonl.zst"
    p.write_bytes(b"\x28\xb5\x2f\xfd")  # zstd magic; never decompressed
    with pytest.raises(CodexCompressedSessionError, match="zstd"):
        adapt_codex_session(p)


def test_assumption_input_includes_cached_and_reasoning_is_subset(tmp_path: Path) -> None:
    """Pins the mapping assumptions settled from source (protocol.rs:2550, responses.rs:846).

    If a real file shows ``input_tokens`` EXCLUDING cached tokens, this test is the one to change.
    """
    event = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"input_tokens": 100, "total_tokens": 110},
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "cache_write_input_tokens": 60,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 5,
                    "total_tokens": 110,
                },
            },
        },
    }
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(event) + "\n", encoding="utf-8")
    record = adapt_codex_session(p)
    turn = _ai(record)[0]
    assert (turn["token_count_input"], turn["token_count_output"]) == (100, 10)
    assert record["total_tokens"] == 110  # matches the source's own total_tokens


def test_hostile_input_never_raises(tmp_path: Path) -> None:
    """Wrong-typed fields, a secret, a huge message and a deeply nested line do not abort."""
    secret = "sk-ant-" + "A" * 40
    user_item = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": f"key {secret} " + "x" * 5000}],
    }
    usage_event = {
        "type": "token_count",
        "info": {
            "total_token_usage": {"input_tokens": "abc", "total_tokens": None},
            "last_token_usage": {
                "input_tokens": -5,
                "cached_input_tokens": 10**30,
                "output_tokens": True,
            },
        },
    }
    rows = [
        json.dumps({"type": "turn_context", "payload": {"model": 7}}),
        json.dumps({"type": "turn_context", "payload": "nope"}),
        json.dumps({"type": 5, "payload": []}),
        json.dumps({"type": "response_item", "payload": user_item}),
        json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": None}}),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": {"content": [1, None, {"text": "t"}]},
                },
            }
        ),
        json.dumps({"type": "event_msg", "payload": usage_event}),
        "[" * 100000,  # RecursionError in json.loads is treated as malformed
    ]
    p = tmp_path / "hostile.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    record = adapt_codex_session(p)
    user = record["digest"]["turns"][0]
    assert secret not in user["content_snippet"] and len(user["content_snippet"]) <= 300
    assert len(record["digest"]["task_description"]) <= 800
    assert record["total_tokens"] >= 0
    assert all(t["token_count_input"] >= 0 for t in _ai(record))
