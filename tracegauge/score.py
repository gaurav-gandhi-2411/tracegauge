# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/score.py — Three-axis efficiency scorer, SDK entry point.

Self-contained implementation (no scripts/ import) so the installed wheel
works without repo access.

Public API:
    score_session(record, baselines, judge_entry=None, waste_entry=None) -> ThreeAxisResult
    load_baselines(path) -> dict

ThreeAxisResult carries all three scoring axes:
  - token:      real_tokens + band_verdict + scope_status (calibrated to B2 corpus)
  - trajectory: judge verdict + score (positive signal only; B3 cross-model corroborated)
  - waste:      deterministic waste events with proof turns (observable-invariant only; B4/P1)

Each axis carries its domain_of_validity in the result object so both SDK and CLI
consumers receive the honesty — not bolted on in CLI formatting only (spec decision 1).
"""

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tracegauge.alarm_baseline import dominant_model
from tracegauge.baselines import compute_real_tokens, load_baselines
from tracegauge.classify import classify_session
from tracegauge.web.cost_format import cost_is_known

if TYPE_CHECKING:
    from tracegauge.attribution import AttributionResult
    from tracegauge.cost import SessionCost
    from tracegauge.self_baseline import SelfBaselineState

# ---------------------------------------------------------------------------
# Domain-of-validity constants (one per axis, inline in result object)
# These match the report language (reports 08, 09, 10, 11) and must appear
# in any output surface — CLI and programmatic alike.
# ---------------------------------------------------------------------------

TOKEN_DOMAIN_OF_VALIDITY: str = (
    "Calibrated to a high-waste infra/ML-ops corpus (1 developer, ungated sessions, "
    "usage counted once per API response; eval/manifest.json). Scope-gated by "
    "per-task-type p10 turn floor. Verdict is relative to the typical sessions of "
    "this one developer for the same task type, not to quality-certified runs. "
    "Baseline reflects high-intensity infra work (corpus characterization: report 11); "
    "ordinary coding sessions may read below-band without being inefficient — "
    "interpret with the trajectory verdict. "
    "UNAVAILABLE when below the scope gate or task type has no baseline."
)

TRAJECTORY_DOMAIN_OF_VALIDITY: str = (
    "Positive signal (MUCH_BETTER/BETTER) is cross-model corroborated (84-96%; B3 "
    "report). Negative signal (WORSE/MUCH_WORSE) is model-dependent; do not treat "
    "as fact. No human accuracy calibration. "
    "UNAVAILABLE when no local judge is configured or no judge result is provided."
)

WASTE_DOMAIN_OF_VALIDITY: str = (
    "Observable-invariant waste only: same shell command + same error + no state "
    "change (REPEATED-FAILED-RETRY); same file content + no edit between reads "
    "(REDUNDANT-READ PATH-A and PATH-B; PATH-B dual-format as of P1). "
    "Proof turns attached to every event. "
    "Redundant-read (PATH-B) depends on CC's Read output format (tab + arrow currently "
    "supported); may under-report on future CC versions if the format changes again. "
    "Judgment-of-progress waste not covered — requires human labeling."
)


def build_api_trajectory_dov(api_model: str) -> str:
    """DOV for API-judge verdicts: same B3 caveats + model-not-validated extra.

    The B3 cross-model corroboration (84-96%) was measured on Qwen3 30B (local)
    and Gemma 3 27B — NOT on any API model. An API judge uses the validated
    v3 rubric on an unvalidated model. The extra caveat is non-negotiable.
    """
    return (
        "Positive signal (MUCH_BETTER/BETTER) is cross-model corroborated (84-96%; B3 "
        "report, validated on Qwen3 30B + Gemma 3 27B). Negative signal (WORSE/MUCH_WORSE) "
        "is model-dependent; do not treat as fact. No human accuracy calibration. "
        f"API judge ({api_model}): rubric is the validated v3 prompt; {api_model} was NOT "
        "part of the B3 cross-model corroboration — treat verdict as indicative, not "
        "equivalent to the validated local judge. "
        "UNAVAILABLE when no judge result is provided."
    )


# ---------------------------------------------------------------------------
# Internal result dataclass (behaviour-preservation compatible with scripts/efficiency_score.py)
# ---------------------------------------------------------------------------


@dataclass
class EfficiencyResult:
    """Per-session efficiency assessment against type-specific baselines.

    Field layout is identical to scripts/efficiency_score.EfficiencyResult so that
    the behaviour-preservation golden tests can compare outputs without modification.
    """

    session_id: str
    task_type: str
    real_tokens: int
    scope_status: str  # "in_scope" | "out_of_scope" | "no_baseline"
    baseline_available: bool
    p25: int | None
    p75: int | None
    median: int | None
    band_verdict: str  # "within_band" | "above_p75" | "below_p25" | "unavailable"
    interpretation: str
    # Judge axis (populated when caller provides judge_entry)
    judge_verdict: str | None
    judge_score: float | None
    judge_reasoning: str | None
    # Deterministic waste axis (populated when caller provides waste_entry)
    waste_event_count: int = 0
    waste_events: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


@dataclass
class ThreeAxisResult:
    """SDK result: three scoring axes, each with domain-of-validity.

    Field layout:
      - All fields from EfficiencyResult (same names and types — golden-test
        compatible; the refactor is a one-line import swap)
      - Three domain_of_validity strings, one per axis (new in P1)

    INVARIANT: domain_of_validity fields are always populated (never empty)
    so both CLI and programmatic consumers always receive the caveats.
    """

    # --- identity ---
    session_id: str
    task_type: str

    # --- token axis ---
    real_tokens: int
    scope_status: str  # "in_scope" | "out_of_scope" | "no_baseline"
    baseline_available: bool
    p25: int | None
    p75: int | None
    median: int | None
    band_verdict: str  # "within_band" | "above_p75" | "below_p25" | "unavailable"
    interpretation: str
    token_domain_of_validity: str
    baseline_source: str  # "self" | "building" | "corpus" | "b2_corpus"

    # --- trajectory axis ---
    judge_verdict: str | None
    judge_score: float | None
    judge_reasoning: str | None
    trajectory_domain_of_validity: str

    # --- waste axis ---
    waste_event_count: int
    waste_events: list[dict]
    waste_domain_of_validity: str

    # --- cost annotation (P5: annotation on token axis, not a score, not a composite) ---
    session_cost_usd: float | None = None
    cost_approximate: bool = False
    cost_domain_of_validity: str = ""
    # Added 0.10.2 (S1 fix): distinct warnings for server-side tool usage
    # (e.g. web search) detected but not reflected in session_cost_usd. Empty
    # list when none detected. See tracegauge.cost.SessionCost.server_tool_warnings.
    cost_server_tool_warnings: list[str] = field(default_factory=list)
    # Pricing simplifications applied to turns that ARE in session_cost_usd (e.g. a tiered
    # model's long-context tier not recoverable from an aggregated subagent turn).
    # See tracegauge.cost.SessionCost.pricing_caveats.
    cost_pricing_caveats: list[str] = field(default_factory=list)

    # --- attribution fractions (persisted at score time so tracegauge.intelligence
    # can cluster ANY scored session regardless of whether its source JSONL is
    # still reachable on disk) ---
    context_resend_pct: float | None = None
    context_growth_pct: float | None = None
    output_pct: float | None = None
    waste_pct: float | None = None

    # --- unpriced model names (persisted at score time, for the same
    # reason -- tracegauge.cost.SessionCost.approximate_reasons is only available
    # while the source file is still readable). Comma-joined raw model
    # strings that failed to resolve against the price table; None if the
    # session priced cleanly or cost wasn't computed at all. ---
    cost_unpriced_models: str | None = None

    # --- explicit pricing status. `priced` is True iff a cost was computed and every
    # turn's model resolved; `unpriced_models` lists the model ids that did not. Machine-
    # readable twins of cost_unpriced_models / cost_approximate (kept for back-compat:
    # cost_approximate only trips past a >25% unresolved-turn threshold, `priced` on any). ---
    priced: bool = False
    unpriced_models: list[str] = field(default_factory=list)
    # False when session_cost_usd is a placeholder 0.0 / null rather than a measurement (no turn
    # could be priced). Additive JSON key; session_cost_usd itself stays numeric (see
    # docs/JSON_OUTPUT.md).
    cost_known: bool = False
    # Price-table keys of the priced turns (main + subagent), so the cost note can state the
    # cache-read multiplier each model was actually billed at. Additive JSON key.
    cost_models: list[str] = field(default_factory=list)

    # --- subagent roll-up. session_cost_usd and the attribution fractions already
    # INCLUDE subagent usage; these expose the split. subagent_tokens is in real_tokens units
    # (input - cache_read + output). real_tokens itself stays main-chain only so the
    # baseline-calibrated verdict is unchanged; real_tokens_incl_subagents is the sum. ---
    subagent_tokens: int = 0
    subagent_cost_usd: float | None = None
    subagent_count: int = 0
    real_tokens_incl_subagents: int = 0
    # Per-type split of the subagent spend (see build_subagents_summary). Fixed key set, always
    # present; token figures are BILLED tokens (input incl. cache + output), unlike
    # subagent_tokens above. Added 0.17.0.
    subagents: dict[str, Any] = field(default_factory=dict)

    # --- code impact (persisted at score time, for the same reason --
    # tool_use.input.old_string/new_string/file_path are only readable
    # while the source transcript still exists). JSON-encoded list of
    # {path, additions, deletions, tool, prior_content_unknown,
    # untested_tool_shape} dicts, or None when the session had no edit
    # operations at all (e.g. a read-only/research session) -- distinct
    # from "[]" (has edit_operations support but genuinely made none) vs.
    # a legacy row (column NULL because scored before this existed);
    # tracegauge.impact's report distinguishes these at read time. ---
    edit_operations: str | None = None

    # --- usage de-duplication (adapter_version 2). Claude Code writes one assistant record
    # per content block of an API response, each repeating the response's usage; tracegauge.adapt
    # counts each distinct message.id once. These make the correction observable:
    # duplicate_usage_records = usage_records - usage_records_deduped. adapter_version is None
    # for a record that did not come from tracegauge.adapt (e.g. an older adapted-session pool fixture). ---
    adapter_version: int | None = None
    # tracegauge.adapt.COST_VERSION of the adapter that priced this record (None = not from tracegauge.adapt
    # or scored before cost versioning, i.e. every cache write priced at the 5-minute rate).
    cost_version: int | None = None
    usage_records: int = 0
    usage_records_deduped: int = 0
    duplicate_usage_records: int = 0

    # --- model era for the live alarm's baseline: the (normalized) model that did most of the
    # token work, see tracegauge.alarm_baseline.dominant_model. None when no turn names a model. ---
    dominant_model: str | None = None

    # --- lever hint: the data-gated cost lever (waste / long context / output
    # share) from tracegauge.takeaway.build_lever_hint, the same function the dashboard uses. None when
    # no lever fires (or attribution was unavailable). Set by the CLI after scoring, not by
    # score_session, because it needs the AttributionResult, which is not part of this result. ---
    lever_hint: str | None = None
    # Informational dollars and shares per bucket (tracegauge.takeaway.build_cost_breakdown); not a
    # finding. None when attribution was unavailable. Set by the CLI after scoring, like lever_hint.
    cost_breakdown: dict[str, Any] | None = None

    # --- cache churn (tracegauge.cache_churn): the `cache_churn` object of `score --json`, with a FIXED
    # key set (see tracegauge.json_out.cache_churn_payload and docs/JSON_OUTPUT.md). Computed on demand
    # from the source JSONL by the CLI/dashboard after scoring, never stored; None only when a
    # caller did not compute it (score_session itself never does). ---
    cache_churn: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _share(part: float | None, total: float | None) -> float | None:
    """part/total, or None when either is unknown or the total is not positive."""
    if part is None or total is None or total <= 0:
        return None
    return part / total


def build_subagents_summary(session_cost: SessionCost | None) -> dict[str, Any]:
    """The ``subagents`` object of the score JSON: totals and a per-type list.

    ``usd`` is None where nothing was priced (never a placeholder 0.0), ``usd_is_floor`` marks
    a lower bound, and ``share_of_session_usd`` is None when the session total is 0 or
    unknown. ``by_type`` is sorted by usd (unknown last) descending, then type.
    """
    if session_cost is None:
        return {
            "count": 0,
            "tokens": 0,
            "usd": None,
            "usd_is_floor": False,
            "share_of_session_usd": None,
            "by_type": [],
        }
    total = (
        session_cost.total_usd
        if cost_is_known(session_cost.total_usd, session_cost.unpriced_models)
        else None
    )
    rows: list[dict[str, Any]] = []
    for name, tc in session_cost.subagent_by_type.items():
        usd = tc.usd if tc.usd_known else None
        rows.append(
            {
                "type": name,
                "files": tc.files,
                "requests": tc.requests,
                "tokens": tc.tokens,
                "usd": usd,
                "usd_is_floor": tc.usd_is_floor or not tc.usd_known,
                "share_of_session_usd": _share(usd, total),
            }
        )
    rows.sort(key=lambda r: (-(r["usd"] or 0.0), r["type"]))
    priced = [r for r in rows if r["usd"] is not None]
    usd_total = sum(r["usd"] for r in priced) if priced else None
    return {
        "count": sum(r["files"] for r in rows),
        "tokens": sum(r["tokens"] for r in rows),
        "usd": usd_total,
        "usd_is_floor": any(r["usd_is_floor"] for r in rows),
        "share_of_session_usd": _share(usd_total, total),
        "by_type": rows,
    }


def _priced_model_keys(session_cost: SessionCost | None) -> list[str]:
    """Distinct price-table keys of the turns that were priced (main chain and subagents)."""
    if session_cost is None:
        return []
    turns = [*session_cost.turn_costs, *session_cost.subagent_turn_costs]
    # <synthetic> stubs are zero-cost non-models: naming a multiplier for them would be noise.
    return sorted(
        {
            tc.model_key
            for tc in turns
            if tc.priced and tc.model_key and tc.model_key != "<synthetic>"
        }
    )


def _lean_judgment(judge_verdict: str | None) -> str:
    """Return a one-sentence trajectory note to append to below_p25 interpretations."""
    if judge_verdict in ("MUCH_BETTER", "BETTER"):
        return f"Trajectory verdict {judge_verdict}: efficient execution at lean cost."
    elif judge_verdict in ("WORSE", "MUCH_WORSE"):
        return (
            f"Trajectory verdict {judge_verdict}: not efficient — "
            f"lean cost does not indicate quality."
        )
    else:
        return "Pair with the trajectory verdict to assess efficiency."


def _score_session_impl(
    record: dict,
    baselines: dict,
    judge_entry: dict | None = None,
    waste_entry: dict | None = None,
) -> EfficiencyResult:
    """Core scoring implementation — identical logic to scripts/efficiency_score.score_session.

    Kept separate from score_session() so behaviour-preservation tests can call it
    directly if needed and to keep the public API thin.
    """
    session_id: str = record.get("session_id", "")
    task_type: str = classify_session(record)
    real_tokens: int = compute_real_tokens(record)

    # Extract judge fields (may all be None when judge_entry is absent)
    je_verdict: str | None = judge_entry.get("verdict") if judge_entry else None
    je_score: float | None = judge_entry.get("judge_score") if judge_entry else None
    je_reasoning: str | None = judge_entry.get("reasoning") if judge_entry else None

    # Extract deterministic waste fields
    waste_events: list[dict] = waste_entry.get("waste_events", []) if waste_entry else []
    waste_event_count: int = len(waste_events)

    type_info: dict = baselines.get("types", {}).get(task_type, {})
    baseline_available: bool = type_info.get("available", False)

    # Scope gate: check turn count against p10 floor
    scope_gates = baselines.get("scope_gates", {})
    gate_info = scope_gates.get(task_type, {})
    p10_turns: int | None = gate_info.get("p10_turns")
    turn_count: int = record.get("turn_count", 0)

    if p10_turns is not None and turn_count < p10_turns:
        scope_status = "out_of_scope"
    else:
        scope_status = "in_scope"

    # Early return when baseline is unavailable OR session is out of scope
    if scope_status == "out_of_scope" or not baseline_available:
        if scope_status == "out_of_scope":
            interpretation = (
                f"Session scope too small for a token-economy reference "
                f"({turn_count} turns; {task_type} floor is {p10_turns} turns). "
                f"Trajectory verdict only."
            )
        else:
            interpretation = (
                f"Task type '{task_type}' has no baseline "
                f"(sparse type or not seen in reference corpus)"
            )
        return EfficiencyResult(
            session_id=session_id,
            task_type=task_type,
            real_tokens=real_tokens,
            scope_status=scope_status if baseline_available else "no_baseline",
            baseline_available=baseline_available,
            p25=None,
            p75=None,
            median=None,
            band_verdict="unavailable",
            interpretation=interpretation,
            judge_verdict=je_verdict,
            judge_score=je_score,
            judge_reasoning=je_reasoning,
            waste_event_count=waste_event_count,
            waste_events=waste_events,
        )

    p25: int = type_info["p25"]
    p75: int = type_info["p75"]
    median: int = type_info["median"]

    if real_tokens > p75:
        band_verdict = "above_p75"
        interpretation = (
            f"Session used {real_tokens:,} tokens, above the p75 reference ({p75:,}) "
            f"for {task_type} sessions — more tokens than typical good runs"
        )
    elif real_tokens < p25:
        band_verdict = "below_p25"
        interpretation = (
            f"Token cost is lean for {task_type} ({real_tokens:,} tokens, "
            f"below the {p25:,} reference floor for exemplary {task_type} sessions). "
            + _lean_judgment(je_verdict)
        )
    else:
        band_verdict = "within_band"
        interpretation = (
            f"Session used {real_tokens:,} tokens, within the "
            f"[{p25:,}–{p75:,}] reference band for {task_type} sessions"
        )

    return EfficiencyResult(
        session_id=session_id,
        task_type=task_type,
        real_tokens=real_tokens,
        scope_status=scope_status,
        baseline_available=True,
        p25=p25,
        p75=p75,
        median=median,
        band_verdict=band_verdict,
        interpretation=interpretation,
        judge_verdict=je_verdict,
        judge_score=je_score,
        judge_reasoning=je_reasoning,
        waste_event_count=waste_event_count,
        waste_events=waste_events,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_session(
    record: dict,
    baselines: dict,
    judge_entry: dict | None = None,
    waste_entry: dict | None = None,
    self_baseline: SelfBaselineState | None = None,
    session_cost: SessionCost | None = None,
    baseline_cost_band: tuple[float, float, float] | None = None,
    attribution: AttributionResult | None = None,
) -> ThreeAxisResult:
    """Score a single adapted session record against the three-axis scorer.

    Calls the validated internal implementation (behaviour-preservation guarantee:
    packaging calls the same logic, adds caveats).

    Parameters
    ----------
    record:
        Adapted session record (from an adapted-session pool or claudecode_adapter.adapt_session).
    baselines:
        Loaded cc_baselines.json dict (use load_baselines() to obtain).
    judge_entry:
        Optional pre-computed judge result dict from pool_judge_scores.jsonl.
        When absent, trajectory axis is UNAVAILABLE (judge_verdict=None).
    waste_entry:
        Optional pre-computed waste signals dict (one line of a waste-signals JSONL).
        When absent, waste axis shows no detected events.
    self_baseline:
        Optional per-user self-baseline state from tracegauge.self_baseline.load_or_compute().
        When provided the token axis scores against the user's own lean reference
        (source='self') or shows the cold-start building state.
        Falls back to the B2 corpus baseline when not provided or when the type is
        still building and corpus_fallback was not enabled.
    session_cost:
        Optional pre-computed session cost from tracegauge.cost.compute_session_cost.
        When provided, session_cost_usd and cost_approximate are populated on the result.
        Cost is an annotation only — not a score, not part of a composite.
    baseline_cost_band:
        Optional (p25_usd, median_usd, p75_usd) from self_baseline.compute_baseline_cost_band.
        Stored for downstream framing; not used in scoring.
    attribution:
        Optional pre-computed six-bucket attribution from tracegauge.attribution.compute_attribution
        (same digest/waste_entry/prices the caller already has for session_cost). When
        provided, the four clustering-feature fractions (context_resend_pct,
        context_growth_pct, output_pct, waste_pct) are populated on the result and
        persisted to the store — see tracegauge.attribution.attribution_fractions. This is
        what lets tracegauge.intelligence cluster a session without re-reading its source JSONL.

    Returns
    -------
    ThreeAxisResult
        All three scoring axes with domain-of-validity per axis.
        baseline_source indicates which reference was used for the token axis.
    """
    impl_result: EfficiencyResult = _score_session_impl(
        record, baselines, judge_entry=judge_entry, waste_entry=waste_entry
    )

    task_type = impl_result.task_type
    turn_count: int = record.get("turn_count", 0)
    real_tokens = impl_result.real_tokens

    # Resolve which TypeBaseline to use for the token axis.
    type_bl = self_baseline.by_type.get(task_type) if self_baseline is not None else None

    if type_bl is not None and type_bl.source == "self":
        # Self-baseline active: score against user's own lean reference.
        scope_floor = type_bl.scope_floor
        if turn_count < scope_floor:
            tok_scope = "out_of_scope"
            tok_baseline_avail = False
            tok_p25: int | None = None
            tok_median: int | None = None
            tok_p75: int | None = None
            tok_band = "unavailable"
            tok_interp = (
                f"Session scope too small for a token-economy reference "
                f"({turn_count} turns; {task_type} self-derived floor is {scope_floor} turns). "
                "Trajectory verdict only."
            )
        else:
            tok_scope = "in_scope"
            tok_baseline_avail = True
            tok_p25, tok_median, tok_p75 = type_bl.p25, type_bl.median, type_bl.p75
            # Non-None for source='self' — TypeBaseline invariant.
            p25_v = tok_p25 if tok_p25 is not None else 0
            p75_v = tok_p75 if tok_p75 is not None else 0
            if real_tokens > p75_v:
                tok_band = "above_p75"
                tok_interp = (
                    f"Session used {real_tokens:,} tokens — above your lean p75 "
                    f"({p75_v:,}) for {task_type}. "
                    "Heavier than your typical efficient run. "
                    "(Relative to YOUR OWN lean waste-free sessions — not an absolute verdict.)"
                )
            elif real_tokens < p25_v:
                tok_band = "below_p25"
                tok_interp = (
                    f"Token cost is lean for {task_type} ({real_tokens:,} tokens, "
                    f"below your lean p25 reference ({p25_v:,})). "
                    + _lean_judgment(impl_result.judge_verdict)
                )
            else:
                tok_band = "within_band"
                tok_interp = (
                    f"Session used {real_tokens:,} tokens — within your lean reference band "
                    f"[{p25_v:,}–{p75_v:,}] for {task_type}. "
                    "(Relative to YOUR OWN lean waste-free sessions — not an absolute verdict.)"
                )
        tok_dov = type_bl.domain_of_validity
        tok_source = "self"

    elif type_bl is not None and type_bl.source == "building":
        # Self-baseline not yet ready: apply self-derived scope floor, band unavailable.
        scope_floor = type_bl.scope_floor
        tok_scope = "out_of_scope" if turn_count < scope_floor else "in_scope"
        tok_baseline_avail = False
        tok_p25, tok_median, tok_p75 = None, None, None
        tok_band = "unavailable"
        tok_interp = (
            f"Building your {task_type} self-baseline: need {type_bl.sessions_needed} more "
            "waste-free sessions. Trajectory verdict available in the meantime."
        )
        tok_dov = type_bl.domain_of_validity
        tok_source = "building"

    else:
        # Corpus fallback (type_bl.source == 'corpus') or no self-baseline → B2 result.
        tok_scope = impl_result.scope_status
        tok_baseline_avail = impl_result.baseline_available
        tok_p25, tok_median, tok_p75 = impl_result.p25, impl_result.median, impl_result.p75
        tok_band = impl_result.band_verdict
        tok_interp = impl_result.interpretation
        tok_dov = TOKEN_DOMAIN_OF_VALIDITY
        tok_source = type_bl.source if type_bl is not None else "b2_corpus"

    cost_usd = session_cost.total_usd if session_cost else None
    cost_approx = session_cost.approximate if session_cost else False
    cost_dov = session_cost.domain_of_validity if session_cost else ""
    cost_server_tool_warnings = session_cost.server_tool_warnings if session_cost else []
    cost_pricing_caveats = session_cost.pricing_caveats if session_cost else []

    # Pull the raw unresolved model name(s) out of
    # approximate_reasons (e.g. "unknown model 'foo-bar-2026' — cost
    # unknown, ..." or "empty model string — cost unknown, ...") -- a
    # regex over an already-computed, in-memory list, not a new lookup.
    _unpriced_models: list[str] = []
    if session_cost is not None:
        if session_cost.unpriced_models:
            _unpriced_models = list(session_cost.unpriced_models)
        else:
            # SessionCost built without unpriced_models (older callers/fixtures).
            for reason in session_cost.approximate_reasons:
                m = re.search(r"unknown model '([^']*)'", reason)
                if m:
                    _unpriced_models.append(m.group(1))
                elif "empty model string" in reason:
                    _unpriced_models.append("(empty)")
    unpriced_models_list = sorted(set(_unpriced_models))
    cost_unpriced_models = ",".join(unpriced_models_list) or None

    _sub_usage: dict[str, int] = record.get("subagent_usage") or {}
    subagent_tokens = int(_sub_usage.get("real_tokens", 0))
    subagent_count = int(_sub_usage.get("file_count", 0))
    _usage_dedupe: dict[str, int] = record.get("usage_dedupe") or {}

    # Persist whatever tracegauge.adapt already extracted from tool_use
    # blocks in its single pass over the source JSONL -- "edit_operations"
    # is always present (possibly []) on a record from a current
    # adapt_session(); a record missing the key entirely (records loaded
    # from an older adapted-session pool fixture predating this feature) is
    # treated the same as "no data available" (None), not "[]" (which
    # would falsely claim zero edits when it's really unknown).
    _edit_ops = record.get("edit_operations")
    edit_operations_json: str | None = json.dumps(_edit_ops) if _edit_ops is not None else None

    _resend_pct: float | None
    _growth_pct: float | None
    _out_pct: float | None
    _waste_pct: float | None
    if attribution is not None:
        from tracegauge.attribution import attribution_fractions

        _resend_pct, _growth_pct, _out_pct, _waste_pct = attribution_fractions(attribution)
    else:
        _resend_pct = _growth_pct = _out_pct = _waste_pct = None

    # Use API-specific DOV when the judge_entry came from the API path.
    # The API judge uses the validated rubric but on a model NOT validated in B3.
    _traj_dov: str
    if judge_entry is not None and judge_entry.get("judge_path") == "api":
        _traj_dov = build_api_trajectory_dov(judge_entry.get("api_model", "api-model"))
    else:
        _traj_dov = TRAJECTORY_DOMAIN_OF_VALIDITY

    return ThreeAxisResult(
        # --- identity ---
        session_id=impl_result.session_id,
        task_type=task_type,
        # --- token axis ---
        real_tokens=real_tokens,
        scope_status=tok_scope,
        baseline_available=tok_baseline_avail,
        p25=tok_p25,
        p75=tok_p75,
        median=tok_median,
        band_verdict=tok_band,
        interpretation=tok_interp,
        token_domain_of_validity=tok_dov,
        baseline_source=tok_source,
        # --- trajectory axis ---
        judge_verdict=impl_result.judge_verdict,
        judge_score=impl_result.judge_score,
        judge_reasoning=impl_result.judge_reasoning,
        trajectory_domain_of_validity=_traj_dov,
        # --- waste axis ---
        waste_event_count=impl_result.waste_event_count,
        waste_events=impl_result.waste_events,
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
        # --- cost annotation ---
        session_cost_usd=cost_usd,
        cost_approximate=cost_approx,
        cost_domain_of_validity=cost_dov,
        cost_server_tool_warnings=cost_server_tool_warnings,
        cost_pricing_caveats=cost_pricing_caveats,
        cost_unpriced_models=cost_unpriced_models,
        priced=session_cost is not None and not unpriced_models_list,
        unpriced_models=unpriced_models_list,
        cost_known=cost_is_known(cost_usd, unpriced_models_list),
        cost_models=_priced_model_keys(session_cost),
        subagent_tokens=subagent_tokens,
        subagent_cost_usd=session_cost.subagent_usd if session_cost else None,
        subagent_count=subagent_count,
        subagents=build_subagents_summary(session_cost),
        real_tokens_incl_subagents=real_tokens + subagent_tokens,
        edit_operations=edit_operations_json,
        adapter_version=record.get("adapter_version"),
        cost_version=record.get("cost_version"),
        usage_records=int(_usage_dedupe.get("usage_records", 0)),
        usage_records_deduped=int(_usage_dedupe.get("usage_records_deduped", 0)),
        duplicate_usage_records=int(_usage_dedupe.get("duplicate_usage_records", 0)),
        dominant_model=dominant_model(record.get("digest", {}).get("turns", [])) or None,
        # --- attribution fractions ---
        context_resend_pct=_resend_pct,
        context_growth_pct=_growth_pct,
        output_pct=_out_pct,
        waste_pct=_waste_pct,
    )


__all__ = [
    "ThreeAxisResult",
    "EfficiencyResult",
    "score_session",
    "build_subagents_summary",
    "load_baselines",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
    "build_api_trajectory_dov",
]
