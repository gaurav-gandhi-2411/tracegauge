# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The dashboard's coverage banner states what is true of THIS store.

It used to say "Self-baseline active on all 5 task types" whatever the store held, and to print a
historical session count from the maintainer's own corpus ("Pre-self-baseline (B2 corpus)"). The
banner now counts the task types whose baseline is actually your own, and shows no number that
the reader's own sessions did not produce.
"""

from pathlib import Path

import pytest
from tracegauge.adapt import ADAPTER_VERSION, COST_VERSION
from tracegauge.store import open_db, upsert_session
from tracegauge.web.server import ServerConfig, create_app

from tests.test_web_patterns import _make_result


def _app(tmp_path: Path, n_sessions: int):
    db = tmp_path / "banner.db"
    conn = open_db(db)
    for i in range(n_sessions):
        sid = f"banner-{i:04d}-aaaa-bbbb-cccc-dddddddddddd"
        upsert_session(
            conn,
            _make_result(sid, real_tokens=1000 + i * 10),
            f"/fake/{sid}.jsonl",
            float(i),
            f"hash-{i}",
            turn_count=80,
        )
    # Rows scored by the current accounting (the self-baseline ignores older ones).
    conn.execute(
        "UPDATE sessions SET adapter_version = ?, cost_version = ?", (ADAPTER_VERSION, COST_VERSION)
    )
    conn.commit()
    conn.close()
    app = create_app(ServerConfig(db_path=db))
    app.config["TESTING"] = True
    return app


def _get(app, path: str) -> str:
    with app.test_client() as c:
        return c.get(path).get_data(as_text=True)


@pytest.mark.parametrize("path", ["/", "/baseline-status"])
def test_no_historical_corpus_count_is_shown(tmp_path: Path, path: str) -> None:
    html = _get(_app(tmp_path, 3), path)
    assert "B2 corpus" not in html and "Pre-self-baseline" not in html


def test_banner_says_no_type_is_active_when_none_is(tmp_path: Path) -> None:
    html = _get(_app(tmp_path, 3), "/")
    assert "Self-baseline active on 0 of 5 task types" in html
    assert "all 5 task types" not in html


def test_banner_counts_the_types_that_are_active(tmp_path: Path) -> None:
    # Negative control for the count: enough waste-free sessions of one type activate it.
    html = _get(_app(tmp_path, 24), "/")
    assert "Self-baseline active on 1 of 5 task types" in html
    assert "on 0 of 5" not in html
