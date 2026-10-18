# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/watcher.py — Background scan loop for auto-scoring finished CC sessions.

Trigger: scheduled-scan + file-stability (sole P2 mechanism).
- Scans cc_path recursively for *.jsonl files (subagent transcripts excluded; their
  usage is rolled up into the parent session by tracegauge.adapt).
- Skips files modified within the stability window (not yet "finished").
- Skips sessions already scored with the same file hash (incremental ledger).
- Scores token + waste axes via the unchanged P1 pipeline.
- Judge is OFF by default; opt-in via background_judge=True (--background-judge flag).
- Writes results to the shared SQLite store.

SessionEnd hook: deferred to a future `tracegauge install-hook` command (opt-in, explicit).
Rationale: configuring ~/.claude/settings.json without user consent violates the
no-surprises/moat posture. The hook IS a real fast-path (exists in CC) but must
be user-initiated, not automatic.
"""

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tracegauge._digest import reconstruct_digest
from tracegauge.adapt import adapt_session
from tracegauge.alarm import AlarmConfig, check_alarm, threshold_for_live
from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.cost import SessionCost, compute_session_cost, load_price_table
from tracegauge.discovery import iter_session_files
from tracegauge.live_monitor import find_active_session, score_live_session
from tracegauge.score import ThreeAxisResult, score_session
from tracegauge.self_baseline import load_or_compute
from tracegauge.store import (
    backfill_turn_counts,
    file_hash,
    needs_scoring,
    open_db,
    resolve_db_path,
    upsert_session,
)
from tracegauge.waste import annotate_waste_costs, build_waste_entry

logger = logging.getLogger(__name__)

DEFAULT_CC_PATH: Path = Path.home() / ".claude" / "projects"
DEFAULT_SCAN_INTERVAL: int = 120  # seconds between scans
DEFAULT_STABILITY_WINDOW: int = 300  # seconds a file must be unmodified to be "finished"


@dataclass
class WatcherConfig:
    cc_path: Path = field(default_factory=lambda: DEFAULT_CC_PATH)
    scan_interval: int = DEFAULT_SCAN_INTERVAL
    stability_window: int = DEFAULT_STABILITY_WINDOW
    db_path: Path | None = None
    background_judge: bool = False  # OFF by default — opt-in only, see spec discipline 3
    alarm_enabled: bool = False  # OFF by default — opt-in live cost/context alarm (0.10.0)
    plan_type: str = "usage_based"  # "usage_based" | "max" — alarm display emphasis only


def score_session_file(
    path: Path,
    baselines: dict,
    *,
    use_judge: bool = False,
    self_baseline=None,
    prices: dict | None = None,
) -> ThreeAxisResult | None:
    """Score a single CC session JSONL file. Returns None on any scoring error.

    Calling convention is identical to the P1 CLI (behavior-preservation guarantee):
      adapt → build_waste_entry → score_session(judge_entry=None unless use_judge).
    Same session → same ThreeAxisResult whether called from the watcher or `tracegauge score`.

    self_baseline: pass load_or_compute() result to route through user's own baseline.
    When None, falls through to B2 corpus (backward-compatible default).
    prices: pre-loaded price table from load_price_table(). Loaded once at startup
    and passed per-session — never reloaded inside this function.
    """
    try:
        record = adapt_session(path)

        session_id: str = record.get("session_id", path.stem)
        turns: list[dict] = record.get("digest", {}).get("turns", [])
        waste_entry = build_waste_entry(session_id, turns)

        judge_entry: dict | None = None
        if use_judge:
            # background_judge opt-in path. Lazy import so the judge module
            # is never loaded (or its GPU checks triggered) unless opted in.
            from tracegauge.judge import JudgeConfig, score_trajectory  # noqa: PLC0415

            judge_entry = score_trajectory(record, JudgeConfig())

        # Cost annotation: compute from measured tokens at per-turn rates.
        # Price table is passed in (loaded once at startup by run_watcher).
        session_cost: SessionCost | None = None
        digest = None
        try:
            digest_dict = record.get("digest", {})
            if digest_dict and digest_dict.get("turns"):
                digest = reconstruct_digest(digest_dict)
                session_cost = compute_session_cost(digest, prices)
        except Exception:
            logger.debug("Cost annotation failed for %s — continuing without cost", path.name)

        # Embed per-event wasted cost (redundant turns only) into waste_events.
        if session_cost is not None:
            per_turn_cost = {tc.turn_index: tc.total_usd for tc in session_cost.turn_costs}
            annotate_waste_costs(waste_entry["waste_events"], per_turn_cost)

        # Attribution fractions, persisted at score time -- this is the
        # background watcher's scoring path, the most common real-world way
        # sessions get scored at all, so this is the primary place the fix
        # for tracegauge patterns' file-lifetime dependency needs to land. See the
        # mirrored comment in tracegauge.cli.score_path.
        attribution = None
        if digest is not None:
            try:
                from tracegauge.attribution import compute_attribution

                attribution = compute_attribution(digest, waste_entry, prices)
            except Exception:
                logger.debug(
                    "Attribution computation failed for %s — continuing without it", path.name
                )

        return score_session(
            record,
            baselines,
            judge_entry=judge_entry,
            waste_entry=waste_entry,
            self_baseline=self_baseline,
            session_cost=session_cost,
            attribution=attribution,
        )
    except Exception:
        logger.exception("Failed to score %s — skipping", path.name)
        return None


def _scan_once(
    config: WatcherConfig,
    conn: Any,
    baselines: dict,
    *,
    self_baseline=None,
    prices: dict | None = None,
    _now: float | None = None,
) -> int:
    """One scan cycle. Returns the count of sessions scored this cycle.

    self_baseline: SelfBaselineState from load_or_compute() — refreshed each cycle
    by run_watcher() so new sessions influence the reference as they accumulate.
    prices: pre-loaded price table passed in by run_watcher (loaded once at startup).
    _now is injectable for testing (avoids sleeping in tests).
    """
    now = _now if _now is not None else time.time()
    scored = 0

    if not config.cc_path.exists():
        logger.debug("cc_path %s does not exist — skipping scan", config.cc_path)
        return 0

    for jsonl_path in iter_session_files(config.cc_path):
        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            continue

        # File-stability: skip if modified too recently (session may still be active)
        if (now - mtime) < config.stability_window:
            continue

        session_id = jsonl_path.stem
        try:
            current_hash = file_hash(jsonl_path)
        except OSError:
            continue

        if not needs_scoring(conn, session_id, current_hash):
            continue

        try:
            result = score_session_file(
                jsonl_path,
                baselines,
                use_judge=config.background_judge,
                self_baseline=self_baseline,
                prices=prices,
            )
            if result is None:
                continue

            upsert_session(conn, result, str(jsonl_path), mtime, current_hash)
            scored += 1
            time.sleep(0)  # yield GIL so web threads can respond between scorings
            logger.info(
                "Scored %s  band=%s  waste=%d",
                session_id,
                result.band_verdict,
                result.waste_event_count,
            )
        except Exception:
            logger.exception("Failed to score/store %s — skipping", jsonl_path.name)
            continue

    return scored


def _check_live_alarm(
    config: WatcherConfig,
    self_baseline,
    prices: dict,
    db_path: Path | None = None,
    baselines: dict[str, Any] | None = None,
) -> None:
    """One-shot live-monitor + alarm check for the currently active session.

    No-op when config.alarm_enabled is False (default). Any failure (unreadable
    active session, mid-write partial JSONL, etc.) is swallowed — the alarm is
    a best-effort convenience, never allowed to break the scan loop.
    """
    if not config.alarm_enabled:
        return
    try:
        active_path = find_active_session(config.cc_path, config.stability_window)
        if active_path is None:
            return
        live = score_live_session(active_path, prices)
        if live is None:
            return
        alarm_cfg = AlarmConfig(enabled=True, plan_type=config.plan_type)
        threshold = (
            threshold_for_live(live, db_path, baselines, alarm_cfg) if db_path is not None else None
        )
        result = check_alarm(live, self_baseline, alarm_cfg, threshold)
        if result is not None:
            logger.warning("[ALARM] %s", result.message)
            print(f"[ALARM] {result.message}", file=sys.stderr)
    except Exception:
        logger.exception("Live alarm check failed — continuing")


def run_watcher(
    config: WatcherConfig,
    stop_event: threading.Event | None = None,
) -> None:
    """Blocking scan loop. Intended to run in a daemon thread via start_watcher().

    Stops cleanly when stop_event is set (or runs forever if None).
    """
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    # Load price table once at startup — prices don't change between sessions.
    prices = load_price_table()
    # Resolve once so both open_db and load_or_compute use the same concrete path.
    # config.db_path is None when tracegauge serve runs without --db-path; Path(None) crashes.
    db_path = resolve_db_path(config.db_path)
    conn = open_db(db_path)
    logger.info(
        "Watcher started: cc_path=%s  interval=%ds  stability=%ds  judge=%s",
        config.cc_path,
        config.scan_interval,
        config.stability_window,
        "ON" if config.background_judge else "OFF",
    )

    # Rows stored before the turn count was recorded carry 0, which keeps every task type on the
    # bundled band. Fix them once, up front; a failure here must never stop the watcher.
    try:
        fixed = backfill_turn_counts(db_path)
        if fixed["updated"]:
            logger.info("Recorded the turn count on %d older session(s)", fixed["updated"])
    except Exception:
        logger.exception("Turn-count backfill failed — continuing")

    while True:
        try:
            # Refresh self-baseline each cycle so new sessions accumulate into
            # the reference pool without restarting the watcher.
            self_bl = load_or_compute(db_path, baselines)
            count = _scan_once(config, conn, baselines, self_baseline=self_bl, prices=prices)
            if count:
                logger.info("Scan complete: %d session(s) scored", count)
            _check_live_alarm(config, self_bl, prices, db_path, baselines)
        except Exception:
            logger.exception("Scan cycle error — continuing")

        if stop_event is not None:
            if stop_event.wait(timeout=config.scan_interval):
                break
        else:
            time.sleep(config.scan_interval)

    logger.info("Watcher stopped")


def start_watcher(
    config: WatcherConfig,
) -> tuple[threading.Thread, threading.Event]:
    """Start the watcher in a daemon thread.

    Returns (thread, stop_event). Call stop_event.set() to stop the watcher;
    then join the thread if you need to wait for it to finish.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_watcher,
        args=(config, stop_event),
        daemon=True,
        name="tracegauge-watcher",
    )
    thread.start()
    return thread, stop_event


__all__ = [
    "WatcherConfig",
    "DEFAULT_CC_PATH",
    "DEFAULT_SCAN_INTERVAL",
    "DEFAULT_STABILITY_WINDOW",
    "score_session_file",
    "run_watcher",
    "start_watcher",
]
