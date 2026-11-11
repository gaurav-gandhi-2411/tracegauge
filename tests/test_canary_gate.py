# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""release.yml's canary gate (scripts/canary_gate.py): scheduled runs decide; a manual run counts
only in the bootstrap case, when no scheduled run exists at all."""

import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("canary_gate", _ROOT / "scripts" / "canary_gate.py")
assert _spec and _spec.loader
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _run(hours_ago: float, conclusion: str = "success", status: str = "completed") -> dict:
    created = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "status": status,
        "conclusion": conclusion,
        "createdAt": created,
        "url": f"u{hours_ago}",
    }


def test_recent_green_scheduled_run_passes_without_the_exception() -> None:
    ok, msg = cg.decide([_run(20)], [], now=NOW)
    assert ok
    assert "BOOTSTRAP" not in msg


def test_a_green_manual_run_never_overrides_a_red_scheduled_run() -> None:
    ok, msg = cg.decide([_run(20, "failure")], [_run(1)], now=NOW)
    assert not ok
    assert "failure" in msg


def test_a_green_manual_run_never_overrides_a_stale_scheduled_run() -> None:
    ok, msg = cg.decide([_run(24 * 4)], [_run(1)], now=NOW)
    assert not ok
    assert "days old" in msg


def test_the_latest_scheduled_run_decides_not_an_older_green_one() -> None:
    ok, _ = cg.decide([_run(48), _run(20, "failure")], [], now=NOW)
    assert not ok


def test_cancelled_and_skipped_scheduled_runs_carry_no_signal() -> None:
    ok, _ = cg.decide([_run(5, "cancelled"), _run(30)], [], now=NOW)
    assert ok
    ok, _ = cg.decide([_run(5, "cancelled"), _run(6, "skipped")], [_run(1)], now=NOW)
    assert not ok  # scheduled runs exist, so the manual run does not count


def test_an_unfinished_scheduled_run_ends_the_exception() -> None:
    ok, msg = cg.decide([_run(0.5, "", "in_progress")], [_run(1)], now=NOW)
    assert not ok
    assert "BOOTSTRAP" not in msg


def test_bootstrap_no_scheduled_run_and_a_recent_green_manual_run_passes_and_says_so() -> None:
    ok, msg = cg.decide([], [_run(2)], now=NOW)
    assert ok
    assert msg.startswith("BOOTSTRAP EXCEPTION USED")


@pytest.mark.parametrize(
    "dispatched",
    [[], [_run(2, "failure")], [_run(24 * 4)], [_run(1, "cancelled")], [_run(1, "", "queued")]],
)
def test_bootstrap_still_refuses_without_a_recent_green_manual_run(dispatched: list) -> None:
    ok, msg = cg.decide([], dispatched, now=NOW)
    assert not ok
    assert "BOOTSTRAP EXCEPTION USED" not in msg


def test_main_passes_writes_the_job_summary_and_fails_closed_on_bad_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    run = {
        "status": "completed",
        "conclusion": "success",
        "createdAt": recent.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": "u",
    }
    sched, disp, summary = tmp_path / "s.json", tmp_path / "d.json", tmp_path / "summary.md"
    sched.write_text("[]", encoding="utf-8")
    disp.write_text(json.dumps([run]), encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cg.main(["canary_gate.py", str(sched), str(disp), "3"]) == 0
    assert "BOOTSTRAP EXCEPTION USED" in summary.read_text(encoding="utf-8")

    disp.write_text('{"not": "a list"}', encoding="utf-8")
    assert cg.main(["canary_gate.py", str(sched), str(disp)]) == 2
    disp.write_text("not json", encoding="utf-8")
    assert cg.main(["canary_gate.py", str(sched), str(disp)]) == 2
    assert cg.main(["canary_gate.py", str(sched)]) == 2
    assert "::error::" in capsys.readouterr().out


def test_release_workflow_lists_both_events_without_a_status_filter_and_runs_the_script() -> None:
    wf = (_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    m = re.search(r"^  gate-canary:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)", wf, re.M | re.S)
    assert m
    job = m.group(1)
    assert "--event schedule --branch master" in job
    assert "--event workflow_dispatch --branch master" in job
    # No --status filter: an unfinished scheduled run must still count as "a scheduled run exists".
    assert "--status" not in job
    assert "scripts/canary_gate.py" in job
    assert "actions: read" in job


def test_a_run_dated_in_the_future_is_refused_beyond_a_small_clock_skew() -> None:
    assert cg.decide([_run(-0.05)], [], now=NOW)[0]  # 3 minutes ahead: tolerated skew
    ok, msg = cg.decide([_run(-24 * 25)], [], now=NOW)
    assert not ok
    assert "future" in msg


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), 0.0, -1.0])
def test_a_non_finite_or_non_positive_age_limit_fails_closed(limit: float) -> None:
    ok, _ = cg.decide([_run(24 * 2000)], [], max_age=limit, now=NOW)
    assert not ok
