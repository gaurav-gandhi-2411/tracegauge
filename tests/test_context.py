# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The multi-developer context line: a labelled percentile, never a verdict.

Hermetic: tables here are synthetic; the shipped table is only checked for shape.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from tracegauge import context as ctxmod
from tracegauge.context import context_for_result, context_line, session_context
from tracegauge.report import format_human, format_json
from tracegauge.store import open_db, upsert_session

from tests.test_web_banner_copy import _app, _get
from tests.test_web_patterns import _make_result

GRID = {str(p): 100 + p * 10 for p in range(5, 100, 5)}  # 150, 200, ..., 1050
_FORBIDDEN = {"above_p75", "below_p25", "within_band", "above the band", "below the band"}


def _table(**cells: dict[str, Any]) -> dict[str, Any]:
    ok = {"available": True, "n": 120, "developers": 30, "grid": GRID}
    types = {"debug-fix": ok, "ml-eval": {"available": False, "n": 19, "developers": 8}}
    types.update(cells)
    return {
        "schema": 2,
        "unit": {"name": "real_tokens_per_turn"},
        "population": {
            "source": "SALT-NLP/SWE-chat, synthetic",
            "dataset_version": "v0",
            "date_range": ["2026-01-01", "2026-02-01"],
        },
        "types": types,
    }


def test_percentile_is_interpolated_on_the_grid() -> None:
    t = _table()
    at = lambda tokens_per_turn: session_context("debug-fix", tokens_per_turn * 10, 10, t)  # noqa: E731
    assert at(600)["percentile"] == 50  # exactly on the grid
    assert at(605)["percentile"] == 50  # 0.5 of a 10-token step rounds to the same point
    assert at(650)["percentile"] == 55
    assert at(625)["percentile"] == 52 or at(625)["percentile"] == 53  # between 50 and 55
    assert at(100)["range"] == "below_p5" and at(100)["percentile"] is None
    assert at(5000)["range"] == "above_p95" and at(5000)["percentile"] is None


def test_flat_run_of_the_grid_reports_its_middle() -> None:
    flat = dict(GRID, **{"40": 500, "45": 500, "50": 500})
    t = _table(**{"debug-fix": {"available": True, "n": 50, "developers": 9, "grid": flat}})
    assert session_context("debug-fix", 5000, 10, t)["percentile"] == 45


def test_unpublished_cells_and_missing_inputs_give_no_context() -> None:
    t = _table()
    assert session_context("debug-fix", 6000, 10, t) is not None  # control
    assert session_context("ml-eval", 6000, 10, t) is None  # cell failed publication
    assert session_context("feature-build", 6000, 10, t) is None  # no such cell
    assert session_context("debug-fix", 0, 10, t) is None
    assert session_context("debug-fix", 6000, 0, t) is None


def test_text_is_labelled_context_with_population_and_notice_and_has_no_band_words() -> None:
    ctx = session_context("debug-fix", 6500, 10, _table())
    line = context_line(ctx)
    assert "Not a verdict" in line and "30 developers" in line and "120 sessions" in line
    assert "2026-01-01 to 2026-02-01" in line and "ODC Attribution License 1.0" in line
    assert "SWE-chat v0" in line and "each developer weighted equally" in line
    assert not any(w in line for w in _FORBIDDEN)
    assert "about the 55th percentile" in line
    assert "above the 95th percentile of" in context_line(
        session_context("debug-fix", 99999, 10, _table())
    )


@pytest.mark.parametrize(
    "n,expected",
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (11, "11th"), (21, "21st"), (52, "52nd"), (95, "95th")],
)
def test_ordinals(n: int, expected: str) -> None:
    assert ctxmod._ordinal(n) == expected


def test_shipped_table_shape_and_no_ids() -> None:
    t = json.loads(ctxmod.MULTIDEV_BASELINES_PATH.read_text(encoding="utf-8"))
    assert (
        t["schema"] == ctxmod.SCHEMA
        and "Open Data Commons Attribution License 1.0" in t["attribution"]
    )
    assert t["types"]["feature-build"]["available"] is False  # never published
    assert "scope_gates" not in t
    for name, cell in t["types"].items():
        if cell["available"]:
            assert cell["developers"] >= 5 and cell["n"] >= 10, name
            vals = [cell["grid"][str(p)] for p in range(5, 100, 5)]
            assert len(vals) == 19 and vals == sorted(vals), name
        else:
            assert "grid" not in cell and "median" not in cell, name
    assert not {"dev", "session_id", "user_id", "path"} & set(
        json.dumps(t).replace('"', " ").split()
    )


def _result(task_type: str, tokens: int, turns: int):
    r = _make_result("ctx-0001-aaaa-bbbb-cccc-dddddddddddd", real_tokens=tokens)
    r.task_type, r.turn_count = task_type, turns
    return r


def test_cli_text_and_json_carry_the_context_for_a_published_type() -> None:
    table = ctxmod.load_context_table()
    assert table is not None
    r = _result("debug-fix", 80_000, 100)
    assert context_for_result(r) is not None
    assert "Context:" in format_human(r) and "Not a verdict" in format_human(r)
    d = json.loads(format_json(r))
    assert set(d["context"]) == {
        "unit", "value", "percentile", "range", "task_type", "developers", "sessions", "source",
        "dataset_version", "date_range", "attribution", "note",
    }  # fmt: skip
    assert d["context"]["task_type"] == "debug-fix" and d["context"]["value"] == 800


def test_cli_hides_the_context_for_feature_build() -> None:
    for t in ("feature-build",):
        r = _result(t, 80_000, 100)
        assert "Context:" not in format_human(r)
        assert json.loads(format_json(r))["context"] is None


def test_dashboard_detail_shows_the_context_and_building_progress(tmp_path: Path) -> None:
    app = _app(tmp_path, 3)  # three stored sessions of debug-fix with 80 turns each
    sid = "banner-0001-aaaa-bbbb-cccc-dddddddddddd"
    conn0 = open_db(tmp_path / "banner.db")
    conn0.execute("UPDATE sessions SET baseline_source = 'building'")
    conn0.commit()
    conn0.close()
    html = _get(app, f"/session/{sid}")
    assert "Context" in html and "Not a verdict" in html
    assert "building (3/10" in html
    conn = open_db(tmp_path / "banner.db")
    res = _result("feature-build", 50_000, 80)
    upsert_session(conn, res, "/fake/fb.jsonl", 1.0, "h", turn_count=80)
    conn.close()
    assert "Not a verdict" not in _get(app, f"/session/{res.session_id}")
