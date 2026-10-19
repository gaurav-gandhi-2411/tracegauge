# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""`tracegauge rescore` command: flags, JSON document, text output and exit codes."""

import json
from pathlib import Path

import pytest
from tracegauge import cli
from tracegauge.store import open_db

from tests.legacy_store import MixedStore, build_mixed_store


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["tracegauge", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def mixed(tmp_path: Path) -> MixedStore:
    return build_mixed_store(tmp_path)


def test_cli_json_dry_run_then_real_and_exit_code(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(
        monkeypatch, capsys, "rescore", "--dry-run", "--json", "--db-path", str(mixed.db)
    )
    doc = json.loads(out)
    assert code == 4  # the unparseable row would fail
    assert doc == {
        "schema_version": 1,
        "command": "rescore",
        "dry_run": True,
        "limit": None,
        "legacy_rows": 4,
        "rescored": 2,
        "skipped_source_missing": 1,
        "skipped_empty_stub": 0,
        "failed": 1,
        "not_attempted": 0,
        "remaining_legacy": 4,
        "legacy_rows_excluded": 4,
    }
    code, out, _ = _run(monkeypatch, capsys, "rescore", "--json", "--db-path", str(mixed.db))
    doc = json.loads(out)
    assert (code, doc["rescored"], doc["remaining_legacy"]) == (4, 2, 2)


def test_cli_text_and_all_clear_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s = build_mixed_store(tmp_path)
    conn = open_db(s.db)
    conn.execute(
        "DELETE FROM sessions WHERE session_id IN (?, ?)", (s.unparseable, s.unrecoverable)
    )
    conn.commit()
    conn.close()
    code, out, _ = _run(monkeypatch, capsys, "rescore", "--db-path", str(s.db))
    assert code == 0
    assert "rescored:" in out and "skipped (source missing):  0" in out
    code, out, _ = _run(monkeypatch, capsys, "rescore", "--db-path", str(s.db))  # idempotent
    assert code == 0 and "Legacy sessions: 0" in out


def test_cli_usage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(monkeypatch, capsys, "rescore", "--db-path", str(tmp_path / "none.db"))
    assert code == 1 and "No tracegauge store" in err
    assert not (tmp_path / "none.db").exists()  # rescore never creates a store
    s = build_mixed_store(tmp_path)
    code, _, err = _run(monkeypatch, capsys, "rescore", "--limit", "0", "--db-path", str(s.db))
    assert code == 1 and "--limit" in err
