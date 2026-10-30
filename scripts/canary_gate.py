# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Decide release.yml's canary gate from two `gh run list --json` listings.

usage: canary_gate.py SCHEDULED_JSON DISPATCH_JSON [MAX_AGE_DAYS]

SCHEDULED_JSON lists the `schedule` runs of pypi-canary.yml on master (every status); DISPATCH_JSON
lists its `workflow_dispatch` runs on master. Fields: status, conclusion, createdAt, url.

The rule: the latest completed scheduled run (success or failure; cancelled and skipped runs carry
no signal) must be green and at most MAX_AGE_DAYS old (default 3: the canary is daily, GitHub
starts scheduled runs late and sometimes drops one). A manual run never overrides a red or stale
scheduled run.

The one exception is the bootstrap case: when NO scheduled run exists at all (a new repository,
whose first scheduled run is up to a day away), the latest completed manual run is judged by the
same rule instead, and the result says so. Any scheduled run, in any state, ends the exception.
No qualifying run is a refusal, not a pass. Exit 0 = pass, 1 = refuse, 2 = unreadable input. On
GitHub Actions the decision is also appended to the job summary. See docs/HOTFIX.md.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIGNAL = ("success", "failure")


def _age_days(run: dict[str, Any], now: datetime) -> float:
    created = datetime.fromisoformat(str(run["createdAt"]).replace("Z", "+00:00"))
    return (now - created).total_seconds() / 86400


def _judge(
    runs: list[dict[str, Any]], kind: str, max_age: float, now: datetime
) -> tuple[bool, str]:
    done = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") in SIGNAL]
    if not done:
        return (
            False,
            f"no completed {kind} pypi-canary run on master; it cannot vouch for this release",
        )
    latest = max(done, key=lambda r: str(r["createdAt"]))
    age = _age_days(latest, now)
    if latest["conclusion"] != "success":
        return False, (
            f"the latest {kind} pypi-canary run on master is '{latest['conclusion']}' "
            f"({latest.get('url', '')}); a manual green re-run does not clear it -- fix the cause "
            "and wait for the next scheduled run"
        )
    if age > max_age:
        return False, (
            f"the latest {kind} pypi-canary run on master is {age:.1f} days old "
            f"(limit {max_age:g}); a scheduled run was missed"
        )
    return (
        True,
        f"latest {kind} run {latest.get('url', '')} is green, {age:.2f} days old (limit {max_age:g})",
    )


def decide(
    scheduled: list[dict[str, Any]],
    dispatched: list[dict[str, Any]],
    max_age: float = 3.0,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """(passes, message). The bootstrap exception applies only while ``scheduled`` is empty."""
    now = now or datetime.now(timezone.utc)
    if scheduled:
        return _judge(scheduled, "scheduled", max_age, now)
    ok, msg = _judge(dispatched, "manual (workflow_dispatch)", max_age, now)
    prefix = (
        "BOOTSTRAP EXCEPTION USED: no scheduled pypi-canary run exists yet, so the latest manual "
        "run was judged instead"
        if ok
        else "no scheduled pypi-canary run exists yet, and no manual run qualifies either"
    )
    return ok, f"{prefix}; {msg}"


def _load(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ValueError(f"{path}: expected a JSON list of run objects")
    return data


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: canary_gate.py SCHEDULED_JSON DISPATCH_JSON [MAX_AGE_DAYS]", file=sys.stderr)
        return 2
    try:
        scheduled, dispatched = _load(argv[1]), _load(argv[2])
        max_age = float(argv[3]) if len(argv) == 4 else 3.0
        ok, msg = decide(scheduled, dispatched, max_age)
    except (OSError, ValueError, KeyError, TypeError) as e:
        # Fail closed: an unreadable listing is never a pass.
        print(f"::error::canary gate could not read the run listings: {e}")
        return 2
    print(f"canary gate: OK: {msg}" if ok else f"::error::{msg}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Canary gate: {'pass' if ok else 'refused'}\n\n{msg}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
