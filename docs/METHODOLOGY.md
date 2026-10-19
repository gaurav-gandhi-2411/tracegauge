# Methodology, limits and caveats

This is everything tracegauge says about how it measures, where its calibration comes from and what
it does not do. It was moved here unchanged from the README so the README can lead with what the
tool finds; no caveat was dropped or softened in the move (the only edits are link targets and the
words "this README" -> "this document"). The command list is in the [README](../README.md) and the
long-form usage is in [COMMANDS.md](COMMANDS.md); the cache-churn detector has its own page,
[CACHE_CHURN.md](CACHE_CHURN.md).

**Where the figures here come from.** Figures in the `text` output blocks (`tracegauge cost`,
`budget`, `monitor`, `cost --roi`) are regenerated from synthetic sessions by
`python scripts/gen_readme_examples.py` and pinned by `tests/test_readme_examples.py`. Every other
number below (the corpus and baseline sizes, the validation rates, the judge agreement, the
extraction counts) is the maintainer's unpublished local measurement unless a repository file is
named next to it: the sessions behind them are not in the repository, so a clone cannot re-derive
them. The bundled baselines themselves are in `tracegauge/data/cc_baselines.json`.

## What it is, and what leaves your machine

Three-axis efficiency scoring for Claude Code sessions — token economy, trajectory quality, deterministic waste. Local by default — no server, no telemetry, nothing transmitted unless you opt in. Two opt-in paths currently do anything: a local contribution export (content-free, stays on your machine); and an API judge that sends session snippets directly to your model provider on per-session explicit consent. A third capability — community corpus contribution (content-free, would transmit to a tracegauge-operated corpus on explicit consent, in exchange for a cross-developer percentile baseline) — is fully built and tested but **not currently active**: no public corpus is operated, so `tracegauge corpus contribute` sends nothing regardless of consent. See [PRIVACY.md](../PRIVACY.md).

tracegauge is feature-complete — the current release bundles the full validated toolchain (B1–B5 research arc + every shipped phase):

- **Self-baseline token scoring** — a session is placed among *your own* sessions of the same task type, not a one-size-fits-all corpus. A task type needs 10 of your other in-scope sessions with usage before it gets a token verdict (waste is not a filter; empty stubs and sessions below the scope floor do not count); until then it shows `building (n/10)`, and no verdict is borrowed from a bundled table or from another population.
- **Dollar cost attribution** — six reconciling buckets (B1–B6) that split every billed token into where the money actually went; token% and cost% shown side by side so the cache-read divergence (lots of tokens, little cost) is visible.
- **Deterministic waste detection** — frozen, observable-invariant detectors (repeated-failed-retry, redundant-read) with proof turns and per-event wasted cost. No LLM judgment, no false-positive guessing.
- **Trajectory judge** — purposefulness verdict from a local Ollama model ($0, GPU) *or* an opt-in API judge that sends snippets to your model provider only on explicit per-session consent. Renders UNAVAILABLE as a complete, expected state when no judge is configured.
- **Diagnostic dashboard** — `tracegauge serve` runs a localhost-only (`127.0.0.1`) web dashboard that auto-scores finished sessions and shows the three axes, attribution, and waste with every domain-of-validity caveat carried to the surface. No composite/blended score — each axis stands on its own.
- **Community baseline (built, not yet active)** — `tracegauge corpus contribute` would send content-free session aggregates (numbers and categories only — see [PRIVACY.md](../PRIVACY.md)) to a tracegauge-operated community corpus, and in return `tracegauge corpus` would show your context-efficiency percentile against other opted-in developers, alongside — never replacing — your own self-baseline. The code, the content-free guard, the consent flow, and `tracegauge corpus withdraw` are all built and tested, but **no corpus is currently provisioned** — until one is, `tracegauge corpus contribute` sends nothing, by construction (see PRIVACY.md).

Local by default: scoring and the dashboard make zero external network calls. The only working egress today is the opt-in API judge (your key, your consent, per session). The community corpus contribution above would be a second opt-in egress path once activated — see PRIVACY.md.

## Notes carried over from the README header

> **Note — 0.10.2 (live on PyPI since 2026-08-15) was a BREAKING bug-fix release for SDK users.**
> Unknown/unresolvable models used to silently return a guessed price (defaulting to
> `claude-sonnet-4-6`'s rate); they now return an explicit unpriced result instead
> (`priced=False`, `total_usd=0.0`). If you call `tracegauge.cost.compute_turn_cost`/
> `compute_session_cost` directly and read `total_usd`, see the `[0.10.2]` entry in
> [CHANGELOG.md](../CHANGELOG.md) for the exact migration path if you're upgrading from
> `0.10.1` or earlier.

> **Note — Python 3.10: 0.12.3, 0.12.4 and 0.13.0 do not import on Python 3.10** (`ImportError: cannot import
> name 'UTC' from 'datetime'`, also from the `tracegauge` command). 0.14.0 fixes it; on 3.10 install 0.14.0 or later,
> or pin `tracegauge<=0.12.2`. Python 3.11+ was never affected. See the
> `[0.14.0]` entry in
> [CHANGELOG.md](../CHANGELOG.md).

## Install and quickstart timing

Two commands. Scores a bundled sample Claude Code session (token economy, deterministic waste detection, cost annotation) and prints a real three-axis report — nothing read from your machine, no local Ollama probe, no consent prompt. All three axes render something meaningful with zero configuration: token economy scores against the research-recon band (the bundled sample is synthetic and labeled so, sized to pass that task type's scope gate), trajectory quality states plainly that it's unavailable without a judge and shows a labeled, non-computed example of what that axis looks like once one is configured, and waste detection reports one deterministic `REPEATED-FAILED-RETRY` finding with its proof turns, followed by an informational cost breakdown (dollars per bucket). **Timing, measured on the published 0.15.0 on one busy Windows 11 machine (CPython 3.13, CPU load 33 to 77%) and not a guarantee: `pip install tracegauge` into a fresh venv took 9.8 to 10.7 s with a warm pip cache (38.3 s with an empty one), the installed `tracegauge quickstart` then took a median 0.41 s, and `uvx tracegauge quickstart` took a median 3.0 s cold and 0.5 s warm; the core install is 10.1 MiB (installed package files, excluding pip), and conditions and raw numbers are in [docs/INSTALL.md](INSTALL.md).**

## Scope & Limitations

Read this before installing. These are not caveats to hide — they're the honest picture of what the tool measures and where the calibration comes from.

**Corpus caveat (the one-developer table).** The bundled one-developer table ("reference: 1 developer"; it is no longer shown as a verdict, and serves only as the live alarm's fallback and as the upper bound of a task type's scope floor) is derived from 71 Claude Code sessions of one developer (not quality-gated; each API response's usage counted once; listed by id and hash in `eval/manifest.json`, which holds no session text, so the baseline is the maintainer's unpublished local measurement), heavy on infrastructure and agent-orchestration work. Four task types have a band; feature-build (8 sessions, below the minimum of 10) has none yet. B5 generalization validation on 1,053 SWE-chat CC sessions (58 attributed developers plus 281 sessions with no user_id) found the generalizable repeated-failed-retry rate is ~1.4% — versus 6.6% in the calibration pool (the maintainer's unpublished local measurement), which is a high-waste infra outlier. A developer doing ordinary coding work may score below-band on the token axis without being inefficient; the baseline encodes one developer's typical heavy infra and agent-orchestration work (it is not filtered for quality), not a universal reference.

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat) (Baumann et al., 2026, arXiv:2604.20779), made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/). See [DATA_SOURCES.md](../DATA_SOURCES.md) for the full attribution and what was derived from it.

**No human accuracy validation.** The trajectory judge (Qwen3-30B) is coherence-validated against a reference LLM (Spearman ρ ≈ 0.79, the maintainer's unpublished local measurement), not calibrated to human expert labels. Positive verdicts (MUCH_BETTER/BETTER) are cross-model corroborated at 84–96% (the maintainer's unpublished local measurement). Negative verdicts (WORSE/MUCH_WORSE) are model-dependent — treat them as a signal to review, not a ground truth.

**Tiered judge.** Token economy and deterministic waste run locally with no GPU and no network — these axes are always available. The trajectory quality axis requires a local Ollama judge (~18 GB VRAM for Qwen3-30B). Without it, trajectory prints UNAVAILABLE, which is the expected complete state for most users, not an error.

**What waste detection covers.** The two waste detectors catch observable-invariant patterns only: exact-match retry loops with no state change, and redundant file reads where the content was unchanged. Judgment-of-progress waste (was this cycle productive? was this approach the right one?) is not covered — that requires human labeling and is out of scope.

**Local by default.** All scoring is local. No telemetry, no phone-home, no external network calls from scoring or the dashboard (except the optional local Ollama endpoint). The localhost bind is enforced by construction, not configuration. The only thing that currently leaves your machine is opt-in and separate from scoring: the API judge (your key, your consent). A second opt-in path, community corpus contribution, is built but not active — see below.

**Optional export (off by default, nothing transmitted).** `tracegauge export-contribution` writes a redacted, content-free local file you inspect and control — numeric token counts, the 5 known task types, detector names, and an opaque random UUID. No code, no prompts, no file paths, no session IDs, no error text, no timestamps. This command itself never transmits anything — the file is yours; the tool never reads it back or uploads it. A separate, further opt-in command, `tracegauge corpus contribute`, is built to send that same content-free data to a tracegauge community corpus in exchange for a cross-developer baseline — but **no corpus is currently provisioned**, so that command sends nothing regardless of consent. See [PRIVACY.md](../PRIVACY.md) for the complete field list, the send-time re-verification, the withdrawal path, and the dormancy notice.

## The three axes

No composite score. Three independent labeled signals, each with its own domain of validity.

### Token economy

Compares the session's real token count (AI turns only; cache-read inflation removed) against YOUR OWN sessions of the same task type (ml-eval, debug-fix, infra-deploy, research-recon, feature-build). From 10 other in-scope sessions with usage it reports the session's mid-rank percentile among all of them ("about the 70th percentile of your 24 debug-fix sessions") and labels it against the p25 and p75 of those sessions; before that the verdict is `unavailable` and the text says `building (n/10)`. There is no lean-subset selection, no outlier cap and no stability guard: every in-scope session counts once, and a session already in the store is not compared with itself. Verdicts: `above_p75`, `within_band`, `below_p25`, `unavailable`. Separately, `score` prints a **context** line: the session's tokens per turn as a percentile of other developers' sessions of the same task type (a developer-weighted table built from the public SWE-chat dataset, labelled with its population, source and dates). It is not a verdict and is not used in one; how it is built, why tokens per turn, and when it is hidden: [MULTIDEV_REFERENCE.md](MULTIDEV_REFERENCE.md).

`unavailable` when the session is below the per-type p10 turn floor (scope gate) — the session is too short relative to the reference mass to produce a meaningful comparison. Not an error.

**Why the pool is per task type.** Task type explains almost none of the spread in tokens per turn within a public SWE-chat developer (0.3% of the variance; developer-cluster bootstrap 95% interval 0.1 to 0.7%; 7,023 in-scope sessions of 480 developers) but a third of it across the maintainer's own sessions (eta² 0.34, project-cluster bootstrap 95% interval 0.16 to 0.57; 45 in-scope sessions of 11 projects; the maintainer's unpublished local measurement). A verdict pooled across task types would only be justified if the type effect were small for the person whose sessions form the pool, and that upper bound is not below 0.10, so the verdict stays per task type. These figures are for log tokens per turn; the verdict itself compares session token totals.

**Domain of validity:** relative to your own sessions of the same task type (their whole history, not a selected subset), not to quality-certified runs and not to anyone else's habits. Interpret alongside the trajectory verdict.

### Trajectory quality

A local Qwen3-30B judge scores the session's trajectory on purposefulness: `MUCH_BETTER` / `BETTER` / `SIMILAR` / `WORSE` / `MUCH_WORSE`.

Requires a local GPU (~18 GB VRAM). Without the judge, this axis is `UNAVAILABLE` — token and waste axes still run fully.

**Domain of validity:** positive signal cross-model corroborated (B3 report); negative signal is model-dependent. No human gold labels.

Just add `--judge` — the tool detects what's available and does the work:

```bash
tracegauge score --judge
```

What `--judge` does, in order:
1. **Local Ollama judge running?** → use it (free, ~18 GB VRAM; `ollama pull qwen3:30b-a3b` to install — see https://ollama.ai).
2. **No local judge, but `ANTHROPIC_API_KEY` set?** → *offers* the API judge and shows a consent screen. **Nothing is sent until you confirm** — auto-detecting a key never auto-sends data.
3. **Neither?** → prints the single simplest setup step. It never fails cryptically. Token + waste axes always run regardless.

To use the opt-in API judge directly (no GPU needed):
```bash
tracegauge score --api-judge                      # uses ANTHROPIC_API_KEY from the env
tracegauge score <path> --api-judge --api-judge-key YOUR_KEY
# Sends session data — including 300-char snippets that may contain your code — to your provider.
# Uses the same validated v3 rubric. Requires explicit consent per session (shown at prompt).
# The API model is not part of the B3 cross-model corroboration — verdict is indicative.
# See PRIVACY.md for what is sent.
```

> **Consent is never silent.** Frictionless means the tool finds the judge for you — not that it sends your data without asking. Every byte of egress passes the per-session consent screen requiring an explicit `y`. The judge also stays OFF by default in the background watcher (a GPU/cost footgun guard).

### Deterministic waste

Two observable-invariant detectors with proof turns attached to every event:

- **REPEATED-FAILED-RETRY** — same shell command + same error output + no state change between retries. Validated on 1,053 SWE-chat CC sessions from 58 attributed developers plus 281 sessions with no user_id. ~1.4% of ordinary CC sessions; ~6.6% in our calibration pool (a high-intensity infra outlier). Both figures are the maintainer's unpublished local measurement.
- **REDUNDANT-READ** — same file content read twice with no edit between reads (PATH-A: CC's own "File unchanged" verdict; PATH-B: content-match, gap ≤ 5 turns). Dual-format regex handles both pre- and post-v2.1.38 CC output.

**Domain of validity:** observable-invariant only. Fires conservatively — misses judgment-of-progress waste by design.

## Token attribution

The session-detail view in `tracegauge serve` breaks billed token spend into six named buckets — context re-send (cache reads), context growth (cache writes), output, fresh input, redundant-read waste, and retry-loop waste — reconciling exactly to total billed tokens.

Dollar and token percentages are shown side-by-side because they diverge significantly: in the bundled sample session (`tracegauge quickstart`), cache re-reads are 94% of billed tokens (`context_resend_pct` 0.939 in `tracegauge score --json` on the bundled sample file `tracegauge/data/quickstart_sample_session.jsonl`; `output_pct` there is 0.008) but 51% of cost (cache reads bill at 0.1× the input rate on most models, 0.05× on Opus/Sonnet 5.5 and 0.025× on Fable/Mythos 5.1), while output at 0.8% of tokens is 22% of cost (billed at the full output rate). The dollar column is what matters for spend; the token column is what the verdict axis measures. These should not be compared directly.

Attribution is computed from the source JSONL on demand. `tracegauge score` and the session page show a COST BREAKDOWN, labelled informational: dollars and share of priced cost for context re-send, context growth, output, fresh input and detected waste (`cost_breakdown` in `--json`; for a session with an unpriced model it says the cost was not computed instead of guessing). A LEVER finding (`lever_hint` in `--json`, `null` when none) appears only when an absolute rule fires: detected waste of at least $0.50 (or at least 10.0% of cost and $0.05), with its proof turns in the waste section, or output at 40.0% of cost or more (both compared unrounded). Context re-send is never a lever: re-reading the context every turn makes it most of the cost of nearly every session, so "context dominates" says nothing about any one of them (the bundled sample fires no lever).

## `tracegauge cost` — period spend report

```bash
tracegauge cost --week                 # rolling last 7 days
tracegauge cost --month                # rolling last 30 days
tracegauge cost --since YYYY-MM-DD      # from this date through now
```

Total spend, session count, and a per-project breakdown for a period — distinct from `tracegauge budget`'s rolling self-trend *projection* (where your pace is heading); `tracegauge cost` reports what you actually spent. `--week`/`--month` are rolling N-day windows ending now, not calendar-aligned (a calendar week/month needs a timezone and first-day-of-week convention this tool has no basis to guess).

> **About the examples.** The output blocks in this document are the real output of this version (0.16.0) on **synthetic sessions**: invented transcripts with a frozen clock, produced by [`scripts/gen_readme_examples.py`](../scripts/gen_readme_examples.py) and pinned by `tests/test_readme_examples.py`, so they cannot drift from the code. No real session content appears in them. Rows stored by 0.14 and earlier are left out of `tracegauge cost` totals and reported on a separate line; see [docs/UPGRADING.md](UPGRADING.md).

Real output on four synthetic sessions across two projects (one older than the 7-day window):

```text
──────────────────────────────────────────────────────────────────────
COST -- last 7 days
──────────────────────────────────────────────────────────────────────

Total: $8.43  (3 sessions)

By project:
  demo-web                                     $4.54  (1 session)
  demo-api                                     $3.89  (2 sessions)
──────────────────────────────────────────────────────────────────────
```

`tracegauge cost --month` against the same data picks up the older session too:

```text
──────────────────────────────────────────────────────────────────────
COST -- last 30 days
──────────────────────────────────────────────────────────────────────

Total: $11.51  (4 sessions)

By project:
  demo-api                                     $6.97  (3 sessions)
  demo-web                                     $4.54  (1 session)
──────────────────────────────────────────────────────────────────────
```

**Filters on `source_mtime`, not `scored_at`** — the session file's own real last-write time (when the usage actually happened), not when `tracegauge score`/`tracegauge scan` happened to run. Under a batch-scoring workflow (scoring a week's worth of sessions in one sitting), these diverge: a `scored_at`-based filter would cluster a week of real spend onto one scoring-run instant, or drop it outside the requested window, silently misattributing spend across period boundaries. `source_mtime` reflects when the money was actually spent, regardless of when you got around to running the scorer. (`tracegauge budget` had the same divergence and was fixed to filter on `source_mtime` as well in 0.12.1.)

Sessions with no cost data yet are counted separately, not silently treated as `$0`. Real output of this version, with one of two synthetic sessions stored without a cost:

```text
──────────────────────────────────────────────────────────────────────
COST -- last 7 days
──────────────────────────────────────────────────────────────────────

Total: $0.60  (1 session)
  (1 additional session in this period has no cost data yet -- excluded from the total above, not counted as $0)

By project:
  demo-api                                     $0.60  (1 session)

Priced coverage: 50% of sessions, 50% of tokens
  (some unpriced sessions predate model tracking -- can't name their model)
──────────────────────────────────────────────────────────────────────
```

**Known gap, not built:** no per-model breakdown. Would need a new schema column and adapter change, and nobody in the originating GitHub issue ([#78148](https://github.com/anthropics/claude-code/issues/78148)) asked for it — left explicit rather than silently omitted.

### Unpriced coverage

Shown automatically whenever coverage is below 100% — never hidden behind a flag, since an incomplete total should always be visible as incomplete. Reports what fraction of the period's *sessions* and *tokens* are actually priced (two different denominators — a handful of huge unpriced sessions can dominate the token figure while barely moving the session count), and names the specific unresolved model string(s) when known. A session with any turn of an unpriced model counts as unpriced in full (the store has no per-model token split), so the token figure is an upper bound on the unpriced tokens, and such a session shows `unpriced (<model>)` instead of `$0.00`. Real output of the 0.16.0 build on two synthetic sessions, one priced and one using a model that is not in the price table:

```text
──────────────────────────────────────────────────────────────────────
COST -- last 7 days
──────────────────────────────────────────────────────────────────────

Total: $0.60 + unpriced (claude-future-9)  (2 sessions)
  (priced subtotal only: turns of the unpriced model(s) below are NOT in this figure -- the real total is higher)

By project:
  demo-api                                     $0.60  (1 session)
  demo-web                                  unpriced (claude-future-9)  (1 session)

Priced coverage: 50% of sessions, 50% of tokens
  unpriced (claude-future-9)
  (1 session not fully priced, 112,500 tokens: a session with any unpriced turn counts as unpriced in full)
──────────────────────────────────────────────────────────────────────
```

A session scored before `0.12.0` has no persisted model name for its own unpriced gap (the same lesson `0.11.1`'s attribution-persistence fix already established: information only available while the source file is readable must be saved at score time, not re-derived later) — that gap is still counted honestly, just flagged as unattributable rather than silently folded into a list that would then look complete (real output on synthetic sessions, with one session's stored cost and model name blanked to stand in for a pre-`0.12.0` row):

```text
──────────────────────────────────────────────────────────────────────
COST -- last 7 days
──────────────────────────────────────────────────────────────────────

Total: $0.60 + unpriced (gpt-6-preview)  (2 sessions)
  (priced subtotal only: turns of the unpriced model(s) below are NOT in this figure -- the real total is higher)
  (1 additional session in this period has no cost data yet -- excluded from the total above, not counted as $0)

By project:
  demo-api                                     $0.60  (1 session)
  demo-web                                  unpriced (gpt-6-preview)  (1 session)

Priced coverage: 33% of sessions, 33% of tokens
  unpriced (gpt-6-preview)
  (2 sessions not fully priced, 225,000 tokens: a session with any unpriced turn counts as unpriced in full)
  (some unpriced sessions predate model tracking -- can't name their model)
──────────────────────────────────────────────────────────────────────
```

### `tracegauge cost --roi` — plan-cost ROI

```bash
tracegauge cost --week --roi
tracegauge cost --week --roi --plan-config /path/to/plan.json   # override the default location
```

"Is my subscription worth it at API-equivalent prices?" Requires a plan config at `~/.tes/plan.json` (or `TRACEGAUGE_PLAN_PATH`/`--plan-config`) — a **history**, not a single static cost, since a plan can change mid-window and each day should price at whichever plan was actually active that day, prorated by exact elapsed time (not calendar-day counting, which would inflate a rolling window that doesn't start/end at midnight — every real invocation):

```json
{"plans": [
  {"name": "Claude Pro", "monthly_cost_usd": 20, "effective_from": "2026-01-01"},
  {"name": "Claude Max", "monthly_cost_usd": 200, "effective_from": "2026-07-01"}
]}
```

Real output of this version on twelve synthetic sessions (Claude Opus 5.5) against the plan history above:

```text
Plan: Claude Max ($46.67 for this window)
ROI: $195.48 API-equivalent / $46.67 plan cost = 4.2x
  (API-equivalent value at measured token rates, not a bill you'd actually pay under a flat plan.)
```

The ROI is built on the corrected total only, so when rows from older versions were left out the multiple is a floor, and the output says so.

**Refuses to print a ratio the data can't support** — never a misleading number:
- No `plan.json` configured: prints setup instructions instead of a ratio.
- Zero priced sessions in the window: "no priced sessions in this period — nothing to compare against plan cost."
- The window falls entirely before your first `plan.json` entry (prorated plan cost is $0): same refusal — a `$X / $0` ratio isn't a real number either.

## `tracegauge impact` — code-impact reconstruction

```bash
tracegauge impact
tracegauge impact --top 20
```

Corpus-wide, from `Edit`/`Write`/`MultiEdit`/`NotebookEdit` tool-call payloads reconstructed and persisted at score time — additions/deletions per file, and a plain, transparent churn ranking (most-edited files and directories). **Deliberately no composite "risk score"**: a hand-weighted blend of signals presented as one number is exactly the kind of invented-precision this project avoids elsewhere (see the CHANGELOG's `[0.12.0]` entry for the reasoning) — every figure here is a direct count or a plainly-labeled fraction.

Real output on the maintainer's own transcript (an unpublished local measurement; that transcript is not in the repo):

```text
291 edit operation(s) across 1 session(s) with impact data
  +14235 / -1994 lines
  66% of additions are from Write/NotebookEdit calls, whose payload never carries the file's PRIOR content -- additions are exact, but a full-file rewrite looks identical to a brand-new file, so this fraction is inherently uncertain in that specific way.

Most-edited files:
    20 edits  +494/-188  (1 session(s))  tracegauge/cli.py
    15 edits  +137/-58  (1 session(s))  tracegauge/score.py
    15 edits  +193/-127  (1 session(s))  tracegauge/intelligence/cache.py
```

**Extraction scope, checked against the maintainer's own local corpus** (883 transcript files, 434,774 lines; an unpublished local measurement): `Edit` (10,175 real occurrences) and `Write` (3,059) are fully supported. `MultiEdit`/`NotebookEdit` are real Claude Code tool names with **zero occurrences in that same real-corpus check** — extraction is written (best-effort, defensive) but every operation from either is flagged internally and surfaced in the output's own inline fraction whenever it contributes to a total, never presented with the same confidence as Edit/Write-derived numbers. `apply_patch`/`str_replace_editor` (Codex/computer-use tool names, not Claude Code ones) are out of scope entirely.

**The `Write` ambiguity, stated plainly, not swallowed**: a `Write` call's payload contains the new file content but never the content it replaced — additions are exact, deletions are always 0 for that operation, and the report states what fraction of the total additions figure rests on this assumption (see the real output above: 66% on this real corpus).

**No cost-per-edit or cost-per-100-lines ratio here.** A bootstrap-CI implementation was built and measured (`docs/audit/EDIT_RATIO_BOOTSTRAP_COVERAGE.md`) before being adopted — coverage came in below nominal at every tested sample size, so no ratio statistic ships until a method with verified coverage exists. A number with a measured-wrong confidence interval is worse than no interval at all.

**If you're upgrading**: sessions scored before this feature has no persisted edit data (same `0.11.1` lesson — `old_string`/`new_string`/`file_path` are only readable while the source transcript exists, so this can't be backfilled from already-scored rows). `tracegauge impact` counts these separately and says so plainly rather than silently omitting them from the total.

## `tracegauge budget` — rolling self-trend pace

```bash
tracegauge budget                      # rolling last 7 days (default)
tracegauge budget --window-days 30
```

A rolling-window spend PROJECTION — distinct from `tracegauge cost`'s period REPORT above. `tracegauge cost` answers "what did I actually spend"; `tracegauge budget` answers "where is my pace heading," extrapolating your own trailing-window spend linearly to the window's end. Always labeled with its sample size and window, never phrased as a promise ("trending toward," never "you will spend"). Real output of this version on ten synthetic sessions over the last week:

```text
──────────────────────────────────────────────────────────────────────
BUDGET / PACE
──────────────────────────────────────────────────────────────────────

At this pace (~$40.32 so far across 10 sessions, 5.2 of 7 days) you're trending toward ~$53.76 over a 7-day window — based on your last 5.2 days, not a forecast of future work; work volume varies.

──────────────────────────────────────────────────────────────────────
```

The day counts are shown rounded to one decimal: the projection uses the unrounded elapsed time, so scaling the displayed 5.2 days by hand gives ~$54.28 rather than the ~$53.76 shown.

Rows stored by older versions are left out of the pace, and the command says how many (`legacy_rows_excluded` in `--json`).

At the default 7-day window, with no cost data that recent, `tracegauge budget` says so plainly rather than fabricating a projection from stale data:

```text
No sessions with current cost data in the last 7 days — nothing to project yet.
```

**Filters on `source_mtime`, not `scored_at`** (since 0.12.1) — when the session file was last written, the same as `tracegauge cost`. Before 0.12.1 it filtered on `scored_at` (when `tracegauge score`/`tracegauge scan` happened to run), so under a batch-scoring workflow it could cluster a batch of older spend onto one recent instant, or miss it once it aged out of the window.

## `tracegauge monitor` — live in-progress check

```bash
tracegauge monitor [--stability-window SECONDS] [--plan usage_based|max]
```

A one-shot check of whatever Claude Code session is currently being written under `~/.claude/projects` — scores it as-is (a partial transcript, not a finished session), prints an estimated cost/context figure explicitly labeled "in progress," and checks the same data-gated cost/context alarm the background watcher uses. A session more recently modified than `--stability-window` (default 300s) is considered "active"; if none is, it says so and does nothing further.

Real output of this version on thirteen synthetic sessions (twelve earlier ones plus one being written right now):

```text
Session: a1b2c3d4-0000-4000-8000-000000000001  (debug-fix)
  ~$7.14 (estimated, in progress)
  ~852,500 context tokens (estimated, in progress)
  98% context re-send (measured)

Live estimate of an IN-PROGRESS session — cost and context size are provisional and will change as the session continues. Computed with the same frozen attribution/cost math used on completed sessions, applied to the partial transcript seen so far. Never a final or billed figure.

[ALARM] This session is at ~$7.14 (estimated, in progress) and ~852,500 context tokens (estimated, in progress), 98% of which is re-sent context (measured) — well above your own recent sessions (p85 of your last 30 days of claude-sonnet-5-5 debug-fix (n=12): 257,550 tokens). Consider `/compact`.
```

The command above exits 3 because the alarm fired.

The alarm fires only when the live session's real tokens exceed the **p85 of your own sessions from the last 30 days in the same model era** (rows from the current accounting only) **and** re-sent context dominates. The pool is the first of these with at least 10 sessions: same era and task type, same era, same task type, any recent session; then the bundled band's upper bound; otherwise the alarm is disabled. `tracegauge monitor` prints the threshold and where it came from (or why the alarm is disabled), and exits 3 only when the alarm fires (0 otherwise, including when no session is active). Method, false-alarm rate and limits: [docs/ALARM.md](ALARM.md); exit codes: [docs/EXIT_CODES.md](EXIT_CODES.md).

## Session intelligence — `tracegauge patterns` and `tracegauge ask`

The most differentiated thing either package does, and previously the least visible: unsupervised clustering of your own session corpus into behavioral archetypes, plus a constrained conversational explainer over the result.

### `tracegauge patterns`

```bash
tracegauge patterns [--recompute]
```

Needs the optional `patterns` extra (since 0.15.0): `pip install "tracegauge[patterns]"` adds numpy and scikit-learn. Without it `tracegauge patterns` and `tracegauge ask` print that one-line install hint and exit 1, and the dashboard shows an "extra not installed" page for Patterns; everything else keeps working.

Runs (or displays the cached result of) validated KMeans clustering over your session corpus, plus statistical anomaly detection. Results cache to a file named after and co-located with your TES database (`<db-name>.intelligence_cache.json` — e.g. `~/.tes/tes.intelligence_cache.json` for the default DB) and are reused by `tracegauge ask` below.

Requires **30+ content sessions**. Attribution fractions (the features clustering runs on) are computed once and persisted directly to the database at score time — a session scored by `tracegauge score`/`tracegauge serve` clusters correctly regardless of whether its original source JSONL still exists on disk later.

**If you're upgrading from `0.11.0` or earlier: read this before running `tracegauge patterns`.** Sessions scored before this version cannot be backfilled — their attribution was never persisted, and recovering it now would require re-reading the original transcript file, which for most real setups no longer exists (transcripts age out, get cleaned up, or move). There is no partial recovery either: the fractions clustering needs (context re-send/growth, output, waste) aren't derivable from anything else this tool stores about a session — checked directly, not assumed (the closest available data, `waste_events`, records dollar cost per detected event, not the raw token breakdown clustering needs, and covers only 1 of the 4 features either way). **Concretely, on the machine this was measured on (the maintainer's unpublished local measurement): 321 of 321 previously-scored content sessions (100%) are permanently unusable for clustering post-upgrade — the pattern corpus starts over at 0 and rebuilds only from sessions scored from this version forward.** Your own numbers will differ, but the mechanism is the same: nothing is lost or corrupted, `tracegauge score`/`tracegauge patterns`/`tracegauge ask` on already-scored sessions keep working exactly as before this upgrade — only the *clustering* feature specifically restarts its corpus. If a legacy session's file is confirmed unreachable, the tool says so plainly rather than guessing (illustrative output: the counts are an example, not a measurement):

```text
Not enough content sessions for pattern analysis yet (12 < 30 needed) -- 8 previously-scored session(s) can't count because their original transcript file no longer exists on disk (scored before this version started saving what it needs at score time; those specific sessions can't be recovered -- re-scoring requires the same file, which is gone). Your pattern corpus rebuilds from sessions scored from now on; nothing else to do.
```

**How the archetypes are derived** (methodology, not invented): KMeans clustering over an 8-feature vector per session (four attribution percentages — context re-send, context growth, output, detected waste — plus three log-scaled size features and a has-waste flag; `FEATURE_NAMES` in `tracegauge/intelligence/features.py`), with `k` chosen by silhouette score over `k ∈ [2, 8]`, validated for stability via 10 reseeded runs (coefficient of variation `< 0.15` required to call the result "stable"). Each archetype's *name* is generated automatically from its centroid's most discriminating features relative to the corpus mean — never hand-labeled, and evaluative words (efficient/wasteful/good/bad) are prohibited by construction; a name describes measured shape, not quality.

**A real, live result** (the maintainer's unpublished local measurement) — 38 real Claude Code sessions, scored fresh this session (`tracegauge score --no-judge` against two real project directories, `--recompute`), demonstrating the score-time-persistence fix directly: `[features] extracted 38 / 39 sessions (persisted=38, stubs=1, no_source=0, failed=0)` — every one of those 38 came from the database, zero source-file re-reads. `k=3`, silhouette `0.479` (above the `0.20` "meaningful structure" bar), stability CV `0.000` (perfectly stable across all 10 reseeded runs); the figures and the output block below are the maintainer's unpublished local measurement:

```text
ARCHETYPES (measured behavioral patterns -- not quality labels):

  [0] medium high context re-send sessions
      22 sessions (57.9%)  context_resend=98.9%  context_growth=0.9%  output=0.2%  waste_flag=no
      task mix: debug-fix:7  ml-eval:6  feature-build:6  research-recon:3

  [1] small high context re-send sessions
      14 sessions (36.8%)  context_resend=97.4%  context_growth=2.1%  output=0.5%  waste_flag=no
      task mix: ml-eval:8  debug-fix:2  feature-build:2  research-recon:2

  [2] medium with detected waste sessions
      2 sessions (5.3%)  context_resend=98.3%  context_growth=1.1%  output=0.2%  waste_flag=yes
      task mix: ml-eval:1  debug-fix:1

ANOMALIES: 2 of 38 sessions (5.3%) are statistical outliers for their cluster.
```

**What you do with this:** the archetypes are a description of your own measured behavior, not a scorecard — there's no "good" archetype to aim for. This particular corpus's three archetypes separate mainly on *size* and *waste presence*, not on dramatically different working styles (consistent with this project's own earlier B-phase clustering research on a larger corpus, `research/12_session_intelligence.md`) — useful context before over-reading meaning into which archetype a given session falls into.

### `tracegauge ask`

```bash
tracegauge ask "What kind of sessions do I run?"
tracegauge ask "Do I have any waste patterns in my sessions?" --api   # opt-in, consent-gated
```

A conversational explainer, constrained by construction to answer only from the same measured metrics/pattern output `tracegauge patterns` produces — never invents analysis, predicts future cost, or judges session quality. Tries local Ollama first (`qwen3:8b` by default, or whatever 7B+ model is available); with no local judge but `ANTHROPIC_API_KEY` set, offers the API path with an explicit per-call consent prompt (sends metrics only, never session content/code).

`tracegauge ask` answers from your measured session metrics using a local model; parts of a question that tracegauge does not measure (forecasts, prompt quality) are declined instead of guessed. Wording varies by model and run.

## What this does NOT do

- No composite efficiency score. The three axes are independent by design — a single number would hide the axis-specific domain limitations.
- No "catches all inefficiency." The waste detectors fire on observable-invariant patterns only.
- No accuracy guarantee on the trajectory axis. It's an LLM judge, coherence-validated, not human-calibrated.
- No cloud scoring. The scoring pipeline is fully local. `tracegauge export-contribution` (P7) provides a local-file-only contribution export: opt-in, content-free, nothing transmitted. Server-side aggregation of that data (`tracegauge corpus contribute`) is built and tested but **not currently active** — no corpus is provisioned, so it sends nothing regardless of consent — and would never be a substitute for the local self-baseline even once activated.
- No cross-agent support yet. The CC adapter is Claude Code–specific; OpenCode/Codex/Aider would need their own adapters and re-validation.

## Validation

The scoring components were validated through a five-phase credibility arc (B1–B5) before packaging. Key results below are the maintainer's unpublished local measurements on the maintainer's own sessions: the pool, its judge scores and its waste signals are not in the repository (the tests run on synthetic fixtures), so none of these figures can be re-derived from a clone.

- **Token baselines (B2, research pool):** 75 quality-gated CC sessions, 5 task types, scope gates at per-type p10 turn floor, on the old per-record usage scale. Since 0.15.0 the bundled baseline is a different, ungated 71-session re-baseline (see Scope & Limitations). See [research/08-baselines.md](../research/08-baselines.md).
- **Trajectory judge (B3):** Cross-model corroboration. Positive verdicts: 84% strict / 96% top-2. Negative verdicts model-dependent. No human gold. See [research/09-cross-model.md](../research/09-cross-model.md).
- **Deterministic waste (B4):** RFR fired 12/181 pool sessions (6.6%). RR fired 20/181 (11.0%). Observable-invariant boundary documented. See [research/10-deterministic-waste.md](../research/10-deterministic-waste.md).
- **Generalization (B5):** RFR and PATH-A validated on 1,053 SWE-chat CC sessions with raw transcripts, from 58 attributed developers plus 281 sessions with no user_id (the dataset's table lists 4,852 CC sessions from 172 attributed developers, but only 1,053 include a transcript) — [ODC-BY licensed, see DATA_SOURCES.md](../DATA_SOURCES.md)). Rate gap (6.6% pool vs 1.4% SWE-chat; the maintainer's unpublished local measurement) explained by corpus characterization — pool is a high-waste infra outlier. Cross-agent generalization inconclusive (parquet lacks tool_result rows for OpenCode/Codex). See [research/11-generalization.md](../research/11-generalization.md).
