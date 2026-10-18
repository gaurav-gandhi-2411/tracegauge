# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The store records each session's turn count, so the self-baseline can activate.

Regression: ``upsert_session`` defaulted ``turn_count`` to 0 and no caller passed it, so every
row held 0 and ``compute_self_baselines`` (which selects ``turn_count >= scope_floor``, or NULL
with ``scope_status = 'in_scope'``) never counted a row: every task type stayed 'building'.
Synthetic transcripts only (tests/churn_synth.py).
"""

import hashlib
import json
import random
import sqlite3
import time
from pathlib import Path

import pytest
from tracegauge import cli
from tracegauge.adapt import adapt_session
from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.judge import JudgeConfig
from tracegauge.report import format_json
from tracegauge.score import score_session
from tracegauge.self_baseline import compute_self_baselines
from tracegauge.store import backfill_turn_counts, open_db
from tracegauge.watcher import WatcherConfig, _scan_once

from tests.churn_synth import clean_chain
from tests.legacy_store import insert_row

N_TURNS = 90  # above every task type's turns floor, so the session counts toward the baseline
# The lean subset is the lower half of the outlier-capped waste-free sessions, so reaching
# min_lean_n (8) takes roughly twice as many sessions.
N_SESSIONS = 20


def _write_chain(path: Path, seed: int, n_turns: int = N_TURNS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = clean_chain(random.Random(seed), n_turns)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def _scan(tmp_path: Path, db: Path) -> None:
    conn = open_db(db)
    config = WatcherConfig(cc_path=tmp_path / "projects", stability_window=0, db_path=db)
    _scan_once(config, conn, load_baselines(BUNDLED_BASELINES_PATH), _now=time.time() + 999)
    conn.close()


def _rows(db: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
    finally:
        conn.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def scanned(tmp_path: Path) -> tuple[Path, list[Path]]:
    """A store populated through the watcher path with N_SESSIONS synthetic sessions."""
    files = [
        _write_chain(tmp_path / "projects" / "p" / f"s{i:02d}.jsonl", i) for i in range(N_SESSIONS)
    ]
    db = tmp_path / "tes.db"
    _scan(tmp_path, db)
    return db, files


# (a) both write paths store the record's turn count ---------------------------------------


def test_watcher_scan_stores_the_record_turn_count(scanned: tuple[Path, list[Path]]) -> None:
    db, files = scanned
    rows = _rows(db)
    assert len(rows) == len(files)
    for f in files:
        stored = next(r for r in rows if r["session_id"] == f.stem)["turn_count"]
        assert stored == adapt_session(f)["turn_count"]
        assert stored > 0  # the pre-fix value was 0


def test_cli_score_store_write_stores_the_record_turn_count(tmp_path: Path) -> None:
    f = _write_chain(tmp_path / "s.jsonl", 1)
    conn = open_db(tmp_path / "tes.db")
    cli.score_path(f, load_baselines(BUNDLED_BASELINES_PATH), JudgeConfig(), False, True, conn)
    stored = conn.execute("SELECT turn_count FROM sessions").fetchone()[0]
    conn.close()
    assert stored == adapt_session(f)["turn_count"] > 0


def test_a_record_without_turns_stores_zero_not_an_invented_count(tmp_path: Path) -> None:
    # Control: the fallback copies the record's count, it does not make one up.
    record = adapt_session(_write_chain(tmp_path / "s.jsonl", 1))
    record["turn_count"] = 0
    result = score_session(record, load_baselines(BUNDLED_BASELINES_PATH))
    assert result.turn_count == 0


def test_score_json_does_not_gain_a_turn_count_key(tmp_path: Path) -> None:
    record = adapt_session(_write_chain(tmp_path / "s.jsonl", 1))
    result = score_session(record, load_baselines(BUNDLED_BASELINES_PATH))
    assert result.turn_count > 0
    assert "turn_count" not in json.loads(format_json(result))


# (b) the self-baseline activates ----------------------------------------------------------


def test_self_baseline_activates_from_watcher_scored_sessions(
    scanned: tuple[Path, list[Path]],
) -> None:
    db, _ = scanned
    task_types = {r["task_type"] for r in _rows(db)}
    assert len(task_types) == 1, "synthetic chains must classify alike"
    state = compute_self_baselines(db, load_baselines(BUNDLED_BASELINES_PATH))
    tb = state.by_type[task_types.pop()]
    assert tb.source == "self"
    assert tb.lean_n >= 8  # lean subset = lower half of the waste-free sessions


def test_zero_turn_count_rows_leave_the_type_building(tmp_path: Path) -> None:
    # Control = the pre-fix store: same rows, turn_count 0 -> never selected -> 'building'.
    db = tmp_path / "tes.db"
    conn = open_db(db)
    for i in range(N_SESSIONS):
        insert_row(conn, f"s{i}", task_type="ml-eval", tokens=100_000 + i)
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    assert compute_self_baselines(db, baselines).by_type["ml-eval"].source == "self"
    conn.execute("UPDATE sessions SET turn_count = 0")
    conn.commit()
    conn.close()
    assert compute_self_baselines(db, baselines).by_type["ml-eval"].source == "building"


# (c) backfill ------------------------------------------------------------------------------


def test_backfill_fixes_only_eligible_rows_and_is_idempotent(tmp_path: Path) -> None:
    good = _write_chain(tmp_path / "good.jsonl", 1)
    db = tmp_path / "tes.db"
    conn = open_db(db)
    insert_row(conn, "eligible", source=str(good))
    insert_row(conn, "gone", source=str(tmp_path / "missing.jsonl"))
    insert_row(conn, "stub", source=str(good), tokens=0)
    insert_row(conn, "legacy_null", source=str(good))
    insert_row(conn, "bad", source=str(tmp_path / "bad.jsonl"))
    (tmp_path / "bad.jsonl").write_text("not json\n", encoding="utf-8")
    conn.execute("UPDATE sessions SET turn_count = 0 WHERE session_id != 'legacy_null'")
    conn.execute("UPDATE sessions SET turn_count = NULL WHERE session_id = 'legacy_null'")
    conn.commit()
    conn.close()
    before = {r["session_id"]: dict(r) for r in _rows(db)}

    summary = backfill_turn_counts(db)
    assert summary == {"updated": 1, "missing_source": 1, "errors": 1, "unchanged": 0}

    after = {r["session_id"]: dict(r) for r in _rows(db)}
    expected = adapt_session(good)["turn_count"]
    assert after["eligible"]["turn_count"] == expected > 0
    for sid in ("gone", "stub", "bad"):
        assert after[sid]["turn_count"] == 0
    assert after["legacy_null"]["turn_count"] is None
    for sid, row in before.items():  # nothing but turn_count may differ
        assert {k: v for k, v in after[sid].items() if k != "turn_count"} == {
            k: v for k, v in row.items() if k != "turn_count"
        }

    assert backfill_turn_counts(db)["updated"] == 0  # idempotent
    assert {r["session_id"]: dict(r) for r in _rows(db)} == after


def test_backfill_then_self_baseline_activates(scanned: tuple[Path, list[Path]]) -> None:
    db, _ = scanned
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET turn_count = 0")  # a store written by the buggy versions
    conn.commit()
    conn.close()
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    tt = _rows(db)[0]["task_type"]
    assert compute_self_baselines(db, baselines).by_type[tt].source == "building"
    assert backfill_turn_counts(db)["updated"] == N_SESSIONS
    assert compute_self_baselines(db, baselines).by_type[tt].source == "self"


def test_backfill_refreshes_a_self_baseline_cache_built_while_turn_counts_were_zero(
    scanned: tuple[Path, list[Path]],
) -> None:
    from tracegauge.self_baseline import load_or_compute

    db, _ = scanned
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET turn_count = 0")  # a store written by the buggy versions
    conn.commit()
    conn.close()
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    tt = _rows(db)[0]["task_type"]
    assert load_or_compute(db, baselines).by_type[tt].source == "building"  # writes the cache
    assert backfill_turn_counts(db)["updated"] == N_SESSIONS
    # No session was added, so only the turn counts changed: the cache must notice.
    assert load_or_compute(db, baselines).by_type[tt].source == "self"


# (d) rescore --dry-run ---------------------------------------------------------------------


def test_rescore_dry_run_writes_nothing_and_real_run_backfills(
    scanned: tuple[Path, list[Path]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, _ = scanned
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET turn_count = 0")
    conn.commit()
    conn.close()
    sha = _sha(db)

    assert cli._run_rescore(db_path=str(db), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "20 older session(s) would get their turn count recorded" in out
    assert _sha(db) == sha
    assert {r["turn_count"] for r in _rows(db)} == {0}

    assert cli._run_rescore(db_path=str(db)) == 0
    assert "20 older session(s) got their turn count recorded" in capsys.readouterr().out
    assert all(r["turn_count"] > 0 for r in _rows(db))
