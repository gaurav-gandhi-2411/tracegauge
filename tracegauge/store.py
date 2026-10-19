# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/store.py — SQLite persistence layer for the TES scoring ledger.

Public API:
    open_db(path)                                          -> sqlite3.Connection
    file_hash(path)                                        -> str
    needs_scoring(conn, session_id, current_hash)          -> bool
    upsert_session(conn, result, source_path, source_mtime, source_hash, turn_count)
    get_session(conn, session_id)                          -> dict | None
    list_sessions(conn, limit, offset)                     -> list[dict]

Schema version 1. Version encoded in PRAGMA user_version — no meta table.
Migration: turn_count column added via ALTER TABLE if absent (added after initial release).
"""

import enum
import hashlib
import json
import sqlite3
import time
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tracegauge._env import env_value
from tracegauge.score import ThreeAxisResult, build_subagents_summary
from tracegauge.web.cost_format import cost_is_known

if TYPE_CHECKING:
    from tracegauge._digest import SessionDigest
    from tracegauge.cost import SessionCost

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10


class TrajectoryRenderState(enum.Enum):
    UNAVAILABLE = "unavailable"  # judge_verdict is None (never ran, or errored at scoring time)
    CURRENT = "current"  # verdict present, hash matches (not stale)
    STALE = "stale"  # verdict present, but judge ran against an older file version


def trajectory_render_state(row: dict) -> TrajectoryRenderState:
    """Return the canonical render state for the trajectory axis of a session row.

    Call this instead of writing null-checks inline. Covers all reachable states.
    judge_verdict=None covers both 'never judged' and 'judge errored' — both render as UNAVAILABLE.
    """
    if row["judge_verdict"] is None:
        return TrajectoryRenderState.UNAVAILABLE
    if row["judge_stale"]:
        return TrajectoryRenderState.STALE
    return TrajectoryRenderState.CURRENT


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id                    TEXT PRIMARY KEY,
    task_type                     TEXT NOT NULL,
    source_path                   TEXT NOT NULL,
    source_mtime                  REAL NOT NULL,
    source_hash                   TEXT NOT NULL,
    scored_at                     TEXT NOT NULL,
    axes_scored                   TEXT NOT NULL,
    real_tokens                   INTEGER NOT NULL,
    scope_status                  TEXT NOT NULL,
    baseline_available            INTEGER NOT NULL,
    p25                           INTEGER,
    p75                           INTEGER,
    median                        INTEGER,
    band_verdict                  TEXT NOT NULL,
    interpretation                TEXT NOT NULL,
    token_domain_of_validity      TEXT NOT NULL,
    baseline_source               TEXT NOT NULL DEFAULT 'b2_corpus',
    turn_count                    INTEGER,
    judge_verdict                 TEXT,
    judge_score                   REAL,
    judge_reasoning               TEXT,
    trajectory_domain_of_validity TEXT NOT NULL,
    judge_source_hash             TEXT,
    waste_event_count             INTEGER NOT NULL,
    waste_events                  TEXT NOT NULL,
    waste_domain_of_validity      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_scored_at ON sessions(scored_at);
CREATE INDEX IF NOT EXISTS idx_sessions_task_type ON sessions(task_type);
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_DIR = Path.home() / ".tes"
_DEFAULT_DB = _DEFAULT_DIR / "tes.db"


def resolve_db_path(path: Path | str | None = None) -> Path:
    """Canonical DB path: explicit arg → TRACEGAUGE_DB_PATH (or deprecated TES_DB_PATH) → ~/.tes/tes.db."""
    if path is not None:
        return Path(path)
    if env_val := env_value("DB_PATH"):
        return Path(env_val)
    return _DEFAULT_DB


def _add_column(conn: sqlite3.Connection, alter_sql: str) -> None:
    """Run one additive ``ALTER TABLE ... ADD COLUMN``, tolerating a concurrent migrator.

    The watcher and a CLI command can open the same pre-migration store at the same moment;
    both see the column missing and both ALTER. SQLite lets only one win and raises
    "duplicate column name" for the other, which used to crash that process. That error means
    the column exists, which is exactly what this wants, so it is swallowed (nothing else is).
    """
    try:
        conn.execute(alter_sql)
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch the store to WAL, tolerating another process doing the same at this moment.

    Changing the journal mode of a not-yet-WAL file needs a lock SQLite's busy handler does not
    wait on, so two simultaneous first opens (watcher + CLI) made one raise "database is locked".
    WAL is persistent in the file, so the loser only has to retry (or skip: the winner set it).
    """
    for _ in range(40):
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                return
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc):
                raise
            time.sleep(0.05)
    # Still contended after ~2s: carry on in the current mode; a later open will set WAL.


def open_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or create) the TES database.

    Path resolution: explicit arg → TRACEGAUGE_DB_PATH (or deprecated TES_DB_PATH) → ~/.tes/tes.db.
    Raises RuntimeError on PermissionError or schema version mismatch.
    """
    db_path = resolve_db_path(path)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create {db_path.parent} — check permissions or set TRACEGAUGE_DB_PATH. ({exc})"
        ) from exc

    conn = sqlite3.connect(db_path)
    _enable_wal(conn)
    conn.row_factory = sqlite3.Row

    existing_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if existing_version == 0:
        # New database — apply schema and stamp version.
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    elif existing_version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"DB schema version {existing_version} is newer than this tool's "
            f"SCHEMA_VERSION={SCHEMA_VERSION}. Upgrade tracegauge."
        )
    # existing_version == SCHEMA_VERSION → run additive migrations only.

    # Additive migration: turn_count was added after the initial schema shipped.
    # ALTER TABLE is safe to re-run guard: check column presence first.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "turn_count" not in existing_cols:
        _add_column(conn, "ALTER TABLE sessions ADD COLUMN turn_count INTEGER")

    if "baseline_source" not in existing_cols:
        _add_column(
            conn,
            "ALTER TABLE sessions ADD COLUMN baseline_source TEXT NOT NULL DEFAULT 'b2_corpus'",
        )

    cost_cols = {
        "session_cost_usd": "ALTER TABLE sessions ADD COLUMN session_cost_usd REAL",
        "cost_approximate": "ALTER TABLE sessions ADD COLUMN cost_approximate INTEGER",
        "cost_domain_of_validity": "ALTER TABLE sessions ADD COLUMN cost_domain_of_validity TEXT",
    }
    for col_name, alter_sql in cost_cols.items():
        if col_name not in existing_cols:
            _add_column(conn, alter_sql)

    # Attribution fractions, persisted at score time so tracegauge.intelligence
    # can cluster ANY scored session without re-reading its source JSONL --
    # see tracegauge.attribution.attribution_fractions. NULL for every row scored
    # before this migration (additive, same pattern as the cost columns above);
    # tracegauge.intelligence.features falls back to on-demand extraction for those.
    attribution_cols = {
        "context_resend_pct": "ALTER TABLE sessions ADD COLUMN context_resend_pct REAL",
        "context_growth_pct": "ALTER TABLE sessions ADD COLUMN context_growth_pct REAL",
        "output_pct": "ALTER TABLE sessions ADD COLUMN output_pct REAL",
        "waste_pct": "ALTER TABLE sessions ADD COLUMN waste_pct REAL",
    }
    for col_name, alter_sql in attribution_cols.items():
        if col_name not in existing_cols:
            _add_column(conn, alter_sql)

    # Raw unresolved model string(s) for a session whose cost is NULL
    # because a model didn't resolve against the price table -- persisted at
    # score time (same reason: source_cost.approximate_reasons is only
    # available while the source file is still readable) so `tracegauge cost --roi`
    # can name unpriced models in its coverage report without re-reading
    # anything. Comma-joined raw model strings, NULL if the session priced
    # cleanly or was scored before this column existed.
    if "cost_unpriced_models" not in existing_cols:
        _add_column(conn, "ALTER TABLE sessions ADD COLUMN cost_unpriced_models TEXT")

    # Reconstructed code-impact per session (Edit/Write/MultiEdit/
    # NotebookEdit operations), persisted at score time -- same reason,
    # tool_use.input.old_string/new_string/file_path are only available
    # while the source transcript is still readable. JSON-encoded list of
    # {path, additions, deletions, tool, prior_content_unknown,
    # untested_tool_shape} dicts (see tracegauge.impact.EditOperation). NULL for
    # every row scored before this migration -- tracegauge impact's own report
    # names this as a legacy-row gap.
    if "edit_operations" not in existing_cols:
        _add_column(conn, "ALTER TABLE sessions ADD COLUMN edit_operations TEXT")

    # Subagent roll-up. session_cost_usd already INCLUDES subagent spend; these
    # expose the split (subagent_tokens in real_tokens units). NULL for rows scored before
    # this migration -- indistinguishable from "no subagents" until the session is re-scored.
    subagent_cols = {
        "subagent_tokens": "ALTER TABLE sessions ADD COLUMN subagent_tokens INTEGER",
        "subagent_cost_usd": "ALTER TABLE sessions ADD COLUMN subagent_cost_usd REAL",
        "subagent_count": "ALTER TABLE sessions ADD COLUMN subagent_count INTEGER",
        # JSON list of per-type rows (tracegauge.score.build_subagents_summary). NULL = not
        # recorded (scored before 0.17.0), distinct from [] = scored, no subagents.
        "subagent_by_type_json": "ALTER TABLE sessions ADD COLUMN subagent_by_type_json TEXT",
    }
    for col_name, alter_sql in subagent_cols.items():
        if col_name not in existing_cols:
            _add_column(conn, alter_sql)

    # Usage de-duplication (tracegauge.adapt.ADAPTER_VERSION 2): which adapter produced this row's
    # real_tokens/cost. NULL = scored before the column existed = the pre-dedupe adapter, which
    # summed usage once per content-block record (~2.4x too high). tracegauge.self_baseline ignores such
    # rows; `tracegauge backfill-waste` refreshes them from the source transcript. Additive, idempotent.
    dedupe_cols = {
        "adapter_version": "ALTER TABLE sessions ADD COLUMN adapter_version INTEGER",
        # tracegauge.adapt.COST_VERSION: NULL/older = cache writes priced all at the 5-minute rate
        # (1h writes under-priced); `tracegauge backfill-waste` re-prices such rows. Separate from
        # adapter_version because real_tokens (and so the baselines) did not change.
        "cost_version": "ALTER TABLE sessions ADD COLUMN cost_version INTEGER",
        "duplicate_usage_records": (
            "ALTER TABLE sessions ADD COLUMN duplicate_usage_records INTEGER"
        ),
        # Model era for the live alarm baseline (tracegauge.alarm_baseline); NULL = unknown era.
        "dominant_model": "ALTER TABLE sessions ADD COLUMN dominant_model TEXT",
    }
    for col_name, alter_sql in dedupe_cols.items():
        if col_name not in existing_cols:
            _add_column(conn, alter_sql)

    # Key/value store-level state (e.g. the one-time legacy-rows notice marker, tracegauge.legacy).
    # Additive and idempotent; never holds anything a score depends on.
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()

    return conn


def file_hash(path: Path | str) -> str:
    """Return sha256 hex digest of the file at path."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def needs_scoring(conn: sqlite3.Connection, session_id: str, current_hash: str) -> bool:
    """Return True if session_id is absent from the ledger or its hash has changed."""
    row = conn.execute(
        "SELECT source_hash FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return True
    return row["source_hash"] != current_hash


def subagent_by_type_json(result: ThreeAxisResult) -> str | None:
    """JSON for the subagent_by_type_json column, or None ("not recorded").

    None when the result carries no per-type summary or no cost was computed (the list would
    then falsely read "scored, no subagents"); "[]" when a priced session had no subagents.
    """
    if not result.subagents or result.session_cost_usd is None:
        return None
    return json.dumps(result.subagents["by_type"])


def upsert_session(
    conn: sqlite3.Connection,
    result: ThreeAxisResult,
    source_path: str,
    source_mtime: float,
    source_hash: str,
    turn_count: int = 0,
) -> None:
    """Write or update a session row using merge semantics.

    Merge rules:
    1. No existing row → INSERT all fields.
    2. New has judge → full UPDATE (judge_source_hash = source_hash).
    3. New has no judge AND existing has judge → partial UPDATE: preserve all
       judge fields (judge_verdict, judge_score, judge_reasoning,
       trajectory_domain_of_validity, judge_source_hash); update everything else.
    4. New has no judge AND existing has no judge → full UPDATE.

    turn_count: from the adapted record (adapt_session returns it at top level). When the
    caller passes none (0), the count carried on ``result.turn_count`` is stored, so every
    write path records it.
    """
    from datetime import datetime

    turn_count = turn_count if turn_count else result.turn_count

    scored_at = datetime.now(UTC).isoformat()
    has_judge = result.judge_verdict is not None

    axes: list[str] = ["token", "waste"]
    if has_judge:
        axes.append("judge")
    axes_json = json.dumps(axes)

    existing = conn.execute(
        "SELECT judge_verdict, judge_source_hash FROM sessions WHERE session_id = ?",
        (result.session_id,),
    ).fetchone()

    if existing is None:
        # Case 1: INSERT
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, task_type,
                source_path, source_mtime, source_hash, scored_at, axes_scored,
                real_tokens, scope_status, baseline_available,
                p25, p75, median, band_verdict, interpretation,
                token_domain_of_validity, baseline_source,
                judge_verdict, judge_score, judge_reasoning,
                trajectory_domain_of_validity, judge_source_hash,
                waste_event_count, waste_events, waste_domain_of_validity,
                turn_count,
                session_cost_usd, cost_approximate, cost_domain_of_validity,
                context_resend_pct, context_growth_pct, output_pct, waste_pct,
                cost_unpriced_models, edit_operations,
                subagent_tokens, subagent_cost_usd, subagent_count, subagent_by_type_json
            ) VALUES (
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                result.session_id,
                result.task_type,
                source_path,
                source_mtime,
                source_hash,
                scored_at,
                axes_json,
                result.real_tokens,
                result.scope_status,
                int(result.baseline_available),
                result.p25,
                result.p75,
                result.median,
                result.band_verdict,
                result.interpretation,
                result.token_domain_of_validity,
                result.baseline_source,
                result.judge_verdict,
                result.judge_score,
                result.judge_reasoning,
                result.trajectory_domain_of_validity,
                source_hash if has_judge else None,
                result.waste_event_count,
                json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd,
                int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct,
                result.context_growth_pct,
                result.output_pct,
                result.waste_pct,
                result.cost_unpriced_models,
                result.edit_operations,
                result.subagent_tokens,
                result.subagent_cost_usd,
                result.subagent_count,
                subagent_by_type_json(result),
            ),
        )

    elif has_judge:
        # Case 2: full UPDATE — new judge data wins entirely.
        conn.execute(
            """
            UPDATE sessions SET
                task_type = ?,
                source_path = ?, source_mtime = ?, source_hash = ?,
                scored_at = ?, axes_scored = ?,
                real_tokens = ?, scope_status = ?, baseline_available = ?,
                p25 = ?, p75 = ?, median = ?, band_verdict = ?,
                interpretation = ?, token_domain_of_validity = ?,
                baseline_source = ?,
                judge_verdict = ?, judge_score = ?, judge_reasoning = ?,
                trajectory_domain_of_validity = ?, judge_source_hash = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?,
                session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?,
                context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, waste_pct = ?,
                cost_unpriced_models = ?, edit_operations = ?,
                subagent_tokens = ?, subagent_cost_usd = ?, subagent_count = ?,
                subagent_by_type_json = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path,
                source_mtime,
                source_hash,
                scored_at,
                axes_json,
                result.real_tokens,
                result.scope_status,
                int(result.baseline_available),
                result.p25,
                result.p75,
                result.median,
                result.band_verdict,
                result.interpretation,
                result.token_domain_of_validity,
                result.baseline_source,
                result.judge_verdict,
                result.judge_score,
                result.judge_reasoning,
                result.trajectory_domain_of_validity,
                source_hash,
                result.waste_event_count,
                json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd,
                int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct,
                result.context_growth_pct,
                result.output_pct,
                result.waste_pct,
                result.cost_unpriced_models,
                result.edit_operations,
                result.subagent_tokens,
                result.subagent_cost_usd,
                result.subagent_count,
                subagent_by_type_json(result),
                result.session_id,
            ),
        )

    elif existing["judge_verdict"] is not None:
        # Case 3: partial UPDATE — preserve existing judge columns.
        conn.execute(
            """
            UPDATE sessions SET
                task_type = ?,
                source_path = ?, source_mtime = ?, source_hash = ?,
                scored_at = ?, axes_scored = ?,
                real_tokens = ?, scope_status = ?, baseline_available = ?,
                p25 = ?, p75 = ?, median = ?, band_verdict = ?,
                interpretation = ?, token_domain_of_validity = ?,
                baseline_source = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?,
                session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?,
                context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, waste_pct = ?,
                cost_unpriced_models = ?, edit_operations = ?,
                subagent_tokens = ?, subagent_cost_usd = ?, subagent_count = ?,
                subagent_by_type_json = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path,
                source_mtime,
                source_hash,
                scored_at,
                axes_json,
                result.real_tokens,
                result.scope_status,
                int(result.baseline_available),
                result.p25,
                result.p75,
                result.median,
                result.band_verdict,
                result.interpretation,
                result.token_domain_of_validity,
                result.baseline_source,
                result.waste_event_count,
                json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd,
                int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct,
                result.context_growth_pct,
                result.output_pct,
                result.waste_pct,
                result.cost_unpriced_models,
                result.edit_operations,
                result.subagent_tokens,
                result.subagent_cost_usd,
                result.subagent_count,
                subagent_by_type_json(result),
                result.session_id,
            ),
        )

    else:
        # Case 4: full UPDATE, no judge on either side.
        conn.execute(
            """
            UPDATE sessions SET
                task_type = ?,
                source_path = ?, source_mtime = ?, source_hash = ?,
                scored_at = ?, axes_scored = ?,
                real_tokens = ?, scope_status = ?, baseline_available = ?,
                p25 = ?, p75 = ?, median = ?, band_verdict = ?,
                interpretation = ?, token_domain_of_validity = ?,
                baseline_source = ?,
                judge_verdict = ?, judge_score = ?, judge_reasoning = ?,
                trajectory_domain_of_validity = ?, judge_source_hash = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?,
                session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?,
                context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, waste_pct = ?,
                cost_unpriced_models = ?, edit_operations = ?,
                subagent_tokens = ?, subagent_cost_usd = ?, subagent_count = ?,
                subagent_by_type_json = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path,
                source_mtime,
                source_hash,
                scored_at,
                axes_json,
                result.real_tokens,
                result.scope_status,
                int(result.baseline_available),
                result.p25,
                result.p75,
                result.median,
                result.band_verdict,
                result.interpretation,
                result.token_domain_of_validity,
                result.baseline_source,
                None,
                None,
                None,
                result.trajectory_domain_of_validity,
                None,
                result.waste_event_count,
                json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd,
                int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct,
                result.context_growth_pct,
                result.output_pct,
                result.waste_pct,
                result.cost_unpriced_models,
                result.edit_operations,
                result.subagent_tokens,
                result.subagent_cost_usd,
                result.subagent_count,
                subagent_by_type_json(result),
                result.session_id,
            ),
        )

    conn.execute(
        "UPDATE sessions SET adapter_version = ?, cost_version = ?, "
        "duplicate_usage_records = ?, dominant_model = ? WHERE session_id = ?",
        (
            result.adapter_version,
            result.cost_version,
            result.duplicate_usage_records,
            result.dominant_model,
            result.session_id,
        ),
    )
    conn.commit()


def _count_turns_from_jsonl(source_path: str) -> int | None:
    """Return turn count for a session JSONL file, or None if unreadable.

    Counts assistant messages and substantive user messages (either plain-text
    or tool-result content), ignoring sidechain messages.  Mirrors the logic
    used in adapt_session() but is inlined here to avoid coupling to adapt.py.
    """
    import json as _json

    p = Path(source_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        turn_count = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                msg = _json.loads(line)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("isSidechain"):
                continue
            msg_type = msg.get("type", "")
            if msg_type == "assistant":
                turn_count += 1
            elif msg_type == "user":
                message = msg.get("message")
                content = message.get("content", "") if isinstance(message, dict) else ""
                if isinstance(content, str) and content.strip():
                    turn_count += 1
                elif isinstance(content, list):
                    has_tr = any(
                        isinstance(x, dict) and x.get("type") == "tool_result" for x in content
                    )
                    if has_tr:
                        turn_count += 1
        return turn_count
    except Exception:
        return None


def _has_usage(record: dict[str, Any]) -> bool:
    """True iff the adapted transcript carried at least one usage record.

    ``adapt_session`` returns zero usage for an empty, garbage or truncated file instead of
    raising; every writer that refreshes a stored row from a transcript must check this first.
    """
    return bool(record.get("usage_dedupe", {}).get("usage_records"))


def _is_empty_stub(conn: sqlite3.Connection, session_id: str) -> bool:
    """True iff the STORED row recorded no turns and no tokens (a session that never started).

    Decided from the stored numbers, not from the transcript: a row that once had turns or
    tokens and whose transcript is now empty or damaged is a failure, not a stub.
    """
    row = conn.execute(
        "SELECT turn_count, real_tokens FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row is not None and row[0] == 0 and row[1] == 0


def _stamp_current_versions(conn: sqlite3.Connection, session_id: str) -> None:
    """Mark one row as scored by the current accounting without changing any number."""
    from tracegauge.adapt import ADAPTER_VERSION, COST_VERSION

    conn.execute(
        "UPDATE sessions SET adapter_version = ?, cost_version = ? WHERE session_id = ?",
        (ADAPTER_VERSION, COST_VERSION, session_id),
    )
    conn.commit()


def _refresh_usage_columns(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    waste_entry: dict[str, Any],
    session_cost: SessionCost | None,
    digest: SessionDigest | None,
    prices: dict[str, Any],
    session_id: str,
) -> None:
    """Re-derive every usage-derived column of one row from a freshly adapted record.

    Used by backfill_waste for rows written by the pre-dedupe adapter (real_tokens and cost
    over-counted ~2.4x). The verdict columns are recomputed against the BUNDLED corpus baseline
    (the stored self-baseline would be built from the very rows being repaired); judge columns
    and waste columns are not touched here.
    """
    from tracegauge.attribution import attribution_fractions, compute_attribution
    from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
    from tracegauge.score import score_session

    attribution = compute_attribution(digest, waste_entry, prices) if digest is not None else None
    r = score_session(
        record,
        load_baselines(BUNDLED_BASELINES_PATH),
        waste_entry=waste_entry,
        session_cost=session_cost,
        attribution=attribution,
    )
    resend, growth, out_pct, waste_pct = (
        attribution_fractions(attribution) if attribution is not None else (None,) * 4
    )
    conn.execute(
        "UPDATE sessions SET real_tokens = ?, scope_status = ?, baseline_available = ?, "
        "p25 = ?, p75 = ?, median = ?, band_verdict = ?, interpretation = ?, "
        "token_domain_of_validity = ?, baseline_source = ?, "
        "session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?, "
        "cost_unpriced_models = ?, subagent_tokens = ?, subagent_cost_usd = ?, "
        "subagent_count = ?, subagent_by_type_json = ?, context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, "
        "waste_pct = ?, adapter_version = ?, cost_version = ?, duplicate_usage_records = ?, "
        "dominant_model = ? WHERE session_id = ?",
        (
            r.real_tokens,
            r.scope_status,
            int(r.baseline_available),
            r.p25,
            r.p75,
            r.median,
            r.band_verdict,
            r.interpretation,
            r.token_domain_of_validity,
            r.baseline_source,
            r.session_cost_usd,
            int(r.cost_approximate),
            r.cost_domain_of_validity or "",
            r.cost_unpriced_models,
            r.subagent_tokens,
            r.subagent_cost_usd,
            r.subagent_count,
            subagent_by_type_json(r),
            resend,
            growth,
            out_pct,
            waste_pct,
            r.adapter_version,
            r.cost_version,
            r.duplicate_usage_records,
            r.dominant_model,
            session_id,
        ),
    )


def backfill_waste(
    db_path: Path | str | None = None,
    prices: dict | None = None,
    *,
    only_legacy: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    """Re-run frozen detectors on all accessible sessions; embed per-event costs.

    ``only_legacy`` (what `tracegauge rescore` uses) restricts the run to LEGACY rows (tracegauge.legacy) and
    refreshes every one whose transcript is readable; a row whose cost cannot be recomputed is
    counted in ``errors`` and left legacy. ``dry_run`` opens the store read-only, runs the same
    computation and writes nothing (not even a schema migration). ``limit`` stops after that many
    readable rows (the rest are counted in ``not_attempted``). With ``only_legacy``,
    ``legacy_rows`` is how many legacy rows the run started with.

    Also refreshes the usage-derived columns (real_tokens, cost, verdict band, attribution) of
    rows produced by the pre-dedupe adapter (adapter_version NULL/older) or priced before
    1-hour cache writes were split out (cost_version NULL/older), counted in ``refreshed``; rows already current are left as they were.

    A transcript that is empty, unreadable or carries no usage records is never written over a
    stored row, on any path (``adapt_session`` returns zero usage for it rather than raising):
    the row is counted in ``errors`` and left exactly as it was. The same holds when a row due a
    refresh cannot be priced.

    Safe to call repeatedly (hash-independent; fixes the stale-zeros bug where sessions
    scored before waste detection was wired show waste_event_count=0 in the store).

    An empty stub (stored ``turn_count`` 0 and ``real_tokens`` 0, transcript without usage) is
    counted in ``skipped_stub`` and only has its two version columns stamped, so it stops being
    legacy; no stored number changes. Any other usage-less row is still a failure.

    Returns summary: {"updated": N, "no_waste": M, "missing_source": K, "errors": E,
    "refreshed": R, "skipped_stub": S}
    where "updated" = sessions that had >= 1 waste event written, "no_waste" = sessions
    processed with 0 detected events, "missing_source" = source file not accessible,
    "errors" = failed rows (left unchanged), "refreshed" = rows re-scored.
    """
    from tracegauge._digest import reconstruct_digest
    from tracegauge.adapt import ADAPTER_VERSION, COST_VERSION, adapt_session
    from tracegauge.cost import compute_session_cost, load_price_table
    from tracegauge.waste import annotate_waste_costs, build_waste_entry

    if prices is None:
        prices = load_price_table()

    if dry_run:
        # No migration, no checkpoint, no side files: a dry run leaves the store as it found it.
        # A read-only handle on a WAL store creates -wal/-shm when none exist, so a store that
        # is idle (no -wal: everything is in the main file) is opened immutable instead.
        resolved = resolve_db_path(db_path).resolve()
        mode = "mode=ro" if Path(f"{resolved}-wal").exists() else "immutable=1"
        conn = sqlite3.connect(f"{resolved.as_uri()}?{mode}", uri=True)
        conn.row_factory = sqlite3.Row
    else:
        conn = open_db(db_path)
    if only_legacy:
        from tracegauge.legacy import legacy_sources

        # (session_id, source_path); every one of these is refreshed, whatever its versions say.
        rows: list[Any] = [
            {"session_id": sid, "source_path": path, "adapter_version": None, "cost_version": None}
            for sid, path in legacy_sources(conn)
        ]
    else:
        rows = conn.execute(
            "SELECT session_id, source_path, adapter_version, cost_version FROM sessions "
            "WHERE source_path IS NOT NULL"
        ).fetchall()

    updated = 0
    no_waste = 0
    missing = 0
    errors = 0
    refreshed = 0
    skipped_stub = 0
    attempted = 0
    not_attempted = 0

    for row in rows:
        session_id: str = row["session_id"]
        if not row["source_path"] or not Path(row["source_path"]).exists():
            missing += 1
            continue
        p = Path(row["source_path"])
        if limit is not None and attempted >= limit:
            not_attempted += 1  # readable, but past --limit: left legacy for the next run
            continue
        attempted += 1
        try:
            record = adapt_session(p)
            if not _has_usage(record):
                # The adapter tolerates garbage/empty/truncated files by returning zero usage;
                # writing that over a stored row would erase its numbers (and stamp it current).
                # Every path counts it as failed and leaves the row exactly as it was...
                if _is_empty_stub(conn, session_id):
                    # ...except an empty stub: a row that never had a turn or a token has no
                    # number to protect and nothing to recompute. Stamp the versions only, so it
                    # stops being legacy (and `tracegauge rescore` can exit 0); no stored number moves.
                    if not dry_run:
                        _stamp_current_versions(conn, session_id)
                    skipped_stub += 1
                    continue
                raise ValueError(f"no usage records in {p.name}")
            turns: list[dict] = record.get("digest", {}).get("turns", [])
            waste_entry = build_waste_entry(session_id, turns)

            per_turn_cost: dict[int, float] = {}
            sc = None
            digest = None
            try:
                digest = reconstruct_digest(record.get("digest", {}))
                sc = compute_session_cost(digest, prices)
                per_turn_cost = {tc.turn_index: tc.total_usd for tc in sc.turn_costs}
            except Exception:
                pass  # cost failure → wasted_cost_usd will be 0 for all events

            waste_events = waste_entry["waste_events"]
            annotate_waste_costs(waste_events, per_turn_cost)

            needs_refresh = (
                only_legacy
                or row["adapter_version"] != ADAPTER_VERSION
                or (row["cost_version"] or 0) != COST_VERSION
            )
            if needs_refresh and sc is None:
                # Refreshing would stamp the row current with no cost at all: report a failure
                # and leave it legacy rather than launder it into the corrected figures.
                raise ValueError(f"cost could not be computed for {session_id}")

            if needs_refresh:
                if not dry_run:
                    _refresh_usage_columns(
                        conn, record, waste_entry, sc, digest, prices, session_id
                    )
                refreshed += 1

            count = len(waste_events)
            if not dry_run:
                conn.execute(
                    "UPDATE sessions SET waste_event_count = ?, waste_events = ? "
                    "WHERE session_id = ?",
                    (count, json.dumps(waste_events), session_id),
                )
                conn.commit()

            if count > 0:
                updated += 1
            else:
                no_waste += 1
        except Exception:
            errors += 1
            if not dry_run:
                conn.rollback()  # drop a half-applied row so the next commit cannot persist it

    conn.close()
    return {
        "updated": updated,
        "no_waste": no_waste,
        "missing_source": missing,
        "errors": errors,
        "refreshed": refreshed,
        "legacy_rows": len(rows) if only_legacy else 0,
        "not_attempted": not_attempted,
        "skipped_stub": skipped_stub,
    }


def backfill_cost(
    db_path: Path | str | None = None,
    prices: dict | None = None,
) -> dict[str, int]:
    """Populate session_cost_usd for sessions currently missing it.

    Re-adapts from source JSONL (not stored digest) so model strings and
    cache_creation tokens are available. Returns summary dict:
      {"updated": N, "missing_source": M, "errors": E, "approximate": A}
    """
    from tracegauge._digest import reconstruct_digest
    from tracegauge.adapt import adapt_session
    from tracegauge.cost import compute_session_cost, load_price_table

    if prices is None:
        prices = load_price_table()

    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT session_id, source_path FROM sessions "
        "WHERE session_cost_usd IS NULL AND source_path IS NOT NULL"
    ).fetchall()

    updated = missing = errors = approximate = 0

    for session_id, source_path in rows:
        p = Path(source_path)
        if not p.exists():
            missing += 1
            continue
        try:
            record = adapt_session(p)
            if not _has_usage(record):
                raise ValueError(f"no usage records in {p.name}")  # unknown must not become 0.0
            digest = reconstruct_digest(record["digest"])
            session_cost = compute_session_cost(digest, prices)
            conn.execute(
                "UPDATE sessions SET "
                "  session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?, "
                "  cost_unpriced_models = ?, subagent_cost_usd = ?, subagent_by_type_json = ? "
                "WHERE session_id = ?",
                (
                    session_cost.total_usd,
                    int(session_cost.approximate),
                    session_cost.domain_of_validity,
                    ",".join(session_cost.unpriced_models) or None,
                    session_cost.subagent_usd,
                    json.dumps(build_subagents_summary(session_cost)["by_type"]),
                    session_id,
                ),
            )
            conn.commit()
            updated += 1
            if session_cost.approximate:
                approximate += 1
        except Exception:
            errors += 1

    conn.close()
    return {
        "updated": updated,
        "missing_source": missing,
        "errors": errors,
        "approximate": approximate,
    }


def backfill_turn_counts(
    db_path: Path | str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Record the turn count on rows written before it was stored (``turn_count`` = 0).

    Only rows with ``turn_count = 0``, ``real_tokens > 0`` and a ``source_path`` are eligible:
    NULL rows (legacy; the self-baseline already handles them via ``scope_status``) and empty
    stubs are left alone. Re-adapts from the source transcript and UPDATEs ``turn_count`` only,
    never another column. Idempotent. ``dry_run`` counts what would change and writes nothing.
    Returns ``{"updated", "missing_source", "errors", "unchanged"}``.
    """
    from tracegauge.adapt import adapt_session

    if dry_run:
        # Same read-only handle as backfill_waste's dry run: no migration, no side files.
        resolved = resolve_db_path(db_path).resolve()
        mode = "mode=ro" if Path(f"{resolved}-wal").exists() else "immutable=1"
        conn = sqlite3.connect(f"{resolved.as_uri()}?{mode}", uri=True)
    else:
        conn = open_db(db_path)
    rows = conn.execute(
        "SELECT session_id, source_path FROM sessions "
        "WHERE turn_count = 0 AND real_tokens > 0 AND source_path IS NOT NULL"
    ).fetchall()

    updated = missing = errors = unchanged = 0
    for session_id, source_path in rows:
        p = Path(source_path)
        if not p.exists():
            missing += 1
            continue
        try:
            record = adapt_session(p)
            if not _has_usage(record):
                raise ValueError(f"no usage records in {p.name}")
            turns = int(record.get("turn_count", 0))
            if turns <= 0:
                unchanged += 1
                continue
            if not dry_run:
                conn.execute(
                    "UPDATE sessions SET turn_count = ? WHERE session_id = ?",
                    (turns, session_id),
                )
                conn.commit()
            updated += 1
        except Exception:
            errors += 1

    conn.close()
    return {"updated": updated, "missing_source": missing, "errors": errors, "unchanged": unchanged}


def _deserialize_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict with JSON fields decoded."""
    d = dict(row)
    d["waste_events"] = json.loads(d["waste_events"])
    d["axes_scored"] = json.loads(d["axes_scored"])
    d["baseline_available"] = bool(d["baseline_available"])
    d["judge_stale"] = bool(
        d["judge_verdict"] and d["judge_source_hash"] and d["judge_source_hash"] != d["source_hash"]
    )
    d["cost_approximate"] = bool(d.get("cost_approximate", 0))
    # Machine-readable pricing status derived from the persisted unpriced model ids.
    d["unpriced_models"] = [m for m in (d.get("cost_unpriced_models") or "").split(",") if m]
    d["priced"] = d.get("session_cost_usd") is not None and not d["unpriced_models"]
    d["cost_known"] = cost_is_known(d.get("session_cost_usd"), d["unpriced_models"])
    return d


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return full session row as dict, or None if not found."""
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    return _deserialize_row(row)


# Mapping from public sort-key names (used in ?sort= query params) to DB column names.
# ONLY these columns are allowed — prevents SQL injection via user-controlled sort params.
_SORT_COLUMN_WHITELIST: dict[str, str] = {
    "date": "scored_at",
    "cost": "session_cost_usd",
    "waste": "waste_event_count",
    "tokens": "real_tokens",
    "verdict": "band_verdict",
}


def list_sessions(
    conn: sqlite3.Connection,
    limit: int = 100,
    offset: int = 0,
    order_by: str = "date",
    direction: str = "DESC",
) -> list[dict]:
    """Return sessions ordered by the requested column.

    order_by must be a key in _SORT_COLUMN_WHITELIST; unknown keys fall back to 'date'.
    direction must be 'ASC' or 'DESC'; anything else falls back to 'DESC'.
    """
    col = _SORT_COLUMN_WHITELIST.get(order_by, "scored_at")
    dir_safe = "ASC" if direction.upper() == "ASC" else "DESC"
    rows = conn.execute(
        f"SELECT * FROM sessions ORDER BY {col} {dir_safe} NULLS LAST LIMIT ? OFFSET ?",  # noqa: S608
        (limit, offset),
    ).fetchall()
    return [_deserialize_row(r) for r in rows]


__all__ = [
    "SCHEMA_VERSION",
    "TrajectoryRenderState",
    "trajectory_render_state",
    "resolve_db_path",
    "open_db",
    "file_hash",
    "needs_scoring",
    "upsert_session",
    "backfill_waste",
    "backfill_cost",
    "backfill_turn_counts",
    "get_session",
    "list_sessions",
]
