# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_export_contribution_output_path.py — Issue #17: export-contribution's
default --output path is derived from the RESOLVED db_path, not a fixed ~/.tes/.

Same class of bug (and same class of fix) as RR2's intelligence_cache.json fix:
before this fix, running `export-contribution` against an isolated/scratch DB
(via --db-path or TRACEGAUGE_DB_PATH) silently dropped its default output file into the
REAL ~/.tes/ directory regardless of which database was actually in use.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
import tracegauge.cli as cli

from tests.legacy_store import stamp_current


def _insert_minimal_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Minimal-but-valid session row with real cost data, so
    build_contribution_payload has something to export."""
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, task_type, source_path, source_mtime, source_hash, scored_at,
            axes_scored, real_tokens, scope_status, baseline_available,
            p25, p75, median, band_verdict, interpretation, token_domain_of_validity,
            baseline_source, judge_verdict, judge_score, judge_reasoning,
            trajectory_domain_of_validity, judge_source_hash,
            waste_event_count, waste_events, waste_domain_of_validity,
            turn_count, session_cost_usd, cost_approximate, cost_domain_of_validity
        ) VALUES (
            ?, 'infra-deploy', '/fake/path.jsonl', 0.0, 'hash', '2026-07-04T12:00:00+00:00',
            '["token"]', 1000, 'in_scope', 1,
            NULL, NULL, NULL, 'within_band', '', '',
            'self', NULL, NULL, NULL,
            '', NULL,
            0, '[]', '',
            30, 1.23, 0, ''
        )
        """,
        (session_id,),
    )
    conn.commit()
    stamp_current(conn)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(db_path))
    from tracegauge.store import open_db

    conn = open_db(db_path)
    _insert_minimal_session(conn, "sess-1")
    conn.close()
    return db_path


def _run(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr("sys.argv", ["tracegauge", *argv])
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")  # confirm the write prompt
    try:
        cli.main()
    except SystemExit:
        pass


def test_default_output_path_derived_from_resolved_db_path_not_real_home(
    monkeypatch, tmp_path, isolated_store
) -> None:
    """The default output file must land next to the RESOLVED (scratch) DB,
    never in the real ~/.tes/ -- regardless of what the real home directory
    happens to contain."""
    real_home_contribution_dir = Path.home() / ".tes"
    today_str = date.today().isoformat()
    real_home_default_path = real_home_contribution_dir / f"contribution-{today_str}.jsonl"
    real_home_existed_before = real_home_default_path.exists()

    _run(monkeypatch, ["export-contribution", "--anonymous"])

    expected_path = isolated_store.parent / f"contribution-{today_str}.jsonl"
    assert expected_path.exists(), (
        f"expected default output at {expected_path} (next to the resolved scratch DB), "
        "was not written there"
    )

    # The real ~/.tes/ contribution file must be untouched by this run -- if it
    # didn't exist before, it must still not exist after.
    if not real_home_existed_before:
        assert not real_home_default_path.exists(), (
            "export-contribution wrote its default output into the REAL ~/.tes/ "
            "directory despite TRACEGAUGE_DB_PATH pointing at an isolated scratch DB"
        )


def test_explicit_output_flag_still_wins(monkeypatch, tmp_path, isolated_store) -> None:
    """--output must still be honored exactly as before -- this fix only changes
    the DEFAULT when --output is omitted."""
    explicit_path = tmp_path / "custom-name.jsonl"
    _run(monkeypatch, ["export-contribution", "--anonymous", "--output", str(explicit_path)])
    assert explicit_path.exists()
