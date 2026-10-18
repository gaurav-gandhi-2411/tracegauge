# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/cost.py — Dollar-cost computation for Claude Code sessions.

Self-contained (no src/ or scripts/ imports). Prices are loaded from a bundled
JSON table; users can override via TRACEGAUGE_PRICE_TABLE env var or ~/.tes/prices.json.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from tracegauge._digest import SessionDigest, TurnDigest
from tracegauge._env import env_value

#: A price entry older than this is flagged by check_price_table_staleness,
#: not silently trusted. 90 days mirrors adk-tracegauge's own
#: STALE_THRESHOLD_DAYS (same reasoning: Claude pricing has no published
#: change cadence to derive this number from precisely; 90 days is a
#: deliberately conservative round number). See check_price_table_staleness.
STALE_THRESHOLD_DAYS = 90

#: Model ids that Claude Code writes for client-side messages that never reached the API
#: (e.g. "<synthetic>" stubs). They cost nothing by definition, so they are explicit zero-cost
#: entries -- never "unknown model", never counted as unpriced. Kept in code, not prices.json,
#: because a user-supplied price override must not be able to give them a price.
NON_MODEL_IDS: frozenset[str] = frozenset({"<synthetic>"})

#: Billing dimensions that cannot be derived from a transcript and are therefore NOT modelled.
#: Surfaced in SessionCost.domain_of_validity so a total is never read as more exact than it is.
#: Source: the saved official pricing page, sha256
#: 67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9 (retrieved 2026-10-10).
#: Web search, fast mode and inference_geo=us ARE priced (see compute_turn_cost); Batch is not a
#: gap -- no usage field carries it and Claude Code does not use the Batch API.
UNMODELLED_BILLING = (
    "Not modelled (not derivable from transcripts): code-execution container time, and the "
    "refund for web searches that errored (the API does not bill those, but a transcript "
    "cannot show which ones errored, so every recorded search is charged). The Batch discount "
    "(0.5x) does not apply to Claude Code transcripts. Fast mode, US-only inference (1.1x) and "
    "web search are priced when the transcript records them."
)

#: usage.server_tool_use kinds that carry no charge beyond tokens (page: "Web fetch tool").
_FREE_SERVER_TOOLS: frozenset[str] = frozenset({"web_fetch_requests"})


@dataclass
class TurnCost:
    """Dollar-cost breakdown for a single AI turn."""

    turn_index: int
    model_key: str
    is_approximate: bool
    approximate_reason: str
    fresh_tokens: int
    fresh_cost: float
    cache_read_cost: float
    cache_creation_cost: float
    output_cost: float
    total_usd: float
    # Added 0.10.2 (S1 fix): True iff total_usd is a real, confidently-computed
    # dollar figure from a resolved price-table entry. False means the model
    # string did not resolve — total_usd is 0.0 (never a guessed/default rate)
    # and approximate_reason names the model and the remedy. See
    # _resolve_model's docstring: no code path may set priced=True while
    # is_approximate=True, or vice versa — the two are always in lockstep.
    priced: bool = True
    # Added 0.10.2 (S1 fix): non-empty iff server-side tool usage (e.g. web
    # search, $10/1,000 searches) was detected on this turn's raw usage data
    # but is NOT reflected in total_usd — tracegauge has no verified per-search
    # billing wired through compute_turn_cost yet. Never silently omitted:
    # this field (and SessionCost.server_tool_warnings) is the loud signal
    # that total_usd is missing real, known-nonzero cost. Empty string when
    # no server-side tool usage was detected on this turn.
    server_tool_warning: str = ""
    # Non-empty iff this turn was priced with a known simplification (currently: a tiered model
    # whose per-request prompt size is unknowable for an aggregated subagent turn, priced at
    # the base tier -> a floor). Distinct from unpriced: the turn IS in total_usd.
    pricing_caveat: str = ""
    # Cache-write tokens priced at the 1-hour rate (2x input) / cache-write tokens whose tier
    # the transcript did not specify and that were therefore priced at the 5-minute rate (the
    # conservative default; a floor). cache_creation_cost covers both tiers.
    cache_write_1h_tokens: int = 0
    cache_write_unspecified_tokens: int = 0
    # Added with COST_VERSION 3. Web searches charged on this turn and their dollars (already in
    # total_usd; fresh/cache/output costs above stay token-only). Requests priced at fast-mode
    # rates / at the US-only 1.1x multiplier, and requests that asked for either on a model
    # without it and were therefore priced standard (a turn stands for ``request_count``
    # requests, all with the same speed and inference_geo -- see tracegauge.adapt).
    web_search_requests: int = 0
    web_search_cost: float = 0.0
    fast_requests: int = 0
    fast_unsupported_requests: int = 0
    geo_us_requests: int = 0
    geo_us_unsupported_requests: int = 0


@dataclass
class SubagentTypeCost:
    """Spend of one subagent type (``agentType``) within a session; see SessionCost."""

    usd: float  # priced part only; meaningless unless usd_known
    usd_is_floor: bool  # True when some of the type's usage is unpriced or priced at a floor
    tokens: int  # billed tokens (input incl. cache + output)
    requests: int
    files: int
    # False when none of the type's usage could be priced: usd is then a placeholder 0.0 and
    # must be shown as unpriced, never as $0.00.
    usd_known: bool = True
    # Real tokens (input + cache writes + output, cache re-reads excluded): the unit of the
    # session's "Includes N subagent transcript(s)" line and of the token-economy axis.
    real_tokens: int = 0


@dataclass
class SessionCost:
    """Aggregated dollar-cost for a full session."""

    session_id: str
    total_usd: float
    turn_costs: list[TurnCost]
    approximate: bool
    approximate_reasons: list[str]
    domain_of_validity: str
    ai_turn_count: int
    approximate_turn_count: int
    # Added 0.10.2 (S1 fix): distinct, human-readable warnings for turns whose
    # server-side tool usage is excluded from total_usd — see
    # TurnCost.server_tool_warning. Empty list when no turn detected any.
    server_tool_warnings: list[str] = field(default_factory=list)
    # Subagent roll-up: total_usd INCLUDES subagent_usd; the split is exposed so the
    # breakdown stays visible. subagent_turn_costs is kept apart from turn_costs because
    # turn_costs is indexed by main-chain turn_index (waste costing joins on it).
    subagent_usd: float = 0.0
    subagent_turn_costs: list[TurnCost] = field(default_factory=list)
    # subagent_turn_costs grouped by subagent type ("unknown" when the type is not recorded);
    # sum of usd over the types == subagent_usd.
    subagent_by_type: dict[str, SubagentTypeCost] = field(default_factory=dict)
    # Raw model ids of every turn (main or subagent) that could not be priced -- the
    # machine-readable twin of approximate_reasons. Empty iff every turn priced.
    unpriced_models: list[str] = field(default_factory=list)
    # Distinct pricing simplifications applied to priced turns (see TurnCost.pricing_caveat).
    pricing_caveats: list[str] = field(default_factory=list)
    # Session totals (main + subagent) of TurnCost.cache_write_1h_tokens and
    # TurnCost.cache_write_unspecified_tokens -- the latter is the part of the cache-write
    # spend that "assumes 5m where unspecified".
    cache_write_1h_tokens: int = 0
    cache_write_unspecified_tokens: int = 0
    # Session totals (main + subagent) of the TurnCost fields of the same names. All zero for a
    # session with no web search, fast-mode or US-only request; the domain_of_validity text
    # then carries no sentence about them.
    web_search_requests: int = 0
    web_search_usd: float = 0.0
    fast_requests: int = 0
    fast_unsupported_requests: int = 0
    geo_us_requests: int = 0
    geo_us_unsupported_requests: int = 0

    @property
    def priced(self) -> bool:
        """True iff every turn resolved against the price table (total_usd is complete)."""
        return not self.unpriced_models


def load_price_table(path: str | Path | None = None) -> dict[str, Any]:
    """Load the price table, resolving in priority order:

    1. Explicit ``path`` argument
    2. ``TRACEGAUGE_PRICE_TABLE`` environment variable
    3. ``~/.tes/prices.json`` if it exists
    4. Bundled ``tracegauge/data/prices.json``
    """
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    env_path = env_value("PRICE_TABLE")
    if env_path:
        return json.loads(Path(env_path).read_text(encoding="utf-8"))

    home_override = Path.home() / ".tes" / "prices.json"
    if home_override.exists():
        return json.loads(home_override.read_text(encoding="utf-8"))

    try:
        # importlib.resources path for installed package
        pkg_files = resources.files("tracegauge") / "data" / "prices.json"
        return json.loads(pkg_files.read_text(encoding="utf-8"))
    except (TypeError, FileNotFoundError, AttributeError):
        bundled = Path(__file__).parent / "data" / "prices.json"
        return json.loads(bundled.read_text(encoding="utf-8"))


_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")


def _resolve_model(model_str: str, prices: dict[str, Any]) -> tuple[str | None, bool, str]:
    """Resolve a raw model string to a price-table key.

    Returns ``(resolved_key, is_approximate, approximate_reason)``.

    ``resolved_key`` is ``None`` when the model could not be resolved against
    the price table — callers (``compute_turn_cost``) MUST NOT substitute a
    default/guessed rate in that case; a ``None`` key means "cost genuinely
    unknown for this turn," not "approximately this many dollars." This
    mirrors adk-tracegauge's ``_pricing.resolve_model`` (fail-closed, no
    default) — until 0.10.2 this function instead silently returned
    ``prices["default_model"]``'s key, which is exactly the S1 audit finding:
    an unresolved claude-opus-5/claude-sonnet-5 call was silently priced at
    claude-sonnet-4-6's stale rate (50% overcharge for Sonnet-5, 40%
    undercharge for Opus-5 — see CHANGELOG.md 0.10.2). ``is_approximate`` is
    True in this same case (kept for backward-compatible field semantics —
    every existing caller of this flag already meant "the returned cost is
    not to be trusted," which now correctly means "cost is $0.00 and
    excluded," not "cost is guessed").
    """
    cleaned = _DATE_SUFFIX_RE.sub("", model_str.strip())

    models: dict[str, Any] = prices["models"]

    if not cleaned:
        return (
            None,
            True,
            "empty model string — cost unknown, not priced at a guessed/default rate",
        )

    if cleaned in models:
        return cleaned, False, ""

    for pattern in prices.get("model_patterns", []):
        if cleaned.startswith(pattern["prefix"]):
            return pattern["model_key"], False, ""

    known = ", ".join(sorted(models))
    reason = (
        f"unknown model '{model_str}' — cost unknown, not priced at a guessed/default rate "
        f"(known models: {known}). Set TRACEGAUGE_PRICE_TABLE to a JSON file with the same schema "
        "as tracegauge/data/prices.json containing an entry for this model, add one to "
        "~/.tes/prices.json, or open an issue at "
        "https://github.com/gaurav-gandhi-2411/tracegauge/issues if it should "
        "ship built-in. This turn is excluded from the session's total_usd."
    )
    return None, True, reason


def cache_read_multiplier(model_entry: dict[str, Any], prices: dict[str, Any]) -> float:
    """Cache-hit price as a multiple of the model's input rate.

    A model entry may carry its own ``cache_read_multiplier`` (0.05 for Opus/Sonnet 5.5, 0.025
    for Fable/Mythos 5.1); otherwise the table-wide ``cache_multipliers.read`` (0.1) applies.
    Entries without the field are unchanged, so older price tables keep their exact results.
    """
    own = model_entry.get("cache_read_multiplier")
    if isinstance(own, int | float) and not isinstance(own, bool):
        return float(own)
    return float(prices["cache_multipliers"]["read"])


def _as_rate(value: Any) -> float | None:
    """``value`` as a float if it is an int/float (not a bool) price-table field, else None."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _select_rates(turn: TurnDigest, entry: dict[str, Any]) -> tuple[float, float, str]:
    """Return ``(input_rate, output_rate, pricing_caveat)`` for ``turn``.

    Honours an optional ``long_context`` tier (``threshold_prompt_tokens`` plus its own
    input/output rates): the tier is chosen per API request by prompt size (input + cache
    reads + cache writes = ``token_count_input``). A main-chain turn is one request, so it is
    exact. An aggregated subagent turn (``request_count`` > 1) sums many requests, so the tier
    is only knowable when even the summed prompt is within the threshold; otherwise the base
    rate is used and the caveat says the figure is a floor.
    """
    input_rate = float(entry["input_usd_per_mtok"])
    output_rate = float(entry["output_usd_per_mtok"])
    tier = entry.get("long_context")
    if not isinstance(tier, dict):
        return input_rate, output_rate, ""
    threshold = int(tier["threshold_prompt_tokens"])
    if turn.request_count <= 1:
        if turn.token_count_input > threshold:
            return float(tier["input_usd_per_mtok"]), float(tier["output_usd_per_mtok"]), ""
        return input_rate, output_rate, ""
    if turn.token_count_input <= threshold:
        return input_rate, output_rate, ""
    caveat = (
        f"{turn.model}: aggregated subagent usage ({turn.request_count} requests, "
        f"{turn.token_count_input} prompt tokens) priced at the base tier because the "
        f"per-request prompt size (>{threshold} tokens bills at the long-context rate) is "
        "not recoverable from the aggregate; this part of the total is a floor."
    )
    return input_rate, output_rate, caveat


def _web_search_rate_per_request(prices: dict[str, Any]) -> float | None:
    """USD per web search from ``prices["server_tool_rates"]["web_search_usd_per_1000"]``.

    Page: "Web search is available on the Claude API for $10 per 1,000 searches, plus standard
    token costs". ``None`` when the table has no (numeric) rate -- e.g. a user override written
    before this field existed -- in which case web search stays unpriced AND warned about
    (fail closed), exactly as before COST_VERSION 3.
    """
    rates = prices.get("server_tool_rates")
    rate = rates.get("web_search_usd_per_1000") if isinstance(rates, dict) else None
    if isinstance(rate, int | float) and not isinstance(rate, bool):
        return float(rate) / 1000
    return None


def _server_tool_warning(turn: TurnDigest, web_search_priced: bool = False) -> str:
    """Return a non-empty warning iff ``turn`` carries detected server-side
    tool usage that this turn's total_usd does NOT reflect.

    Added 0.10.2 (S1 fix): before this, ``tracegauge.adapt._parse_usage`` silently
    dropped any ``usage.server_tool_use`` counts, so a session with
    server-side tool calls was priced as if they never happened. Since
    COST_VERSION 3 web search is priced (``web_search_priced``) and web fetch is
    free (page: no charge beyond tokens), so only the rest -- code execution
    (billed by container time, which a transcript does not carry) and any kind
    this module has never seen -- is still warned about, never silently omitted.
    """
    counts = turn.server_tool_use
    if not counts:
        return ""
    detail = ", ".join(
        f"{n} {kind}"
        for kind, n in sorted(counts.items())
        if n
        and kind not in _FREE_SERVER_TOOLS
        and not (web_search_priced and kind == "web_search_requests")
    )
    if not detail:
        return ""
    return (
        f"turn {turn.turn_index}: server-side tool usage detected ({detail}) but NOT "
        "priced — tracegauge has no verified billing rate for these wired through cost "
        "computation yet (e.g. web search is $10/1,000 searches). total_usd for this "
        "turn excludes this cost; the true cost is higher than shown."
    )


def compute_turn_cost(
    turn: TurnDigest,
    prices: dict[str, Any],
    cache_duration: str | None = None,
) -> TurnCost:
    """Compute the dollar cost for a single AI turn.

    Cache writes are priced per tier from the turn's own split: ``turn.cache_creation_1h``
    tokens at the 1-hour multiplier (2x input), the rest at the 5-minute one (1.25x). Writes
    whose tier the transcript did not specify (``cache_creation`` beyond
    ``cache_creation_tier_known``, e.g. a record without ``usage.cache_creation``) are priced
    at the 5-minute rate -- the conservative default -- and counted in
    ``TurnCost.cache_write_unspecified_tokens``. A turn built without the split fields (any
    caller of the pre-split API) therefore prices exactly as before.

    ``cache_duration`` overrides the split: ``"5min"`` / ``"1hr"`` prices EVERY cache write at
    that multiplier (the pre-split behaviour); ``None`` (default) uses the split.

    When ``turn.model`` does not resolve against ``prices`` (see
    ``_resolve_model``), this returns a TurnCost with ``priced=False``,
    ``total_usd=0.0``, and ``approximate_reason`` naming the model and the
    remedy — NEVER a dollar figure computed at a guessed/default rate
    (0.10.2 S1 fix). Server-side tool usage that is not priced (e.g. code
    execution) is separately flagged via ``server_tool_warning`` regardless of
    whether the model itself resolved — the model resolving doesn't mean the
    turn's total is complete.

    Since COST_VERSION 3 three more dimensions are priced from the saved official page
    (sha256 67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9): web search
    (``server_tool_rates.web_search_usd_per_1000``, added to ``total_usd``), fast mode
    (``turn.speed == "fast"``: the model's ``fast_{input,output}_usd_per_mtok``, which the
    cache multipliers and the geo multiplier stack on) and US-only inference
    (``turn.inference_geo == "us"``: the model's ``geo_us_multiplier`` on every token category).
    A turn with none of them takes the same arithmetic path as before.
    """
    server_tool_warning = _server_tool_warning(turn)
    if turn.model.strip() in NON_MODEL_IDS:
        # Not a billed API response: an explicit zero, priced=True so it is never "unpriced".
        return TurnCost(
            turn_index=turn.turn_index,
            model_key=turn.model.strip(),
            is_approximate=False,
            approximate_reason="",
            fresh_tokens=0,
            fresh_cost=0.0,
            cache_read_cost=0.0,
            cache_creation_cost=0.0,
            output_cost=0.0,
            total_usd=0.0,
            priced=True,
            server_tool_warning=server_tool_warning,
        )
    model_key, is_approximate, approximate_reason = _resolve_model(turn.model, prices)

    if model_key is None:
        # Cost genuinely unknown for this turn -- never substitute the
        # default model's rate. See _resolve_model's and this module's notes.
        return TurnCost(
            turn_index=turn.turn_index,
            model_key=turn.model or "(empty)",
            is_approximate=is_approximate,
            approximate_reason=approximate_reason,
            fresh_tokens=0,
            fresh_cost=0.0,
            cache_read_cost=0.0,
            cache_creation_cost=0.0,
            output_cost=0.0,
            total_usd=0.0,
            priced=False,
            server_tool_warning=server_tool_warning,
        )

    entry: dict[str, Any] = prices["models"][model_key]
    input_rate, output_rate, pricing_caveat = _select_rates(turn, entry)
    requests = max(1, turn.request_count)

    # Fast mode (usage.speed == "fast"): the model's own fast rates replace input/output for the
    # whole request, across the full context window (so a long-context tier does not apply);
    # cache multipliers and the geo multiplier below stack on top of them. A model without fast
    # rates (Opus 4.7 rejects the request, Opus 4.6 runs and bills standard, others never had
    # it) is priced standard and counted, so the session note can say so.
    fast_requests = fast_unsupported = 0
    if turn.speed == "fast":
        fast_in = _as_rate(entry.get("fast_input_usd_per_mtok"))
        fast_out = _as_rate(entry.get("fast_output_usd_per_mtok"))
        if fast_in is not None and fast_out is not None:
            input_rate, output_rate, pricing_caveat = fast_in, fast_out, ""
            fast_requests = requests
        else:
            fast_unsupported = requests

    # US-only inference (usage.inference_geo == "us"): a model-level multiplier (1.1x, Claude
    # 4.6 and later) on EVERY token category -- input, output, cache writes and cache reads, and
    # Haiku 5.5's long-context rates -- applied to the (possibly fast) rates before the cache
    # multipliers, which is equivalent because the cache prices are linear in the input rate.
    geo_us_requests = geo_us_unsupported = 0
    if turn.inference_geo == "us":
        geo_mult = _as_rate(entry.get("geo_us_multiplier"))
        if geo_mult is not None:
            input_rate *= geo_mult
            output_rate *= geo_mult
            geo_us_requests = requests
        else:
            geo_us_unsupported = requests

    cache_mult: dict[str, float] = prices["cache_multipliers"]
    read_mult = cache_read_multiplier(entry, prices)

    created = max(0, turn.cache_creation)
    if cache_duration == "1hr":
        tokens_1h = created
    elif cache_duration == "5min":
        tokens_1h = 0
    else:
        tokens_1h = min(max(0, turn.cache_creation_1h), created)
    tokens_5m = created - tokens_1h
    unspecified = (
        0 if cache_duration is not None else created - min(turn.cache_creation_tier_known, created)
    )

    fresh_tokens = max(0, turn.token_count_input - turn.cache_read - turn.cache_creation)

    fresh_cost = fresh_tokens * input_rate / 1_000_000
    cache_read_cost = turn.cache_read * (input_rate * read_mult) / 1_000_000
    cache_creation_cost = (
        tokens_5m * (input_rate * cache_mult["write_5min"])
        + tokens_1h * (input_rate * cache_mult["write_1hr"])
    ) / 1_000_000
    output_cost = turn.token_count_output * output_rate / 1_000_000
    total = fresh_cost + cache_read_cost + cache_creation_cost + output_cost

    # Web search: $10 per 1,000 searches ON TOP of the tokens (not scaled by fast/geo: the page
    # lists it as a flat per-search charge). Errors are not billed, but a transcript cannot show
    # which searches errored, so every recorded search is charged (documented, a ceiling).
    search_rate = _web_search_rate_per_request(prices)
    searches = int((turn.server_tool_use or {}).get("web_search_requests", 0))
    web_search_cost = 0.0
    if search_rate is not None and searches > 0:
        web_search_cost = searches * search_rate
        total += web_search_cost
    server_tool_warning = _server_tool_warning(turn, web_search_priced=search_rate is not None)

    return TurnCost(
        turn_index=turn.turn_index,
        model_key=model_key,
        is_approximate=is_approximate,
        approximate_reason=approximate_reason,
        fresh_tokens=fresh_tokens,
        fresh_cost=fresh_cost,
        cache_read_cost=cache_read_cost,
        cache_creation_cost=cache_creation_cost,
        output_cost=output_cost,
        total_usd=total,
        priced=True,
        server_tool_warning=server_tool_warning,
        pricing_caveat=pricing_caveat,
        cache_write_1h_tokens=tokens_1h,
        cache_write_unspecified_tokens=unspecified,
        web_search_requests=searches if web_search_cost else 0,
        web_search_cost=web_search_cost,
        fast_requests=fast_requests,
        fast_unsupported_requests=fast_unsupported,
        geo_us_requests=geo_us_requests,
        geo_us_unsupported_requests=geo_us_unsupported,
    )


def compute_session_cost(
    digest: SessionDigest,
    prices: dict[str, Any] | None = None,
    cache_duration: str | None = None,
) -> SessionCost:
    """Compute the aggregated dollar cost for a full session.

    Only AI turns (``role == "ai"``) are priced; user/tool/system turns are skipped.
    ``prices`` defaults to the bundled price table when ``None``.
    """
    if prices is None:
        prices = load_price_table()

    price_table_date: str = prices.get("as_of", "unknown")
    approximate_threshold_pct: int = prices.get("approximate_threshold_pct", 25)

    turn_costs: list[TurnCost] = []
    for turn in digest.turns:
        if turn.role != "ai":
            continue
        turn_costs.append(compute_turn_cost(turn, prices, cache_duration))

    # Subagent turns (digest.subagent_turns) are priced like any other AI turn but kept in
    # their own list; see SessionCost.subagent_turn_costs.
    subagent_turn_costs = [
        compute_turn_cost(t, prices, cache_duration) for t in digest.subagent_turns
    ]

    ai_turn_count = len(turn_costs)
    approximate_turn_count = sum(1 for tc in turn_costs if tc.is_approximate)

    # Session-level approximate flag: threshold is STRICTLY greater than the pct, over main
    # turns (unchanged). Subagent turns are few, aggregated and large, so a turn-count
    # percentage is meaningless for them: ANY unpriced subagent usage marks the session
    # approximate, because a whole agent's spend is missing from the total.
    session_approximate = False
    if ai_turn_count > 0:
        pct = approximate_turn_count / ai_turn_count * 100
        session_approximate = pct > approximate_threshold_pct
    if any(tc.is_approximate for tc in subagent_turn_costs):
        session_approximate = True

    all_turn_costs = turn_costs + subagent_turn_costs
    approximate_reasons = list(
        {tc.approximate_reason for tc in all_turn_costs if tc.approximate_reason}
    )
    server_tool_warnings = list(
        {tc.server_tool_warning for tc in all_turn_costs if tc.server_tool_warning}
    )
    pricing_caveats = sorted({tc.pricing_caveat for tc in all_turn_costs if tc.pricing_caveat})
    # A zero-token turn costs nothing whatever its model id, so it must not make a session
    # look unpriced ("<synthetic>" is additionally an explicit zero in compute_turn_cost).
    ai_turns = [t for t in digest.turns if t.role == "ai"] + list(digest.subagent_turns)
    unpriced_models = sorted(
        {
            tc.model_key
            for tc, t in zip(all_turn_costs, ai_turns, strict=True)
            if not tc.priced and (t.token_count_input or t.token_count_output)
        }
    )

    # total_usd sums ONLY confidently-priced turns (priced=True) -- an
    # unresolved-model turn contributes total_usd=0.0 (see compute_turn_cost),
    # never a guessed/default-rate figure. When approximate_turn_count > 0,
    # this total is a floor, not the true total -- approximate/
    # approximate_reasons is the loud, always-checked signal of that (0.10.2
    # S1 fix; pre-0.10.2 this summed wrongly-guessed dollar amounts instead).
    subagent_usd = sum(tc.total_usd for tc in subagent_turn_costs)
    total_usd = sum(tc.total_usd for tc in turn_costs) + subagent_usd

    all_writes = sum(t.cache_creation for t in ai_turns)
    unspecified_writes = sum(tc.cache_write_unspecified_tokens for tc in all_turn_costs)

    web_search_requests = sum(tc.web_search_requests for tc in all_turn_costs)
    web_search_usd = sum(tc.web_search_cost for tc in all_turn_costs)
    fast_requests = sum(tc.fast_requests for tc in all_turn_costs)
    fast_unsupported = sum(tc.fast_unsupported_requests for tc in all_turn_costs)
    geo_us_requests = sum(tc.geo_us_requests for tc in all_turn_costs)
    geo_us_unsupported = sum(tc.geo_us_unsupported_requests for tc in all_turn_costs)
    billing_note = _billing_modifier_note(
        web_search_requests,
        web_search_usd,
        fast_requests,
        fast_unsupported,
        geo_us_requests,
        geo_us_unsupported,
    )

    domain_of_validity = (
        f"Computed from measured tokens at per-turn, per-model rates (prices as of {price_table_date}; "
        "bundled — override with TRACEGAUGE_PRICE_TABLE env var or ~/.tes/prices.json). "
        "Cache writes are split by the transcript's usage.cache_creation: 1-hour writes at 2x "
        "input, 5-minute at 1.25x; writes whose tier is unspecified are assumed 5-minute "
        f"({unspecified_writes} of {all_writes} cache-write tokens in this session). "
        "Cache read at 0.1x input unless "
        "the model has its own cache_read_multiplier (0.05x Opus/Sonnet 5.5, 0.025x Fable/"
        "Mythos 5.1). "
        "Output at full rate. A turn whose model string does not resolve against the "
        "price table is EXCLUDED from total_usd (never priced at a guessed/default "
        "rate, as of 0.10.2) — session flagged approximate when >"
        f"{approximate_threshold_pct}% of turns "
        "are unresolved this way; see approximate_reasons for the specific model(s) "
        "and remedy. Server-side tool usage that is detected but not priced (e.g. code "
        "execution) is flagged separately in server_tool_warnings, regardless of approximate. "
        f"{billing_note}"
        f"{UNMODELLED_BILLING} "
        "API-equivalent token cost; flat-plan users' marginal cost differs. "
        "Cost annotates the token axis — not a score, not part of a composite."
    )

    return SessionCost(
        session_id=digest.session_id,
        total_usd=total_usd,
        turn_costs=turn_costs,
        approximate=session_approximate,
        approximate_reasons=approximate_reasons,
        domain_of_validity=domain_of_validity,
        ai_turn_count=ai_turn_count,
        approximate_turn_count=approximate_turn_count,
        server_tool_warnings=server_tool_warnings,
        subagent_usd=subagent_usd,
        subagent_turn_costs=subagent_turn_costs,
        subagent_by_type=_subagent_by_type(digest, subagent_turn_costs),
        unpriced_models=unpriced_models,
        pricing_caveats=pricing_caveats,
        cache_write_1h_tokens=sum(tc.cache_write_1h_tokens for tc in all_turn_costs),
        cache_write_unspecified_tokens=unspecified_writes,
        web_search_requests=web_search_requests,
        web_search_usd=web_search_usd,
        fast_requests=fast_requests,
        fast_unsupported_requests=fast_unsupported,
        geo_us_requests=geo_us_requests,
        geo_us_unsupported_requests=geo_us_unsupported,
    )


def _subagent_by_type(
    digest: SessionDigest, subagent_turn_costs: list[TurnCost]
) -> dict[str, SubagentTypeCost]:
    """Group priced subagent turns by ``agent_type`` ("" -> "unknown", the legacy/no-meta case)."""
    out: dict[str, SubagentTypeCost] = {}
    priced_any: dict[str, bool] = {}
    for t, tc in zip(digest.subagent_turns, subagent_turn_costs, strict=True):
        key = t.agent_type or "unknown"
        acc = out.setdefault(key, SubagentTypeCost(0.0, False, 0, 0, 0, usd_known=False))
        acc.usd += tc.total_usd
        acc.tokens += t.token_count_input + t.token_count_output
        acc.real_tokens += t.token_count_input - t.cache_read + t.token_count_output
        acc.requests += t.request_count
        has_tokens = bool(t.token_count_input or t.token_count_output)
        if tc.priced and has_tokens:
            priced_any[key] = True
        # Unpriced usage, a base-tier pricing simplification or unpriced server-side tools all
        # make the type's dollars a lower bound.
        if (not tc.priced and has_tokens) or tc.pricing_caveat or tc.server_tool_warning:
            acc.usd_is_floor = True
    for key, acc in out.items():
        acc.files = digest.subagent_files_by_type.get(key, 0)
        # A type with no token usage at all costs a known zero; otherwise known iff something priced.
        acc.usd_known = priced_any.get(key, False) or not acc.tokens
    return out


def _billing_modifier_note(
    searches: int,
    search_usd: float,
    fast: int,
    fast_unsupported: int,
    geo: int,
    geo_unsupported: int,
) -> str:
    """Per-session sentences for web search / fast mode / US-only inference, ending in a space.

    Empty string when the session used none of them, so such a session's cost text carries no
    trace of these dimensions beyond the always-present UNMODELLED_BILLING sentence.
    """
    parts: list[str] = []
    if searches:
        parts.append(
            f"Web search: {searches} search(es) added at $10 per 1,000 (${search_usd:.2f} of "
            "total_usd); the API does not bill errored searches but a transcript cannot show "
            "which ones errored, so every recorded search is charged."
        )
    if fast:
        parts.append(
            f"Fast mode: {fast} request(s) priced at the model's fast-mode input/output rates "
            "(cache and data-residency multipliers stack on top)."
        )
    if fast_unsupported:
        parts.append(
            f"Fast mode: {fast_unsupported} request(s) asked for speed=fast on a model with no "
            "fast-mode rate (Opus 4.7 rejects it, Opus 4.6 runs and bills standard) and were "
            "priced at the standard rate."
        )
    if geo:
        parts.append(f"US-only inference: {geo} request(s) priced at 1.1x on every token category.")
    if geo_unsupported:
        parts.append(
            f"US-only inference: {geo_unsupported} request(s) carried inference_geo=us on a model "
            "that does not support it (before Claude 4.6) and were priced at the standard rate."
        )
    return "".join(f"{p} " for p in parts)


def check_price_table_staleness(
    prices: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[tuple[str, str, int, str]]:
    """Return a list of ``(model_key, as_of, age_days, source_url)`` for every
    non-retired price-table entry older than ``STALE_THRESHOLD_DAYS``.

    Minimum-viable port of adk-tracegauge's staleness-guard CONCEPT
    (``_pricing.STALE_THRESHOLD_DAYS`` / ``ResolvedModel.is_stale`` /
    ``scripts/check_price_freshness.py``), adapted to tracegauge's own
    per-model ``as_of``/``source_url`` fields (adk-tracegauge's equivalent
    fields are named ``fetched_on``/``source_url``) and its ``retired``
    flag (adk-tracegauge has no equivalent — every entry there is expected
    to be live-verifiable). An entry with no per-model ``as_of`` inherits
    the table-level ``as_of``. An entry marked ``"retired": true`` is
    skipped entirely — a retired model's published rate does not change
    again, so "stale" does not apply (see prices.json's top-level note).
    An unparseable/missing ``as_of`` (on a non-retired entry, after falling
    back to the table-level date) is itself treated as stale — fail closed,
    never silently skipped. Deliberately does NOT port promo-expiry
    handling, tiering, multi-provider support, or the regression gate —
    A per-entry, full-engine port is out of scope here.

    Pure date arithmetic; no network calls, no paid API calls, zero cost.
    Empty list means every entry is fresh. See
    scripts/check_price_freshness.py for the CI entry point.
    """
    if prices is None:
        prices = load_price_table()
    if today is None:
        today = date.today()

    table_as_of = str(prices.get("as_of") or "")
    models: dict[str, Any] = prices.get("models", {})

    stale: list[tuple[str, str, int, str]] = []
    for model_key, entry in models.items():
        if entry.get("retired"):
            continue
        as_of = str(entry.get("as_of") or table_as_of)
        source_url = str(
            entry.get("source_url") or prices.get("source_url") or "<no source_url recorded>"
        )
        try:
            as_of_date = date.fromisoformat(as_of)
        except ValueError:
            # An unparseable/missing date is itself a staleness signal --
            # fail closed rather than skip the entry silently.
            stale.append((model_key, as_of or "<missing>", -1, source_url))
            continue
        age_days = (today - as_of_date).days
        if age_days > STALE_THRESHOLD_DAYS:
            stale.append((model_key, as_of, age_days, source_url))
    return stale


__all__ = [
    "NON_MODEL_IDS",
    "UNMODELLED_BILLING",
    "cache_read_multiplier",
    "TurnCost",
    "SessionCost",
    "SubagentTypeCost",
    "STALE_THRESHOLD_DAYS",
    "load_price_table",
    "compute_turn_cost",
    "compute_session_cost",
    "check_price_table_staleness",
]
