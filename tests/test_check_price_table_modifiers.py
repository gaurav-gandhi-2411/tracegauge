# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""scripts/check_price_table_vs_vendor.py: web search, fast mode and inference_geo.

The page fixture is the model table plus the "Data residency pricing", "Fast mode pricing" and
"Web search tool" sections copied verbatim from the saved official page (sha256
67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9, retrieved 2026-10-10). No
network.
"""

import copy
from pathlib import Path
from typing import Any

import pytest
from scripts.check_price_table_vs_vendor import (
    FetchError,
    compare_modifiers,
    main,
    model_version,
    parse_vendor_modifiers,
)
from tracegauge.cost import load_price_table

_PAGE = Path(__file__).parent / "fixtures" / "pricing" / "anthropic_pricing_2026-10-09_excerpt.md"
_PAGE_TEXT = _PAGE.read_text(encoding="utf-8")
_BUNDLED = Path(__file__).resolve().parent.parent / "tracegauge" / "data" / "prices.json"


def _table() -> dict[str, Any]:
    return copy.deepcopy(load_price_table(_BUNDLED))


def test_parses_web_search_fast_rates_and_the_residency_rule() -> None:
    mods = parse_vendor_modifiers(_PAGE_TEXT)
    assert mods.web_search_per_1000 == 10.0
    assert mods.fast == {
        "claude-opus-5-5": (8.0, 40.0),
        "claude-opus-5": (10.0, 50.0),  # one page row names "Claude Opus 5 / Claude Opus 4.8"
        "claude-opus-4-8": (10.0, 50.0),
    }
    assert (mods.geo_us_multiplier, mods.geo_min_version) == (1.1, (4, 6))


def test_a_usd_label_in_the_price_cells_still_parses() -> None:
    # The live page began writing "$8 USD / MTok" in the fast-mode table (2026-10-19).
    labelled = _PAGE_TEXT.replace(" / MTok", " USD / MTok")
    assert labelled != _PAGE_TEXT
    assert parse_vendor_modifiers(labelled) == parse_vendor_modifiers(_PAGE_TEXT)


@pytest.mark.parametrize(
    "wording",
    [
        "$10 USD per 1,000 searches",  # the live page from 2026-10-26 (scheduled run failed)
        "$10.00 USD per 1,000 searches",
        "$10 per 1000 searches",
    ],
)
def test_a_usd_label_in_the_web_search_price_still_parses(wording: str) -> None:
    labelled = _PAGE_TEXT.replace("$10 per 1,000 searches", wording)
    assert labelled != _PAGE_TEXT
    assert parse_vendor_modifiers(labelled).web_search_per_1000 == 10.0


def test_a_usd_labelled_second_web_search_price_is_still_ambiguous() -> None:
    md = _PAGE_TEXT + "\nAlso $12 USD per 1,000 searches elsewhere.\n"
    with pytest.raises(FetchError, match="not found exactly once"):
        parse_vendor_modifiers(md)


def test_the_bundled_table_matches_the_page_modifiers() -> None:
    assert compare_modifiers(_table(), parse_vendor_modifiers(_PAGE_TEXT)) == ([], [])


def test_main_is_ok_on_the_saved_page_and_names_the_new_checks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--page", str(_PAGE)]) == 0
    assert "fast-mode rates" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("key", "version"),
    [
        ("claude-opus-4-6", (4, 6)),
        ("claude-opus-5-5", (5, 5)),
        ("claude-fable-5", (5, 0)),
        ("claude-sonnet-4", (4, 0)),
        ("claude-haiku-3-5", (3, 5)),
        ("claude-3-5-sonnet", (3, 5)),
        ("claude-3-opus", (3, 0)),
        ("gpt-4", None),
    ],
)
def test_model_version(key: str, version: tuple[int, int] | None) -> None:
    assert model_version(key) == version


def _problems(table: dict[str, Any]) -> list[str]:
    missing, mismatches = compare_modifiers(table, parse_vendor_modifiers(_PAGE_TEXT))
    return missing + mismatches


def test_wrong_web_search_price_is_caught() -> None:
    table = _table()
    table["server_tool_rates"]["web_search_usd_per_1000"] = 25.0
    assert _problems(table) == ["web search: ours=25.0 vs page=$10 per 1,000 searches"]
    del table["server_tool_rates"]
    assert len(_problems(table)) == 1


def test_wrong_or_missing_fast_rate_is_caught() -> None:
    table = _table()
    table["models"]["claude-opus-5-5"]["fast_output_usd_per_mtok"] = 50.0
    assert _problems(table) == ["claude-opus-5-5: fast output ours=50.0 vs page=$40 per MTok"]
    table = _table()
    del table["models"]["claude-opus-4-8"]["fast_input_usd_per_mtok"]
    assert any("claude-opus-4-8: fast input" in p for p in _problems(table))


def test_fast_rates_on_a_model_the_page_does_not_list_are_caught() -> None:
    # Opus 4.7 rejects speed=fast and Opus 4.6 bills standard: neither may carry fast rates.
    for key in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"):
        table = _table()
        table["models"][key]["fast_input_usd_per_mtok"] = 10.0
        table["models"][key]["fast_output_usd_per_mtok"] = 50.0
        assert any(
            f"{key}: has fast-mode rates the page does not list" in p for p in _problems(table)
        )


def test_geo_eligibility_follows_the_page_not_a_hard_coded_list() -> None:
    table = _table()
    del table["models"]["claude-sonnet-4-6"]["geo_us_multiplier"]  # 4.6 is eligible
    table["models"]["claude-haiku-4-5"]["geo_us_multiplier"] = 1.1  # 4.5 is not
    table["models"]["claude-opus-4-8"]["geo_us_multiplier"] = 1.2  # wrong value
    problems = _problems(table)
    assert any(p.startswith("claude-sonnet-4-6: geo_us_multiplier ours=None") for p in problems)
    assert any(p.startswith("claude-haiku-4-5: has geo_us_multiplier") for p in problems)
    assert any(p.startswith("claude-opus-4-8: geo_us_multiplier ours=1.2") for p in problems)
    assert len(problems) == 3


def test_a_page_that_moves_the_threshold_is_caught() -> None:
    md = _PAGE_TEXT.replace("For Claude 4.6 and later models", "For Claude 4.5 and later models")
    missing, mismatches = compare_modifiers(_table(), parse_vendor_modifiers(md))
    assert {p.split(":")[0] for p in mismatches} >= {
        "claude-haiku-4-5",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
    }


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("### Fast mode pricing", "### Fast mode"),  # section gone
        ("### Data residency pricing", "### Residency"),
        ("$10 per 1,000 searches", "contact sales for search pricing"),
        ("1.1x multiplier", "premium"),  # every statement of the number
    ],
)
def test_a_page_missing_a_section_fails_closed(old: str, new: str, tmp_path: Path) -> None:
    md = _PAGE_TEXT.replace(old, new)
    assert md != _PAGE_TEXT
    with pytest.raises(FetchError):
        parse_vendor_modifiers(md)
    page = tmp_path / "page.md"
    page.write_text(md, encoding="utf-8")
    assert main(["--page", str(page)]) == 1  # COULD NOT VERIFY, never a pass


def test_two_different_web_search_prices_on_the_page_are_ambiguous() -> None:
    md = _PAGE_TEXT + "\nAlso $12 per 1,000 searches elsewhere.\n"
    with pytest.raises(FetchError):
        parse_vendor_modifiers(md)
