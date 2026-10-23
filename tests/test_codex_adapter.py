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
        "decreasing_total_events": 0,
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


def test_no_token_count_session_is_approximate_never_confident_zero() -> None:
    record = adapt_codex_session(FIXTURES / "no_token_count.jsonl")
    assert record["models_seen"] == ["gpt-synth-a"]  # the real id survives outside the turns
    assert {t["model"] for t in _ai(record)} == {""}
    # With a price entry for the model, and with the bundled table: never a confident $0.
    for prices in (_prices(), load_price_table()):
        cost = compute_session_cost(reconstruct_digest(record["digest"]), prices)
        assert cost.approximate is True
        assert any("empty model string" in r for r in cost.approximate_reasons)


def _token_count_line(total: int, last_input: int, **extra: int) -> str:
    last = {"input_tokens": last_input, "output_tokens": 0, "total_tokens": last_input, **extra}
    info = {"total_token_usage": {"total_tokens": total}, "last_token_usage": last}
    return json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": info}})


def test_aba_replay_is_counted_as_a_reset_and_caveated(tmp_path: Path) -> None:
    """A, B, A: ASSUMPTION: a drop is a counter reset, so a real replay is over-counted but flagged.

    Silently ignoring a drop would lose all later usage after a real reset (see the next tests),
    which is the worse error for money.
    """
    p = tmp_path / "aba.jsonl"
    rows = [
        _token_count_line(1000, 1000),
        _token_count_line(1260, 260),
        _token_count_line(1000, 1000),
    ]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    record = adapt_codex_session(p)
    assert record["total_tokens"] == 1000 + 260 + 1000 == 2260
    assert record["usage_dedupe"]["usage_records_deduped"] == 3
    assert record["usage_dedupe"]["decreasing_total_events"] == 1
    assert any("over-counted" in c for c in record["usage_caveats"])


def test_reset_after_compaction_keeps_later_usage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Totals 100010, 320, 740: the drop is a reset; both later calls are counted."""
    p = tmp_path / "reset.jsonl"
    rows = [
        _token_count_line(100010, 100010),
        _token_count_line(320, 320),
        _token_count_line(740, 420),
        _token_count_line(740, 420),  # repeat of the new baseline: ignored
    ]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    record = adapt_codex_session(p)
    assert record["total_tokens"] == 100010 + 320 + 420 == 100750
    assert record["usage_dedupe"]["decreasing_total_events"] == 1
    assert "went down" in capsys.readouterr().err
    assert any("over-counted" in c for c in record["usage_caveats"])


def test_huge_corrupt_first_total_does_not_swallow_real_events(tmp_path: Path) -> None:
    p = tmp_path / "huge.jsonl"
    rows = [_token_count_line(10**12, 500), _token_count_line(300, 300)]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    record = adapt_codex_session(p)
    assert record["total_tokens"] == 500 + 300
    assert record["usage_caveats"]  # the drop is flagged, not silent


def test_events_without_total_tokens_are_not_collapsed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No numeric total_tokens: fall back to (total, input, output) vs the previous event."""

    def line(inp: int, out: int) -> str:
        info = {
            "total_token_usage": {"input_tokens": inp, "output_tokens": out},
            "last_token_usage": {"input_tokens": inp, "output_tokens": out},
        }
        return json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": info}})

    p = tmp_path / "nototal.jsonl"
    p.write_text("\n".join([line(100, 5), line(100, 5), line(250, 7)]) + "\n", encoding="utf-8")
    record = adapt_codex_session(p)
    assert record["total_tokens"] == (100 + 5) + (250 + 7) == 362  # the exact repeat is dropped
    assert "numeric total_tokens" in capsys.readouterr().err


def test_input_below_cached_part_is_made_consistent(tmp_path: Path) -> None:
    """Malformed input < cached + write: billed input becomes cached + write (conservative)."""
    p = tmp_path / "clamp.jsonl"
    p.write_text(
        _token_count_line(5, -100, cached_input_tokens=600, cache_write_input_tokens=50) + "\n",
        encoding="utf-8",
    )
    turn = _ai(adapt_codex_session(p))[0]
    assert turn["token_count_input"] == 650
    assert turn["cache_read"] + turn["cache_creation"] <= turn["token_count_input"]


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
