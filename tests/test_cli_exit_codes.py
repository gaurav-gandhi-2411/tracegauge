# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Exit codes are a contract (docs/EXIT_CODES.md): scripts and hooks branch on them.

`score` on a corrupt file used to print [ERROR] and exit 0; `monitor` printed [ALARM] and exit 0,
so there was no machine-readable pass/fail. These tests run the real CLI (subprocess for the
score paths, main() with the live-session seam stubbed for the alarm) and pin every code.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import tracegauge.cli as cli
from tracegauge.exit_codes import EXIT_ADAPT_ERROR, EXIT_ALARM, EXIT_OK, EXIT_USAGE, epilog
from tracegauge.store import open_db

from tests.legacy_store import build_mixed_store
from tests.test_alarm_measured import _live, _threshold_active, _threshold_disabled

GOOD_RECORDS: list[dict[str, Any]] = [
    {"type": "user", "message": {"role": "user", "content": "do the thing"}},
    {
        "type": "assistant",
        "message": {
            "id": "m0",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 20},
        },
    },
]


def _good(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in GOOD_RECORDS) + "\n", encoding="utf-8")
    return path


def _corrupt(path: Path) -> Path:
    """Invalid UTF-8: the adapter raises while reading it (a parse error, not an empty session)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not json\n\xff\xfe\x00 garbage\n{broken")
    return path


def _run(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home),
        "APPDATA": str(home),
        "TRACEGAUGE_DB_PATH": str(tmp_path / "tes.db"),
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "tracegauge", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )


def test_codes_are_distinct_and_stable() -> None:
    # Documented numbers: changing one is a breaking change to every script that branches on it.
    assert (EXIT_OK, EXIT_USAGE, EXIT_ALARM, EXIT_ADAPT_ERROR) == (0, 1, 3, 4)


def test_epilog_lists_every_requested_code() -> None:
    text = epilog(EXIT_OK, EXIT_ALARM)
    assert text.startswith("Exit codes:")
    assert "  3  monitor" in text
    assert "docs/EXIT_CODES.md" in text


def test_score_good_file_exits_zero(tmp_path: Path) -> None:
    proc = _run(tmp_path, "score", str(_good(tmp_path / "p" / "ok.jsonl")), "--no-judge")
    assert proc.returncode == EXIT_OK, proc.stderr


def test_score_corrupt_file_exits_adapt_error(tmp_path: Path) -> None:
    proc = _run(tmp_path, "score", str(_corrupt(tmp_path / "p" / "bad.jsonl")), "--no-judge")
    assert proc.returncode == EXIT_ADAPT_ERROR
    assert "[ERROR] Failed to adapt bad.jsonl" in proc.stderr
    assert "1 of 1 session(s) could not be parsed" in proc.stderr


def test_score_missing_path_exits_usage(tmp_path: Path) -> None:
    proc = _run(tmp_path, "score", str(tmp_path / "nope.jsonl"), "--no-judge")
    assert proc.returncode == EXIT_USAGE
    assert "Path not found" in proc.stderr


def test_score_directory_scores_the_rest_but_exits_adapt_error(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    _good(d / "a-good.jsonl")
    _corrupt(d / "b-bad.jsonl")
    _good(d / "c-good.jsonl")
    proc = _run(tmp_path, "score", str(d), "--no-judge", "--json")
    assert proc.returncode == EXIT_ADAPT_ERROR
    # both good sessions were still scored (two JSON documents on stdout)
    assert proc.stdout.count('"session_id"') == 2
    assert "1 of 3 session(s) could not be parsed" in proc.stderr
    assert "b-bad.jsonl" in proc.stderr
    assert "[ERROR]" not in proc.stdout  # JSON mode keeps stdout machine-clean


def test_score_directory_of_good_files_exits_zero(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    _good(d / "a.jsonl")
    _good(d / "b.jsonl")
    assert _run(tmp_path, "score", str(d), "--no-judge").returncode == EXIT_OK


def test_help_epilog_documents_the_codes(tmp_path: Path) -> None:
    out = _run(tmp_path, "score", "--help").stdout
    assert "Exit codes:" in out
    assert "  4  score: at least one session could not be parsed" in out
    assert "  3  monitor" not in out  # only the codes this command can return
    assert "  3  monitor" in _run(tmp_path, "monitor", "--help").stdout


def test_cost_with_unopenable_store_exits_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A directory where the DB file should be: open_db fails. Used to exit 0 after "[ERROR]".
    bad_db = tmp_path / "isdir.db"
    bad_db.mkdir()
    proc = _run(tmp_path, "cost", "--week", "--db-path", str(bad_db))
    assert proc.returncode == EXIT_USAGE
    assert "Cannot open tracegauge store" in proc.stderr


# ----------------------------------------------------------------------------- monitor


def _monitor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, live: Any, baseline: Any) -> int:
    import tracegauge.alarm as alarm_mod
    import tracegauge.live_monitor as lm

    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: Path("/fake/active.jsonl"))
    monkeypatch.setattr(lm, "score_live_session", lambda *a, **k: live)
    monkeypatch.setattr(alarm_mod, "threshold_for_live", lambda *a, **k: baseline)
    monkeypatch.setattr("sys.argv", ["tracegauge", "monitor"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return int(exc.value.code or 0)


def test_monitor_alarm_firing_exits_alarm_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _monitor(monkeypatch, tmp_path, _live(), _threshold_active())
    assert code == EXIT_ALARM
    assert "[ALARM]" in capsys.readouterr().out


def test_monitor_no_alarm_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _monitor(monkeypatch, tmp_path, _live(), _threshold_disabled())
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "No alarm" in out
    assert "alarm disabled: needs at least 10 of your sessions" in out  # the reason is shown


def test_monitor_without_active_session_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing is running, so there is nothing to alarm on. A SessionEnd hook or cron job that
    # runs `tracegauge monitor` must not read "no session" as a failure.
    import tracegauge.live_monitor as lm

    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["tracegauge", "monitor", "--cc-path", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == EXIT_OK
    assert "No active session" in capsys.readouterr().out


# ----------------------------------------------------------------------------- every documented case

DOC = Path(__file__).resolve().parent.parent / "docs" / "EXIT_CODES.md"


@pytest.fixture
def hermetic_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """main() in-process with the store, home and judge all redirected under tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: None)
    return tmp_path


def _main_code(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr("sys.argv", ["tracegauge", *argv])
    try:
        code = cli.main()
    except SystemExit as exc:
        code = exc.code  # type: ignore[assignment]
    return int(code or 0)


def test_every_row_of_exit_codes_md_matches_behaviour(
    hermetic_cli: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    t = hermetic_cli
    good = _good(t / "p" / "ok.jsonl")
    bad = _corrupt(t / "p" / "bad.jsonl")
    empty = t / "empty"
    empty.mkdir()
    isdir_db = t / "isdir.db"
    isdir_db.mkdir()
    for sub_dir in ("mixed", "gone"):
        (t / sub_dir).mkdir()
    legacy_db = build_mixed_store(t / "mixed").db  # holds one unparseable legacy row
    gone_db = build_mixed_store(t / "gone").db
    conn = open_db(gone_db)
    conn.execute(
        "DELETE FROM sessions WHERE source_path LIKE '%sess-%'"
    )  # leaves only the gone one
    conn.commit()
    conn.close()
    # (documented code, the doc row it comes from, argv)
    cases: list[tuple[int, str, list[str]]] = [
        (0, "scored, nothing notable", ["score", str(good), "--no-judge"]),
        (0, "monitor with no active session", ["monitor", "--cc-path", str(empty)]),
        (1, "path not found", ["score", str(t / "nope.jsonl"), "--no-judge"]),
        (1, "no .jsonl files found", ["score", str(empty), "--no-judge"]),
        (
            1,
            "--judge with --no-judge (our own check)",
            ["score", str(good), "--judge", "--no-judge"],
        ),
        (1, "a bad --since", ["cost", "--since", "notadate"]),
        (1, "store cannot be opened: cost", ["cost", "--week", "--db-path", str(isdir_db)]),
        (1, "store cannot be opened: budget", ["budget", "--db-path", str(isdir_db)]),
        (1, "store cannot be opened: impact", ["impact", "--db-path", str(isdir_db)]),
        (1, "store cannot be opened: rescore", ["rescore", "--db-path", str(isdir_db)]),
        (1, "no store to rescore", ["rescore", "--db-path", str(t / "none.db")]),
        (1, "rescore --limit below 1", ["rescore", "--limit", "0", "--db-path", str(legacy_db)]),
        (
            0,
            "rescore: gone transcripts are skipped, not failed",
            ["rescore", "--db-path", str(gone_db)],
        ),
        (2, "unknown flag", ["score", str(good), "--bogus"]),
        (2, "missing value", ["budget", "--window-days"]),
        (2, "missing required choice", ["cost"]),
        (2, "argparse mutually exclusive flags", ["cost", "--week", "--since", "2026-01-01"]),
        (4, "a session cannot be parsed", ["score", str(bad), "--no-judge"]),
        (4, "rescore: a legacy row yields no usage", ["rescore", "--db-path", str(legacy_db)]),
    ]
    for want, why, argv in cases:
        got = _main_code(monkeypatch, argv)
        capsys.readouterr()
        assert got == want, (
            f"{why}: `tracegauge {' '.join(argv)}` exited {got}, EXIT_CODES.md says {want}"
        )


def test_monitor_alarm_row_of_exit_codes_md(
    hermetic_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _monitor(monkeypatch, hermetic_cli, _live(), _threshold_active()) == 3


def test_exit_codes_md_describes_the_two_contradictory_flag_cases() -> None:
    text = DOC.read_text(encoding="utf-8")
    rows = {
        int(cells[0]): row
        for row in text.splitlines()
        if row.startswith("| ")
        and (cells := [c.strip() for c in row.strip("|").split("|")])
        and cells[0].isdigit()
    }
    assert set(rows) == {0, 1, 2, 3, 4}
    assert "--judge --no-judge" in rows[1]
    assert "contradictory" not in rows[1]  # the old, wrong claim: argparse conflicts are code 2
    assert "mutually exclusive" in rows[2]
