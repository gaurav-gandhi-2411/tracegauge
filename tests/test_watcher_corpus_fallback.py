# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""A task type whose own baseline is still building is scored against the bundled band.

The docs promise it ("falls back to the bundled reference corpus until your self-baseline
activates"), but the watcher and the dashboard asked for the baseline without the fallback, so on
that path every session of a first-run user showed ``unavailable``. Only ``tracegauge score FILE``
used the bundled band. These tests pin the promise on the watcher and dashboard path.
"""

import json
from pathlib import Path
from unittest.mock import patch

from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.classify import classify_session
from tracegauge.self_baseline import load_for_scoring, load_or_compute
from tracegauge.store import (
    file_hash,
    needs_scoring,
    open_db,
    queue_rescore_of_hidden_verdicts,
    upsert_session,
)
from tracegauge.watcher import WatcherConfig, _scan_once, score_session_file

from tests.test_web_banner_copy import _app, _get
from tests.test_web_patterns import _make_result

_POOL = Path(__file__).parent / "fixtures" / "synthetic_pool.jsonl"


def _bands() -> dict:
    return load_baselines(BUNDLED_BASELINES_PATH)


def _active_types(b2: dict) -> list[str]:
    return [t for t, v in b2["types"].items() if v.get("available")]


def _in_scope_record(b2: dict) -> dict:
    """A pool record classified into a type with a bundled band, with a turn count past its gate."""
    for line in _POOL.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        t = classify_session(rec)
        gate = b2.get("scope_gates", {}).get(t, {}).get("p10_turns")
        if t in _active_types(b2) and gate is not None:
            rec["turn_count"] = gate + 50  # pool sessions are short; only the gate is under test
            return rec
    raise AssertionError("no synthetic pool record falls in a type with a bundled band")


def _empty_store(tmp_path: Path) -> Path:
    db = tmp_path / "tes.db"
    open_db(db).close()
    return db


def _score(rec: dict, state) -> object:
    with patch("tracegauge.watcher.adapt_session", return_value=rec):
        return score_session_file(Path("x.jsonl"), _bands(), self_baseline=state)


def test_empty_store_types_with_a_bundled_band_use_it(tmp_path: Path) -> None:
    b2 = _bands()
    state = load_for_scoring(_empty_store(tmp_path), b2)
    for t, v in b2["types"].items():
        src = state.by_type[t].source
        assert src == ("corpus" if v.get("available") else "building"), (t, src)
    assert any(tb.source == "corpus" for tb in state.by_type.values())


def test_control_without_the_fallback_every_type_is_building(tmp_path: Path) -> None:
    # What the callers did before: this is the behaviour the fix removes from the scoring path.
    state = load_or_compute(_empty_store(tmp_path), _bands())
    assert {tb.source for tb in state.by_type.values()} == {"building"}


def test_watcher_path_gives_the_bundled_verdict_when_the_gate_passes(tmp_path: Path) -> None:
    b2 = _bands()
    rec = _in_scope_record(b2)
    db = _empty_store(tmp_path)
    got = _score(rec, load_for_scoring(db, b2))
    assert got.baseline_source == "corpus"
    assert got.band_verdict in {"within_band", "above_p75", "below_p25"}
    assert got.baseline_available is True
    # Same record, same answer as the plain `score FILE` path (no self-baseline given).
    plain = _score(rec, None)
    assert plain.band_verdict == got.band_verdict


def test_control_building_state_hides_the_available_bundled_verdict(tmp_path: Path) -> None:
    b2 = _bands()
    rec = _in_scope_record(b2)
    got = _score(rec, load_or_compute(_empty_store(tmp_path), b2))
    assert got.baseline_source == "building"
    assert got.band_verdict == "unavailable"


def test_turn_gate_still_applies_on_the_bundled_band(tmp_path: Path) -> None:
    b2 = _bands()
    rec = dict(_in_scope_record(b2))
    rec["turn_count"] = 1
    got = _score(rec, load_for_scoring(_empty_store(tmp_path), b2))
    assert got.band_verdict == "unavailable"
    assert got.scope_status == "out_of_scope"


def test_type_without_a_bundled_band_stays_unavailable(tmp_path: Path) -> None:
    b2 = _bands()
    state = load_for_scoring(_empty_store(tmp_path), b2)
    missing = [t for t, v in b2["types"].items() if not v.get("available")]
    assert missing, "the bundled corpus is expected to ship at least one inactive cell"
    for t in missing:
        assert state.by_type[t].source == "building"
        assert state.by_type[t].p25 is None


def test_a_cache_written_without_the_fallback_is_not_served_with_it(tmp_path: Path) -> None:
    b2 = _bands()
    db = _empty_store(tmp_path)
    old = load_or_compute(db, b2)  # writes self_baseline_cache.json: every type "building"
    assert {tb.source for tb in old.by_type.values()} == {"building"}
    new = load_for_scoring(db, b2)
    assert any(tb.source == "corpus" for tb in new.by_type.values())
    # And the other way round: the second call is served from its own cache, not recomputed wrongly.
    again = load_for_scoring(db, b2)
    assert {t: tb.source for t, tb in again.by_type.items()} == {
        t: tb.source for t, tb in new.by_type.items()
    }


def test_dashboard_baseline_status_shows_the_bundled_fallback(tmp_path: Path) -> None:
    html = _get(_app(tmp_path, 3), "/baseline-status")
    assert "corpus fallback" in html
    assert "building" in html  # the cell the bundled corpus has no band for


def _seed(db: Path) -> None:
    """Three stored rows: a hidden verdict, a normal one, and a type the bundled corpus lacks."""
    conn = open_db(db)
    for sid, ttype, src, verdict in (
        ("hidden-aaaa", "debug-fix", "building", "unavailable"),
        ("normal-bbbb", "debug-fix", "corpus", "within_band"),
        ("noband-cccc", "feature-build", "building", "unavailable"),
    ):
        res = _make_result(sid, real_tokens=1000)
        res.task_type, res.baseline_source, res.band_verdict = ttype, src, verdict
        upsert_session(conn, res, f"/fake/{sid}.jsonl", 1.0, f"hash-{sid}", turn_count=80)
    conn.close()


def test_rows_that_hid_a_bundled_verdict_are_queued_once(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    _seed(db)
    conn = open_db(db)
    assert queue_rescore_of_hidden_verdicts(conn, ["debug-fix", "ml-eval"]) == 1
    assert needs_scoring(conn, "hidden-aaaa", "hash-hidden-aaaa")
    assert not needs_scoring(conn, "normal-bbbb", "hash-normal-bbbb")  # control: untouched
    assert not needs_scoring(conn, "noband-cccc", "hash-noband-cccc")  # no bundled band: untouched
    assert queue_rescore_of_hidden_verdicts(conn, ["debug-fix", "ml-eval"]) == 0  # idempotent
    assert queue_rescore_of_hidden_verdicts(conn, []) == 0
    conn.close()


def test_a_queued_row_is_scored_again_with_the_bundled_band(tmp_path: Path) -> None:
    b2 = _bands()
    rec = _in_scope_record(b2)
    db = tmp_path / "e2e.db"
    conn = open_db(db)
    rec["session_id"] = "e2e-aaaa"  # the pool record carries its own id; the row to rescore is this
    ttype = classify_session(rec)
    res = _make_result("e2e-aaaa", real_tokens=1000)
    res.task_type, res.baseline_source, res.band_verdict = ttype, "building", "unavailable"
    jsonl = tmp_path / "proj" / "e2e-aaaa.jsonl"
    jsonl.parent.mkdir()
    jsonl.write_bytes(b"{}")
    # The stored hash matches the file, so only the queue can make the scan score it again.
    upsert_session(conn, res, str(jsonl), 1.0, file_hash(jsonl), turn_count=80)
    cfg = WatcherConfig(cc_path=tmp_path, stability_window=0, db_path=db)
    with patch("tracegauge.watcher.adapt_session", return_value=rec):
        before = _scan_once(cfg, conn, b2, self_baseline=load_for_scoring(db, b2), _now=1e12)
    assert before == 0  # control: not queued, not scored again
    assert queue_rescore_of_hidden_verdicts(conn, _active_types(b2)) == 1
    with patch("tracegauge.watcher.adapt_session", return_value=rec):
        scored = _scan_once(cfg, conn, b2, self_baseline=load_for_scoring(db, b2), _now=1e12)
    assert scored == 1
    row = conn.execute(
        "SELECT baseline_source, band_verdict FROM sessions WHERE session_id = 'e2e-aaaa'"
    ).fetchone()
    assert row["baseline_source"] == "corpus"
    assert row["band_verdict"] != "unavailable"
    conn.close()
