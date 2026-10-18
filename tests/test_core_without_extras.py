# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_core_without_extras.py -- the core install (no `tracegauge[patterns]`) must work.

numpy, scikit-learn and scipy are an optional extra. These tests simulate their absence by
setting sys.modules[name] = None in a subprocess (any import of them then raises ImportError), so
they hold even though the dev environment has them installed. Every root (HOME, USERPROFILE,
TRACEGAUGE_DB_PATH) is redirected into tmp_path.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BLOCK = ("numpy", "scipy", "sklearn")
HINT = 'pip install "tracegauge[patterns]"'

_RUNNER = textwrap.dedent(
    """
    import runpy, sys
    for n in {block!r}:
        sys.modules[n] = None
    sys.argv = ["tracegauge", *{args!r}]
    runpy.run_module("tracegauge", run_name="__main__", alter_sys=True)
    """
)


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env["HOME"] = env["USERPROFILE"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"  # the report prints box-drawing characters
    env["TRACEGAUGE_DB_PATH"] = str(tmp_path / "core.db")
    return env


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    code = _RUNNER.format(block=BLOCK, args=list(args))
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(tmp_path),
        timeout=120,
    )


def _run_code(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(tmp_path),
    )


def test_importing_tes_cli_and_server_loads_no_extra_packages(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        f"for n in {BLOCK!r}: sys.modules[n] = None\n"
        "import tracegauge, tracegauge.cli, tracegauge.web.server, tracegauge.intelligence\n"
        "import tracegauge.intelligence.cache, tracegauge.intelligence.chat\n"
        "assert tracegauge.intelligence.ask_local is tracegauge.intelligence.chat.ask_local\n"
        "print('ok')\n"
    )
    r = _run_code(tmp_path, code)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_quickstart_runs_and_shows_finding_without_extras(tmp_path: Path) -> None:
    r = _run_cli(tmp_path, "quickstart")
    assert r.returncode == 0, r.stderr
    assert "REPEATED-FAILED-RETRY" in r.stdout
    # A first-run user has no sessions of their own, so the demo says it uses a synthetic history.
    token = r.stdout.split("TRAJECTORY QUALITY")[0]
    assert "SYNTHETIC HISTORY" in token and "synthetic-history research-recon sessions" in token


def test_score_runs_without_extras(tmp_path: Path) -> None:
    sample = (
        Path(__file__).parent.parent / "tracegauge" / "data" / "quickstart_sample_session.jsonl"
    )
    r = _run_cli(tmp_path, "score", str(sample), "--no-judge", "--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["schema_version"] == 1


@pytest.mark.parametrize("cmd", ["cost", "budget", "impact"])
def test_store_commands_run_without_extras(tmp_path: Path, cmd: str) -> None:
    extra = ["--week"] if cmd == "cost" else []
    r = _run_cli(tmp_path, cmd, "--json", *extra)
    assert r.returncode == 0, (cmd, r.stderr)
    assert json.loads(r.stdout)["command"] == cmd


def test_monitor_runs_without_extras(tmp_path: Path) -> None:
    r = _run_cli(tmp_path, "monitor", "--json")
    assert r.returncode in (0, 3), r.stderr
    assert json.loads(r.stdout)["command"] == "monitor"


@pytest.mark.parametrize("args", [["patterns"], ["ask", "what costs the most?"]])
def test_patterns_and_ask_print_a_one_line_hint_and_exit_1(tmp_path: Path, args: list[str]) -> None:
    r = _run_cli(tmp_path, *args)
    assert r.returncode == 1
    assert HINT in r.stderr
    assert "Traceback" not in r.stderr
    assert len([ln for ln in r.stderr.splitlines() if ln.strip()]) == 1


def test_dashboard_routes_show_a_friendly_extra_missing_response(tmp_path: Path) -> None:
    code = textwrap.dedent(
        f"""
        import sys, json
        for n in {BLOCK!r}:
            sys.modules[n] = None
        from pathlib import Path
        from tracegauge.store import open_db
        from tracegauge.web.server import ServerConfig, create_app
        cfg = ServerConfig(db_path=Path({str(tmp_path / "web.db")!r}))
        open_db(cfg.db_path).close()
        with create_app(cfg).test_client() as c:
            page = c.get("/patterns")
            ask = c.post("/ask", data=json.dumps({{"question": "hi"}}),
                         content_type="application/json")
            home = c.get("/")
        print(json.dumps([page.status_code, page.get_data(as_text=True),
                          ask.status_code, ask.get_json(), home.status_code]))
        """
    )
    r = _run_code(tmp_path, code)
    assert r.returncode == 0, r.stderr
    page_status, page_body, ask_status, ask_json, home_status = json.loads(r.stdout)
    assert page_status == 200
    assert "tracegauge[patterns]" in page_body
    assert "Traceback" not in page_body
    assert ask_status == 501
    assert ask_json["needs_extra"] == "patterns"
    assert "tracegauge[patterns]" in ask_json["error"]
    assert home_status == 200
