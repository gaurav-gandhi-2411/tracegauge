# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/web/server.py — Localhost-only Flask dashboard for the TES scoring ledger.

Binds 127.0.0.1 ONLY. Never 0.0.0.0. No external network egress.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request

from tracegauge._digest import reconstruct_digest
from tracegauge.adapt import adapt_session
from tracegauge.attribution import AttributionResult, compute_attribution
from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.cost import load_price_table
from tracegauge.intelligence.cache import get_or_compute_intelligence
from tracegauge.intelligence.chat import ChatApiConfig, ask_api, ask_local
from tracegauge.legacy import LEGACY_ROW_LABEL, is_legacy_row
from tracegauge.patterns_extra import PatternsExtraMissing, require_patterns_extra
from tracegauge.self_baseline import SelfBaselineState, compute_baseline_cost_band, load_or_compute
from tracegauge.store import (
    _SORT_COLUMN_WHITELIST,
    TrajectoryRenderState,
    get_session,
    list_sessions,
    open_db,
    resolve_db_path,
    trajectory_render_state,
)
from tracegauge.takeaway import BREAKDOWN_NOTE, build_cost_breakdown, build_lever_hint
from tracegauge.waste import build_waste_entry
from tracegauge.web.cost_format import (
    format_cost_display,
    format_cost_pct_vs_baseline,
    format_price_provenance,
)

# ---------------------------------------------------------------------------
# Attribution helpers
# ---------------------------------------------------------------------------


def _compute_session_attribution(
    session: dict,
    prices: dict,
) -> AttributionResult | None:
    """Try to compute attribution for a session; returns None on any failure."""
    source_path = session.get("source_path")
    if not source_path:
        return None
    src = Path(source_path)
    if not src.exists():
        return None
    try:
        record = adapt_session(src)
        if record is None:
            return None
        digest_turns = record.get("digest", {}).get("turns", [])
        waste_entry = build_waste_entry(record["session_id"], digest_turns)
        digest_obj = record.get("digest")
        if digest_obj is None:
            return None
        digest = reconstruct_digest(digest_obj)
        return compute_attribution(digest, waste_entry, prices)
    except Exception:
        return None


def _compute_session_cache_churn(
    session: dict[str, object], prices: dict[str, object]
) -> dict[str, object]:
    """Cache-churn payload for a session, computed on demand from its source JSONL.

    Same object as `score --json`'s `cache_churn`; status "unavailable" (figures null) when the
    transcript is missing or unreadable (with a `reason`), never an exception.
    """
    from tracegauge.json_out import (  # noqa: PLC0415
        cache_churn_payload,
        cache_churn_payload_for_path,
    )

    source_path = session.get("source_path")
    if not isinstance(source_path, str):
        return cache_churn_payload(None)
    return cache_churn_payload_for_path(Path(source_path), prices)


def _build_attribution_rows(attr: AttributionResult) -> list[dict]:
    """Build attribution table rows sorted by cost% descending."""
    tb = attr.total_billed_tokens
    total_usd = attr.total_usd

    def tok_pct(v: int) -> float:
        return round(v / tb * 100, 1) if tb else 0.0

    def cost_pct(v: float) -> float:
        return round(v / total_usd * 100, 1) if total_usd else 0.0

    rows = [
        {
            "label": "Context re-send (cache reads)",
            "bucket": "B3",
            "tokens": attr.context_resend_tokens,
            "tok_pct": tok_pct(attr.context_resend_tokens),
            "usd": attr.context_resend_usd,
            "cost_pct": cost_pct(attr.context_resend_usd),
            "is_waste": False,
        },
        {
            "label": "Output",
            "bucket": "B4",
            "tokens": attr.output_tokens,
            "tok_pct": tok_pct(attr.output_tokens),
            "usd": attr.output_usd,
            "cost_pct": cost_pct(attr.output_usd),
            "is_waste": False,
        },
        {
            "label": "Context growth (cache writes)",
            "bucket": "B6",
            "tokens": attr.context_growth_tokens,
            "tok_pct": tok_pct(attr.context_growth_tokens),
            "usd": attr.context_growth_usd,
            "cost_pct": cost_pct(attr.context_growth_usd),
            "is_waste": False,
        },
        {
            "label": "Fresh input (not attributable to detected waste)",
            "bucket": "B5",
            "tokens": attr.fresh_input_tokens,
            "tok_pct": tok_pct(attr.fresh_input_tokens),
            "usd": attr.fresh_input_usd,
            "cost_pct": cost_pct(attr.fresh_input_usd),
            "is_waste": False,
        },
        {
            "label": "Redundant-read waste",
            "bucket": "B1",
            "tokens": attr.rr_waste_tokens,
            "tok_pct": tok_pct(attr.rr_waste_tokens),
            "usd": attr.rr_waste_usd,
            "cost_pct": cost_pct(attr.rr_waste_usd),
            "is_waste": True,
        },
        {
            "label": "Retry-loop waste",
            "bucket": "B2",
            "tokens": attr.rfr_waste_tokens,
            "tok_pct": tok_pct(attr.rfr_waste_tokens),
            "usd": attr.rfr_waste_usd,
            "cost_pct": cost_pct(attr.rfr_waste_usd),
            "is_waste": True,
        },
    ]
    return sorted(rows, key=lambda r: r["cost_pct"], reverse=True)


def _stored_attribution_line(session: dict) -> str | None:
    """One-line attribution summary from STORED data only (no file I/O).

    Used in session list — shows waste cost breakdown from stored waste_events JSON.
    """
    cost_usd = session.get("session_cost_usd")
    if cost_usd is None:
        return None
    waste_events_raw = session.get("waste_events", "[]")
    try:
        waste_events = (
            json.loads(waste_events_raw)
            if isinstance(waste_events_raw, str)
            else (waste_events_raw or [])
        )
    except (json.JSONDecodeError, TypeError):
        waste_events = []

    waste_usd = sum(e.get("wasted_cost_usd") or 0 for e in waste_events)
    n_events = session.get("waste_event_count", 0) or 0

    unpriced = session.get("unpriced_models") or ()
    total = format_cost_display(float(cost_usd), unpriced)
    if n_events > 0:
        plural = "s" if n_events != 1 else ""
        if unpriced and waste_usd <= 0:
            # Nothing priced: "$0.00" would read as free waste. Say how many events, not what.
            return f"{total} total · {n_events} waste event{plural} (waste cost unpriced)"
        waste = format_cost_display(waste_usd, unpriced)
        return f"{total} total · waste {waste} ({n_events} event{plural})"
    return f"{total} total · no waste detected"


def _check_ollama(endpoint: str = "http://localhost:11434") -> bool:
    """Probe Ollama health endpoint; returns True if reachable. Non-blocking (3 s cap)."""
    try:
        import httpx as _httpx

        resp = _httpx.get(f"{endpoint}/api/tags", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"  # NEVER change to 0.0.0.0 — moat discipline
    port: int = 4747
    db_path: Path | None = None
    cc_baselines_path: Path | None = None
    cc_path: Path | None = None  # for the /monitor live view; defaults to DEFAULT_CC_PATH
    stability_window: int = 300  # a session modified more recently than this is "active"
    plan_type: str = "usage_based"  # "usage_based" | "max" — alarm display emphasis only


def _projected_metrics(conn: sqlite3.Connection, self_bl_state) -> dict:
    """Compute coverage metrics using content-session denominator.

    Does NOT re-score sessions — computes from stored turn_counts and band_verdicts.
    Splits sessions into content (turn_count > 0) vs empty stubs (turn_count = 0/NULL).
    Empty stubs are never scorable by anything — they're excluded from the coverage %.
    """
    total: int = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    content_sessions: int = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE turn_count > 0"
    ).fetchone()[0]
    empty_stubs: int = total - content_sessions
    content_self_scored: int = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE turn_count > 0 AND band_verdict != 'unavailable'"
    ).fetchone()[0]

    def pct_of_total(n: int) -> float:
        return round(n / max(1, total) * 100, 1)

    def pct_of_content(n: int) -> float:
        return round(n / max(1, content_sessions) * 100, 1)

    return {
        "total": total,
        "content_sessions": content_sessions,
        "empty_stubs": empty_stubs,
        "empty_stubs_pct": pct_of_total(empty_stubs),
        "content_self_scored": content_self_scored,
        "content_self_pct": pct_of_content(content_self_scored),
        # How many task types have switched from "building" to your own baseline.
        "types_active": sum(1 for tb in self_bl_state.by_type.values() if tb.source == "self"),
        "types_total": len(self_bl_state.by_type),
    }


def _per_type_status(conn: sqlite3.Connection, self_bl_state) -> list[dict]:
    """Build per-type status rows for the baseline-status panel."""
    rows = []
    for task_type, type_bl in sorted(self_bl_state.by_type.items()):
        sf = type_bl.scope_floor
        total_type: int = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE task_type = ?", (task_type,)
        ).fetchone()[0]
        in_scope: int = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE task_type = ? AND turn_count >= ?",
            (task_type, sf),
        ).fetchone()[0]
        rows.append(
            {
                "task_type": task_type,
                "total": total_type,
                "in_scope": in_scope,
                "own_n": type_bl.own_n,
                "scope_floor": sf,
                "source": type_bl.source,
                "sessions_needed": type_bl.sessions_needed,
                "p25": type_bl.p25,
                "median": type_bl.median,
                "p75": type_bl.p75,
                "domain_of_validity": type_bl.domain_of_validity,
            }
        )
    return rows


def create_app(config: ServerConfig) -> Flask:
    """Create and configure the Flask dashboard application."""
    app = Flask(__name__, template_folder="templates")
    # Templates never format a $ by hand -- unpriced models must show as
    # "unpriced (<model>)", not "$0.00".
    app.jinja_env.filters["cost_display"] = format_cost_display

    baselines_path = config.cc_baselines_path or BUNDLED_BASELINES_PATH
    _b2 = load_baselines(baselines_path)
    # Load price table once at app startup — prices don't change between requests.
    _prices = load_price_table()
    _price_provenance = format_price_provenance(_prices)

    # -----------------------------------------------------------------------
    # Database connection helpers
    # -----------------------------------------------------------------------

    def get_db() -> sqlite3.Connection:
        if "_db" not in g:
            g._db = open_db(config.db_path)
        return g._db

    @app.teardown_appcontext
    def close_db(exc: BaseException | None = None) -> None:
        db = g.pop("_db", None)
        if db is not None:
            db.close()

    def get_self_bl():
        if config.db_path is not None:
            return load_or_compute(config.db_path, _b2)
        return None

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------

    @app.route("/")
    def session_list() -> str:
        conn = get_db()
        sort_key = request.args.get("sort", "date")
        if sort_key not in _SORT_COLUMN_WHITELIST:
            sort_key = "date"
        raw_dir = request.args.get("dir", "desc").upper()
        sort_dir = "ASC" if raw_dir == "ASC" else "DESC"
        sessions = list_sessions(conn, limit=500, order_by=sort_key, direction=sort_dir)

        total_scored = len(sessions)
        task_type_counts: dict[str, int] = {}
        for s in sessions:
            tt = s["task_type"]
            task_type_counts[tt] = task_type_counts.get(tt, 0) + 1

        self_bl = get_self_bl()
        headline = _projected_metrics(conn, self_bl) if self_bl is not None else None

        # Annotate each session dict with cost-vs-baseline framing.
        # Compute cost bands per task type (one DB query per unique type).
        cost_bands: dict[str, tuple[float, float, float] | None] = {}
        for s in sessions:
            tt = s["task_type"]
            if tt not in cost_bands:
                type_bl = self_bl.by_type.get(tt) if self_bl is not None else None
                scope_floor = type_bl.scope_floor if type_bl is not None else 20
                cost_bands[tt] = compute_baseline_cost_band(conn, tt, scope_floor)

            band = cost_bands.get(tt)
            cost_usd = s.get("session_cost_usd")
            if band is not None and cost_usd is not None:
                pct = format_cost_pct_vs_baseline(float(cost_usd), band)
                s["cost_vs_baseline_pct"] = pct
                s["baseline_cost_median"] = band[1]
            else:
                s["cost_vs_baseline_pct"] = None
                s["baseline_cost_median"] = None

        # Legacy rows (pre-0.15 accounting) stay listed but are marked, and are never compared
        # with a baseline built from corrected sessions.
        for s in sessions:
            s["is_legacy"] = is_legacy_row(s)
            if s["is_legacy"]:
                s["cost_vs_baseline_pct"] = None
                s["baseline_cost_median"] = None
        legacy_count = sum(1 for s in sessions if s["is_legacy"])

        pairs = [(s, trajectory_render_state(s)) for s in sessions]

        # Annotate each session with a stored-data attribution line (no file I/O).
        for s, _ in pairs:
            s["attribution_line"] = _stored_attribution_line(s)

        return render_template(
            "session_list.html",
            pairs=pairs,
            total_scored=total_scored,
            task_type_counts=task_type_counts,
            headline=headline,
            TrajectoryRenderState=TrajectoryRenderState,
            price_provenance=_price_provenance,
            sort_key=sort_key,
            sort_dir=sort_dir,
            legacy_count=legacy_count,
            legacy_label=LEGACY_ROW_LABEL,
        )

    @app.route("/session/<session_id>")
    def session_detail(session_id: str) -> str:
        conn = get_db()
        session = get_session(conn, session_id)
        if session is None:
            abort(404)
        traj_state = trajectory_render_state(session)

        # Compute cost band for this session's task type.
        self_bl = get_self_bl()
        task_type = session.get("task_type", "")
        type_bl = self_bl.by_type.get(task_type) if self_bl is not None else None
        scope_floor = type_bl.scope_floor if type_bl is not None else 20
        cost_band = compute_baseline_cost_band(conn, task_type, scope_floor)

        is_legacy = is_legacy_row(session)
        cost_usd = session.get("session_cost_usd")
        if is_legacy:
            # An overcounted cost must not be compared with a corrected baseline.
            cost_vs_baseline_pct = None
            baseline_cost_median = None
        elif cost_band is not None and cost_usd is not None:
            pct = format_cost_pct_vs_baseline(float(cost_usd), cost_band)
            cost_vs_baseline_pct = pct
            baseline_cost_median = cost_band[1]
        else:
            cost_vs_baseline_pct = None
            baseline_cost_median = None

        # Attribution — requires source JSONL file; gracefully returns None if unavailable.
        attribution = _compute_session_attribution(session, _prices)
        unpriced = session.get("unpriced_models") or ()
        # An absolute finding (waste above its threshold, output >= 40%) or None; the breakdown
        # is informational and always shown when attribution is available.
        attribution_takeaway = build_lever_hint(attribution, unpriced) if attribution else None
        cost_breakdown = build_cost_breakdown(attribution, unpriced) if attribution else None
        attribution_rows = _build_attribution_rows(attribution) if attribution else None
        cache_churn = _compute_session_cache_churn(session, _prices)

        return render_template(
            "session_detail.html",
            session=session,
            traj_state=traj_state,
            TrajectoryRenderState=TrajectoryRenderState,
            price_provenance=_price_provenance,
            is_legacy=is_legacy,
            legacy_label=LEGACY_ROW_LABEL,
            cost_band=cost_band,
            cost_vs_baseline_pct=cost_vs_baseline_pct,
            baseline_cost_median=baseline_cost_median,
            attribution=attribution,
            attribution_takeaway=attribution_takeaway,
            cost_breakdown=cost_breakdown,
            breakdown_note=BREAKDOWN_NOTE,
            attribution_rows=attribution_rows,
            cache_churn=cache_churn,
        )

    @app.route("/trends")
    def trends() -> str:
        conn = get_db()

        task_type_rows = conn.execute(
            "SELECT task_type, COUNT(*) AS cnt FROM sessions GROUP BY task_type ORDER BY cnt DESC"
        ).fetchall()
        band_verdict_rows = conn.execute(
            "SELECT band_verdict, COUNT(*) AS cnt FROM sessions GROUP BY band_verdict ORDER BY cnt DESC"
        ).fetchall()
        waste_over_time_rows = conn.execute(
            """
            SELECT scored_at, session_id, waste_event_count
            FROM sessions
            ORDER BY scored_at DESC
            LIMIT 50
            """
        ).fetchall()

        return render_template(
            "trends.html",
            task_type_rows=[dict(r) for r in task_type_rows],
            band_verdict_rows=[dict(r) for r in band_verdict_rows],
            waste_over_time_rows=[dict(r) for r in waste_over_time_rows],
        )

    @app.route("/baseline-status")
    def baseline_status() -> str:
        conn = get_db()
        self_bl = get_self_bl()
        if self_bl is None:
            return render_template(
                "baseline_status.html", status_rows=[], headline=None, waste_by_type=[]
            )

        headline = _projected_metrics(conn, self_bl)
        status_rows = _per_type_status(conn, self_bl)

        waste_rows = conn.execute(
            """
            SELECT task_type,
                   COUNT(*) AS total,
                   SUM(CASE WHEN waste_event_count > 0 THEN 1 ELSE 0 END) AS with_waste,
                   SUM(waste_event_count) AS total_events
            FROM sessions
            GROUP BY task_type
            ORDER BY total_events DESC
            """
        ).fetchall()
        waste_by_type = [dict(r) for r in waste_rows]

        return render_template(
            "baseline_status.html",
            status_rows=status_rows,
            headline=headline,
            waste_by_type=waste_by_type,
        )

    @app.route("/patterns")
    def patterns() -> str:
        from tracegauge.store import resolve_db_path

        try:
            require_patterns_extra()
        except PatternsExtraMissing as exc:
            return render_template("extra_missing.html", feature="Session patterns", hint=str(exc))

        cache = get_or_compute_intelligence(db_path=resolve_db_path(config.db_path))
        ollama_available = _check_ollama()
        api_key_available = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
        return render_template(
            "patterns.html",
            cache=cache,
            ollama_available=ollama_available,
            api_key_available=api_key_available,
        )

    # NOTE: no /coach route in this release. tracegauge/coach.py + web/templates/coach.html are
    # built and tested (see tests/test_coach_grounded.py + CHANGELOG [0.10.0]) but held —
    # the default output on real data was thin/repetitive, not worth shipping as a headline
    # capability yet. See research/13_coach_alarm_honesty_design.md's addendum for the fix
    # needed (message must state the disproportionate-$/token finding; ranking must not
    # bury the one actionable habit under repeated generic ones) before this route returns.

    @app.route("/budget")
    def budget() -> str:
        """Rolling-window pace + honest self-trend projection, always labeled."""
        from tracegauge.budget import (
            DEFAULT_WINDOW_DAYS,
            compute_budget_projection,
            legacy_rows_excluded_in_window,
        )

        conn = get_db()
        window_days = request.args.get("window_days", DEFAULT_WINDOW_DAYS, type=int)
        projection = compute_budget_projection(conn, window_days=window_days)
        return render_template(
            "budget.html",
            projection=projection,
            window_days=window_days,
            legacy_excluded=legacy_rows_excluded_in_window(conn, window_days),
        )

    @app.route("/monitor")
    def monitor() -> str:
        """Live view of the currently active (in-progress) CC session + the alarm.

        Read-only, best-effort — the active session may not exist right now,
        in which case this shows an honest 'no active session' state, not an error.
        """
        from tracegauge.alarm import AlarmConfig, check_alarm, threshold_for_live
        from tracegauge.live_monitor import find_active_session, score_live_session
        from tracegauge.watcher import DEFAULT_CC_PATH

        cc_path = config.cc_path or DEFAULT_CC_PATH
        self_bl = get_self_bl()

        active_path = find_active_session(cc_path, config.stability_window)
        live = score_live_session(active_path, _prices) if active_path is not None else None

        alarm_result = None
        threshold = None
        if live is not None:
            alarm_cfg = AlarmConfig(enabled=True, plan_type=config.plan_type)
            threshold = threshold_for_live(live, resolve_db_path(config.db_path), _b2, alarm_cfg)
            alarm_result = check_alarm(live, self_bl or SelfBaselineState(), alarm_cfg, threshold)

        return render_template(
            "monitor.html",
            live=live,
            alarm=alarm_result,
            threshold=threshold,
            plan_type=config.plan_type,
        )

    @app.route("/ask", methods=["POST"])
    def ask() -> str:
        payload = request.get_json(force=True) or {}
        question = (payload.get("question") or "").strip()
        if not question:
            return jsonify({"error": "No question provided."}), 400
        question = question[:500]  # length cap — no runaway prompts

        try:
            require_patterns_extra()
        except PatternsExtraMissing as exc:
            return jsonify({"error": str(exc), "needs_extra": "patterns"}), 501  # type: ignore[return-value]  # as the other /ask returns

        db_path_str = str(config.db_path) if config.db_path else None

        # Local first — no egress
        answer = ask_local(question, db_path=db_path_str)
        if answer:
            return jsonify({"answer": answer, "source": "local"})

        # API path: requires explicit consent from the UI
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        api_consent = bool(payload.get("api_consent"))
        if api_key and api_consent:
            cfg = ChatApiConfig(api_key=api_key)
            answer = ask_api(question, cfg, consent_given=True, db_path=db_path_str)
            if answer:
                return jsonify({"answer": answer, "source": "api"})

        if api_key and not api_consent:
            return jsonify(
                {
                    "error": "Ollama unavailable. API path available — check the consent box to proceed.",
                    "needs_consent": True,
                }
            )

        return jsonify(
            {
                "error": (
                    "No LLM available. Run Ollama locally (ollama run qwen3:8b) "
                    "or set ANTHROPIC_API_KEY."
                )
            }
        )

    return app


def start_server(config: ServerConfig) -> None:
    """Start the Flask development server (blocking).

    Binds exclusively to 127.0.0.1 — never exposed to external interfaces.
    """
    app = create_app(config)
    app.run(host=config.host, port=config.port, debug=False, use_reloader=False, threaded=True)
