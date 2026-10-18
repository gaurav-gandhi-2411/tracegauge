# `--json` output

Every reporting command takes `--json`: `score`, `cost`, `budget`, `monitor`, `impact`, `patterns`.
The contract, pinned by `tests/test_cli_json.py` (exact key sets) and implemented in
`tracegauge/json_out.py`:

* **stdout is exactly one JSON document** (`score` on a directory prints one document per session,
  each complete). Warnings, hints and errors go to stderr; on an error stdout is empty and the exit
  code is non-zero (see [EXIT_CODES.md](EXIT_CODES.md)).
* Every document starts with `schema_version` (int, currently `1`) and `command`.
* **The key set is fixed per command.** A field that does not apply is `null` (or `[]`), never
  absent, so consumers can index without guards. Adding a key is backwards compatible; renaming or
  removing one bumps `schema_version`.
* **Money is never silently partial.** Where a USD figure can be incomplete the document carries
  `priced` (bool) and `unpriced_models` (list of model ids missing from the price table). When
  `priced` is `false` the figure covers the priced turns only: it is a floor, never a true total.
  **Unpriced rows keep a numeric USD of `0.0`** (changing it to `null` would break consumers that
  sum it, and bump `schema_version`), so the number alone cannot tell "free" from "unknown". The
  additive boolean `cost_known` does: it is `false` exactly when the figure is a placeholder (models
  are unpriced and nothing at all was priced, or no cost was computed) and `true` otherwise,
  including for a partly priced figure (known, but a floor: see `priced`). It appears on `score`
  (`session_cost_usd`), `cost` (`total_usd` and each `by_project` row), `budget`
  (`total_usd_so_far`, `projected_usd_for_window`; `null` when `available` is false) and `monitor`
  (`live_cost_usd`; `null` unless `status` is `ok`). Consumers must read `cost_known` (or `priced`)
  before treating a `0.0` as a price. This is a new key, so `schema_version` stays `1`.
  `cost` also reports `sessions_missing_cost` (sessions in the window with no cost stored yet; they
  are not in `total_usd` and are not counted as $0).

## `cost --week|--month|--since D [--roi]`

`period{label,start,end}`, `total_usd`, `priced`, `unpriced_models`, `unpriced_models_incomplete`,
`session_count`, `sessions_missing_cost`, `session_coverage_pct`, `token_coverage_pct`,
`token_total`, `token_priced`, `tokens_unpriced`, `sessions_unpriced`,
`by_project[{project,total_usd,session_count,priced,unpriced_models}]`, `roi`,
`legacy_rows_excluded`, `legacy{label,session_count,total_usd,included_in_total_usd}`.

`legacy_rows_excluded` counts stored sessions in the window from the pre-0.15 accounting (usage
counted once per content block, ~2x too high). They are in none of the figures above; `legacy` is
their own history line, with `included_in_total_usd` always `false`. Both keys were added with
`schema_version` staying `1`. See [UPGRADING.md](UPGRADING.md).

Coverage counts a session as priced only when none of its turns used an unpriced model.
`sessions_unpriced` is the number of sessions in the window that are not fully priced (an unpriced
model among their turns, or no cost stored); `token_priced` is the tokens of fully priced sessions
and `tokens_unpriced = token_total - token_priced`. The store keeps no per-model token split, so a
session with one unpriced turn moves all its tokens to `tokens_unpriced` (an upper bound on the
tokens that could not be priced). `session_coverage_pct` and `token_coverage_pct` follow the same
rule, so they are below 100 whenever `priced` is `false`.

`roi` is `null` unless `--roi` is passed; otherwise `{status, plan_names, plan_cost_usd,
api_equivalent_usd, multiple, is_floor, error}` where `status` is `ok`, `no_plan`,
`no_priced_sessions` or `plan_config_error` (numbers are `null` unless `ok`; `is_floor` is true when
unpriced turns were excluded from `api_equivalent_usd`).

`subagents` (additive, added in 0.17.0, `schema_version` stays `1`) is the subagent share of the
period, over the sessions that stored a per-type split: `{sessions_recorded, sessions_not_recorded,
usd, usd_is_floor, share_of_total_usd, tokens_real, share_of_total_tokens, by_type[{type, files,
requests, tokens, real_tokens, usd, usd_is_floor, share_of_total_usd}]}`. Sessions scored before 0.17.0 are
`sessions_not_recorded` (re-score to include); they are in neither numerator nor denominator, so the
shares describe `sessions_recorded` sessions only. `usd` is `null` where nothing was priced and
`usd_is_floor` is `true` when unpriced or base-tier-priced usage makes it a lower bound.
`share_of_total_tokens` and `tokens_real` use real-token units (input - cache read + output);
`by_type[].real_tokens` are in the same unit; `by_type[].tokens` are billed tokens (input incl. cache
re-reads + output). `by_type` is sorted by `usd`
descending, then `type`.

## `budget [--window-days N]`

`available` (false when the window has no cost data; the numeric fields are then `null`),
`window_days`, `session_count`, `days_observed`, `total_usd_so_far`, `projected_usd_for_window`,
`priced`, `unpriced_models`, `legacy_rows_excluded`, `message`. `legacy_rows_excluded` is the number of
legacy sessions (with a cost) in the window that the pace leaves out; it is present, possibly `0`,
whether or not `available` is true.

## `rescore [--dry-run] [--limit N]`

`dry_run`, `limit` (`null` if not given), `legacy_rows` (legacy rows the run started with),
`rescored` (on a dry run: how many WOULD be rescored; nothing is written), `skipped_source_missing`,
`skipped_empty_stub` (rows with 0 turns and 0 tokens, marked current with no number changed),
`failed`, `not_attempted` (readable rows past `--limit`), `remaining_legacy` (`legacy_rows` minus the
rows actually rescored or stub-stamped). Exit code 4 when `failed > 0`.

## `monitor`

`status` is `no_active_session`, `insufficient_data` or `ok`; `active` is false only for the first.
The session fields (`session_id`, `task_type`, `live_cost_usd`, `priced`, `unpriced_models`,
`live_context_tokens`, `live_resend_ratio`, `context_resend_dominant`, `ai_turn_count`,
`domain_of_validity`) are `null` unless `status` is `ok`; `cc_path` and `source_path` are always
present. `alarm` is `null`, or `{message, resend_pct, baseline_p75_tokens, plan_type, threshold_tokens,
baseline_tier, baseline_n, baseline_percentile}`, and is non-null exactly when the process exits 3
(`baseline_p75_tokens` is the legacy name of `threshold_tokens`; it holds the threshold at whatever
percentile is configured). `alarm_baseline` (non-null whenever `status` is `ok`) says what the alarm compared
against, or why there is nothing to compare: `{status ("active" | "disabled"), tier, threshold_tokens (null
when disabled), n, percentile, window_days, era, task_type, reason}`; the tiers and the one-line reasons are
in `docs/ALARM.md`. Live figures are estimates for an in-progress session.

## `impact [--top N]`

`top_n`, `sessions_with_data`, `sessions_legacy`, `total_operations`, `total_additions`,
`total_deletions`, `prior_content_unknown_additions`, `prior_content_unknown_pct`,
`untested_tool_shape_operations`, `untested_tool_shape_pct`, `top_files` and `top_directories`
(each `[{path,edits,additions,deletions,sessions_touched}]`). The two `_pct` fields qualify the
totals (a full-file rewrite looks like a new file; MultiEdit/NotebookEdit extraction is unverified
on real data) and are `null` when their denominator is zero. `legacy_rows_excluded` (additive) is the
number of stored sessions scored before 0.15 that were left out of every figure; it is not
`sessions_legacy` (sessions that predate edit-operation tracking).

## `patterns [--recompute]`

`valid`, `status`, `n_sessions`, `domain_of_validity`, `legacy_rows_excluded` (additive: stored sessions
scored before 0.15 that the analysis left out; `n_sessions` counts current sessions only), `analysis`. `analysis` is `null` when
`valid` is false (for example, not enough content sessions yet); otherwise it is the cached
analysis as `tracegauge patterns` reads it (`k`, `silhouette`, `archetypes`, `anomaly_count`,
`anomaly_pct`, ...). If the pattern-analysis dependencies are not installed the command prints a
one-line hint to stderr (`pip install "tracegauge[patterns]"`) and exits 1; `require_patterns_extra()`
in `tracegauge/patterns_extra.py` is the single check.

## `score`

The full result (see `tracegauge/score.py::ThreeAxisResult`), with `schema_version`, `command` (`"score"`) and
`legacy_rows_excluded` (pre-0.15 rows in the store, `0` with no store; a single score is not
adjusted by them) as the first keys, and
`lever_hint` (a string only when an absolute finding fires, else `null`: detected waste of at least
$0.50, or at least 10.0% of cost and $0.05, or output at 40.0% of cost or more, compared unrounded; "context dominates" is never
one) and `cost_breakdown` (additive, informational, `null` when attribution was unavailable):
`{label, total_usd, priced, unpriced_models, buckets, note}` where `buckets` is the five entries
`context_resend`, `context_growth`, `output`, `fresh_input`, `waste`, each `{key, label, usd, share_pct,
tokens}`; `usd` sums to `total_usd` (priced turns only), `share_pct` is of that total (`null` when nothing was
priced) and `note` says so when models are unpriced. `priced` / `unpriced_models` are as above.
`cost_models` (additive) lists the price-table keys of the priced turns; the human cost note uses it to name
the cache-read multiplier each model was billed at.

`subagents` (additive, added in 0.17.0, `schema_version` stays `1`) is always an object with the
fixed keys `count` (subagent transcripts), `tokens` (billed tokens), `usd` (`null` when nothing was
priced), `usd_is_floor`, `share_of_session_usd` (`null` when the session total is 0 or unpriced) and
`by_type`: a list of `{type, files, requests, tokens, real_tokens, usd, usd_is_floor,
share_of_session_usd}`
sorted by `usd` descending, then `type`. `type` is the subagent `agentType` (`unknown` when its
meta file is missing or unreadable). `usd` of a type is `null` when none of its usage could be priced
(never `0.0`); the type totals sum to `subagent_cost_usd`. The existing `subagent_tokens` stays in
real-token units, as is each type's `real_tokens`; the new `tokens` fields are billed tokens (they include
every re-read of the cached context, so they are much larger).

`context` (additive, `schema_version` stays 1) is `null`, or an object with the fixed keys `unit`
(`real_tokens_per_turn`), `value` (this session's tokens per turn), `percentile` (an integer 5 to 95, or
`null` outside that range), `range` (`within`, `below_p5` or `above_p95`), `task_type`, `developers`,
`sessions`, `source`, `dataset_version`, `date_range`, `attribution` and `note`. It is where the session
falls among other developers' sessions of the same task type: context, not a verdict. It is `null` for
`feature-build`, for a task type whose cell was not published and when the turn count is unknown. See
[MULTIDEV_REFERENCE.md](MULTIDEV_REFERENCE.md).

`own_percentile` (an integer 0 to 100, or `null`) and `own_sessions` (an integer) are additive: where the
token reading sits among your other in-scope sessions of the same task type (mid-rank percentile) and
how many were compared. `null` / `0` while the type is `building` and when no history is available;
they are not stored.

`cache_churn` (additive, `schema_version` stays 1) is always an object with a fixed key set; see
[CACHE_CHURN.md](CACHE_CHURN.md) for what it means. It is computed from the source transcript when
`score` runs and is never stored. Keys: `status` (`"ok"`, or `"unavailable"` when the transcript could
not be analysed, in which case every figure below is `null`, not 0), `reason` (`null` when ok, else
`"source_missing"`, `"read_error"` or `"internal_error"`; the traceback of an internal error goes to
the log on stderr, never to stdout), `events`, `churn_tokens`, `churn_usd`,
`priced`, `unpriced_events`, `by_cause`, `by_scope`, `avoidable_usd`, `unavoidable_usd`, `advice`,
`events_listed`, `duplicate_turns_dropped`, `unsplit_cache_write_tokens`, `thresholds`,
`judgement_note`.

* `churn_usd` sums the priced events only; when a model is not in the price table `priced` is `false`,
  `unpriced_events` counts the events left out, and `churn_usd` is a floor. An unpriced event has
  `churn_usd: null` and `priced: false`, never `0.0`.
* `by_cause` has exactly the keys `TTL_GAP`, `MODEL_SWITCH`, `COMPACTION` and `UNEXPLAINED`, each
  `{events, churn_tokens, churn_usd, unpriced_events}`. `UNEXPLAINED` is a bucket, not a cause: no cause
  is claimed for those events.
* `by_scope` has the keys `main` and `subagent`, each `{events, churn_tokens, churn_usd,
  unpriced_events, chains}`; the two add up to the totals.
* `avoidable_usd` / `unavoidable_usd` are a judgement, see `judgement_note`; they do not add up to
  `churn_usd` (unexplained events are in neither).
* `advice` is empty unless a priced counterfactual for this session has a positive net saving. Each
  entry: `{scope, setting, env_var, value, premium_usd, saved_usd, net_usd, events_avoided, chains,
  assumptions}`.
* `events_listed` is the (at most 10) largest events by dollars: `{scope, chain, cause, avoidability,
  gap_s, ttl_s, note, marker_index, churn_tokens, churn_usd, priced, previous_turn, current_turn}`, where each
  proof turn is `{turn_index, record_index, message_id, model, cache_read, cache_creation,
  cache_creation_1h}`. `avoidability` is `plausibly_avoidable`, `unavoidable` or `unknown`.
* `unsplit_cache_write_tokens` is the number of cache-write tokens, over all chains, of requests whose
  usage has no `cache_creation` 5-minute / 1-hour split. Their tier is unknown: they are not priced as
  5-minute writes in the advice premium and are not used to infer the cache lifetime. `ttl_s` is `null`
  (and `note` says why) on an event of a chain where no request has the split; such an event is
  `UNEXPLAINED` with `avoidability: "unknown"`. `note` is `""` when there is nothing to add.
* `thresholds` records the constants the detector used (`min_prefix_tokens`, `read_drop_ratio`,
  `rewrite_share`, `ttl_grace_s`).
