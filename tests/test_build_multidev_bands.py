# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""scripts/build_multidev_bands.py: developer-clustered bands from a per-session extract.

Hermetic: the extracts here are synthetic; nothing reads the SWE-chat data.
"""

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_multidev_bands as bm  # noqa: E402


def _row(dev: str | None, tokens: int, ttype: str = "debug-fix", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "dev": dev,
        "task_type": ttype,
        "real_tokens": tokens,
        "turn_count": 1,  # so the unit (tokens per turn) equals the tokens given
        "usage_deduped": 100,
        "ds_api_calls": 100,
        "first_type": "file-history-snapshot",
        "created": "2026-03-01",
    }
    base.update(kw)
    return base


def _table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return bm.build_table(rows, reference_id="t", dataset_version="v0", source="synthetic")


def _devs(n_devs: int, per_dev: int, tokens: int, ttype: str = "debug-fix") -> list[dict[str, Any]]:
    return [
        _row(f"d{d}", tokens + d * 10 + s, ttype) for d in range(n_devs) for s in range(per_dev)
    ]


def test_prolific_developer_does_not_set_the_band() -> None:
    # Six developers with 3 modest sessions each, one with 300 huge sessions: pooled, the huge
    # developer is the median; clustered, they carry the weight of one developer.
    rows = _devs(6, 3, 1_000)
    rows += [_row("whale", 5_000_000 + i) for i in range(300)]
    cell = _table(rows)["types"]["debug-fix"]
    pooled_median = sorted(r["real_tokens"] for r in rows)[len(rows) // 2]
    assert pooled_median >= 5_000_000  # control: the unclustered median is the whale's
    assert cell["available"] and cell["median"] < 10_000


def test_cell_needs_enough_developers() -> None:
    few = _table(_devs(bm.MIN_DEVELOPERS - 1, 10, 1_000))["types"]["debug-fix"]
    assert few["available"] is False and "median" not in few
    enough = _table(_devs(bm.MIN_DEVELOPERS, 10, 1_000))["types"]["debug-fix"]
    assert enough["available"] is True  # control: one more developer activates it
    assert few["developers"] == bm.MIN_DEVELOPERS - 1 and enough["developers"] == bm.MIN_DEVELOPERS


def test_cell_needs_enough_sessions() -> None:
    cell = _table(_devs(bm.MIN_DEVELOPERS, 1, 1_000))["types"]["debug-fix"]  # 5 sessions
    assert cell["available"] is False


def test_unstable_band_is_not_published() -> None:
    # Five developers, three low and two 100x higher: the p75 sits on the boundary between the two
    # groups, so leaving one high developer out moves it from the high group to the low one.
    rows = _devs(3, 4, 1_000) + [_row(f"h{d}", 100_000 + s) for d in range(2) for s in range(4)]
    cell = _table(rows)["types"]["debug-fix"]
    assert cell["available"] is False
    assert cell["inactive_reason"] == "unstable_when_one_developer_is_left_out"
    assert "p25" not in cell and "median" not in cell and "ci95" not in cell
    stable = _table(_devs(6, 4, 1_000))["types"]["debug-fix"]  # control
    assert stable["available"] is True


def test_partial_and_unattributed_sessions_are_excluded_and_counted() -> None:
    rows = _devs(6, 3, 1_000)
    rows += [
        _row("d0", 9, first_type="summary"),
        _row("d0", 0),
        _row("d0", 9, usage_deduped=10, ds_api_calls=100),
        _row(None, 9),
    ]
    t = _table(rows)
    pop = t["population"]
    assert pop["sessions"] == 18 and pop["developers"] == 6
    assert pop["excluded_sessions"]["partial"] == 3
    assert pop["excluded_sessions"]["unattributed"] == 1
    assert pop["excluded_sessions"]["partial_by_reason"] == {
        "fewer_responses_than_recorded": 1,
        "no_usage": 1,
        "starts_with_summary": 1,
    }
    assert t["types"]["debug-fix"]["n"] == 18  # none of the excluded rows reached the cell


def test_published_table_holds_no_ids_and_no_per_developer_values() -> None:
    rows = _devs(6, 4, 1_000)
    text = json.dumps(_table(rows))
    for d in range(6):
        assert f'"d{d}"' not in text
    forbidden = {"dev", "developer_id", "session_id", "path", "user_id", "per_developer"}

    def keys(o: Any) -> set[str]:
        if isinstance(o, dict):
            return set(o) | {k for v in o.values() for k in keys(v)}
        if isinstance(o, list):
            return {k for v in o for k in keys(v)}
        return set()

    assert not (keys(_table(rows)) & forbidden)


def test_unit_is_tokens_per_turn_and_row_order_does_not_matter() -> None:
    rows = _devs(6, 4, 1_000)
    for r in rows:
        r["turn_count"] = 10  # 10 turns: the unit is a tenth of the tokens
    a, b = _table(rows), _table(list(reversed(rows)))
    assert a == b
    cell = a["types"]["debug-fix"]
    assert 90 <= cell["median"] <= 110  # about 1,000 tokens / 10 turns, not about 1,000
    assert a["unit"]["name"] == "real_tokens_per_turn" and "scope_gates" not in a


def test_published_cell_carries_a_monotone_percentile_grid() -> None:
    cell = _table(_devs(6, 4, 1_000))["types"]["debug-fix"]
    grid = cell["grid"]
    assert list(grid) == [str(p) for p in range(5, 100, 5)]
    vals = list(grid.values())
    assert vals == sorted(vals) and cell["p25"] <= cell["median"] <= cell["p75"]


def test_feature_build_is_never_published() -> None:
    t = _table(_devs(30, 10, 1_000, "feature-build"))["types"]["feature-build"]
    assert t["available"] is False and t["inactive_reason"] == "context_not_published_for_type"
    assert "median" not in t and "grid" not in t
    ok = _table(_devs(30, 10, 1_000, "debug-fix"))["types"]["debug-fix"]  # control: same data
    assert ok["available"] is True


def test_label_and_attribution_are_present() -> None:
    t = _table(_devs(6, 4, 1_000))
    assert t["label"].startswith("6 developers")
    assert "Open Data Commons Attribution License 1.0" in t["attribution"]
    assert t["population"]["date_range"] == ["2026-03-01", "2026-03-01"]


def test_table_does_not_depend_on_the_pseudonyms() -> None:
    rows = _devs(6, 4, 1_000)
    renamed = [{**r, "dev": "z" + str(9 - int(r["dev"][1:]))} for r in rows]
    assert _table(rows) == _table(renamed)
