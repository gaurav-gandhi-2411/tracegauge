# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_alarm_measured.py — Alarm fires only on measured, data-gated thresholds.

Covers: two-part AND gate (magnitude + cause), silent when self-baseline is
'building', silent when disabled (opt-in default off), and flat-plan-safe
message construction (both $ and token framings always present).
"""

from tracegauge.alarm import (
    PLAN_MAX,
    PLAN_USAGE_BASED,
    AlarmConfig,
    check_alarm,
    format_alarm_message,
)
from tracegauge.alarm_baseline import AlarmThreshold
from tracegauge.live_monitor import LIVE_ESTIMATE_DOV, LiveSessionState
from tracegauge.self_baseline import SelfBaselineState, TypeBaseline


def _live(
    *,
    task_type: str = "infra-deploy",
    live_cost_usd: float = 8.10,
    live_context_tokens: int = 320_000,
    live_resend_ratio: float = 0.92,
    context_resend_dominant: bool = True,
) -> LiveSessionState:
    return LiveSessionState(
        session_id="live-session",
        task_type=task_type,
        source_path="/fake/path.jsonl",
        live_cost_usd=live_cost_usd,
        live_context_tokens=live_context_tokens,
        live_resend_tokens=int(live_context_tokens * live_resend_ratio),
        live_resend_ratio=live_resend_ratio,
        context_resend_dominant=context_resend_dominant,
        ai_turn_count=42,
        domain_of_validity=LIVE_ESTIMATE_DOV,
    )


def _self_baseline_active(task_type: str = "infra-deploy", p75: int = 140_000) -> SelfBaselineState:
    """A self-baseline with an ACTIVE ('self') band for task_type."""
    tb = TypeBaseline(
        task_type=task_type,
        source="self",
        p25=40_000,
        median=80_000,
        p75=p75,
        own_n=20,
        sessions_needed=0,
        scope_floor=20,
        domain_of_validity="calibrated to your own sessions",
    )
    return SelfBaselineState(by_type={task_type: tb}, total_sessions=40)


def _self_baseline_building(task_type: str = "infra-deploy") -> SelfBaselineState:
    tb = TypeBaseline(
        task_type=task_type,
        source="building",
        p25=None,
        median=None,
        p75=None,
        own_n=2,
        sessions_needed=6,
        scope_floor=20,
        domain_of_validity="building your baseline",
    )
    return SelfBaselineState(by_type={task_type: tb}, total_sessions=2)


def _threshold_active(threshold_tokens: int = 140_000) -> AlarmThreshold:
    """An active recent same-era threshold, as tracegauge.alarm_baseline would resolve it."""
    return AlarmThreshold(
        status="active",
        tier="recent_era_type",
        threshold_tokens=threshold_tokens,
        n=12,
        percentile=0.8,
        window_days=30,
        era="claude-sonnet-5-5",
        task_type="infra-deploy",
        reason=f"p80 of your last 30 days of claude-sonnet-5-5 infra-deploy (n=12): {threshold_tokens:,} tokens",
    )


def _threshold_disabled() -> AlarmThreshold:
    return AlarmThreshold(
        status="disabled",
        tier="disabled",
        threshold_tokens=None,
        n=0,
        percentile=0.8,
        window_days=30,
        era="claude-sonnet-5-5",
        task_type="infra-deploy",
        reason="alarm disabled: needs at least 10 of your sessions from the last 30 days (have 2)",
    )


# ---------------------------------------------------------------------------
# Opt-in default
# ---------------------------------------------------------------------------


def test_alarm_silent_when_disabled_even_if_both_gates_would_pass() -> None:
    live = _live()  # magnitude + cause both would trip
    self_bl = _self_baseline_active()
    config = AlarmConfig(enabled=False)  # opt-in default is OFF
    assert check_alarm(live, self_bl, config) is None


def test_alarm_config_defaults_to_disabled() -> None:
    assert AlarmConfig().enabled is False
    assert AlarmConfig().plan_type == PLAN_USAGE_BASED


# ---------------------------------------------------------------------------
# Gate 1 — magnitude (data-gated on the user's OWN self-baseline p75)
# ---------------------------------------------------------------------------


def test_alarm_silent_when_baseline_still_building() -> None:
    """No cry-wolf: a type without an active self-baseline never alarms, no matter how big."""
    live = _live(live_context_tokens=10_000_000, live_resend_ratio=0.99)
    self_bl = _self_baseline_building()
    config = AlarmConfig(enabled=True)
    assert check_alarm(live, self_bl, config) is None


def test_alarm_silent_when_type_has_no_baseline_at_all() -> None:
    live = _live(task_type="research-recon")
    self_bl = _self_baseline_active(task_type="infra-deploy")  # different type
    config = AlarmConfig(enabled=True)
    assert check_alarm(live, self_bl, config) is None


def test_alarm_silent_when_below_own_p75() -> None:
    """A normal session (below the user's own p75) must stay silent — no cry-wolf."""
    live = _live(live_context_tokens=100_000, context_resend_dominant=True)  # below p75=140_000
    self_bl = _self_baseline_active(p75=140_000)
    config = AlarmConfig(enabled=True)
    assert check_alarm(live, self_bl, config) is None


# ---------------------------------------------------------------------------
# Gate 2 — cause (context re-send must be the dominant driver)
# ---------------------------------------------------------------------------


def test_alarm_silent_when_big_but_not_resend_dominant() -> None:
    """A large but legitimately fresh-work session must NOT get a /compact nudge."""
    live = _live(live_context_tokens=500_000, context_resend_dominant=False)
    self_bl = _self_baseline_active(p75=140_000)
    config = AlarmConfig(enabled=True)
    assert check_alarm(live, self_bl, config) is None


# ---------------------------------------------------------------------------
# Both gates pass — the alarm fires
# ---------------------------------------------------------------------------


def test_alarm_fires_when_both_gates_pass() -> None:
    live = _live(live_context_tokens=320_000, context_resend_dominant=True)
    self_bl = _self_baseline_active(p75=140_000)
    config = AlarmConfig(enabled=True)
    result = check_alarm(live, self_bl, config)
    assert result is not None
    assert result.task_type == "infra-deploy"
    assert result.resend_pct == 92
    assert "compact" in result.message.lower()


# ---------------------------------------------------------------------------
# Flat-plan-safe message construction (approved design decision 2.3-c)
# ---------------------------------------------------------------------------


def test_message_always_shows_both_dollar_and_token_framing_usage_based() -> None:
    live = _live()
    tb = TypeBaseline(
        task_type="infra-deploy",
        source="self",
        p25=40_000,
        median=80_000,
        p75=140_000,
        own_n=20,
        sessions_needed=0,
        scope_floor=20,
        domain_of_validity="",
    )
    config = AlarmConfig(enabled=True, plan_type=PLAN_USAGE_BASED)
    msg = format_alarm_message(live, tb, config, resend_pct=92)
    assert "$" in msg
    assert "context tokens" in msg
    assert "estimated, in progress" in msg


def test_message_never_hides_dollar_figure_on_max_plan() -> None:
    """plan=max reorders emphasis but must NEVER fully hide the dollar figure."""
    live = _live()
    tb = TypeBaseline(
        task_type="infra-deploy",
        source="self",
        p25=40_000,
        median=80_000,
        p75=140_000,
        own_n=20,
        sessions_needed=0,
        scope_floor=20,
        domain_of_validity="",
    )
    config = AlarmConfig(enabled=True, plan_type=PLAN_MAX)
    msg = format_alarm_message(live, tb, config, resend_pct=92)
    assert "$" in msg  # dollar figure still present
    assert "API-equivalent" in msg  # but demoted/labeled, not presented as billed
    assert "context tokens" in msg
    # Tokens framing must lead (appear before the dollar parenthetical) on plan=max.
    assert msg.index("context tokens") < msg.index("$")


def test_live_figures_always_labeled_estimated_in_progress() -> None:
    live = _live()
    tb = TypeBaseline(
        task_type="infra-deploy",
        source="self",
        p25=40_000,
        median=80_000,
        p75=140_000,
        own_n=20,
        sessions_needed=0,
        scope_floor=20,
        domain_of_validity="",
    )
    for plan in (PLAN_USAGE_BASED, PLAN_MAX):
        config = AlarmConfig(enabled=True, plan_type=plan)
        msg = format_alarm_message(live, tb, config, resend_pct=50)
        assert msg.count("estimated, in progress") >= 1
