# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""The command is ``tracegauge`` (alias ``tgauge``); the ``tes`` names were removed in 0.18."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tracegauge import __version__, cli


def _argv(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["x", *argv])


def test_version_names_tracegauge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _argv(monkeypatch, "--version")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert out.strip() == f"tracegauge {__version__}"
    assert err == ""


def test_the_old_entry_point_is_gone() -> None:
    assert not hasattr(cli, "tes_main")
    assert not hasattr(cli, "TES_DEPRECATION_NOTICE")


def _run_clean(code: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    # -I and a scratch cwd: the repo root may hold a stale, untracked tes/__pycache__ that would
    # import as an empty namespace package and hide the removal.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TES_", "PYTHON"))}
    return subprocess.run(
        [sys.executable, "-I", *code],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )


def test_import_tes_fails(tmp_path: Path) -> None:
    proc = _run_clean(["-c", "import tes"], tmp_path)
    assert proc.returncode != 0
    assert "No module named 'tes'" in proc.stderr


def test_python_dash_m_tes_fails(tmp_path: Path) -> None:
    proc = _run_clean(["-m", "tes", "--version"], tmp_path)
    assert proc.returncode != 0
    assert "No module named tes" in proc.stderr


def test_python_dash_m_tracegauge_still_works(tmp_path: Path) -> None:
    proc = _run_clean(["-m", "tracegauge", "--version"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"tracegauge {__version__}"
