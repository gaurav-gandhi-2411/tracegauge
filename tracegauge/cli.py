# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/cli.py — Command-line interface for tracegauge.

Frictionless front door (the tool does the work; the user does almost nothing):
    tracegauge                          — bare command launches the dashboard (= tracegauge serve)
    tracegauge score                    — scores your MOST RECENT session (no path needed)
    tracegauge score --pick             — pick from a list of recent sessions
    tracegauge score <path> [options]   — score a specific file/directory (power users)
    tracegauge score --judge            — run the trajectory judge (auto-detects Ollama/API)
    tracegauge serve [options]          — background watcher + localhost dashboard

Session resolution order: explicit PATH > --pick > newest session by mtime.
Sessions are adapted through the frozen CC adapter (secret redaction ON by
default) and scored on three axes. The engine, numbers, and honesty surfacing
are unchanged from 0.5.0 — this is invocation ergonomics only.

API-judge egress is NEVER silent: auto-detecting ANTHROPIC_API_KEY only OFFERS
the API judge; the per-session consent screen still gates every byte that leaves.
"""

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

from tracegauge import __version__
from tracegauge._digest import reconstruct_digest
from tracegauge._env import deprecation_line, notice_suppressed
from tracegauge.adapt import adapt_session
from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.cost import SessionCost, compute_session_cost, load_price_table
from tracegauge.cost_period import PeriodCostReport
from tracegauge.discovery import iter_session_files
from tracegauge.exit_codes import (
    EXIT_ADAPT_ERROR,
    EXIT_ALARM,
    EXIT_OK,
    EXIT_USAGE,
    ExitCodeHelpFormatter,
    epilog,
)
from tracegauge.json_out import (
    budget_payload,
    cost_payload,
    cost_roi_payload,
    emit,
    impact_payload,
    monitor_payload,
    patterns_payload,
    rescore_payload,
)
from tracegauge.judge import (
    JUDGE_SETUP_HINT_FULL,
    ApiJudgeConfig,
    JudgeConfig,
    build_api_judge_consent_notice,
    detect_env_api_key,
    is_judge_available,
    score_trajectory,
    score_trajectory_api,
)
from tracegauge.legacy import LEGACY_LABEL, excluded_note, partition_legacy
from tracegauge.patterns_extra import PatternsExtraMissing, require_patterns_extra
from tracegauge.report import format_human, format_json
from tracegauge.score import ThreeAxisResult, score_session
from tracegauge.waste import (
    annotate_waste_costs,
    build_waste_entry,
)
from tracegauge.watcher import DEFAULT_CC_PATH
from tracegauge.web.cost_format import format_cost_display, format_unpriced

# Load price table once at import time — prices don't change between sessions in a run.
_PRICES: dict = load_price_table()


def _print_contribution_preview(payload: object, out_path: Path) -> None:
    """Print the consent/preview screen for export-contribution.

    Shows: row count, one real sample row (JSON), full field list, explicit
    exclusions, output path, and the non-transmission statement.
    """
    from tracegauge.contribution import ALLOWED_FIELDS

    payload = payload  # type: ContributionPayload

    sep = "─" * 72
    print(sep)
    print("CONTRIBUTION EXPORT PREVIEW")
    print(sep)
    print(f"\n  {payload.manifest.row_count} session(s) found in your store.\n")

    print("SAMPLE ROW (real data from your store):\n")
    sample = payload.rows[0]
    print(json.dumps(sample, indent=2, default=str))

    print("\nFIELDS INCLUDED (all content-free):")
    for field_name in sorted(ALLOWED_FIELDS):
        print(f"  {field_name}")

    print("\nNEVER INCLUDED:")
    for excluded in payload.manifest.fields_excluded:
        print(f"  {excluded}")

    print(f"\nOutput: {out_path}")
    print("This writes a local file ONLY.")
    print("NOTHING is transmitted anywhere — tracegauge has no server and sends no data.")
    print("You can open and inspect the file yourself.")
    print(f"\n{sep}")


def _discover_sessions(path: Path) -> list[Path]:
    """Return JSONL paths to score: single file or all *.jsonl in a directory."""
    if path.is_file():
        return [path]
    return sorted(path.glob("*.jsonl"))


def _resolve_cc_path(cc_path_arg: str | None) -> Path:
    """Resolve the Claude Code projects directory (the tool knows where sessions live)."""
    return Path(cc_path_arg).expanduser() if cc_path_arg else DEFAULT_CC_PATH


def _recent_sessions(cc_path: Path, limit: int | None = None) -> list[tuple[Path, float]]:
    """Return (path, mtime) for *.jsonl sessions under cc_path, newest first.

    The tool already scans ~/.claude/projects (this mirrors the watcher's discovery)
    so the user never has to hunt for a session path. limit=None returns all.
    """
    if not cc_path.exists():
        return []
    found: list[tuple[Path, float]] = []
    for p in iter_session_files(cc_path):
        try:
            found.append((p, p.stat().st_mtime))
        except OSError:
            continue
    found.sort(key=lambda pm: pm[1], reverse=True)
    return found[:limit] if limit is not None else found


def _newest_session(cc_path: Path) -> Path | None:
    """Return the single most-recently-modified CC session, or None if none exist."""
    recent = _recent_sessions(cc_path, limit=1)
    return recent[0][0] if recent else None


def _fmt_age(mtime: float, _now: float | None = None) -> str:
    """Human-readable 'modified N ago' string from an mtime."""
    now = _now if _now is not None else time.time()
    delta = max(0.0, now - mtime)
    if delta < 90:
        return "just now"
    if delta < 5400:  # < 90 min
        return f"{int(delta // 60)}m ago"
    if delta < 172800:  # < 48 h
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _fmt_size(path: Path) -> str:
    """Human-readable file size."""
    try:
        size = float(path.stat().st_size)
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _project_label(path: Path) -> str:
    """A short, readable label for the project a session belongs to.

    CC encodes the project path as the parent directory name under
    ~/.claude/projects (e.g. 'C--Users-alice-projects-myrepo').
    Show the tail so the user can recognize it without a wall of path encoding.
    """
    name = path.parent.name
    return name[-40:] if len(name) > 40 else name


def _pick_session(cc_path: Path) -> list[Path]:
    """Interactive picker: show recent sessions, return the chosen one (or [] if aborted)."""
    recent = _recent_sessions(cc_path, limit=10)
    if not recent:
        print(f"[ERROR] No CC sessions found under {cc_path}.", file=sys.stderr)
        return []
    print("Recent Claude Code sessions:\n")
    for i, (p, m) in enumerate(recent, 1):
        print(
            f"  [{i}]  {_project_label(p):<40}  {p.stem[:8]}…  {_fmt_age(m):>8}  {_fmt_size(p):>8}"
        )
    try:
        raw = input(f"\nPick a session to score [1-{len(recent)}, default 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return []
    if raw == "":
        choice = 1
    else:
        try:
            choice = int(raw)
        except ValueError:
            print("Not a number — aborted.")
            return []
    if not (1 <= choice <= len(recent)):
        print("Out of range — aborted.")
        return []
    return [recent[choice - 1][0]]


def _resolve_score_targets(args: argparse.Namespace) -> list[Path]:
    """Resolve which session(s) to score.

    Resolution order (locked): explicit PATH > --pick (interactive list) > newest.
    The common case needs no path at all — the tool scores the most recent session.
    Returns [] when nothing should be scored (error printed, or pick aborted).
    """
    if args.path is not None:
        target = Path(args.path).expanduser().resolve()
        if not target.exists():
            print(f"[ERROR] Path not found: {target}", file=sys.stderr)
            return []
        paths = _discover_sessions(target)
        if not paths:
            print(f"[ERROR] No .jsonl files found in {target}", file=sys.stderr)
        return paths

    cc_path = _resolve_cc_path(getattr(args, "cc_path", None))

    if getattr(args, "pick", False):
        return _pick_session(cc_path)

    newest = _newest_session(cc_path)
    if newest is None:
        print(
            f"[ERROR] No Claude Code sessions found under {cc_path}.\n"
            "        Run some Claude Code sessions first, or pass an explicit PATH.",
            file=sys.stderr,
        )
        return []
    # First-run / orientation: tell the user exactly what was auto-selected.
    print(
        "No path given — scoring your most recent session "
        "(use `tracegauge score --pick` to choose, or pass a PATH):\n"
        f"  {newest.name}\n"
        f"  {_project_label(newest)} · modified {_fmt_age(newest.stat().st_mtime)}\n",
        file=sys.stderr,
    )
    return [newest]


class SessionAdaptError(Exception):
    """A session file could not be read/parsed. The scoring loop reports it and keeps going."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


def _with_lever_hint(result: ThreeAxisResult, attribution: object) -> ThreeAxisResult:
    """Attach the cost breakdown (informational) and the lever hint (an absolute finding, or None).

    Never raises: a hint failure must not break scoring output.
    """
    if attribution is None:
        return result
    try:
        from tracegauge.takeaway import build_cost_breakdown, build_lever_hint  # noqa: PLC0415

        unpriced = tuple(result.unpriced_models)
        hint = build_lever_hint(attribution, unpriced)  # type: ignore[arg-type]
        breakdown = build_cost_breakdown(attribution, unpriced)  # type: ignore[arg-type]
    except Exception:
        return result
    return dataclasses.replace(result, lever_hint=hint, cost_breakdown=breakdown)


def _with_cache_churn(result: ThreeAxisResult, path: Path) -> ThreeAxisResult:
    """Attach the cache-churn object, computed on demand from the source JSONL (not stored).

    Never raises: an unreadable transcript yields ``status: "unavailable"`` with a ``reason``
    (``read_error`` / ``internal_error``; the traceback goes to the log), not a failure.
    """
    from tracegauge.json_out import cache_churn_payload_for_path  # noqa: PLC0415

    return dataclasses.replace(result, cache_churn=cache_churn_payload_for_path(path, _PRICES))


def _legacy_count(store_conn: object) -> int:
    """Pre-0.15 rows in the open store, or 0 when there is no store or it cannot be read."""
    if store_conn is None:
        return 0
    try:
        from tracegauge.legacy import count_legacy  # noqa: PLC0415

        return count_legacy(store_conn)  # type: ignore[arg-type]
    except Exception:
        return 0


def _db_legacy_count(db_path: Path) -> int:
    """Pre-0.15 rows in the store at db_path; 0 when it does not exist or cannot be read."""
    if not db_path.exists():
        return 0
    try:
        from tracegauge.store import open_db  # noqa: PLC0415

        conn = open_db(db_path)
        try:
            return _legacy_count(conn)
        finally:
            conn.close()
    except Exception:
        return 0


def _self_baseline_for(store_conn: object, baselines: dict, override: object = None):
    """The user's own baseline state for the token verdict of a session being scored.

    With a store it is computed (or read from its cache) from the user's stored sessions; without
    one every task type is ``building`` (0/10). Either way no verdict is borrowed from a bundled
    table. A failure reading the store falls back to the empty state, never to a bundled verdict.
    """
    from tracegauge.self_baseline import building_state, load_or_compute  # noqa: PLC0415

    if override is not None:  # the quickstart's in-memory synthetic history; no store involved
        return override
    if store_conn is not None:
        try:
            db_file = store_conn.execute("PRAGMA database_list").fetchone()[2]  # type: ignore[attr-defined]
            if db_file:
                return load_or_compute(Path(db_file), baselines)
        except Exception:
            pass
    return building_state(baselines)


def score_path(
    path: Path,
    baselines: dict,
    judge_config: JudgeConfig,
    use_judge: bool,
    json_mode: bool,
    store_conn: object = None,
    self_baseline: object = None,
) -> None:
    """Adapt, detect waste, optionally judge, score, and print one session.

    store_conn is an optional open sqlite3.Connection. When provided the
    ThreeAxisResult is written to the tracegauge store after printing. Any store
    write failure is swallowed — it must never break CLI output.
    """
    try:
        record = adapt_session(path)
    except Exception as exc:
        print(f"[ERROR] Failed to adapt {path.name}: {exc}", file=sys.stderr)
        raise SessionAdaptError(path) from exc

    session_id: str = record.get("session_id", path.stem)
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    waste_entry = build_waste_entry(session_id, turns)

    judge_entry: dict | None = None
    if use_judge:
        judge_entry = score_trajectory(record, judge_config)

    # Cost annotation: compute from measured tokens at per-turn rates.
    session_cost: SessionCost | None = None
    digest_dict = record.get("digest", {})
    digest = None
    if digest_dict and digest_dict.get("turns"):
        try:
            digest = reconstruct_digest(digest_dict)
            session_cost = compute_session_cost(digest, _PRICES)
        except Exception:
            pass  # cost failure must never break CLI output

    # Embed per-event wasted cost (redundant turns only) into waste_events.
    if session_cost is not None:
        per_turn_cost = {tc.turn_index: tc.total_usd for tc in session_cost.turn_costs}
        annotate_waste_costs(waste_entry["waste_events"], per_turn_cost)

    # Attribution fractions, computed from the same digest/waste_entry/prices
    # already in scope for session_cost above, persisted at score time so
    # tracegauge.intelligence can cluster this session without ever re-reading its
    # source JSONL — see tracegauge.attribution.attribution_fractions.
    attribution = None
    if digest is not None:
        try:
            from tracegauge.attribution import compute_attribution

            attribution = compute_attribution(digest, waste_entry, _PRICES)
        except Exception:
            pass  # attribution failure must never break CLI output

    result = score_session(
        record,
        baselines,
        self_baseline=_self_baseline_for(store_conn, baselines, self_baseline),
        judge_entry=judge_entry,
        waste_entry=waste_entry,
        session_cost=session_cost,
        attribution=attribution,
    )

    # Cost vs baseline framing: look up from the store if available.
    baseline_cost_band: tuple[float, float, float] | None = None
    if store_conn is not None and session_cost is not None:
        try:
            from tracegauge.self_baseline import compute_baseline_cost_band  # noqa: PLC0415

            task_type = result.task_type
            # Derive scope_floor from DB: use p10 of turn_counts as a rough floor (min 20).
            tc_rows = store_conn.execute(  # type: ignore[union-attr]
                "SELECT turn_count FROM sessions "
                "WHERE task_type = ? AND turn_count > 0 ORDER BY turn_count",
                (task_type,),
            ).fetchall()
            if tc_rows:
                counts = [r[0] for r in tc_rows]
                p10_idx = max(0, int(len(counts) * 0.10) - 1)
                scope_floor = max(20, counts[p10_idx])
            else:
                scope_floor = 20
            baseline_cost_band = compute_baseline_cost_band(
                store_conn,
                task_type,
                scope_floor,  # type: ignore[arg-type]
            )
        except Exception:
            pass  # baseline band lookup failure is non-fatal

    result = _with_cache_churn(_with_lever_hint(result, attribution), path)

    if json_mode:
        print(format_json(result, _legacy_count(store_conn)))
    else:
        print(format_human(result, baseline_cost_band=baseline_cost_band))

    if store_conn is not None:
        try:
            from tracegauge.store import file_hash, upsert_session

            source_hash = file_hash(path)
            source_mtime = path.stat().st_mtime
            upsert_session(store_conn, result, str(path), source_mtime, source_hash)
        except Exception:
            pass  # store write failure must never break the CLI output


def _score_path_with_api_judge(
    path: Path,
    baselines: dict,
    judge_config: JudgeConfig,
    use_local_judge: bool,
    json_mode: bool,
    store_conn: object = None,
    api_judge_config: ApiJudgeConfig | None = None,
    api_judge_consent: bool = False,
) -> None:
    """Score a session with optional local or API judge.

    When api_judge_config is provided AND api_judge_consent=True, uses the API
    judge instead of the local judge. Otherwise falls through to use_local_judge.
    """
    try:
        record = adapt_session(path)
    except Exception as exc:
        print(f"[ERROR] Failed to adapt {path.name}: {exc}", file=sys.stderr)
        raise SessionAdaptError(path) from exc

    session_id: str = record.get("session_id", path.stem)
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    waste_entry = build_waste_entry(session_id, turns)

    judge_entry: dict | None = None
    if api_judge_config is not None and api_judge_consent:
        judge_entry = score_trajectory_api(record, api_judge_config, consent_given=True)
    elif use_local_judge:
        judge_entry = score_trajectory(record, judge_config)

    session_cost: SessionCost | None = None
    digest_dict = record.get("digest", {})
    digest = None
    if digest_dict and digest_dict.get("turns"):
        try:
            digest = reconstruct_digest(digest_dict)
            session_cost = compute_session_cost(digest, _PRICES)
        except Exception:
            pass

    if session_cost is not None:
        per_turn_cost = {tc.turn_index: tc.total_usd for tc in session_cost.turn_costs}
        annotate_waste_costs(waste_entry["waste_events"], per_turn_cost)

    # See the mirrored comment in score_path (no-API-judge sibling above).
    attribution = None
    if digest is not None:
        try:
            from tracegauge.attribution import compute_attribution

            attribution = compute_attribution(digest, waste_entry, _PRICES)
        except Exception:
            pass

    result = score_session(
        record,
        baselines,
        self_baseline=_self_baseline_for(store_conn, baselines),
        judge_entry=judge_entry,
        waste_entry=waste_entry,
        session_cost=session_cost,
        attribution=attribution,
    )

    baseline_cost_band: tuple[float, float, float] | None = None
    if store_conn is not None and session_cost is not None:
        try:
            from tracegauge.self_baseline import compute_baseline_cost_band  # noqa: PLC0415

            task_type = result.task_type
            tc_rows = store_conn.execute(  # type: ignore[union-attr]
                "SELECT turn_count FROM sessions "
                "WHERE task_type = ? AND turn_count > 0 ORDER BY turn_count",
                (task_type,),
            ).fetchall()
            if tc_rows:
                counts = [r[0] for r in tc_rows]
                p10_idx = max(0, int(len(counts) * 0.10) - 1)
                scope_floor = max(20, counts[p10_idx])
            else:
                scope_floor = 20
            baseline_cost_band = compute_baseline_cost_band(
                store_conn,
                task_type,
                scope_floor,  # type: ignore[arg-type]
            )
        except Exception:
            pass

    result = _with_cache_churn(_with_lever_hint(result, attribution), path)

    if json_mode:
        print(format_json(result, _legacy_count(store_conn)))
    else:
        print(format_human(result, baseline_cost_band=baseline_cost_band))

    if store_conn is not None:
        try:
            from tracegauge.store import file_hash, upsert_session  # noqa: PLC0415

            source_hash = file_hash(path)
            source_mtime = path.stat().st_mtime
            upsert_session(store_conn, result, str(path), source_mtime, source_hash)
        except Exception:
            pass


def _store_session_count(db_path: Path | None) -> int | None:
    """Return the number of sessions already in the store, or None if unavailable.

    Used only for a friendly first-run orientation line — never affects scoring.
    """
    try:
        from tracegauge.store import open_db, resolve_db_path  # noqa: PLC0415

        conn = open_db(resolve_db_path(db_path))
        try:
            return int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


def _run_serve(
    *,
    port: int = 4747,
    scan_interval: int = 120,
    stability_window: int = 300,
    cc_path_arg: str | None = None,
    db_path_arg: str | None = None,
    background_judge: bool = False,
    alarm_enabled: bool = False,
    plan_type: str = "usage_based",
) -> None:
    """Launch the watcher + localhost dashboard. Shared by `tracegauge serve` and bare `tracegauge`.

    Blocks until Ctrl+C. Prints a first-run orientation line so the user is never
    left staring at a blank screen wondering whether anything happened.
    """
    from tracegauge.watcher import WatcherConfig, start_watcher  # noqa: PLC0415
    from tracegauge.web.server import ServerConfig, start_server  # noqa: PLC0415

    db_path = Path(db_path_arg).expanduser() if db_path_arg else None
    cc_path = _resolve_cc_path(cc_path_arg)

    if background_judge:
        print(
            "\nWARNING: --background-judge enabled.\n"
            "This runs qwen3:30b-a3b (~18 GB VRAM) on your GPU for every new CC session.\n"
            "Ensure Ollama is running before proceeding.\n",
            file=sys.stderr,
        )

    watcher_config = WatcherConfig(
        cc_path=cc_path,
        scan_interval=scan_interval,
        stability_window=stability_window,
        db_path=db_path,
        background_judge=background_judge,
        alarm_enabled=alarm_enabled,
        plan_type=plan_type,
    )
    server_config = ServerConfig(
        host="127.0.0.1",
        port=port,
        db_path=db_path,
        cc_path=cc_path,
        stability_window=stability_window,
        plan_type=plan_type,
    )

    # First-run orientation — the tool tells you what it found and where to look.
    found = len(_recent_sessions(cc_path))
    already_scored = _store_session_count(db_path)
    print("tracegauge service starting...")
    print(f"  Dashboard:        http://127.0.0.1:{port}/")
    print(f"  Watching:         {cc_path}  ({found} session file(s) found)")
    if not already_scored:
        print("  First run:        scoring begins as sessions settle; the dashboard fills in live.")
    print(f"  Scan interval:    {scan_interval}s")
    print(f"  Stability window: {stability_window}s")
    print(
        f"  Judge:            {'ON (--background-judge)' if background_judge else 'OFF (token+waste only)'}"
    )
    print(
        f"  Alarm:            {'ON — plan=' + plan_type if alarm_enabled else 'OFF (--alarm to enable)'}"
    )
    print(f"  Database:         {db_path or '~/.tes/tes.db'}")
    print("  Press Ctrl+C to stop.", flush=True)

    watcher_thread, stop_event = start_watcher(watcher_config)
    try:
        start_server(server_config)  # blocks until Ctrl+C / process exit
    finally:
        stop_event.set()
        watcher_thread.join(timeout=5)


def _run_corpus(args: argparse.Namespace) -> None:
    """Handle `tracegauge corpus contribute|withdraw|reset-id`.

    This is the ONLY code path that transmits session-derived data. Every
    branch goes through tracegauge.corpus_client, which enforces (unconditionally,
    regardless of how this function is called): no network call without
    consent_given=True, and no send without passing the content-free guard
    on the ACTUAL bytes about to be POSTed.
    """
    from tracegauge.contribution import get_or_create_contributor_id
    from tracegauge.corpus_client import (
        CorpusConfig,
        build_corpus_consent_notice,
        contribute,
        reset_contributor_id,
        withdraw,
    )

    corpus_command = getattr(args, "corpus_command", None)

    if corpus_command is None:
        print("Usage: tracegauge corpus {contribute|withdraw|reset-id}")
        return

    if corpus_command == "reset-id":
        new_id = reset_contributor_id()
        print(f"New contributor_id generated: {new_id}")
        print("Prior rows under your old ID are now unlinked from future contributions.")
        return

    if corpus_command == "withdraw":
        if CorpusConfig.from_env() is None:
            print(
                "[NOT AVAILABLE] No community corpus is currently operated — "
                "there is nothing to withdraw from yet. This command will work "
                "once a corpus is provisioned; see PRIVACY.md.",
                file=sys.stderr,
            )
            return
        print("This will permanently delete every row tied to your contributor_id")
        print("from the tracegauge community corpus. This cannot be undone.")
        try:
            answer = input("Continue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            print("Aborted — nothing withdrawn.")
            return
        result = withdraw(confirmed=True, config=CorpusConfig.from_env())
        if result.deleted:
            print(f"Withdrawn: {result.deleted_count} row(s) deleted from the community corpus.")
        else:
            print(f"[NOT WITHDRAWN] {result.reason}", file=sys.stderr)
        return

    if corpus_command == "contribute":
        from tracegauge.store import open_db as _open_db

        # Checked FIRST, before opening the store or showing any preview/consent
        # screen: no community corpus is currently operated (pre-launch — see
        # PRIVACY.md), so this command cannot send anything today regardless of
        # what the user does below. Walking someone through a full preview +
        # "Send to the community corpus? [y/N]" consent prompt only to reveal
        # [NOT SENT] afterward is worse than telling them up front — it looks
        # functional right up until the last line.
        if CorpusConfig.from_env() is None:
            print(
                "[NOT AVAILABLE] No community corpus is currently operated — "
                "`tracegauge corpus contribute` has nowhere to send data yet. The code "
                "path is built and tested (see PRIVACY.md); it activates once a "
                "corpus is provisioned. Nothing is sent by this command today.",
                file=sys.stderr,
            )
            return

        db_path = Path(args.db_path).expanduser() if getattr(args, "db_path", None) else None
        try:
            conn = _open_db(db_path)
        except Exception as exc:
            print(f"[ERROR] Cannot open tracegauge store: {exc}", file=sys.stderr)
            sys.exit(1)

        anonymous = getattr(args, "anonymous", False)
        contributor_id: str | None = None if anonymous else get_or_create_contributor_id()

        from tracegauge.contribution import build_contribution_payload

        try:
            payload = build_contribution_payload(
                conn, contributor_id=contributor_id, include_source_components=True
            )
        except Exception as exc:
            print(f"[ERROR] Failed to build contribution payload: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        if payload.manifest.row_count == 0:
            print("No sessions found in store. Run `tracegauge score` or `tracegauge serve` first.")
            conn.close()
            return

        print(build_corpus_consent_notice(payload.rows[0], contributor_id))

        try:
            answer = input("\nSend to the community corpus? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        consent_given = answer == "y"
        if not consent_given:
            print("Aborted — nothing sent.")
            conn.close()
            return

        config = CorpusConfig.from_env()
        result = contribute(
            conn,
            consent_given=consent_given,
            contributor_id=contributor_id,
            config=config,
        )
        conn.close()

        if result.sent:
            print(f"Sent {result.row_count} row(s) to the community corpus. Thank you.")
            print("Withdraw at any time with `tracegauge corpus withdraw`.")
        else:
            print(f"[NOT SENT] {result.reason}", file=sys.stderr)
        return

    print(f"Unknown corpus subcommand: {corpus_command!r}")


def _run_patterns(
    *,
    db_path: str | None = None,
    force_recompute: bool = False,
    json_mode: bool = False,
) -> int:
    """Show the ML pattern analysis for the session corpus."""
    try:
        require_patterns_extra()
    except PatternsExtraMissing as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE

    from tracegauge.intelligence.cache import get_or_compute_intelligence
    from tracegauge.store import resolve_db_path

    # verbose output goes to stdout; --json must leave stdout as the one document.
    if not json_mode:
        print("Computing session patterns...", flush=True)
    cache = get_or_compute_intelligence(
        db_path=resolve_db_path(db_path),
        force_recompute=force_recompute,
        verbose=not json_mode,
    )

    if json_mode:
        emit(patterns_payload(cache))
        return EXIT_OK

    if not cache.get("valid"):
        print(f"\n{cache.get('status', 'Pattern analysis unavailable.')}")
        legacy_line = excluded_note(int(cache.get("legacy_rows_excluded") or 0), "this analysis")
        if legacy_line:
            print(legacy_line)
        if cache.get("n_sessions") is not None:
            print(
                f"Content sessions: {cache['n_sessions']} (need {cache.get('n_content_sessions_needed', 30)}+)"
            )
        return EXIT_OK

    sep = "─" * 70
    print(f"\n{sep}")
    print("SESSION PATTERN ANALYSIS")
    print(sep)
    print(
        f"  {cache['n_sessions']} content sessions  |  k={cache['k']}  |  "
        f"silhouette={cache['silhouette']:.3f}  |  {'stable' if cache['stable'] else 'variable'}"
    )
    print(f"  {cache['status']}")
    legacy_line = excluded_note(int(cache.get("legacy_rows_excluded") or 0), "this analysis")
    if legacy_line:
        print(f"  {legacy_line}")
    print()
    print("ARCHETYPES (measured behavioral patterns — not quality labels):")
    for a in cache["archetypes"]:
        c = a["centroid"]
        task_str = "  ".join(
            f"{k}:{v}" for k, v in sorted(a["task_type_counts"].items(), key=lambda x: -x[1])
        )
        print(f"\n  [{a['cluster_id']}] {a['name']}")
        print(
            f"      {a['size']} sessions ({a['fraction'] * 100:.1f}%)  "
            f"context_resend={c.get('context_resend_pct', 0):.1%}  "
            f"context_growth={c.get('context_growth_pct', 0):.1%}  "
            f"output={c.get('output_pct', 0):.1%}  "
            f"waste_flag={'yes' if c.get('has_waste', 0) > 0.5 else 'no'}"
        )
        print(f"      task mix: {task_str}")
    print()
    print(
        f"ANOMALIES: {cache['anomaly_count']} of {cache['n_sessions']} sessions "
        f"({cache['anomaly_pct']:.1f}%) are statistical outliers for their cluster."
    )
    print()
    print(f"Domain of validity: {cache['domain_of_validity']}")
    print(
        f"Computed from {cache['session_count']} total sessions in store  "
        f"|  tracegauge {cache['tracegauge_version']}  |  {cache.get('computed_at', '')[:19]}"
    )
    print(sep)
    print(
        "\nTip: 'tracegauge ask \"<question>\"' to ask questions about these patterns in plain language."
    )
    return EXIT_OK


def _run_impact(*, db_path: str | None = None, top_n: int = 10, json_mode: bool = False) -> int:
    """Handle `tracegauge impact` -- corpus-wide code-impact reconstruction from
    persisted Edit/Write/MultiEdit/NotebookEdit operations. Plain
    counts only; the untested-tool-shape and prior-content-unknown
    fractions are reported inline, next to the totals they qualify, never
    buried.
    """
    from tracegauge.impact import compute_impact_report
    from tracegauge.store import list_sessions, open_db, resolve_db_path

    resolved_db = resolve_db_path(db_path)
    try:
        conn = open_db(resolved_db)
    except Exception as exc:
        print(f"[ERROR] Cannot open tracegauge store: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rows = list_sessions(conn, limit=5000, offset=0)
    conn.close()

    # Legacy rows (pre-0.15 accounting) stay out of every aggregate and are counted instead.
    current_rows, legacy_excluded = partition_legacy(rows)
    report = compute_impact_report(current_rows, top_n=top_n, legacy_rows_excluded=legacy_excluded)
    legacy_line = excluded_note(legacy_excluded, "these figures")

    if json_mode:
        emit(impact_payload(report, top_n))
        return EXIT_OK

    sep = "─" * 70
    print(f"\n{sep}")
    print("CODE IMPACT")
    print(sep)

    if report.sessions_with_data == 0:
        if report.sessions_legacy > 0:
            print(
                f"\n{report.sessions_legacy} session(s) in this store predate edit-operation "
                "tracking -- nothing to report yet. Your impact data rebuilds from sessions "
                "scored from now on; nothing else to do."
            )
        else:
            print("\nNo sessions found in this store.")
        if legacy_line:
            print(f"\n{legacy_line}")
        print(sep)
        return EXIT_OK

    print(
        f"\n{report.total_operations} edit operation(s) across "
        f"{report.sessions_with_data} session(s) with impact data"
        + (
            f" ({report.sessions_legacy} additional session(s) predate this tracking, "
            "not counted -- see the CHANGELOG)"
            if report.sessions_legacy
            else ""
        )
    )
    print(f"  +{report.total_additions} / -{report.total_deletions} lines")
    if legacy_line:
        print(f"  {legacy_line}")

    if report.prior_content_unknown_pct is not None:
        print(
            f"  {report.prior_content_unknown_pct:.0f}% of additions are from Write/"
            f"NotebookEdit calls, whose payload never carries the file's PRIOR content "
            "-- additions are exact, but a full-file rewrite looks identical to a "
            "brand-new file, so this fraction is inherently uncertain in that specific way."
        )
    if report.untested_tool_shape_pct is not None and report.untested_tool_shape_operations:
        print(
            f"  {report.untested_tool_shape_pct:.0f}% of operations came from MultiEdit/"
            "NotebookEdit -- extraction paths with ZERO real-corpus verification "
            "(see README). Not presented with the same confidence as Edit/Write."
        )

    if report.top_files:
        print("\nMost-edited files:")
        for f in report.top_files:
            print(
                f"  {f.edits:>4} edits  +{f.additions}/-{f.deletions}  "
                f"({f.sessions_touched} session(s))  {f.path}"
            )

    if report.top_directories:
        print("\nMost-edited directories:")
        for d in report.top_directories:
            print(
                f"  {d.edits:>4} edits  +{d.additions}/-{d.deletions}  "
                f"({d.sessions_touched} session(s))  {d.path}"
            )

    print(sep)
    return EXIT_OK


def _print_ask_legacy_note(db_path: str | None) -> None:
    """Say how many legacy sessions `ask` left out of the numbers it answered from."""
    from tracegauge.legacy import count_legacy
    from tracegauge.store import open_db, resolve_db_path

    try:
        conn = open_db(resolve_db_path(db_path))
        n = count_legacy(conn)
        conn.close()
    except Exception:  # noqa: BLE001 -- a note about exclusions must never fail the answer
        return
    note = excluded_note(n, "the numbers above")
    if note:
        print(f"({note})")


def _run_ask(
    question: str,
    *,
    db_path: str | None = None,
    use_api: bool = False,
    api_model: str = "claude-haiku-4-5-20251001",
    api_key: str | None = None,
    force_recompute: bool = False,
) -> None:
    """Handle `tracegauge ask "<question>"` — the conversational explainer."""
    try:
        require_patterns_extra()
    except PatternsExtraMissing as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    from tracegauge.intelligence.chat import (
        CHAT_EGRESS_NOTICE,
        ChatApiConfig,
        ask_api,
        ask_local,
    )

    print("\nLooking up your session data...", flush=True)

    # --- Try local Ollama first (unless --api is specified) ---
    if not use_api:
        answer = ask_local(
            question,
            db_path=db_path,
            force_recompute=force_recompute,
        )
        if answer:
            print(f"\n{answer}\n")
            print("(answered from measured metrics — local Ollama)")
            _print_ask_legacy_note(db_path)
            return

        # Local unavailable — offer API if key is present
        if not api_key:
            api_key = None
            import os as _os

            api_key = _os.environ.get("ANTHROPIC_API_KEY")

        if api_key:
            print(
                "\nNo local Ollama judge available. "
                "ANTHROPIC_API_KEY is set — the API can answer instead (metrics only, consent required).\n",
            )
            use_api = True
        else:
            print(
                "\nNo LLM available to answer. To enable:\n"
                "  Option 1 — Local (free): install Ollama + pull any 7B+ model\n"
                '  Option 2 — API: export ANTHROPIC_API_KEY=<key> then tracegauge ask --api "<question>"\n'
            )
            return

    # --- API path ---
    if not api_key:
        print("[ERROR] --api requires ANTHROPIC_API_KEY env var or --api-key.", file=sys.stderr)
        return

    # Show consent notice
    print(CHAT_EGRESS_NOTICE)
    try:
        consent = (
            input("\nSend metrics to Anthropic to answer this question? [y/N]: ").strip().lower()
        )
    except (EOFError, KeyboardInterrupt):
        consent = ""

    if consent != "y":
        print("Aborted — nothing sent.")
        return

    cfg = ChatApiConfig(api_key=api_key, model=api_model)
    answer = ask_api(
        question,
        cfg,
        consent_given=True,
        db_path=db_path,
        force_recompute=force_recompute,
    )
    if answer:
        print(f"\n{answer}\n")
        print(f"(answered from measured metrics — {api_model})")
        _print_ask_legacy_note(db_path)
    else:
        print("[ERROR] API call failed. Check your key and try again.", file=sys.stderr)


def _run_rescore(
    *,
    db_path: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    json_mode: bool = False,
) -> int:
    """Handle `tracegauge rescore` -- re-score LEGACY rows from their source transcripts.

    Rows whose transcript is gone are left exactly as they are (counted, never touched). Exit 4
    when any readable row failed to re-score (the others still were), 1 when there is no usable
    store, else 0.
    """
    from tracegauge.store import backfill_turn_counts, backfill_waste, resolve_db_path

    resolved = Path(db_path).expanduser() if db_path else resolve_db_path(None)
    if limit is not None and limit < 1:
        print("[ERROR] --limit must be at least 1.", file=sys.stderr)
        return EXIT_USAGE
    if not resolved.exists():
        print(f"[ERROR] No tracegauge store at {resolved}.", file=sys.stderr)
        return EXIT_USAGE
    try:
        summary = backfill_waste(resolved, only_legacy=True, dry_run=dry_run, limit=limit)
    except Exception as exc:
        print(f"[ERROR] Cannot rescore the tracegauge store: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # Rows stored before the turn count was recorded keep every task type on the bundled band;
    # --limit bounds the legacy rescore only. The JSON document is unchanged (text mode reports it).
    turns: dict[str, int] | None
    try:
        turns = backfill_turn_counts(resolved, dry_run=dry_run)
    except Exception as exc:
        turns = None
        print(f"[WARN] Could not record turn counts on older sessions: {exc}", file=sys.stderr)

    if json_mode:
        emit(rescore_payload(summary, dry_run=dry_run, limit=limit))
        return EXIT_ADAPT_ERROR if summary["errors"] else EXIT_OK

    verb = "would be rescored" if dry_run else "rescored"
    print(
        f"{'DRY RUN -- nothing written. ' if dry_run else ''}Legacy sessions: {summary['legacy_rows']}"
    )
    print(f"  {verb}:{' ' * (24 - len(verb))}{summary['refreshed']}")
    print(f"  skipped (source missing):  {summary['missing_source']}")
    print(f"  skipped (empty stub):      {summary['skipped_stub']}")
    print(f"  failed (parse error):      {summary['errors']}")
    if limit is not None:
        print(f"  not attempted (--limit):   {summary['not_attempted']}")
    if summary["missing_source"]:
        print("  Rows whose transcript is gone were left exactly as they were.")
    if summary["skipped_stub"]:
        print("  Empty stubs (0 turns, 0 tokens) have nothing to rescore: marked current.")
    if turns is not None and turns["updated"]:
        tverb = "would get" if dry_run else "got"
        print(f"  {turns['updated']} older session(s) {tverb} their turn count recorded.")
    if turns is not None and turns["errors"]:
        print(f"  {turns['errors']} older session(s) could not be read for their turn count.")
    return EXIT_ADAPT_ERROR if summary["errors"] else EXIT_OK


def _run_budget(
    *,
    db_path: str | None = None,
    window_days: int = 7,
    json_mode: bool = False,
) -> int:
    """Handle `tracegauge budget` — rolling-window pace + honest self-trend projection."""
    from tracegauge.budget import compute_budget_projection, legacy_rows_excluded_in_window
    from tracegauge.store import open_db, resolve_db_path

    resolved_db = Path(db_path).expanduser() if db_path else resolve_db_path(None)
    try:
        conn = open_db(resolved_db)
    except Exception as exc:
        print(f"[ERROR] Cannot open tracegauge store: {exc}", file=sys.stderr)
        return EXIT_USAGE

    projection = compute_budget_projection(conn, window_days=window_days)
    legacy_excluded = legacy_rows_excluded_in_window(conn, window_days)
    conn.close()

    if json_mode:
        emit(budget_payload(projection, window_days, legacy_excluded))
        return EXIT_OK

    legacy_note = (
        f"({legacy_excluded} legacy session{'s' if legacy_excluded != 1 else ''} in this window "
        "left out: pre-0.15 accounting overcounted ~2x. `tracegauge rescore` recovers those whose "
        "transcript still exists.)"
        if legacy_excluded
        else ""
    )

    if projection is None:
        print(
            f"No sessions with current cost data in the last {window_days} days — "
            "nothing to project yet."
        )
        if legacy_note:
            print(legacy_note)
        return EXIT_OK

    sep = "─" * 70
    print(f"\n{sep}")
    print("BUDGET / PACE")
    print(sep)
    print(f"\n{projection.message}\n")
    if legacy_note:
        print(f"{legacy_note}\n")
    print(sep)
    return EXIT_OK


def _run_cost(
    *,
    db_path: str | None = None,
    week: bool = False,
    month: bool = False,
    since: str | None = None,
    roi: bool = False,
    plan_config: str | None = None,
    json_mode: bool = False,
) -> int:
    """Handle `tracegauge cost` -- a period-scoped spend REPORT (total, session
    count, per-project breakdown), distinct from `tracegauge budget`'s rolling
    self-trend PROJECTION. See tracegauge/cost_period.py's module docstring for why
    this filters on source_mtime, not scored_at.
    """
    from tracegauge.cost_period import compute_period_cost, resolve_period
    from tracegauge.store import open_db, resolve_db_path

    try:
        period_start, period_end, period_label = resolve_period(week=week, month=month, since=since)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_USAGE

    # An explicitly named plan file that does not exist is a usage error, not "no plan configured"
    # (that message is for the default location, which a new user legitimately lacks).
    if roi and plan_config is not None and not Path(plan_config).expanduser().is_file():
        print(f"[ERROR] --plan-config: no such file: {plan_config}", file=sys.stderr)
        return EXIT_USAGE

    resolved_db = Path(db_path).expanduser() if db_path else resolve_db_path(None)
    try:
        conn = open_db(resolved_db)
    except Exception as exc:
        print(f"[ERROR] Cannot open tracegauge store: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report = compute_period_cost(conn, period_start, period_end, period_label=period_label)
    conn.close()

    if json_mode:
        emit(cost_payload(report, cost_roi_payload(report, plan_config) if roi else None))
        return EXIT_OK

    sep = "─" * 70
    print(f"\n{sep}")
    print(f"COST -- {report.period_label}")
    print(sep)

    if (
        report.session_count == 0
        and report.sessions_missing_cost == 0
        and report.legacy_rows_excluded == 0
    ):
        print(
            f"\nNo sessions found in this period ({report.period_start.date()} "
            f"to {report.period_end.date()})."
        )
        print(sep)
        return EXIT_OK

    print(
        f"\nTotal: {format_cost_display(report.total_usd, report.unpriced_models)}  "
        f"({report.session_count} session{'s' if report.session_count != 1 else ''})"
    )
    if report.unpriced_models:
        print(
            "  (priced subtotal only: turns of the unpriced model(s) below are NOT in this "
            "figure -- the real total is higher)"
        )
    if report.sessions_missing_cost:
        print(
            f"  ({report.sessions_missing_cost} additional session"
            f"{'s' if report.sessions_missing_cost != 1 else ''} in this period "
            f"{'have' if report.sessions_missing_cost != 1 else 'has'} no cost "
            "data yet -- excluded from the total above, not counted as $0)"
        )

    if report.legacy_rows_excluded:
        n = report.legacy_rows_excluded
        print(
            f"\n{LEGACY_LABEL}: ${report.legacy_total_usd:,.2f} across {n} session"
            f"{'s' if n != 1 else ''} -- NOT included in the total above; shown only as history. "
            "`tracegauge rescore` recovers those whose transcript still exists."
        )

    if report.by_project:
        print("\nBy project:")
        for b in report.by_project:
            amount = format_cost_display(b.total_usd, b.unpriced_models)
            print(
                f"  {b.project_label:<40}  {amount:>8}  ({b.session_count} session"
                f"{'s' if b.session_count != 1 else ''})"
            )

    _print_cost_subagents(report)

    # Unpriced coverage -- always shown, not gated behind --roi.
    sess_cov = report.session_coverage_pct
    tok_cov = report.token_coverage_pct
    if report.unpriced_models or (
        sess_cov is not None and (sess_cov < 100.0 or (tok_cov is not None and tok_cov < 100.0))
    ):
        if sess_cov is not None:
            print(
                f"\nPriced coverage: {sess_cov:.0f}% of sessions"
                + (f", {tok_cov:.0f}% of tokens" if tok_cov is not None else "")
            )
        if report.unpriced_models:
            print(f"  {format_unpriced(report.unpriced_models)}")
            print(
                f"  ({report.sessions_unpriced} session"
                f"{'s' if report.sessions_unpriced != 1 else ''} not fully priced, "
                f"{report.tokens_unpriced:,} tokens: a session with any unpriced turn "
                "counts as unpriced in full)"
            )
        if report.unpriced_models_incomplete:
            print("  (some unpriced sessions predate model tracking -- can't name their model)")

    if roi:
        _print_cost_roi(report, plan_config)

    print(sep)
    return EXIT_OK


def _print_cost_subagents(report: PeriodCostReport) -> None:
    """Subagent share of the period's spend plus a per-type table (see PeriodSubagents)."""
    sub = report.subagents
    if sub.sessions_not_recorded:
        n = sub.sessions_not_recorded
        print(
            f"\nSubagents: {n} session{'s' if n != 1 else ''} not recorded, re-score "
            "(`tracegauge score <path>`) to include."
        )
    if not sub.by_type:
        return  # nothing to report: no subagents among the recorded sessions
    floor = ">= " if sub.usd_is_floor else ""
    usd_share = (
        f"{sub.share_of_total_usd * 100:.0f}% of spend"
        f"{' (priced part)' if sub.usd_is_floor else ''}"
        if sub.share_of_total_usd is not None
        else ""
    )
    tok_share = (
        f"{sub.share_of_total_tokens * 100:.0f}% of tokens"
        if sub.share_of_total_tokens is not None
        else ""
    )
    shares = ", ".join(x for x in (usd_share, tok_share) if x)
    print(
        f"\nSubagents: {floor}${sub.usd:,.2f}"
        + (f" ({shares})" if shares else "")
        + f" across {sub.sessions_recorded} session{'s' if sub.sessions_recorded != 1 else ''}"
        " with a recorded split."
    )
    for t in sub.by_type:
        floor_t = ">= " if t.usd_is_floor else ""
        if t.usd is None:
            amount = "unpriced"
        elif 0 < t.usd < 0.005:
            amount = f"{floor_t}<$0.01"  # a real sub-cent price must not read as "$0.00"
        else:
            amount = f"{floor_t}${t.usd:,.2f}"
        share = (
            f", {t.share_of_total_usd * 100:.0f}% of spend"
            if t.share_of_total_usd is not None
            else ""
        )
        print(f"  {t.type:<24}  {t.files} file(s), {t.real_tokens:,} tokens, {amount}{share}")


def _print_cost_roi(report: PeriodCostReport, plan_config: str | None) -> None:
    """Plan-cost ROI -- refuses to print a ratio the data can't
    support (no plan configured, or zero priced sessions in the window),
    by design: an unsupported ratio would mislead."""
    from tracegauge.plan import compute_roi, load_plan_config, resolve_plan_config_path

    try:
        plans = load_plan_config(plan_config)
    except ValueError as exc:
        print(f"\n[ERROR] Plan config: {exc}")
        return

    if not plans:
        cfg_path = resolve_plan_config_path(plan_config)
        print(f"\nROI: no plan configured. Create {cfg_path} to enable -- e.g.:")
        print(
            '  {"plans": [{"name": "Claude Max", "monthly_cost_usd": 200, '
            '"effective_from": "2026-01-01"}]}'
        )
        return

    result = compute_roi(
        report.total_usd, report.session_count, plans, report.period_start, report.period_end
    )
    if result is None:
        print("\nROI: no priced sessions in this period -- nothing to compare against plan cost.")
        return

    plan_label = (
        " + ".join(result.plan_names) if len(result.plan_names) > 1 else result.plan_names[0]
    )
    print(f"\nPlan: {plan_label} (${result.plan_cost_usd:.2f} for this window)")
    print(
        f"ROI: ${result.api_equivalent_usd:.2f} API-equivalent / "
        f"${result.plan_cost_usd:.2f} plan cost = {result.multiple:.1f}x"
    )
    if report.legacy_rows_excluded:
        print(
            f"  (corrected spend only: {report.legacy_rows_excluded} legacy session(s) are "
            "excluded, so the multiple is a floor.)"
        )
    print(
        "  (API-equivalent value at measured token rates, not a bill you'd "
        "actually pay under a flat plan.)"
    )
    if report.unpriced_models:
        print(
            f"  (API-equivalent excludes {format_unpriced(report.unpriced_models)} -- "
            "the multiple is a floor.)"
        )


def _run_monitor(
    *,
    cc_path_arg: str | None = None,
    db_path: str | None = None,
    stability_window: int = 300,
    plan_type: str = "usage_based",
    json_mode: bool = False,
) -> int:
    """Handle `tracegauge monitor` — one-shot live check of the currently active session.

    Returns EXIT_ALARM when the alarm fires, else EXIT_OK (including "no active session": an
    idle machine is not a failure for a hook that calls this between sessions).
    """
    from tracegauge.alarm import AlarmConfig, check_alarm, threshold_for_live
    from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
    from tracegauge.live_monitor import find_active_session, score_live_session
    from tracegauge.self_baseline import SelfBaselineState
    from tracegauge.store import resolve_db_path

    cc_path = _resolve_cc_path(cc_path_arg)
    active = find_active_session(cc_path, stability_window)
    if active is None:
        if json_mode:
            emit(monitor_payload("no_active_session", str(cc_path)))
        else:
            print(
                f"No active session detected under {cc_path} "
                f"(nothing modified in the last {stability_window}s)."
            )
        return EXIT_OK

    live = score_live_session(active, _PRICES)
    if live is None:
        if json_mode:
            emit(monitor_payload("insufficient_data", str(cc_path), source_path=str(active)))
        else:
            print(f"Active session found ({active.name}) but not enough data to score yet.")
        return EXIT_OK

    resolved_db = Path(db_path).expanduser() if db_path else resolve_db_path(None)
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    config = AlarmConfig(enabled=True, plan_type=plan_type)
    threshold = threshold_for_live(live, resolved_db, baselines, config)
    alarm = check_alarm(live, SelfBaselineState(), config, threshold)

    if json_mode:
        emit(
            monitor_payload(
                "ok",
                str(cc_path),
                live=live,
                alarm=alarm,
                threshold=threshold,
                legacy_rows_excluded=_db_legacy_count(resolved_db),
            )
        )
        return EXIT_ALARM if alarm is not None else EXIT_OK

    print(f"Session: {live.session_id}  ({live.task_type})")
    live_cost = format_cost_display(live.live_cost_usd, live.live_unpriced_models, approx=True)
    print(f"  {live_cost} (estimated, in progress)")
    print(f"  ~{live.live_context_tokens:,} context tokens (estimated, in progress)")
    print(f"  {live.live_resend_ratio * 100:.0f}% context re-send (measured)")
    print(f"\n{live.domain_of_validity}")

    if alarm is not None:
        print(f"\n[ALARM] {alarm.message}")
        return EXIT_ALARM
    if threshold.status != "active":
        print(f"\nNo alarm: {threshold.reason}.")
    else:
        print(f"\nNo alarm (threshold: {threshold.reason}).")
    return EXIT_OK


# Commands that open the store: the first run after an upgrade tells the user about legacy rows.
# (`rescore` and `backfill-waste` are the fix, `quickstart` never reads the store.)
_STORE_COMMANDS = frozenset(
    {
        "score",
        "cost",
        "budget",
        "monitor",
        "serve",
        "impact",
        "patterns",
        "ask",
        "export-contribution",
    }
)


def main() -> None:
    """CLI entry point."""
    # Ensure UTF-8 output on Windows (cp1252 console cannot encode ═/─ box-drawing chars)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Deprecated TES_* variables still work; say so once, on stderr, before anything else.
    if (env_line := deprecation_line()) is not None:
        print(env_line, file=sys.stderr)

    parser = argparse.ArgumentParser(
        prog="tracegauge",
        description=(
            "tracegauge: token economy, trajectory quality and waste report for Claude Code sessions."
        ),
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help=(
            "Suppress the one-time notice about stored sessions from an older version "
            "(same as TRACEGAUGE_NO_NOTICE=1). Goes before the command: tracegauge --quiet cost --week."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    score_p = sub.add_parser(
        "score",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2, EXIT_ADAPT_ERROR),
        help="Score CC session log(s).",
        description=(
            "Score one or more Claude Code session JSONL files. "
            "PATH may be a single .jsonl file or a directory of .jsonl files. "
            "Secret redaction is ON by default at ingestion."
        ),
    )
    score_p.add_argument(
        "path",
        metavar="PATH",
        nargs="?",
        default=None,
        help=(
            "Optional path to a CC session JSONL file or directory of JSONL files. "
            "If omitted, the most recent session under ~/.claude/projects is scored. "
            "Use --pick to choose from a list instead."
        ),
    )
    score_p.add_argument(
        "--pick",
        action="store_true",
        help="Choose from a numbered list of your recent sessions instead of scoring the newest.",
    )
    score_p.add_argument(
        "--cc-path",
        default=None,
        dest="cc_path",
        metavar="PATH",
        help="Claude Code projects directory to search (default: ~/.claude/projects).",
    )
    score_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Output full ThreeAxisResult as JSON (includes domain-of-validity strings).",
    )
    score_p.add_argument(
        "--judge",
        action="store_true",
        help=(
            "Run the trajectory-quality judge. Auto-detects a local Ollama judge; if none is "
            "found but an API key is in your environment, offers the API judge (consent "
            "required before any data is sent). With neither, prints the single simplest "
            "setup step — token + waste axes always run regardless."
        ),
    )
    score_p.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip trajectory quality axis even if a judge is available.",
    )
    score_p.add_argument(
        "--judge-model",
        default="qwen3:30b-a3b",
        metavar="MODEL",
        help="Ollama model name for the trajectory judge (default: qwen3:30b-a3b).",
    )
    score_p.add_argument(
        "--judge-endpoint",
        default="http://localhost:11434",
        metavar="URL",
        help="Ollama endpoint URL (default: http://localhost:11434).",
    )
    score_p.add_argument(
        "--api-judge",
        action="store_true",
        dest="api_judge",
        help=(
            "Use the Anthropic API as trajectory judge (opt-in). "
            "Requires ANTHROPIC_API_KEY env var or --api-judge-key. "
            "Shows a consent screen before sending any session data. "
            "Uses the same validated v3 rubric as the local judge. "
            "Cannot be combined with --no-judge."
        ),
    )
    score_p.add_argument(
        "--api-judge-model",
        default="claude-haiku-4-5-20251001",
        metavar="MODEL",
        dest="api_judge_model",
        help="Anthropic model for the API judge (default: claude-haiku-4-5-20251001).",
    )
    score_p.add_argument(
        "--api-judge-key",
        default=None,
        metavar="KEY",
        dest="api_judge_key",
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var).",
    )

    sub.add_parser(
        "quickstart",
        help=(
            "Score a bundled sample Claude Code session -- no files to create, no local judge, "
            "no network call. Prints a real three-axis report immediately after install."
        ),
    )

    backfill_p = sub.add_parser(
        "backfill-waste",
        help="Re-run frozen detectors on all stored sessions; fix stale waste counts.",
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2, EXIT_ADAPT_ERROR),
        description=(
            "Re-run REPEATED-FAILED-RETRY and REDUNDANT-READ detectors on every session "
            "in the store whose source file is accessible, embed per-event wasted_cost_usd "
            "(redundant turns only, P5 cost model), and write correct waste_event_count + "
            "waste_events to the store. Fixes the stale-zeros bug from sessions scored "
            "before waste detection was fully wired. Detectors are frozen (byte-verbatim). "
            "Also re-scores rows whose token counts came from the pre-dedupe adapter "
            "(Claude Code writes one record per content block, each repeating the response's "
            "usage; those rows were counted ~2.4x too high): real_tokens, cost and the verdict "
            "band are refreshed from the source transcript."
        ),
    )
    backfill_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )

    rescore_p = sub.add_parser(
        "rescore",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2, EXIT_ADAPT_ERROR),
        help="Re-score legacy (pre-0.15 accounting) sessions from their source transcripts.",
        description=(
            "Re-score every stored session written before usage de-duplication / 1-hour cache "
            "pricing whose source transcript still exists, with the current adapter and price "
            "table. Rows whose transcript is gone are left untouched (and stay excluded from "
            "baselines, the alarm, budget and cost totals). Idempotent: a second run changes "
            "nothing. Waste events are re-detected for the rescored rows; judge verdicts are kept."
        ),
    )
    rescore_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    rescore_p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Compute and report what would change; open the store read-only and write nothing.",
    )
    rescore_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Re-score at most N sessions this run (most recently written first).",
    )
    rescore_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Print one JSON document (counts) instead of text.",
    )

    serve_p = sub.add_parser(
        "serve",
        help="Launch background watcher + localhost dashboard (token+waste auto-scoring).",
        description=(
            "Start the tracegauge service: a background scan loop that auto-scores finished CC sessions "
            "(token economy + deterministic waste) and a web dashboard on localhost. "
            "Judge is OFF by default — token+waste run continuously, trajectory requires --background-judge."
        ),
    )
    serve_p.add_argument(
        "--port",
        type=int,
        default=4747,
        metavar="PORT",
        help="Dashboard port (default: 4747).",
    )
    serve_p.add_argument(
        "--scan-interval",
        type=int,
        default=120,
        dest="scan_interval",
        metavar="SECONDS",
        help="Seconds between scan cycles (default: 120).",
    )
    serve_p.add_argument(
        "--stability-window",
        type=int,
        default=300,
        dest="stability_window",
        metavar="SECONDS",
        help="Seconds a session file must be unmodified before scoring (default: 300).",
    )
    serve_p.add_argument(
        "--cc-path",
        default=None,
        dest="cc_path",
        metavar="PATH",
        help="Path to Claude Code projects directory (default: ~/.claude/projects).",
    )
    serve_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    serve_p.add_argument(
        "--background-judge",
        action="store_true",
        dest="background_judge",
        help=(
            "Enable trajectory judge in the background watcher (local Ollama only). "
            "Requires Ollama + qwen3:30b-a3b (~18 GB VRAM). "
            "Setup: install Ollama (https://ollama.ai) then 'ollama pull qwen3:30b-a3b'. "
            "For on-demand judging without a GPU: use 'tracegauge score <path> --api-judge' instead."
        ),
    )
    serve_p.add_argument(
        "--alarm",
        action="store_true",
        dest="alarm",
        help=(
            "Enable the live cost/context alarm (OFF by default). Data-gated: only fires when "
            "the active session's context already exceeds your own p75 for that task_type AND "
            "context re-send is the dominant cost driver. Never fires on a normal session."
        ),
    )
    serve_p.add_argument(
        "--plan",
        default="usage_based",
        dest="plan_type",
        choices=["usage_based", "max"],
        help=(
            "Billing plan, for alarm display emphasis only (default: usage_based). "
            "'max' leads with tokens/context and demotes the dollar figure to a parenthetical "
            "API-equivalent note; the dollar figure is never hidden outright."
        ),
    )

    export_p = sub.add_parser(
        "export-contribution",
        help="Export a redacted, content-free local file for the optional corpus contribution program.",
        description=(
            "Build an allow-listed, content-free summary of your scored sessions and write it "
            "to a local file you can inspect. NOTHING is transmitted — tracegauge has no server. "
            "Shows a preview and requires explicit confirmation before writing."
        ),
    )
    export_p.add_argument(
        "--output",
        default=None,
        dest="output",
        metavar="PATH",
        help="Output file path (default: ~/.tes/contribution-<date>.jsonl).",
    )
    export_p.add_argument(
        "--anonymous",
        action="store_true",
        help="Omit contributor_id from all rows.",
    )
    export_p.add_argument(
        "--preview",
        action="store_true",
        help="Show the sample row and field list without writing any file.",
    )
    export_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )

    ask_p = sub.add_parser(
        "ask",
        help="Ask a natural-language question about your sessions (conversational explainer).",
        description=(
            "Ask questions about your session history in plain language. "
            "The LLM answers ONLY from already-measured metrics and ML pattern results — "
            "it never invents analysis, predicts future costs, or judges session quality. "
            "Tries local Ollama first; with ANTHROPIC_API_KEY set, offers the API path "
            "(sends metrics only — no session content — with your consent)."
        ),
    )
    ask_p.add_argument(
        "question",
        metavar="QUESTION",
        help='Question about your sessions, e.g. "What kind of sessions do I run?"',
    )
    ask_p.add_argument(
        "--api",
        action="store_true",
        help=(
            "Use the Anthropic API for answering (opt-in). Requires ANTHROPIC_API_KEY. "
            "Sends corpus metrics only — no session content — with your explicit consent."
        ),
    )
    ask_p.add_argument(
        "--api-model",
        default="claude-haiku-4-5-20251001",
        metavar="MODEL",
        dest="api_model",
        help="Anthropic model for the API chat path (default: claude-haiku-4-5-20251001).",
    )
    ask_p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        dest="api_key",
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var).",
    )
    ask_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    ask_p.add_argument(
        "--recompute",
        action="store_true",
        help="Force re-computation of ML patterns instead of using cached results.",
    )

    patterns_p = sub.add_parser(
        "patterns",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2),
        help="Show the session archetypes and anomaly summary (ML pattern analysis).",
        description=(
            "Run or display the ML pattern analysis: validated clustering of your session corpus "
            "into behavioral archetypes, plus statistical anomaly detection. "
            "Results are cached to ~/.tes/intelligence_cache.json and re-used by 'tracegauge ask'."
        ),
    )
    patterns_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    patterns_p.add_argument(
        "--recompute",
        action="store_true",
        help="Force re-computation even if a fresh cache exists.",
    )
    patterns_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Print one JSON document (schema_version, valid, status, analysis) instead of text.",
    )

    impact_p = sub.add_parser(
        "impact",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2),
        help="Code-impact reconstruction: additions/deletions, churn ranking, from Edit/Write payloads.",
        description=(
            "Corpus-wide, from Edit/Write/MultiEdit/NotebookEdit tool-call payloads persisted at "
            "score time. Plain counts only -- no composite risk score, no ranking weight invented. "
            "MultiEdit/NotebookEdit extraction is written but has zero real-corpus verification "
            "(see the README) and is flagged inline wherever it contributes to a total."
        ),
    )
    impact_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    impact_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Print one JSON document (schema_version, counts, top files/directories) instead of text.",
    )
    impact_p.add_argument(
        "--top",
        type=int,
        default=10,
        dest="top_n",
        metavar="N",
        help="Number of files/directories to show in the churn ranking (default: 10).",
    )

    corpus_p = sub.add_parser(
        "corpus",
        help="Community corpus: opt-in contribution and withdrawal. NOT YET ACTIVE — no corpus is operated.",
        description=(
            "Opt-in transmission of content-free session aggregates to the tracegauge "
            "community corpus (Supabase), and withdrawal of your contributed rows. "
            "This is the ONLY tracegauge command that sends session-derived data "
            "off-machine without a per-call API key you typed in yourself. "
            "NOT YET ACTIVE: no public corpus is currently operated, so these "
            "subcommands print [NOT AVAILABLE] and do nothing until one is "
            "provisioned — see PRIVACY.md."
        ),
    )
    corpus_sub = corpus_p.add_subparsers(dest="corpus_command")

    corpus_contribute_p = corpus_sub.add_parser(
        "contribute",
        help=(
            "Preview + consent + send content-free session aggregates to the "
            "community corpus. NOT YET ACTIVE — no corpus is operated."
        ),
    )
    corpus_contribute_p.add_argument(
        "--anonymous",
        action="store_true",
        help="Omit contributor_id from all rows (rows cannot be individually withdrawn later).",
    )
    corpus_contribute_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )

    corpus_sub.add_parser(
        "withdraw",
        help=(
            "Delete every row tied to your contributor_id from the community "
            "corpus. NOT YET ACTIVE — no corpus is operated."
        ),
    )
    corpus_sub.add_parser(
        "reset-id",
        help="Generate a new contributor_id (local only, no network). Prior rows become unlinked.",
    )

    budget_p = sub.add_parser(
        "budget",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2),
        help="Show your rolling-window spend pace and an honest self-trend projection.",
        description=(
            "Rolling-window spend/token pace tracking. The projection is YOUR OWN trend, "
            "labeled with its sample size and window — never a forecast of future work."
        ),
    )
    budget_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    budget_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Print one JSON document (schema_version, pace, projection, priced) instead of text.",
    )
    budget_p.add_argument(
        "--window-days",
        type=int,
        default=7,
        dest="window_days",
        metavar="N",
        help="Rolling window size in days (default: 7).",
    )

    cost_p = sub.add_parser(
        "cost",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2),
        help="Spend report for a period: total, session count, per-project breakdown.",
        description=(
            "A period-scoped spend REPORT (what you actually spent), distinct from "
            "`tracegauge budget`'s rolling self-trend PROJECTION (where your pace is heading). "
            "--week/--month are rolling N-day windows ending now, not calendar-aligned."
        ),
    )
    cost_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    cost_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Print one JSON document (schema_version, totals, by_project, priced, roi) instead of text.",
    )
    cost_period_group = cost_p.add_mutually_exclusive_group(required=True)
    cost_period_group.add_argument(
        "--week",
        action="store_true",
        help="Rolling last 7 days.",
    )
    cost_period_group.add_argument(
        "--month",
        action="store_true",
        help="Rolling last 30 days.",
    )
    cost_period_group.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="From this date (inclusive) through now.",
    )
    cost_p.add_argument(
        "--roi",
        action="store_true",
        help=(
            "Also report plan ROI: API-equivalent spend against your configured plan "
            "cost for this window. Requires ~/.tes/plan.json (or --plan-config) -- "
            "prints nothing if no plan is configured or the window has no priced sessions."
        ),
    )
    cost_p.add_argument(
        "--plan-config",
        default=None,
        dest="plan_config",
        metavar="PATH",
        help="Path to plan config JSON (default: ~/.tes/plan.json, or TRACEGAUGE_PLAN_PATH env var).",
    )

    monitor_p = sub.add_parser(
        "monitor",
        formatter_class=ExitCodeHelpFormatter,
        epilog=epilog(EXIT_OK, EXIT_USAGE, 2, EXIT_ALARM),
        help="One-shot live check of the currently active (in-progress) CC session.",
        description=(
            "Score the session currently being written, print its estimated cost/context "
            "(labeled 'in progress'), and check the data-gated cost/context alarm once."
        ),
    )
    monitor_p.add_argument(
        "--cc-path",
        default=None,
        dest="cc_path",
        metavar="PATH",
        help="Claude Code projects directory to search (default: ~/.claude/projects).",
    )
    monitor_p.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        metavar="PATH",
        help="Path to the tracegauge database (default: ~/.tes/tes.db, or TRACEGAUGE_DB_PATH env var).",
    )
    monitor_p.add_argument(
        "--stability-window",
        type=int,
        default=300,
        dest="stability_window",
        metavar="SECONDS",
        help="A session modified more recently than this is considered 'active' (default: 300).",
    )
    monitor_p.add_argument(
        "--plan",
        default="usage_based",
        dest="plan_type",
        choices=["usage_based", "max"],
        help="Billing plan, for alarm display emphasis only (default: usage_based).",
    )
    monitor_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Print one JSON document (schema_version, status, live figures, alarm) instead of text.",
    )

    args = parser.parse_args()
    if args.command in _STORE_COMMANDS or args.command is None:
        # stderr, once per store per version; never raises, never blocks (see tracegauge.legacy).
        from tracegauge.legacy import maybe_notify

        maybe_notify(getattr(args, "db_path", None), quiet=args.quiet)
    if args.command is None:
        # Bare `tracegauge` does the obvious useful thing: launch the dashboard.
        # (`tracegauge --help` still shows help; `tracegauge <unknown>` still errors via argparse.)
        _run_serve()
        sys.exit(0)

    if args.command == "quickstart":
        from importlib import resources

        from tracegauge.quickstart_history import synthetic_state

        print(
            "tracegauge quickstart -- no local judge, no network call, no files of yours read.\n"
            "Scoring a bundled sample Claude Code session (token economy, deterministic waste "
            "detection, cost annotation -- the trajectory-quality axis is skipped, since that "
            "needs a local Ollama judge or an API key, neither required for this demo).\n"
            "SYNTHETIC HISTORY: the token reading compares the sample with 12 invented sessions.\n"
            "A token verdict compares a session with 10 or more of YOUR OWN sessions of the same "
            "task type. You have none to compare here, so 12 invented research-recon sessions ship "
            "with the demo. They are not your sessions, are held in memory only and are never "
            "written to your store.\n"
        )
        sample_path = resources.files("tracegauge.data") / "quickstart_sample_session.jsonl"
        with resources.as_file(sample_path) as concrete_path:
            try:
                score_path(
                    concrete_path,
                    load_baselines(),
                    JudgeConfig(),
                    use_judge=False,
                    json_mode=False,
                    self_baseline=synthetic_state(load_baselines()),
                )
            except SessionAdaptError:
                sys.exit(EXIT_ADAPT_ERROR)
        print(
            "Example -- what TRAJECTORY QUALITY looks like with a judge configured\n"
            "(illustrative only: a fixed example, not computed from this session --\n"
            "quickstart never probes or calls a judge):\n"
            "\n"
            "  Verdict:     BETTER (score: 1)\n"
            "  Reasoning:   Direct, minimal-detour path to the stated goal; no\n"
            "               backtracking or redundant exploration.\n"
        )
        print(
            "This ran entirely from what shipped in the installed package. Next: `tracegauge score` "
            "(no path) scores your own most recent real Claude Code session, or "
            "`tracegauge score --judge` once you have a local Ollama judge or an API key configured."
        )
        sys.exit(0)

    if args.command == "backfill-waste":
        from pathlib import Path as _Path

        from tracegauge.store import backfill_waste

        db_path = _Path(args.db_path).expanduser() if args.db_path else None
        print("Running waste backfill — re-running frozen detectors on all accessible sessions...")
        summary = backfill_waste(db_path=db_path)
        print(f"  Sessions with waste written: {summary['updated']}")
        print(f"  Sessions confirmed 0-waste:  {summary['no_waste']}")
        print(f"  Source files not accessible: {summary['missing_source']}")
        print(f"  Errors (left unchanged):     {summary['errors']}")
        print(f"  Rows re-scored (pre-dedupe usage): {summary['refreshed']}")
        total_processed = summary["updated"] + summary["no_waste"]
        print(f"  Total processed: {total_processed}")
        print(
            f"  Summary: rescored: {summary['refreshed']}, "
            f"skipped (source missing): {summary['missing_source']}, "
            f"failed: {summary['errors']}"
        )
        if summary["errors"]:
            print(
                "  A failed row (transcript has no usage records, is unreadable, or its cost "
                "could not be computed) was left exactly as it was."
            )
        sys.exit(EXIT_ADAPT_ERROR if summary["errors"] else EXIT_OK)

    if args.command == "rescore":
        sys.exit(
            _run_rescore(
                db_path=args.db_path,
                dry_run=args.dry_run,
                limit=args.limit,
                json_mode=args.json_mode,
            )
        )

    if args.command == "serve":
        _run_serve(
            port=args.port,
            scan_interval=args.scan_interval,
            stability_window=args.stability_window,
            cc_path_arg=args.cc_path,
            db_path_arg=args.db_path,
            background_judge=args.background_judge,
            alarm_enabled=args.alarm,
            plan_type=args.plan_type,
        )
        sys.exit(0)

    if args.command == "budget":
        sys.exit(
            _run_budget(
                db_path=args.db_path, window_days=args.window_days, json_mode=args.json_mode
            )
        )

    if args.command == "cost":
        sys.exit(
            _run_cost(
                db_path=args.db_path,
                week=args.week,
                month=args.month,
                since=args.since,
                roi=args.roi,
                plan_config=args.plan_config,
                json_mode=args.json_mode,
            )
        )

    if args.command == "monitor":
        sys.exit(
            _run_monitor(
                cc_path_arg=args.cc_path,
                db_path=args.db_path,
                stability_window=args.stability_window,
                plan_type=args.plan_type,
                json_mode=args.json_mode,
            )
        )

    if args.command == "export-contribution":
        from datetime import date as _date

        from tracegauge.contribution import build_contribution_payload, get_or_create_contributor_id
        from tracegauge.store import open_db as _open_db
        from tracegauge.store import resolve_db_path as _resolve_db_path

        db_path = Path(args.db_path).expanduser() if args.db_path else None

        try:
            conn = _open_db(db_path)
        except Exception as exc:
            print(f"[ERROR] Cannot open tracegauge store: {exc}", file=sys.stderr)
            sys.exit(1)

        contributor_id: str | None = None if args.anonymous else get_or_create_contributor_id()

        try:
            payload = build_contribution_payload(
                conn,
                contributor_id=contributor_id,
                include_source_components=True,
            )
        except Exception as exc:
            print(f"[ERROR] Failed to build contribution payload: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        if payload.manifest.row_count == 0:
            if payload.manifest.legacy_rows_excluded:
                print(
                    f"No current sessions to export: {payload.manifest.legacy_rows_excluded} "
                    "legacy session(s) were left out (overcounted ~2x). "
                    "Run `tracegauge rescore` or score new sessions."
                )
            else:
                print(
                    "No sessions found in store. Run `tracegauge score` or `tracegauge serve` first."
                )
            conn.close()
            sys.exit(0)

        today_str = _date.today().isoformat()
        out_path = (
            Path(args.output).expanduser()
            if args.output
            # Issue #17: default output derived from the RESOLVED db_path
            # (same class of fix as intelligence/cache.py's _cache_path)
            # -- not a fixed ~/.tes/ regardless of --db-path/
            # TRACEGAUGE_DB_PATH, which used to drop this file into the real
            # ~/.tes/ even when running against an isolated/scratch DB.
            else _resolve_db_path(db_path).parent / f"contribution-{today_str}.jsonl"
        )

        _print_contribution_preview(payload, out_path)

        if args.preview:
            print("\n[--preview mode: no file written]")
            conn.close()
            sys.exit(0)

        try:
            answer = input("\nContinue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if answer != "y":
            print("Aborted — no file written.")
            conn.close()
            sys.exit(0)

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                for row in payload.rows:
                    fh.write(json.dumps(row) + "\n")
            print(f"\nWritten: {out_path}")
            print(f"  {payload.manifest.row_count} row(s)")
            if payload.manifest.legacy_rows_excluded:
                print(f"  {payload.manifest.legacy_rows_excluded} legacy session(s) left out")
            print("  Open the file to inspect it. Nothing has been transmitted.")
        except Exception as exc:
            print(f"[ERROR] Failed to write file: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        conn.close()
        sys.exit(0)

    if args.command == "corpus":
        _run_corpus(args)
        sys.exit(0)

    if args.command == "patterns":
        sys.exit(
            _run_patterns(
                db_path=args.db_path,
                force_recompute=args.recompute,
                json_mode=args.json_mode,
            )
        )

    if args.command == "impact":
        sys.exit(_run_impact(db_path=args.db_path, top_n=args.top_n, json_mode=args.json_mode))

    if args.command == "ask":
        import os as _os

        _run_ask(
            question=args.question,
            db_path=args.db_path,
            use_api=args.api,
            api_model=args.api_model,
            api_key=getattr(args, "api_key", None) or _os.environ.get("ANTHROPIC_API_KEY"),
            force_recompute=args.recompute,
        )
        sys.exit(0)

    # --- score command ---
    import os as _os  # noqa: PLC0415

    # Contradictory judge flags — fail fast and clearly (never a cryptic argparse error).
    if getattr(args, "no_judge", False) and getattr(args, "judge", False):
        print("[ERROR] --judge and --no-judge are mutually exclusive.", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "api_judge", False) and getattr(args, "no_judge", False):
        print("[ERROR] --api-judge and --no-judge are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    judge_config = JudgeConfig(
        model=args.judge_model,
        endpoint=args.judge_endpoint,
    )

    # ----- Resolve which session(s) to score (explicit PATH > --pick > newest). -----
    session_paths = _resolve_score_targets(args)
    if not session_paths:
        # _resolve_score_targets already printed why (no sessions, bad path, or aborted pick).
        sys.exit(0 if getattr(args, "pick", False) else 1)

    # ----- Resolve the judge plan: auto-detect + guide. Consent stays the egress gate. -----
    # Detecting an API key NEVER sends data: any API-judge call still passes the
    # unconditional per-session consent prompt below.
    use_local_judge = False
    want_api = getattr(args, "api_judge", False)

    if getattr(args, "no_judge", False):
        pass  # judge explicitly skipped — token + waste still run
    elif want_api:
        pass  # explicit API path — handled by want_api below
    elif getattr(args, "judge", False):
        # Explicit --judge: auto-detect the best available judge.
        if is_judge_available(judge_config):
            use_local_judge = True
        elif detect_env_api_key() is not None:
            # An API key is present. OFFER the API judge (still consent-gated below).
            # We do NOT send anything here — we route to the consent screen.
            print(
                "\nNo local judge detected — but ANTHROPIC_API_KEY is set in your environment.\n"
                "The API judge can run instead (your key; sends trajectory data to Anthropic).\n"
                "Review the consent notice below — NOTHING is sent until you confirm.\n",
                file=sys.stderr,
            )
            want_api = True
        else:
            # Neither available: the single simplest next step, never a cryptic fail.
            print(f"\n{JUDGE_SETUP_HINT_FULL}\n", file=sys.stderr)
    else:
        # Default (no judge flag): attempt the local judge if present — behavior preserved.
        use_local_judge = True

    # Build the API judge config when the API path is in play (explicit or offered).
    api_judge_config: ApiJudgeConfig | None = None
    api_judge_consent: bool = False
    api_key_source = ""
    if want_api:
        api_key = getattr(args, "api_judge_key", None) or _os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print(
                "[ERROR] --api-judge requires ANTHROPIC_API_KEY env var or --api-judge-key.",
                file=sys.stderr,
            )
            sys.exit(1)
        api_judge_config = ApiJudgeConfig(
            api_key=api_key,
            model=getattr(args, "api_judge_model", "claude-haiku-4-5-20251001"),
        )
        api_key_source = (
            "--api-judge-key argument"
            if getattr(args, "api_judge_key", None)
            else "ANTHROPIC_API_KEY env var"
        )

    # API judge consent: obtained once before scoring any sessions. UNCONDITIONAL egress gate.
    # Declining does not abort the run — token + waste axes still score (a complete result).
    if api_judge_config is not None:
        notice_session_id = "this session" if len(session_paths) > 1 else session_paths[0].stem
        notice = build_api_judge_consent_notice(
            session_id=notice_session_id,
            task_type="auto-detected",
            model=api_judge_config.model,
            api_key_source=api_key_source,
        )
        print(notice)
        try:
            answer = input("\nContinue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            print("Aborted — no data sent. Scoring token + waste axes only.")
            api_judge_config = None  # no egress
            api_judge_consent = False
        else:
            api_judge_consent = True

    # Print judge on-ramp hint when local judge would be used but is unavailable.
    if use_local_judge and not is_judge_available(judge_config):
        print(f"\n{JUDGE_SETUP_HINT_FULL}\n", file=sys.stderr)

    from tracegauge.store import open_db  # noqa: PLC0415

    store_conn = None
    try:
        store_conn = open_db()
    except Exception:
        pass

    failed: list[Path] = []
    try:
        for sp in session_paths:
            try:
                _score_path_with_api_judge(
                    sp,
                    baselines,
                    judge_config,
                    use_local_judge,
                    args.json_mode,
                    store_conn=store_conn,
                    api_judge_config=api_judge_config,
                    api_judge_consent=api_judge_consent,
                )
            except SessionAdaptError:
                # Already reported on stderr; keep scanning the rest, fail at the end.
                failed.append(sp)
            if not args.json_mode and len(session_paths) > 1:
                print()
    finally:
        if store_conn is not None:
            store_conn.close()

    if failed:
        print(
            f"[ERROR] {len(failed)} of {len(session_paths)} session(s) could not be parsed: "
            + ", ".join(str(f) for f in failed),
            file=sys.stderr,
        )
        sys.exit(EXIT_ADAPT_ERROR)


# The old command name. Printed to stderr (never stdout, so --json and pipes are untouched), once per
# process, before anything else, and not again per subcommand. Removed together with the name in 0.18.
TES_DEPRECATION_NOTICE = (
    "tes is deprecated; use tracegauge (or tgauge). It will be removed in 0.18."
)


def tes_main() -> None:
    """Entry point of the deprecated ``tes`` command: print the one-line notice, then run ``main``.

    Set TRACEGAUGE_NO_DEPRECATION_NOTICE=1 (or true/yes) to silence the notice.
    """
    if not notice_suppressed():
        print(TES_DEPRECATION_NOTICE, file=sys.stderr)
    main()
