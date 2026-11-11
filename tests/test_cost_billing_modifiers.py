# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Web search, fast mode and US-only inference are priced (COST_VERSION 3).

Every expected dollar figure is computed by hand from the per-MTok rates of the saved official
pricing page (sha256 67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9), with the
arithmetic in the comment above each test -- never by calling the code under test. Synthetic
records only.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import tracegauge
from tracegauge._digest import SessionDigest, TurnDigest
from tracegauge.adapt import adapt_session
from tracegauge.cost import compute_session_cost, compute_turn_cost
from tracegauge.discovery import SUBAGENTS_DIRNAME

PRICES: dict[str, Any] = json.loads(
    (Path(tracegauge.__file__).parent / "data" / "prices.json").read_text(encoding="utf-8")
)


def _turn(
    model: str,
    *,
    fresh: int = 0,
    created: int = 0,
    one_hour: int = 0,
    read: int = 0,
    out: int = 0,
    speed: str = "",
    geo: str = "",
    stu: dict[str, int] | None = None,
    requests: int = 1,
) -> TurnDigest:
    return TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=fresh + created + read,
        token_count_output=out,
        cache_read=read,
        h2_duplicate=False,
        cache_creation=created,
        model=model,
        server_tool_use=stu,
        request_count=requests,
        cache_creation_1h=one_hour,
        cache_creation_tier_known=created,
        speed=speed,
        inference_geo=geo,
    )


def _big(model: str, **kw: Any) -> TurnDigest:
    # 1,000,000 fresh / 1,000,000 written (400,000 of them 1h) / 2,000,000 read / 100,000 out.
    return _turn(
        model,
        fresh=1_000_000,
        created=1_000_000,
        one_hour=400_000,
        read=2_000_000,
        out=100_000,
        **kw,
    )


def _session(turns: list[TurnDigest], sub: list[TurnDigest] | None = None) -> SessionDigest:
    return SessionDigest(
        session_id="s",
        domain="t",
        resolved=True,
        total_tokens=0,
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="t",
        turns=turns,
        subagent_turns=sub or [],
    )


# --- web search -------------------------------------------------------------------------


def test_web_search_is_added_at_ten_dollars_per_thousand() -> None:
    # sonnet-4-6 ($3 in / $15 out): 1,000 fresh in = 1,000*3/1e6 = 0.003; 100 out = 100*15/1e6
    # = 0.0015; tokens 0.0045. 20 searches = 20 * $10/1000 = 0.20. Total 0.2045.
    tc = compute_turn_cost(
        _turn("claude-sonnet-4-6", fresh=1000, out=100, stu={"web_search_requests": 20}), PRICES
    )
    assert tc.total_usd == pytest.approx(0.2045, rel=1e-9)
    assert tc.web_search_cost == pytest.approx(0.20, rel=1e-9)
    assert (tc.web_search_requests, tc.server_tool_warning) == (20, "")
    # the token breakdown stays token-only
    assert tc.fresh_cost + tc.output_cost == pytest.approx(0.0045, rel=1e-9)


def test_web_search_is_not_scaled_by_fast_or_geo() -> None:
    # opus-5-5 fast+us, 5 searches only (no tokens): 5 * 0.01 = 0.05 flat.
    tc = compute_turn_cost(
        _turn("claude-opus-5-5", speed="fast", geo="us", stu={"web_search_requests": 5}), PRICES
    )
    assert tc.total_usd == pytest.approx(0.05, rel=1e-9)


def test_web_fetch_is_free_and_code_execution_is_still_warned() -> None:
    free = compute_turn_cost(
        _turn("claude-sonnet-4-6", fresh=1000, stu={"web_fetch_requests": 3}), PRICES
    )
    assert free.server_tool_warning == ""
    assert free.total_usd == pytest.approx(0.003, rel=1e-9)  # 1,000 * $3 / 1e6, nothing extra
    mixed = compute_turn_cost(
        _turn(
            "claude-sonnet-4-6",
            fresh=1000,
            stu={"web_search_requests": 1, "code_execution_requests": 2},
        ),
        PRICES,
    )
    assert "code_execution_requests" in mixed.server_tool_warning
    assert "web_search_requests" not in mixed.server_tool_warning
    # The explanation must not cite web search as an unpriced example once it is priced
    # (it did until 0.18.0: "e.g. web search is $10/1,000 searches").
    assert "web search" not in mixed.server_tool_warning
    assert "container time" in mixed.server_tool_warning
    assert mixed.total_usd == pytest.approx(0.003 + 0.01, rel=1e-9)


def test_table_without_a_web_search_rate_fails_closed() -> None:
    table = copy.deepcopy(PRICES)
    del table["server_tool_rates"]
    tc = compute_turn_cost(
        _turn("claude-sonnet-4-6", fresh=1000, stu={"web_search_requests": 4}), table
    )
    assert tc.total_usd == pytest.approx(0.003, rel=1e-9)  # not priced ...
    assert "web_search_requests" in tc.server_tool_warning  # ... and said so


# --- fast mode ---------------------------------------------------------------------------


def test_fast_opus_5_5_prices_all_four_token_types_at_fast_rates() -> None:
    # opus-5-5 fast: in $8, out $40, read 0.05x = $0.40, 5m write 1.25x = $10, 1h write 2x = $16.
    #   fresh 1M * 8                      = 8.00
    #   read  2M * 0.40                   = 0.80
    #   write 0.6M * 10 + 0.4M * 16       = 6.00 + 6.40 = 12.40
    #   out   0.1M * 40                   = 4.00     -> 25.20 (standard: 12.60)
    tc = compute_turn_cost(_big("claude-opus-5-5", speed="fast"), PRICES)
    assert tc.fresh_cost == pytest.approx(8.00, rel=1e-9)
    assert tc.cache_read_cost == pytest.approx(0.80, rel=1e-9)
    assert tc.cache_creation_cost == pytest.approx(12.40, rel=1e-9)
    assert tc.output_cost == pytest.approx(4.00, rel=1e-9)
    assert tc.total_usd == pytest.approx(25.20, rel=1e-9)
    assert (tc.fast_requests, tc.fast_unsupported_requests) == (1, 0)


@pytest.mark.parametrize(
    ("model", "in_rate", "out_rate"),
    [("claude-opus-5", 10.0, 50.0), ("claude-opus-4-8", 10.0, 50.0)],
)
def test_fast_opus_5_and_4_8_are_ten_and_fifty(model: str, in_rate: float, out_rate: float) -> None:
    # 1M fresh in + 100k out: 1M*10 = 10.00 + 0.1M*50 = 5.00 -> 15.00
    tc = compute_turn_cost(_turn(model, fresh=1_000_000, out=100_000, speed="fast"), PRICES)
    assert tc.total_usd == pytest.approx(15.00, rel=1e-9)


@pytest.mark.parametrize("model", ["claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"])
def test_fast_on_a_model_without_fast_rates_is_priced_standard_and_counted(model: str) -> None:
    # Opus 4.7 / 4.6 and Sonnet 4.6: $5 (sonnet $3) in. Opus: 1M*5 + 0.1M*25 = 5.00 + 2.50 = 7.50;
    # sonnet-4-6: 1M*3 + 0.1M*15 = 3.00 + 1.50 = 4.50.
    expected = 4.50 if "sonnet" in model else 7.50
    tc = compute_turn_cost(_turn(model, fresh=1_000_000, out=100_000, speed="fast"), PRICES)
    assert tc.total_usd == pytest.approx(expected, rel=1e-9)
    assert (tc.fast_requests, tc.fast_unsupported_requests) == (0, 1)


def test_only_the_three_page_models_have_fast_rates() -> None:
    with_fast = {k for k, v in PRICES["models"].items() if "fast_input_usd_per_mtok" in v}
    assert with_fast == {"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"}
    without = {k for k, v in PRICES["models"].items() if "fast_output_usd_per_mtok" not in v}
    assert with_fast.isdisjoint(without)


# --- inference geo -------------------------------------------------------------------------


def test_geo_us_multiplies_every_category_on_a_4_6_plus_model() -> None:
    # opus-4-8 standard: fresh 1M*5 = 5.00; read 2M*0.50 = 1.00; write 0.6M*6.25 + 0.4M*10 =
    # 3.75 + 4.00 = 7.75; out 0.1M*25 = 2.50 (16.25). x1.1: 5.50 / 1.10 / 8.525 / 2.75 = 17.875.
    tc = compute_turn_cost(_big("claude-opus-4-8", geo="us"), PRICES)
    assert tc.fresh_cost == pytest.approx(5.50, rel=1e-9)
    assert tc.cache_read_cost == pytest.approx(1.10, rel=1e-9)
    assert tc.cache_creation_cost == pytest.approx(8.525, rel=1e-9)
    assert tc.output_cost == pytest.approx(2.75, rel=1e-9)
    assert tc.total_usd == pytest.approx(17.875, rel=1e-9)
    assert (tc.geo_us_requests, tc.geo_us_unsupported_requests) == (1, 0)


def test_geo_us_multiplies_haiku_5_5_long_context_tier() -> None:
    # One request of 150,000 prompt tokens (> 100,000) -> long tier $0.50 in / $2.50 out, read
    # 0.1x = $0.05. 100k fresh * 0.50 = 0.05; 50k read * 0.05 = 0.0025; 10k out * 2.50 = 0.025
    # -> 0.0775. x1.1 = 0.08525.
    turn = _turn("claude-haiku-5-5", fresh=100_000, read=50_000, out=10_000)
    assert compute_turn_cost(turn, PRICES).total_usd == pytest.approx(0.0775, rel=1e-9)
    turn.inference_geo = "us"
    assert compute_turn_cost(turn, PRICES).total_usd == pytest.approx(0.08525, rel=1e-9)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-haiku-4-5", 1.50),  # 1M*1 + 0.1M*5
        ("claude-sonnet-4-5", 4.50),  # 1M*3 + 0.1M*15
        ("claude-opus-4-5", 7.50),  # 1M*5 + 0.1M*25
    ],
)
def test_geo_us_on_a_pre_4_6_model_is_priced_standard_and_counted(
    model: str, expected: float
) -> None:
    tc = compute_turn_cost(_turn(model, fresh=1_000_000, out=100_000, geo="us"), PRICES)
    assert tc.total_usd == pytest.approx(expected, rel=1e-9)
    assert (tc.geo_us_requests, tc.geo_us_unsupported_requests) == (0, 1)


def test_the_eligible_model_set_is_4_6_and_later() -> None:
    eligible = {k for k, v in PRICES["models"].items() if v.get("geo_us_multiplier") == 1.1}
    assert eligible == {
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-mythos-5",
        "claude-mythos-5-1",
    }


@pytest.mark.parametrize("geo", ["", "global", "not_available"])
def test_non_us_geo_values_are_standard(geo: str) -> None:
    # adapt normalises these to "", but a hand-built digest may carry the raw string.
    tc = compute_turn_cost(_turn("claude-opus-4-8", fresh=1_000_000, geo=geo), PRICES)
    assert tc.total_usd == pytest.approx(5.00, rel=1e-9)
    assert tc.geo_us_requests == 0


# --- stacking ------------------------------------------------------------------------------


def test_fast_and_geo_stack_multiplicatively_with_the_cache_multipliers() -> None:
    # opus-5-5 fast (25.20, see above) x 1.1 = 27.72.
    tc = compute_turn_cost(_big("claude-opus-5-5", speed="fast", geo="us"), PRICES)
    assert tc.total_usd == pytest.approx(27.72, rel=1e-9)
    # opus-4-8 fast $10/$50, read 0.1x = $1.00, 5m write $12.50, 1h write $20:
    #   fresh 10.00 + read 2.00 + write (0.6M*12.5 + 0.4M*20 = 7.50 + 8.00) + out 5.00 = 32.50;
    #   x1.1 = 35.75
    tc2 = compute_turn_cost(_big("claude-opus-4-8", speed="fast", geo="us"), PRICES)
    assert tc2.total_usd == pytest.approx(35.75, rel=1e-9)


# --- a session that uses none of it ----------------------------------------------------------


def test_a_session_with_none_of_them_is_unchanged_and_says_nothing_about_them() -> None:
    # opus-5-5 standard big turn = 12.60 (fresh 4.00 + read 0.40 + write 3.00+3.20 + out 2.00).
    sc = compute_session_cost(_session([_big("claude-opus-5-5")]), PRICES)
    assert sc.total_usd == pytest.approx(12.60, rel=1e-9)
    assert (sc.web_search_requests, sc.fast_requests, sc.geo_us_requests) == (0, 0, 0)
    assert (sc.fast_unsupported_requests, sc.geo_us_unsupported_requests) == (0, 0)
    for marker in ("Web search:", "Fast mode:", "US-only inference:"):
        assert marker not in sc.domain_of_validity
    assert (
        "Batch discount (0.5x) does not apply to Claude Code transcripts" in sc.domain_of_validity
    )


def test_session_notes_count_requests_and_name_the_unsupported_cases() -> None:
    main = [
        _big("claude-opus-5-5", speed="fast"),
        _turn("claude-haiku-4-5", fresh=1000, geo="us"),
        _turn("claude-opus-4-6", fresh=1000, speed="fast"),
        _turn("claude-sonnet-4-6", fresh=1000, stu={"web_search_requests": 7}),
    ]
    sub = [_turn("claude-opus-4-8", fresh=1000, speed="fast", geo="us", requests=3)]
    sc = compute_session_cost(_session(main, sub), PRICES)
    assert (sc.fast_requests, sc.fast_unsupported_requests) == (1 + 3, 1)
    assert (sc.geo_us_requests, sc.geo_us_unsupported_requests) == (3, 1)
    assert sc.web_search_requests == 7
    assert sc.web_search_usd == pytest.approx(0.07, rel=1e-9)
    note = sc.domain_of_validity
    assert "Web search: 7 search(es)" in note and "$0.07" in note
    assert "Fast mode: 4 request(s) priced at the model's fast-mode" in note
    assert "Fast mode: 1 request(s) asked for speed=fast on a model with no fast-mode rate" in note
    assert "US-only inference: 3 request(s) priced at 1.1x" in note
    assert "US-only inference: 1 request(s) carried inference_geo=us" in note
    assert "priced at the standard rate" in note


# --- end to end: per-request pricing inside a subagent aggregate --------------------------------


def _assistant(msg_id: str, usage: dict[str, Any], *, sidechain: bool) -> dict[str, Any]:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "uuid": msg_id,
        "message": {
            "id": msg_id,
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "x"}],
            "usage": usage,
        },
    }


def test_subagent_requests_are_priced_with_their_own_speed_and_geo(tmp_path: Path) -> None:
    def usage(**extra: Any) -> dict[str, Any]:
        return {"input_tokens": 1000, "output_tokens": 1000, **extra}

    parent = tmp_path / "proj" / "sid.jsonl"
    parent.parent.mkdir(parents=True)
    parent.write_text(json.dumps(_assistant("p1", usage(), sidechain=False)) + "\n", "utf-8")
    sub = tmp_path / "proj" / "sid" / SUBAGENTS_DIRNAME / "agent-a.jsonl"
    sub.parent.mkdir(parents=True)
    records = [
        _assistant("s1", usage(), sidechain=True),  # standard
        _assistant("s2", usage(speed="fast"), sidechain=True),  # fast
        _assistant("s3", usage(speed="fast", inference_geo="us"), sidechain=True),  # fast + us
    ]
    sub.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    from tracegauge._digest import reconstruct_digest

    digest = reconstruct_digest(adapt_session(parent)["digest"])
    sc = compute_session_cost(digest, PRICES)
    # opus-5-5 standard: 1000 in * $4 + 1000 out * $20 = 0.004 + 0.020 = 0.024
    # fast ($8/$40): 0.008 + 0.040 = 0.048; fast + us: 0.048 * 1.1 = 0.0528
    # subagent = 0.024 + 0.048 + 0.0528 = 0.1248; plus the standard main turn 0.024 = 0.1488.
    # (Priced as one standard aggregate it would be 3 * 0.024 = 0.072.)
    assert sc.subagent_usd == pytest.approx(0.1248, rel=1e-9)
    assert sc.total_usd == pytest.approx(0.1488, rel=1e-9)
    assert (sc.fast_requests, sc.geo_us_requests) == (2, 1)
