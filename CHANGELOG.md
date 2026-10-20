# Changelog

All notable changes to **tracegauge** are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/)
conventions.

A note on version numbers: the published PyPI artifacts are `0.1.0`, `0.3.0`, `0.3.1`, `0.5.0`,
`0.6.0`, `0.7.0`, `0.7.1`, `0.8.0`, `0.10.0`, `0.10.1`, `0.10.2`, `0.11.0`, `0.11.1`, `0.12.0`,
`0.12.1`, `0.12.2`, `0.12.3`, `0.12.4`, `0.13.0`, `0.14.0`, `0.15.0`, `0.16.0`, `0.17.0`, and `0.17.1` (the current release; the earlier ones confirmed live against PyPI's own JSON API;
this note previously stopped enumerating at `0.10.2` for four releases running — corrected here
rather than left standing, per the same "nothing re-checks this note against reality" gap
RELEASING.md's own incident section already names). Versions `0.2.0` and `0.4.0` were built and
tagged internally but never published to PyPI. `0.9.0` is built, tested, and committed, but
**deliberately not published** — see its entry for why (corpus stays dormant). `0.17.1` is the
**current release**.

`0.12.3`, `0.12.4` and `0.13.0` are **yanked** on PyPI (reason: "ImportError on Python 3.10 (datetime.UTC); fixed in
0.14.0"): they do not import on Python 3.10 and are fixed in `0.14.0`. A yank hides a version from unpinned and range
installs (`pip install tracegauge`, `tracegauge<0.14`) but not from an exact pin: `pip install tracegauge==0.13.0`
still installs, with pip's yanked-release warning. Checked against the real index on 2026-10-09.

## [Unreleased]

## [0.17.1] - 2026-10-21 — a malformed or null usage field no longer aborts scoring a session

- **Fixed:** `tracegauge score` exited 4 on a Claude Code transcript with `"cache_creation_input_tokens": null` (or another null, boolean or
  non-numeric token count), and failed the whole file on a line that is valid JSON but not an object (`5`, `[1, 2]`) or on a `message` of the
  wrong type. Such counts now count as 0, non-object and over-nested lines are skipped with a warning, and wrong-typed `message`, `usage`
  and `model` fields are treated as empty. Test: `tests/test_adapt_hostile_input.py`.
- **How it was found:** 500 random public SWE-chat sessions through the published 0.17.0 CLI: 499 scored, the failure had a null
  `cache_creation_input_tokens`. A seeded structural fuzz (1,600 mutations) went from 5 escaped exceptions to none.
- **Docs:** `docs/INSTALL.md` core install size re-measured on 0.17.0: 11.3 MiB (10.1 MiB was measured on 0.15.0).

## [0.17.0] - 2026-10-19 — token verdicts from your own sessions only, a multi-developer context line, subagent spend by type, contributor on-ramp

The scoring math, the waste detectors, the cache-churn detector and the prices are unchanged
(`ADAPTER_VERSION` and `COST_VERSION` did not move; every existing cost and waste figure in a report is the
same). Three behaviours change and are listed first.

### Behaviour changes: read before upgrading

1. **A token verdict comes from your own sessions only, as a percentile.** A session's token reading is now its
   place among your other in-scope sessions of the same task type: "about the 70th percentile of your 24 debug-fix
   sessions", labelled `below_p25` / `within_band` / `above_p75` against the p25 and p75 of those
   sessions. It is active from 10 of them; before that it says `building (n/10)` and the verdict is `unavailable`.
   Nothing is borrowed from the bundled one-developer table or from any other population on the command line, the watcher or the dashboard (a program that calls `score_session` with no state still gets the raw table result, as before). Every
   in-scope session with usage counts once: waste is not a filter, empty stubs (no usage), sessions below your
   scope floor and rows scored by the pre-dedupe accounting do not count, and a session already in the store is
   not compared with itself. The cost framing ("cost vs your usual") keeps its own conservative reference of waste-free, lower-half sessions; it is not the token verdict. The lean-half selection, the 90th-percentile cap and the stability guard of 0.16.0 are
   removed (they needed about 17 waste-free sessions, and on the maintainer's 51 sessions left none of the five task types active at the end). Until
   0.16.0 `tracegauge score FILE` compared a session with that bundled table while the watcher and dashboard showed
   no verdict; they now agree, and `score FILE` reads your store for the count (an empty state when there is none).
   `score --json` gains `own_percentile` and `own_sessions`. The one-developer table remains in the package,
   labelled "reference: 1 developer", only as the live alarm's fallback threshold and as the upper bound of a task
   type's scope floor. `tracegauge quickstart` has no history of yours, so it scores the sample against 12 seeded,
   invented research-recon sessions held in memory and labelled `SYNTHETIC HISTORY` in the output; nothing is read
   from or written to your store. Replay on the maintainer's 51 local sessions (the maintainer's unpublished local
   measurement; the 68 local transcripts minus 17 from three excluded projects), scored in arrival order into a
   scratch store: 2 of 5 task types were active at the end (debug-fix from its 11th session, infra-deploy from its
   12th) and 5 of the 51 sessions got a verdict (2 `below_p25`, 1 `within_band`, 2 `above_p75`); the other 46 were
   `building` or below the scope floor: debug-fix and infra-deploy sessions that arrived before those types had
   10 earlier ones (10 and 11 sessions), and all of ml-eval (10 sessions, 9 in scope), research-recon (9) and
   feature-build (6 sessions, 2 in scope), which never reached 10 of their own. See [docs/UPGRADING.md](docs/UPGRADING.md).
2. **The store records each session's turn count, which lets your own baseline activate.** Up to 0.16.0 every
   stored row had `turn_count = 0` (the callers of `upsert_session` never passed it and the helper that backfilled
   it had been removed as unused), so no row ever counted toward a baseline and the dashboard banner counted every
   session as an empty stub. From 0.17.0 a session's result carries its turn count, `upsert_session` stores it,
   and `tracegauge rescore` and the watcher's start-up record it for older rows (only that one column is
   written). The scope floor printed for a task type is derived from your own turns (p10, never below 20) once you
   have 10 sessions of the type, instead of the corpus floor.
3. **`tracegauge cost` and `tracegauge score` show subagent spend by type.** See Added. Rows stored by earlier
   versions show as "not recorded" in the period report until the transcript is scored again.

### Added

- **A multi-developer context line.** `score` prints, under the token reading, where the session's tokens per
  turn fall among other developers' sessions of the same task type: "about the Nth percentile of M developers'
  sessions", with the population, source, dates and the ODC-BY notice, and "Not a verdict". It is not used in any
  verdict and never says above or below a band. `score --json` gains a fixed-key `context` object (`null` when
  hidden). Hidden for `feature-build` (the classifier's fall-through type), for a task type whose cell was not
  published and when the turn count is unknown. The table is built from the Claude Code sessions of SWE-chat v2
  (495 developers, 8,367 sessions, created 2026-01-15 to 2026-09-04; developer-weighted; a cell needs at least 5
  developers and moves at most 30% when any developer is left out; no ids, text or per-developer values).
  Tokens per turn was chosen before the build because it was the most stable of three normalisers on both
  dataset releases; v1 (57 developers) was built under the same rules and rejects the ml-eval cell. Method,
  both tables side by side and limits: [docs/MULTIDEV_REFERENCE.md](docs/MULTIDEV_REFERENCE.md). Contains
  information from SALT-NLP/SWE-chat, made available under the ODC Attribution License 1.0.
- **Subagent attribution.** `tracegauge score` prints, under the cost note, the subagent share of the session's
  spend and one line per subagent type (type, files, tokens, dollars, share), and `score --json` gains a fixed-key
  `subagents` object. `tracegauge cost` (and `cost --json`) adds the subagent share of the period's spend and
  tokens and a per-type table. The type is the `agentType` Claude Code writes in `agent-<id>.meta.json` next to each
  subagent transcript (`unknown` when the file is missing or unreadable). A type with unpriced usage shows
  `unpriced` or a `>=` lower bound, never `$0.00`. Per-type token counts in the text are real tokens, the unit of
  the line above them; the JSON also carries billed tokens. New store column `subagent_by_type_json`. Fields:
  [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md). On the maintainer's local transcripts (unpublished local measurement; 60 main transcripts,
  56 with a price, 32 with subagents) subagents were 53.8% of API-equivalent spend. That is a ratio of sums (subagent spend over total spend), so
  the interval is a percentile bootstrap that resamples sessions (2,000 draws, seed 42): 41.7 to 64.3%. It does not
  resample projects, so sessions of one project being alike is not reflected and the true uncertainty is wider.
  *Note added 2026-10-21 (the figure above is unchanged): re-measured on 2026-10-20 with the same method on the transcripts that
  were still on disk after Claude Code aged out older ones (38 priced sessions, 20 with subagents), the share is 51.3%
  (31.2 to 65.8%). The earlier 60-transcript population can no longer be reconstructed.*
- **How often a session has a finding worth acting on**: [docs/ACTIONABLE_FINDINGS.md](docs/ACTIONABLE_FINDINGS.md)
  (the maintainer's unpublished local measurement: 32 of 47 sessions with priced usage, Wilson 53.8 to 79.6%; 18.7% of 1,051 public
  SWE-chat sessions, developer-cluster bootstrap 13.9 to 27.6%) and a check of the churn cause by three
  three language models. Three models of different families (Claude, gpt-oss-120b, qwen3:8b), given only derived numbers and never the detector's label, agreed with its cause label on 158 of 160 sampled events (Fleiss kappa 0.865); this is LLM-consensus agreement with a rule that uses the same facts, not ground truth.
- **Controlled cache runs** documented in [docs/CACHE_CHURN.md](docs/CACHE_CHURN.md): plan mode on and off and a
  hook output added then changed did not collapse the cache read inside the lifetime; the thinking toggle did not
  change the request, so it is reported as untested; the first resume of a `-p` session reads about 70% of the
  prefix (3 of 3 runs). No new cause rule: nothing reproduced in 2 of 2 runs and was identifiable from the
  transcript, so those events stay `UNEXPLAINED`.
- **Contributor on-ramp**: issue forms (feature request, adapter request) and a pull request template, a
  quickstart in CONTRIBUTING.md, [ROADMAP.md](ROADMAP.md), and [docs/ADAPTERS.md](docs/ADAPTERS.md), the adapter contract
  with per-agent format notes (VERIFIED/UNVERIFIED tags kept, sources pinned).
- **Dashboard screenshots on synthetic data** in `docs/screenshots/`, with the generator
  `scripts/gen_dashboard_demo.py` and the commands that produced them.
- README: a pinned subagent example block generated by `scripts/gen_readme_demo.py` and tested.

### Fixed

- The dashboard's coverage banner said "Self-baseline active on all 5 task types" whatever the store held and
  showed a session count from the maintainer's own earlier corpus to every user; it now counts the task types that
  are actually on your own baseline and shows no number your own sessions did not produce.
- Three CodeQL alerts (URL host comparison in two tests and a one-off script, one assertion on text); a fourth
  was dismissed with a written justification (a log line prints a count and a pattern name, never the secret).
- `tests/test_public_text.py` scans tracked files only, so ignored local data cannot fail it on one machine.

### Known gaps

- **Sessions are dated by when they were scored** on the dashboard's trends page, not by the transcript's own
  timestamps, so scoring old sessions in one go shows them all on one date.
- **The thinking setting was not tested** in the controlled cache runs (the toggle used did not change the request).
- The live alarm still ends its chain of thresholds at the one-developer table when you have no usable recent sessions of a type; it first tries your own recent sessions.
- The `tes` names are still removed in 0.18.

## [0.16.0] - 2026-10-17 — renamed to tracegauge (import package, command, environment variables), relicensed Apache-2.0, cache-churn detector

Measurements and migration detail: [docs/releases/0.16.0.md](docs/releases/0.16.0.md). The scoring math and the
bundled data are unchanged. The waste detectors are byte-identical to 0.15.0 (`git diff -M --numstat v0.15.0 HEAD --
tes/_waste_detectors.py tracegauge/_waste_detectors.py` reports a rename with 0 added and 0 removed lines) and a
test pins their sha256. Loaded as JSON, the bundled `cc_baselines.json` differs from 0.15.0 in two fields only
(`baseline_population`, whose text names the renamed module, and `provenance.manifest_sha256`, because the
manifest key `in_phase0_set_b` was renamed `in_eval_set`; every band, count and gate is identical) and
`prices.json` differs in two `note` strings only (no rate, date or flag changed). The behavioural changes are:
task classification no longer special-cases a single private session id; the score output gains a cache-churn
section and the `score --json` output gains a `cache_churn` key (see Added); the deprecated `tes` command prints a
deprecation notice to stderr, `import tes` raises a `DeprecationWarning` (Python hides it by default outside
`__main__` unless warnings are enabled), and the `tracegauge` command prints a stderr notice when it reads a
`TES_*` environment variable ("TES_DB_PATH is deprecated; use TRACEGAUGE_DB_PATH. The TES_ names will be removed in
0.18."); and several user-visible strings and the dashboard's identity (logo, favicon, fonts) changed (see BREAKING
and Changed).

### BREAKING: read before upgrading

1. **The import package, the command and the environment variables are renamed from `tes` to `tracegauge`.**
   `import tracegauge`, `tracegauge score ...`, `TRACEGAUGE_DB_PATH` and the other `TRACEGAUGE_*` variables
   replace `import tes`, `tes score ...` and `TES_*`. `tgauge` is a short alias for `tracegauge`. The old names
   keep working with a deprecation notice and are removed in 0.18: `import tes` is an alias of the same module
   objects (one `DeprecationWarning` per process), the `tes` command prints one line to stderr first (silence it
   with `TRACEGAUGE_NO_DEPRECATION_NOTICE=1`), and a `TES_*` variable is read when its `TRACEGAUGE_*` twin is
   unset. The store stays at `~/.tes/`. User-visible strings changed, among them: `--version` prints
   `tracegauge <version>`, the prose of the JSON field `cost_domain_of_validity` names `TRACEGAUGE_PRICE_TABLE`, the
   text report banner reads `TRACEGAUGE`, the command's argparse description and `--db-path` help text, the
   "No sessions found" hint, the store schema error, the price path in the report footer, the prose of the
   `approximate_reason` field for an unknown model, and the legacy-rows notice hint (it now names
   `TRACEGAUGE_NO_NOTICE`). Scores, JSON field names and types, and exit codes are the same.
   Steps and the full list: [docs/MIGRATING_FROM_TES.md](docs/MIGRATING_FROM_TES.md).
2. **The licence is Apache-2.0 from this release.** `LICENSE` is the Apache-2.0 text, `pyproject.toml` declares
   `license = "Apache-2.0"`, first-party Python files carry an SPDX header (exceptions: `tracegauge/_waste_detectors.py`, which is
   byte-frozen and pinned by sha256 in `tests/test_prior_features_intact.py`, so no header was added, and the
   three files under `src/token_efficiency/`, which are not part of the published wheel), and `NOTICE` states the boundary.
   Releases up to and including 0.15.0 were published under AGPL-3.0-only and stay under it for anyone who
   received them, except that `tes/cost.py`, `tes/_digest.py` and `tes/discovery.py` carried a dual
   "AGPL-3.0-only OR Apache-2.0" header. The grant covers tracegauge's own code and text only: bundled fonts keep
   SIL OFL 1.1 and the datasets keep their own terms (see [DATA_SOURCES.md](DATA_SOURCES.md)), and it gives no
   right to the "tracegauge" name or logo.

### Added

- **Cache-churn detector** (`tracegauge.cache_churn`, documented in [docs/CACHE_CHURN.md](docs/CACHE_CHURN.md)).
  Finds turns where Claude Code re-wrote cached context instead of reading it, prices the difference with the same
  price table as `tracegauge cost`, and gives each event at most one rule-based cause (compaction, model switch,
  idle gap past the cache lifetime), otherwise `UNEXPLAINED`. Surfaces: a CACHE CHURN section in
  `tracegauge score`, a `cache_churn` object in `tracegauge score --json`, and a card on the dashboard's session
  page. Computed from the transcript on demand, nothing is stored. A model missing from the price table is shown
  as unpriced, never `$0.00`. The cause is a rule on transcript markers and timestamps, not a verified fact.
- **`tgauge`** short alias, **`TRACEGAUGE_*`** environment variables and `python -m tracegauge` (see above).
- **DCO sign-off check:** `scripts/check_dco.py` and a `dco` job in `.github/workflows/ci.yml` fail a pull request
  with a commit that lacks a well-formed `Signed-off-by:` matching its author or committer
  ([CONTRIBUTING.md](CONTRIBUTING.md)). Making `dco` a required status check is a repository setting and is not
  done by this change.
- **Secret scan:** a gitleaks pre-commit hook and a `secret-scan` CI job (pinned release, sha256-verified) using one
  shared `.gitleaks.toml`.
- **Docs:** [docs/METHODOLOGY.md](docs/METHODOLOGY.md) (the methodology and every caveat that used to be in the
  README, moved unchanged), [docs/COMMANDS.md](docs/COMMANDS.md) (long-form usage and the SDK),
  [docs/MIGRATING_FROM_TES.md](docs/MIGRATING_FROM_TES.md) and [docs/RENAME_REPO.md](docs/RENAME_REPO.md) (the
  owner's steps for renaming the repository).

### Changed

- **The README leads with what the tool finds:** a one-line pitch, a recording of `tracegauge quickstart` on the
  bundled sample, a three-line install, real output including the cache-churn detector, a commands table, supported
  agents (Claude Code only; others are planned, none is supported), and a methodology summary. The example blocks
  and the recording are generated by `scripts/gen_readme_demo.py` and pinned by `tests/test_readme_demo.py`.
- **The dashboard has a new identity:** new logo, favicon and webfonts (`TG Sans` / `TG Mono`, see Bundled fonts).
- **The waste detectors are pinned by sha256** in `tests/test_prior_features_intact.py` as well as by `git diff`, so a
  committed change to them fails CI (the `git diff` check alone passed on any committed edit).
- **Bundled fonts:** the OFL texts now ship in the wheel, and the IBM Plex subsets are renamed `TG Sans` / `TG Mono`
  because IBM Plex reserves the name "Plex" (OFL clause 3).
- **Dataset attribution corrected** in `DATA_SOURCES.md`: SWE-ZERO is Apache-2.0 (not MIT), CC-Bench is MIT (not
  CC-BY-4.0), and each third-party trajectory dataset now lists its licence, what is used and whether it is
  redistributed.
- **Scripts that defaulted to the maintainer's own session data** (`scripts/pull_corpora.py` and others) now require
  explicit input paths; `scripts/pull_corpora.py` also refuses to write into a tracked or un-ignored path.

### Removed

- **Third-party and derived material this release no longer redistributes:** the 50 SWE-Gym corpus entries (no
  licence stated on the dataset card; the trace files themselves were never committed) with their annotations; the nebius trajectory text (derived numbers kept);
  free-text judge and rater commentary about the SWE-Gym sessions; and `data/public_waste_signals.jsonl` (see below).
  `DATA_SOURCES.md` discloses what derived material remains. What stays in git history (for example at the `v0.15.0` tag) is the 50 SWE-Gym rows of
  `data/validation-corpus/corpus_manifest.jsonl` and the annotation and heuristic-output files removed here; the trace
  files were gitignored and never committed.
- **PR-evidence screenshots under `reports/screenshots/`** (11 images that shipped in 0.15.0; 6 more added to the
  development branch after 0.15.0, 17 in all, were also removed), which showed local paths or real session ids. No
  README or document references them.

### Known gaps

- **The removed screenshots (11 from 0.15.0, 17 counting those added after it) were not re-captured.** The dashboard changes they documented (cost breakdown, lever
  rule, unpriced display, cache-churn card) have no screenshot in the repository; the behaviour is covered by tests.
- **Figures that came from removed data cannot be re-derived from a clone.** The research reports' pool statistics
  and the pre-0.15.0 bands are the maintainer's unpublished local measurement (see the next two entries).

### Data changes (from the unreleased section)

- **The scoring tests no longer read the maintainer's own session text.** They run on a seeded (42) synthetic pool,
  synthetic judge scores and synthetic baselines under `tests/fixtures/`, produced by
  `scripts/gen_synthetic_fixtures.py`; waste entries are produced by running the real detectors over the fixture
  digests. The maintainer's adapted pool, its waste signals, its session digests and the old `data/cc_baselines.json`
  are removed from the tree. Scripts that defaulted to them (`scripts/efficiency_score.py`, `scripts/build_baselines.py`,
  `scripts/run_waste_analysis.py` and others) now take explicit input paths. Figures that came from those files (the
  research reports' pool statistics, the pre-0.15.0 bands) are the maintainer's unpublished local measurement. The shipped `tracegauge/data/cc_baselines.json`, `eval/manifest.json` and the waste detectors are unchanged.
- **`data/public_waste_signals.jsonl` is no longer distributed.** It held per-session detector results keyed by 1,946 SWE-chat session ids (the maintainer's unpublished local measurement), and SWE-chat-derived data stays local (see `DATA_SOURCES.md`). It is now gitignored; regenerate it with `scripts/generalization_run.py`, which gained `--signals-out`, and read it with `--swechat-signals` (`scripts/diagnose_rfr_rate.py`) or `--signals` (`scripts/fix_noncc_signals.py`).

## [0.15.0] - 2026-10-11 — token and cost totals were counted once per content block (tokens ~2.4x, cost ~2.3x too high)

Measurements and migration detail: [docs/releases/0.15.0.md](docs/releases/0.15.0.md). Figures called "the
maintainer's" are unpublished local measurements on the maintainer's own transcripts.

### BREAKING: read before upgrading

1. **Usage is deduplicated per `message.id`, so token counts drop ~2.4x on affected sessions, and the shipped
   bands are rebuilt.** `real_tokens`, `total_tokens`, `session_cost_usd` and every figure derived from them
   change (the maintainer's 10 sessions: 35,895,367 -> 14,889,709 tokens, x2.41; per-session factor 1.0x to
   4.0x). The bundled `tes/data/cc_baselines.json` is a re-baseline (71 ungated sessions, `adapter_version` 2),
   not a correction. New bands (tokens, p25 / median / p75): ml-eval n=13: 1,335,924 / 1,515,851 / 2,796,600;
   debug-fix n=19: 397,878 / 802,486 / 3,381,684; infra-deploy n=15: 376,422 / 1,577,905 / 3,119,425;
   research-recon n=16: 411,052 / 620,842 / 1,194,136; feature-build n=8 is inactive (below the minimum of 10)
   and reports no band. Anything you published or stored from an earlier version is on the old scale. The bands
   describe one developer's work (small n, sessions not independent): "above_p75" means heavier than that
   developer's typical session, not wasteful and not heavier than other developers.
2. **numpy and scikit-learn moved to the `[patterns]` extra; the core install is flask + httpx.** `tes patterns`
   and `tes ask` now need `pip install "tracegauge[patterns]"`; without it they print that one-line hint and
   exit 1.
3. **New exit codes** (see [docs/EXIT_CODES.md](docs/EXIT_CODES.md)): 1 = bad usage or nothing to do (no
   sessions found, store cannot be opened), 3 = the `tes monitor` alarm fired (it exited 0 before), 4 = a session
   could not be read or scored (`tes score` printed `[ERROR]` and exited 0 before). Scripts that treated every
   exit as success must handle these.
4. **Rows written by older versions are labelled legacy and excluded until `tes rescore`.** They are left out of
   the self-baselines, the alarm pool, `budget`, `cost` totals, `impact`, `patterns`, `ask` and contribution
   exports; nothing is deleted. `tes rescore` re-scores those whose source transcript still exists; where the
   transcript is gone the row cannot be corrected. See [docs/UPGRADING.md](docs/UPGRADING.md).

### Added and fixed

- **Usage counted once per API response, not once per content block.** Claude Code repeats a response's `usage`
  on every content-block record; ccusage dedupes the same way (`ccusage/ccusage@c3a4eb0ecdcfbd3655dbcec8205644b046032332`,
  `rust/adapters/claude/src/lib.rs:345-366`). Waste events and proof turns are unchanged.
  [Details](docs/releases/0.15.0.md#usage-was-counted-once-per-content-block).
- **Bundled baseline rebuilt from 71 sessions** (was 75 quality-gated); `scripts/rebuild_baselines.py build`
  re-derives the bands from `eval/manifest.json`, which holds ids, hashes and per-session counts only: the counts came
  from transcripts that are not in the repository, so the baseline itself is the maintainer's unpublished local measurement.
  [Details](docs/releases/0.15.0.md#usage-was-counted-once-per-content-block).
- **Subagent usage is rolled into its parent session** (`subagent_tokens`, `subagent_cost_usd`, `subagent_count`)
  instead of scoring each subagent file as an empty session; `real_tokens` and the verdict stay main-chain.
  [Details](docs/releases/0.15.0.md#added-and-fixed-in-this-release).
- **Prices for Opus 5.5, Sonnet 5.5, Fable 5.1, Mythos 5.1 and Haiku 5.5**, per-model cache-read multiplier, Haiku
  5.5 long-context tier; CI derives the model list from the vendor page and fails on an unpriced model.
  [docs/PRICING_FIELDS.md](docs/PRICING_FIELDS.md).
- **1-hour cache writes are priced at 2x input (was 1.25x).** On the maintainer's 50 sessions the main chain was
  $1,328.60 before the 1-hour fix and $1,443.58 after. [docs/PRICING_FIELDS.md](docs/PRICING_FIELDS.md).
- **Web search ($10 per 1,000), fast mode and `inference_geo: us` (1.1x) are priced**, each with a per-session note;
  the Batch discount does not apply to Claude Code transcripts; code-execution time and errored searches remain
  unmodelled. Prices from the vendor page (sha256 `67904e28...37ca9`). [docs/PRICING_FIELDS.md](docs/PRICING_FIELDS.md).
- **Unpriced models print `unpriced (<model>)` instead of `$0.00`**, and `priced` / `unpriced_models` are in JSON.
  [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md).
- **COST BREAKDOWN replaces the "context dominates" lever.** The old rule fired on all 76 of the maintainer's
  priced sessions; a LEVER finding now appears only for detected waste (>= $0.50, or >= 10.0% of cost and >= $0.05)
  or output >= 40.0% of cost, compared unrounded. [Details](docs/releases/0.15.0.md#added-and-fixed-in-this-release).
- **The live alarm is the p85 of your own last 30 days of sessions in the same model era**, falls back to a
  documented chain, and says what it used; `tes monitor` exits 3 when it fires. [docs/ALARM.md](docs/ALARM.md).
- **`--json` on `cost`, `budget`, `monitor`, `impact` and `patterns`** (with `score`): `schema_version`, `command`
  and `legacy_rows_excluded` in every document emitted, fixed key sets. [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md).
- **`cost --roi --plan-config PATH` exits 1 when PATH does not exist** (it printed "no plan configured" and exited
  0). [docs/EXIT_CODES.md](docs/EXIT_CODES.md).
- **`tes rescore [--dry-run] [--json] [--limit N]`** recovers legacy rows whose transcript still exists; a first
  notice after the upgrade says how many rows need it; two processes opening an old store at once no longer fail
  with `database is locked`. [docs/UPGRADING.md](docs/UPGRADING.md).
- **Legacy rows are marked on `impact`, `patterns`, `ask` and the dashboard (including `/budget`) and left out of
  their aggregates;** `export-contribution` leaves them out too. [docs/UPGRADING.md](docs/UPGRADING.md).
- **`backfill-waste` no longer overwrites a stored row from a zero-usage or unreadable transcript** and exits 4
  when a row fails. [docs/EXIT_CODES.md](docs/EXIT_CODES.md).
- **The cost note names the cache-read multiplier each model was billed at** (0.05x on Opus/Sonnet 5.5, 0.025x on
  Fable/Mythos 5.1), not a flat 0.10x. [docs/PRICING_FIELDS.md](docs/PRICING_FIELDS.md).
- **Lighter default install:** core is 10.1 MiB of installed package files, excluding pip (flask + httpx); the `[patterns]` extra adds numpy and
  scikit-learn. [docs/INSTALL.md](docs/INSTALL.md).
- **`tracegauge quickstart` shows a verdict and one deterministic finding again** (the rebuilt baseline had pushed
  the bundled sample under its turn gate). [Details](docs/releases/0.15.0.md#added-and-fixed-in-this-release).
- **The JSONL reader no longer drops records containing U+2028, U+2029 or U+0085.**
  [Details](docs/releases/0.15.0.md#added-and-fixed-in-this-release).
- **CI runs the suite on Linux, macOS and Windows x Python 3.10-3.13 plus four core-only legs; the coverage floor
  is 86** (86.70% measured on the GitHub runner, CI run 38091475996). [docs/CI.md](docs/CI.md).
- **README example blocks are generated:** every `tes cost`, `cost --roi`, `budget` and `monitor` example
  is this version's output on synthetic sessions from
  [`scripts/gen_readme_examples.py`](scripts/gen_readme_examples.py), pinned by `tests/test_readme_examples.py`;
  the 0.11.0 blocks and every estimated equivalent were removed. README and PyPI text corrected where this
  release made it false or stale. [Details](docs/releases/0.15.0.md#added-and-fixed-in-this-release).

## [0.14.0] — the package imports on Python 3.10 again, plus packaging metadata

### Fixed
- **The package imports on Python 3.10 again.** 0.12.3, 0.12.4 and 0.13.0 fail on 3.10 with
  `ImportError: cannot import name 'UTC' from 'datetime'` (also the `tes` console script) although
  `requires-python` is `>=3.10`; 0.12.2 and earlier import fine (checked on the published wheels, Python
  3.10.20). Cause: ruff's `target-version` was `py311`, so the 2026-08-21 lint pass rewrote
  `timezone.utc` to `datetime.UTC` (3.11+); CI only ran 3.11. Seven modules now use
  `UTC = timezone.utc`, ruff targets `py310`, and a new `python-compat` CI job runs the suite on 3.10,
  3.12, 3.13 and 3.14. On 3.10 install 0.14.0 or later, or pin `tracegauge<=0.12.2`.
- **`tes.community_baseline` could not be imported on Python 3.10 in any release from 0.10.0 on** (same
  `datetime.UTC` import). Nothing else in the package imports that module, so `import tes` and the CLI were
  unaffected before 0.12.3 (checked on the published wheels, Python 3.10.20).
- Added the `Programming Language :: Python :: 3.14` classifier (the 3.14 leg is green).

### Changed (packaging only)
- **The package is now marked typed** (`tes/py.typed`, classifier `Typing :: Typed`). Verified with mypy on
  a consumer snippet: against 0.13.0 mypy reports `module is installed, but missing library stubs or py.typed
  marker`; with the marker it type-checks the consumer against `tes`'s annotations and flags a deliberate
  mistake. The package itself is not mypy-strict clean (125 errors in `tes` with its dependencies installed, 75
  of them bare-generic `type-arg`); the marker promises inline annotations, not strict correctness.
- **The sdist** no longer ships `tests/` (several tests read repo-level `scripts/`, `corpus/` and `data/`
  files an sdist does not contain, so they could only fail there) and now includes `CHANGELOG.md`,
  `PRIVACY.md`, `DATA_SOURCES.md` and `SECURITY.md` (146 -> 83 files).
- Added the `Topic :: Software Development :: Testing` and `Topic :: Scientific/Engineering :: Artificial
  Intelligence` classifiers; tagged the 14 untagged README code fences as `text`.

## [0.13.0] — dead-code removal, a real public API change

### Removed
- **`tes.corpus_client.CorpusNotConfigured`** and **`tes.store.backfill_turn_counts`**, both
  exported in their module's `__all__` but never actually raised or called anywhere in the
  codebase (confirmed exhaustively — source, tests, scripts, docs — before removal; both had been
  reported as dead-code issues). `CorpusNotConfigured` was documented as "raised when no CorpusConfig is available,"
  but `contribute()`/`withdraw()` always used a Result-object pattern
  (`ContributeResult`/`WithdrawResult` with a `.reason` string) for this and every other failure
  mode instead — the exception was vestigial, not a real code path. `backfill_turn_counts` was
  self-documented as having zero callers since the write-path audit that hardened it.
  **This is a real public API removal, not a behavior change** — anyone who imported either name
  directly gets an `ImportError` starting with this release; anyone who didn't (everyone, as far
  as this repo's own history can tell) sees no difference. Minor version bump (`0.13.0`, not a
  patch) per semver: this repo's earlier `0.10.2` precedent changed the *behavior* of an existing,
  still-present function (still a patch under semver's own guidance for bug fixes); this instead
  deletes exported names outright, the more conservative call for an actual name-removal.

## [0.12.4] — SDK usage example crashed as published

### Fixed
- **The README's "SDK usage" code block, which becomes PyPI's own project description
  verbatim, crashed on its first real line.** `adapt_session("path/to/session.jsonl")` passed a
  bare string; `adapt_session`'s real signature takes a `Path` and calls `.stem` on it
  internally, so the exact block published on `0.12.3`'s PyPI page raised
  `AttributeError: 'str' object has no attribute 'stem'` if run verbatim. Fixed to
  `adapt_session(Path("path/to/session.jsonl"))` with the missing `from pathlib import Path`
  added. Verified by actually running the corrected block end to end against a real session
  file, not just reading it for syntax. Every prior published version's PyPI page (`0.12.0`
  through `0.12.3`) carries the broken block permanently — PyPI does not allow re-uploading a
  version's metadata.

## [0.12.3] — ODC-BY attribution for the SWE-chat dataset; repo-wide ruff cleanup

### Fixed
- **License compliance gap on the published PyPI page.** The `172 independent developers,
  1,053 SWE-chat CC sessions` generalization-validation figures in the README (which becomes
  PyPI's own project description verbatim, per `pyproject.toml`'s `readme = "README.md"`) are
  a "Produced Work" derived from SALT-NLP/SWE-chat, an ODC-BY 1.0-licensed dataset — publishing
  derived statistics from ODC-BY data requires an attribution notice under the license's §4.3,
  which this project never carried. Added the notice (with citation, Baumann et al. 2026,
  arXiv:2604.20779) everywhere the figures appear, plus a new `DATA_SOURCES.md` recording every
  external dataset/model this project uses, its license, and what was derived from it. This
  release is the first one whose published PyPI page actually carries the notice — every prior
  published version's page remains permanently non-compliant (PyPI does not allow re-uploading
  a version's metadata; see RELEASING.md, "do not yank").
  (Erratum, 2026-10: the figure quoted above, "172 independent developers, 1,053 sessions", was
  wrong. sessions.parquet lists 4,852 Claude Code sessions from 172 attributed developers (1,660 of those sessions have no user_id); only 1,053 have a raw transcript, from 58 attributed developers plus 281 sessions with no user_id. The entry is left as published.)
- Two internal research-corpus documentation errors, unrelated to any public claim but worth
  a clean record: `research/09-cross-model.md` twice mis-described the project's own real
  Spearman ρ≈0.79 trajectory-judge figure as correlating against a "5-session human-rated set"
  that never existed — corrected to the real methodology (Qwen3-30B-A3B vs. Claude Sonnet
  acting as an LLM reference rater, N=53/67, not human labels). `data/README.md` described a
  `raw/`/`processed/`/`benchmarks/` layout that was never actually populated.

### Changed
- **Repo-wide ruff lint debt resolved.** 1,030 pre-existing findings (not user-facing — no
  behavior change, full test suite verified identical before and after: 755 passed, 20 skipped)
  triaged and closed: the dominant class (E402, a structural false-positive against this
  repo's own `from __future__ import annotations`-before-docstring convention) permanently
  exempted via config; genuinely fixable findings (unused/unsorted imports, f-string/datetime
  modernization) auto-fixed; the small remainder needing real per-call judgment scoped via
  per-file ignores rather than silently permitted everywhere going forward. CI's ruff
  check/format steps tightened from informational to blocking, matching the pre-commit hook.
- New pre-commit hook (`scripts/check_staged_scope.py`) blocks a commit staging more than 20
  files unless explicitly overridden — a guard against an accidentally-broad `git add`.

## [0.12.2] — Calibration brand identity wired into `tes serve`; corpus fail-fast fix

### Added
- **The Calibration brand identity (`assets/brand/BRAND.md`) is now applied to `tes serve`**,
  not just held as unused assets: favicon, self-hosted webfonts (Space Grotesk, IBM Plex Sans,
  IBM Plex Mono — no CDN dependency), the 7-token palette across every dashboard template, the
  real wordmark in the nav, and a tick-mark motif on the session-detail attribution chart's
  X-axis. Verified by rendering the live dashboard with headless Chromium and checking the
  favicon at true 16px/32px sizes, not just reading the CSS.
- README brand badge.

### Fixed
- **Packaging gap the brand-wiring work itself surfaced**: `web/static/brand/*.svg` and
  `web/static/fonts/*.woff2` were never declared in `[tool.setuptools.package-data]` — a real
  `pip install` (as opposed to the editable checkout used to build the feature) would have
  shipped a dashboard silently missing its favicon and falling back to system fonts. Verified
  by building an actual wheel and diffing its contents before/after.
- **`tes corpus contribute`/`withdraw` walked users through a full preview and a real
  consent prompt, then revealed `[NOT SENT]`/`[NOT WITHDRAWN]` only on the last line** — always,
  since no public corpus is provisioned pre-launch. Both commands now check corpus availability
  *before* prompting for anything, print `[NOT AVAILABLE]` immediately, and `--help` states NOT
  YET ACTIVE directly rather than only in the README. No change to the underlying
  `contribute()`/`withdraw()` library functions or their consent/config safety gates — CLI
  ordering only.
- `uv.lock`'s self-referential version, twice-drifted from PR merge ordering across `0.12.0`→
  `0.12.1`, resynced.

## [0.12.1] — Two source_mtime/db-scoping fixes (issues #12, #17)

### Fixed
- **`tes budget`'s rolling-window projection filtered/ordered on `scored_at`
  (when `tes score`/`tes scan` happened to run) instead of `source_mtime`
  (when the real usage happened)** — correct under an immediate-scoring
  workflow, wrong under a batch-scoring workflow: every session scored in
  one `tes scan` run shares the same `scored_at`, which could cluster a
  week of real spend onto one instant or drop it outside the window
  entirely. Same fix `tes cost`'s `compute_period_cost` already applied
  (`0.11.0`) for the identical reason. New regression test: sessions with
  real usage spread across days but scored in one batch prove the
  projection now reflects when the usage happened, not when it was
  scored.
- **`export-contribution`'s default `--output` path was a fixed
  `~/.tes/contribution-{date}.jsonl` regardless of `--db-path`/
  `TES_DB_PATH`** — same bug class as the `intelligence_cache.json` fix
  (`0.11.1`), lower severity (an explicit, user-inspected export with an
  existing `--output` escape hatch), but still real: running against an
  isolated/scratch DB dropped the default output file into the real
  `~/.tes/` regardless of which database was actually in use. Default now
  derives from the resolved `db_path`'s own directory, matching
  `intelligence/cache.py`'s established pattern. `--output` still
  overrides the default exactly as before.

Both fixes are read/write-path corrections only — no schema change, no
new columns, no migration needed.

## [0.12.0] — Plan-cost ROI, unpriced coverage reporting

### Added
- **`tes cost --roi`**: "is my subscription worth it at API-equivalent prices?"
  Compares the period's API-equivalent spend against a configured plan cost
  (`~/.tes/plan.json`, or `TES_PLAN_PATH`/`--plan-config`). The config is a
  plan **history** (`{"plans": [{"name", "monthly_cost_usd",
  "effective_from"}, ...]}`), not a single static cost — a plan change
  landing inside the reporting window is priced day-by-day at whichever plan
  was actually active, prorated by exact elapsed time (not calendar-date
  counting, which would silently inflate a rolling window that doesn't start
  and end at midnight — every real invocation). Refuses to print a ratio the
  data can't support: no plan configured, zero priced sessions in the
  window, or a window entirely predating the plan history all print an
  honest explanation instead of a misleading number.
- **Unpriced coverage reporting** on `tes cost` (always shown when coverage
  is below 100%, not gated behind a flag): the fraction of the period's
  *sessions* and *tokens* that are actually priced, and the specific
  unresolved model string(s) causing the gap. A new nullable
  `cost_unpriced_models` column persists the raw model name(s) at score
  time (same lesson `0.11.1`'s attribution-persistence fix established:
  information only available while the source transcript is readable must
  be saved then, not re-derived later) — sessions scored before this
  column existed still count toward the coverage gap honestly, flagged as
  unattributable rather than silently omitted from the unpriced-model list.
- **Legacy-row count at release time, measured against the real store, not
  estimated**: both new nullable columns (`cost_unpriced_models`,
  `edit_operations`) are additive — every pre-existing row predates them.
  Queried read-only against the real `~/.tes/tes.db` immediately before
  tagging this release: **1,450 of 1,450 sessions (100%)** have
  `edit_operations IS NULL` and `cost_unpriced_models IS NULL` — every
  session scored before this release. `tes cost --roi`'s coverage
  reporting and `tes impact`'s `prior_content_unknown`/`untested_tool_shape`
  fractions both already handle this honestly (a legacy row is counted and
  flagged, never silently dropped or backfilled with a guess) — this is
  the number that count applies to on a freshly-upgraded real installation,
  not a hypothetical.
- **`tes impact`**: corpus-wide code-impact reconstruction from `Edit`/
  `Write`/`MultiEdit`/`NotebookEdit` tool-call payloads, persisted at score
  time (new nullable `edit_operations` column — same score-time-persistence
  lesson as the two entries above). Additions/deletions per file, and a
  churn ranking (most-edited files/directories) as **plain transparent
  counts — deliberately no composite "risk score"**: a hand-weighted blend
  of signals presented as one number would be exactly the kind of
  invented-precision this project avoids elsewhere. Extraction scope
  checked against this project's own real corpus (883 files, 434,774
  lines): `Edit`/`Write` are fully supported (10,175 / 3,059 real
  occurrences); `MultiEdit`/`NotebookEdit` extraction is written but has
  **zero real-corpus verification** and is flagged inline (as a fraction of
  the total) wherever it contributes, never presented with the same
  confidence as Edit/Write. The `Write` tool's payload never carries a
  file's prior content — additions are exact, deletions are always 0 for
  that operation, and the report states what fraction of the total
  additions figure rests on this specific assumption rather than treating
  a full-file rewrite as if it were an exact diff.
- **No cost-per-edit / cost-per-100-lines ratio ships with this** —
  measured first, not assumed: a bootstrap-CI implementation was built and
  its coverage validated against a known-answer simulation
  (`docs/audit/EDIT_RATIO_BOOTSTRAP_COVERAGE.md`) before being adopted for
  anything. Both percentile and BCa intervals came in below nominal
  coverage at every tested sample size (10–250 sessions) — the harness
  itself was independently audited (a known-answer sanity check, resampling-
  unit and jackknife-unit verification) before that result was trusted, and
  the true mechanism (skewness in the underlying cost distribution, not
  anything specific to the ratio structure) was confirmed by direct
  experiment, not assumed. No interval ships until a method with verified
  coverage exists — a number with a measured-wrong confidence interval is
  worse than no interval at all.

### Fixed (found during this release's own verification)
- The plan-cost proration was originally implemented as day-by-day
  calendar-date iteration, which inflated a `--week` (7.0 elapsed days)
  window to 8 calendar dates' worth of cost whenever the window's start/end
  timestamps didn't both land on midnight — true for every real invocation,
  since `--week`/`--month` are rolling windows ending at `datetime.now()`.
  Replaced with exact interval-overlap arithmetic (each plan period treated
  as a segment, priced by its precise overlap with the window in elapsed
  days) before this ever shipped; caught by manual verification against a
  real 7-day window, not by the original unit tests (which happened to use
  midnight-aligned test fixtures and couldn't have caught it) — a dedicated
  regression test using a non-midnight-aligned window was added alongside
  the fix.

## [0.11.1] — Score-time attribution persistence, cache scoping fix

### Fixed
- **`tes patterns` clustered 0 sessions against a real corpus even when 30+ content
  sessions existed in the database.** Root cause: feature extraction re-read each
  session's original source JSONL transcript at cluster time to compute attribution
  fractions — if that file had since moved, been deleted, or been cleaned up (the
  normal lifecycle for a session transcript once it's been scored), the session
  silently couldn't be clustered, with no visible reason why. Fixed by persisting the
  4 attribution fractions (`context_resend_pct`, `context_growth_pct`, `output_pct`,
  `waste_pct`) directly to the database at score time (`tes score`, `tes serve`, and
  the background watcher all covered) via a new `attribution_fractions()` helper in
  `tes/attribution.py` — the single source of truth used by both the score-time
  persist path and the legacy on-demand fallback, so the two paths can't drift.
  `tes.intelligence.features.extract_features()` now prefers the persisted columns
  and only falls back to re-reading source JSONL for rows scored before this fix
  existed. **Schema migration:** 4 new nullable `REAL` columns, added via the
  existing additive-only `ALTER TABLE` guard — no action needed on upgrade.
- **The ML intelligence cache (`intelligence_cache.json`) was a single fixed global
  path**, shared across every database in use. Running `tes patterns`/`tes ask`
  against an isolated or scratch database (e.g. via `TES_DB_PATH`) silently
  overwrote the real cache the next real invocation would read — found live while
  verifying the fix above. `_cache_path()` now resolves the same way
  `tes.store.resolve_db_path()` does (explicit arg → `TES_DB_PATH` → `~/.tes/tes.db`
  default) and is co-located with, and named after, its own database
  (`<db-name>.intelligence_cache.json`), so a given database always maps to the same
  cache file and a different one never collides with it.
- The "not enough sessions" message now distinguishes a genuinely thin corpus from
  one blocked by unreachable source files on rows scored before this fix — naming
  the real cause (count of affected sessions, why, and that nothing further is
  needed going forward) instead of a generic "your corpus will grow" message that
  didn't apply.
- `tes cost`'s missing-cost-data message no longer hardcodes "have" for every count
  (`"1 session have no cost data"` → `"1 session has no cost data"`).

### If you're upgrading from `0.11.0` or earlier
Every content session scored before this fix has the source-file problem above, not
just some of them — clustering doesn't pick up where it left off. On the real corpus
this was verified against, all 321 of 321 previously-scored content sessions (100%)
had unreachable source files. **Nothing is lost or corrupted** — no row is deleted,
no score is wrong — clustering (`tes patterns`, `tes ask`) simply restarts from zero
and rebuilds as you keep scoring sessions going forward; the tool says so plainly
when it happens rather than showing an empty or misleading result. There is no
database-only backfill path: the closest candidate, `waste_events.wasted_cost_usd`,
covers only 1 of the 4 attribution fractions clustering needs, and as dollar cost
rather than raw tokens — recovering a legacy row genuinely requires its original
transcript file, so this release does not attempt one.

### Test suite: 0 known-failing tests, corrected boundary enforcement
The legacy-row finding above initially left 8 pre-existing tests failing against
this machine's real, thin-again corpus (5 in `tests/test_cluster_validity.py`,
3 in `tests/test_chat_grounding.py`) — both files read `~/.tes/tes.db` directly.
Rather than leave a red suite as the accepted state, each was fixed at the root:
- `test_chat_grounding.py`'s `TestContextFormatUnambiguous` class tests
  `format_intelligence_summary()`'s formatting contract — a pure `dict -> str`
  function that never needed a live database at all. It now runs against a
  synthetic, always-valid cache dict, deterministically, on every machine
  (including CI, which previously excluded this class entirely — see below).
- `test_cluster_validity.py`'s real-corpus clustering-quality checks now
  `pytest.skip()` with a stated, diagnostic reason (distinguishing "too few
  sessions to evaluate" from "evaluated and it's a real quality regression")
  when the local corpus can't support them — the same pattern
  `tests/test_anomaly_threshold.py` already used correctly. Several other
  tests in the same class had been passing *vacuously* on an empty corpus
  (looping over zero archetypes proves nothing); those now honestly skip
  instead of silently reporting a pass that verified nothing.
- `.github/workflows/ci.yml`'s `--ignore=tests/test_cluster_validity.py` and
  `--deselect=...TestContextFormatUnambiguous` are removed — both files now
  run unrestricted in CI, since the fix above makes them either pass
  deterministically or skip with a reason, on any machine, real corpus or not.

Full suite: `700 passed, 20 skipped, 1 deselected` (the 1 deselection is a
pre-existing, separately-investigated Ubuntu-runner-specific flake in
`test_watcher_incremental.py`, unrelated to this release — see `ci.yml`'s own
comment for the investigation).

### Write-boundary enforcement: `~/.tes/` scoping is now structural, not conventional
RR2 (above) fixed one instance of a class of bug: a function that computes AND
WRITES a derived artifact (the ML intelligence cache), with a `db_path` parameter
that silently defaulted to the real `~/.tes/tes.db` location whenever a caller
omitted it. RR2's own verification, and separately this release's own verification,
both re-triggered the same class of mistake via an ad hoc interactive call that
forgot to pass a path. `db_path` is no longer optional on `tes.intelligence.cache`'s
`get_or_compute_intelligence()`, `save_cache()`, `load_cache()`, or `_cache_path()`,
nor on `tes.store.backfill_turn_counts()` (found unreferenced during this audit,
hardened for consistency) — omitting it is now a `TypeError` at the call site, not a
silent write to the real cache file. Resolution (explicit arg → `TES_DB_PATH` →
default) still happens, but now exactly once, at each top-level entry point
(`tes.cli`'s command handlers, `tes.intelligence.chat.build_chat_context`,
`tes.web.server`'s routes) — everything below that boundary is handed an
already-resolved, concrete path and can no longer guess one. Covered by new tests
in `tests/test_intelligence_cache_scoping.py` proving the omission itself fails
loudly, alongside the existing test proving a scoped write never touches the real
default location.

## [0.11.0] — Period cost report (`tes cost`)

### Added
- **`tes cost --week` / `--month` / `--since <date>`**: total spend, session count, and a
  per-project breakdown for a period. Distinct from `tes budget`'s rolling self-trend
  projection (`tes budget` answers "where is my pace heading"; `tes cost` answers "what did
  I actually spend"). `--week`/`--month` are rolling N-day windows ending now (7/30 days,
  matching `tes budget`'s own rolling-window convention) — not calendar-aligned, since a
  calendar week/month needs a user timezone and first-day-of-week convention this tool has
  no basis to guess. `--since YYYY-MM-DD` is an explicit lower bound through now.
- **Filters on `source_mtime`, not `scored_at`** (deliberate design decision, not the default
  `tes budget` uses): the session file's own real last-write time, i.e. when the usage
  actually happened, not when `tes score`/`tes scan` happened to run. Under a batch-scoring
  workflow (scoring a week's sessions in one sitting) these diverge — a `scored_at`-based
  filter would cluster a week of real spend onto one scoring-run instant, or drop it outside
  the requested window, silently misattributing spend across period boundaries.
  `tes/budget.py`'s existing projection has this same divergence (found, not fixed here —
  tracked as a GitHub issue, fixed in 0.12.1).
- Sessions with no cost data yet are counted separately (`sessions_missing_cost`) and
  reported, never silently treated as `$0` or dropped from the total without a trace.
- **Known gap, not built:** no per-model breakdown. Would need a new schema column and
  adapter change, and nobody in the originating GitHub issue
  ([#78148](https://github.com/anthropics/claude-code/issues/78148)) asked for it — left
  explicit rather than silently omitted.

### Fixed (documentation)
- Corrected two stale "not yet published" claims for `0.10.1`/`0.10.2` (see their entries
  below) — both were, in fact, already live on PyPI by the time this release shipped.

## [0.10.2] — Pricing-defect bug-fix release — LIVE on PyPI

**Published:** https://pypi.org/project/tracegauge/0.10.2/ — uploaded 2026-08-15
(confirmed via PyPI's JSON API). **Git tag:** `v0.10.2` at commit `710928c`. This entry
originally said "BUILT, NOT YET PUBLISHED" — stale by the time of the 0.11.0 release;
corrected here.

### BREAKING: unresolved models no longer return a guessed price

**Unknown/unresolvable models previously returned a guessed rate — silently defaulting to
`claude-sonnet-4-6`'s pricing. They now return an explicit unpriced result instead.** If your
code calls `tes.cost.compute_turn_cost`/`compute_session_cost` and reads `total_usd` off the
result, it can no longer assume that number is always a real, usable dollar figure — for a
model `tes` doesn't recognize, `total_usd` is now `0.0` by construction, not a wrong-but-
plausible guess. Read "Migration" immediately below before relying on this release.

**Migration — if you call `tes.cost.compute_turn_cost`/`compute_session_cost` directly:**

- `TurnCost` gained a `priced: bool` field (default `True`). Check `turn_cost.priced` before
  trusting `turn_cost.total_usd`. `priced=False` means `total_usd == 0.0` by construction
  (never a guessed/default-rate figure), and `turn_cost.approximate_reason` names the
  unresolved model plus the exact remedy (set `TES_PRICE_TABLE`, add an entry to
  `~/.tes/prices.json`, or file an issue).
- `SessionCost.approximate` (existing field, now correctly populated) is `True` whenever any
  turn in the session was unresolved; `SessionCost.approximate_reasons` (existing field) lists
  the distinct reasons; `SessionCost.approximate_turn_count` gives the count of affected turns.
  `SessionCost.total_usd` already excludes unpriced turns (they contribute `$0.00`, not a wrong
  dollar amount), so a session with unresolved-model turns reports a lower, honest total rather
  than a wrong-but-confident one.
- What to do with a `priced=False`/`approximate=True` result depends on the caller — there is
  no single correct answer, which is exactly why `tes` no longer picks one silently on your
  behalf. Sensible patterns: skip the unpriced turn/session from any aggregate spend total
  instead of silently counting it as `$0` of real cost; surface `approximate_reason`/
  `approximate_reasons` to the end user so they know the total is a floor, not the true cost;
  or, if a caller needs a number regardless, apply its own explicit fallback rate rather than
  relying on `tes` to have guessed one for it.

**This is a bug-fix release correcting real mispricing in a published, actively-installed
package — not a routine update.** `tracegauge==0.10.1` is live on PyPI today (169
downloads/week per pypistats, an upper bound; 10 releases since 2026-06-07 — actively
maintained and installed, not dormant) and, until this release, silently mispriced every
real `claude-opus-5`/`claude-sonnet-5` Claude Code session — the current Claude flagship
models, and very likely the *mainline* real-world case, since this package's whole purpose
is scoring Claude Code sessions. Found and reported (not fixed) in a prior read-only audit
(see `adk-tracegauge`'s Phase 5 S1 report, a sibling package by the same author); this
release is that audit's recommended `0.10.2` patch, implemented.

**1. Fixed: `claude-opus-5`/`claude-sonnet-5` were missing from the price table and
silently fell through to a stale default-model rate (root cause).** `tes/cost.py`'s
`_resolve_model` had no entry for either model, so both fell through to
`prices["default_model"]` (`claude-sonnet-4-6`, $3/$15 per Mtok) with only a buried
`is_approximate` flag — no direction, no magnitude, and (in `tes/cli.py`, the primary CLI
surface) no visible indication at all beyond raw JSON fields. Concretely, for a realistic
single call (10,000 input tokens, 2,000 output tokens, no cache):
  - **claude-sonnet-5**: pre-fix charged **$0.06** (wrong default rate) vs. the real
    **$0.04** — a **50% overcharge** (+$0.02).
  - **claude-opus-5**: pre-fix charged **$0.06** (wrong default rate) vs. the real
    **$0.10** — a **40% undercharge** (−$0.04, i.e. only 60% of true cost reported).

  Added both models to `tes/data/prices.json` with real rates fetched live from
  `platform.claude.com/docs/en/about-claude/pricing` on 2026-08-15 (`claude-opus-5`:
  $5.00/$25.00 per Mtok; `claude-sonnet-5`: $2.00/$10.00 per Mtok — confirmed the
  previously-introductory $2/$10 rate is now the permanent standard rate, not a promo
  scheduled to expire 2026-08-31). Also added `claude-fable-5` and `claude-mythos-5`
  ($10/$50 per Mtok each), the two other current Claude models missing from the table.

**2. Fixed: unresolved models are never priced at a guessed/default rate again (the actual
S1 root-cause fix, not just a data patch).** `_resolve_model` now returns `None` (never a
default key) for a model string that doesn't match any table entry or pattern —
`compute_turn_cost` returns an explicit unpriced result (`priced=False`, `total_usd=0.0`,
`approximate_reason` naming the model and the exact remedy: set `TES_PRICE_TABLE`, add an
entry to `~/.tes/prices.json`, or file an issue) instead of silently substituting
`default_model`'s rate. This is the general fix behind items above and behind any future
new model that ships without a price-table entry — mirrors `adk-tracegauge`'s own B1 fix
(fail closed, never guess). **Behavior change:** `SessionCost.total_usd` now excludes
unresolved-model turns entirely (contributes $0.00, not a wrong dollar figure) — a session
with unresolved-model turns reports a lower, honest total rather than a wrong-but-confident
one; `SessionCost.approximate`/`approximate_reasons` (unchanged fields, now correctly
populated) is the loud, always-checked signal that the total is a floor, not the true cost.
Any external caller of `tes.cost.compute_turn_cost`/`compute_session_cost` relying on
always getting a nonzero dollar figure for an unresolved model will now see `$0.00` +
`is_approximate=True`/`priced=False` instead — this is the correction, not a regression;
see `tests/test_cost_unpriced.py` for the structural guard that no code path can return a
confidently-priced result for a model absent from the table. `TurnCost` gains a new
`priced: bool` field (defaults `True`) alongside the existing `is_approximate`/
`approximate_reason` fields.

**3. Fixed: server-side tool billing (e.g. web search) is now flagged instead of silently
dropped.** `tes/adapt.py`'s usage parser previously read only 4 token-count fields from a
turn's raw `usage` dict and never looked at `usage.server_tool_use` (e.g.
`{"web_search_requests": N}`, billed at $10/1,000 searches on top of token costs) — a
session with 20 web-search calls silently dropped $0.20 of real cost with zero warning of
any kind, worse than the unresolved-model case (which at least set a flag). `TurnDigest`
gains a `server_tool_use: dict[str, int] | None` field (populated by
`tes.adapt._parse_server_tool_use`); `TurnCost`/`SessionCost` gain `server_tool_warning`/
`server_tool_warnings` fields that name the detected-but-unpriced usage; `tes/report.py`
prints a `[NOT PRICED: ...]` line whenever any turn triggers this. **This does not attempt
to price server-tool usage** — that would require confirming which token fields (if any)
already reflect search-generated content, a larger change than this minimal fix — the
honest minimum is a loud warning rather than a silent gap. `ThreeAxisResult` gains a
`cost_server_tool_warnings: list[str]` field (default `[]`).

**4. Added: a 90-day staleness guard + weekly CI job, ported from `adk-tracegauge`'s own
`price-freshness.yml`.** Before this, `tes/data/prices.json` had zero staleness signal of
any kind and had gone 67 days stale with nobody noticing before the S1 audit caught it —
this is the gap that let the missing flagship-model entries above go undetected for as
long as they did. Every price-table entry now carries its own `as_of`/`source_url`
(falling back to the table-level `as_of` when absent); `tes.cost.check_price_table_staleness`
flags any non-retired entry older than `STALE_THRESHOLD_DAYS` (90); retired legacy models
(`claude-3-opus`, `claude-3-5-sonnet`, etc. — kept only to price historical sessions) are
marked `"retired": true` and exempt, since Anthropic doesn't change pricing for a retired
model. `.github/workflows/price-freshness.yml` (new) runs
`scripts/check_price_freshness.py` every Monday — pure date arithmetic, no network calls,
no paid API calls. Deliberately minimum-viable: does **not** port promo-expiry handling,
tiering, multi-provider support, or the regression gate — that is Phase 7's larger,
full-engine-move scope (see `adk-tracegauge`'s Phase 5 S2 recommendation), out of bounds
for this bug-fix release.

**Downstream-dependent check (done, per the audit mandate):** `tracegauge`'s own test suite
(`tests/test_cost_approximate.py`) previously asserted `compute_turn_cost`/
`compute_session_cost` return a numeric result for an unknown model without checking
whether that number was *correct* — those tests are updated in this release to assert the
new fail-closed behavior explicitly (`priced=False`, `total_usd == 0.0`), which is itself
the signal that a real external caller relying on the old default-rate behavior would break
in the same way. No public "used by" listing exists on PyPI or GitHub for `tracegauge`
beyond `adk-tracegauge` (which already removed its dependency, per its own Phase 4 R5, and
is unaffected by this release either way); a GitHub code search for `from tes` / `import
tes` outside these two repos found no other real consumer as of this release. This does not
rule out unlisted private/internal users — the pypistats download count (169/week) is
consistent with real usage the two known repos don't account for — which is exactly why
this ships as a `0.10.2` patch with the fix fully backward-compatible in the success case
(every model that resolved correctly before still resolves identically) rather than a
breaking major-version change.

**Tests:** `tests/test_cost_unpriced.py` (new) — structural guard mirroring
`adk-tracegauge`'s own B1 test pattern (direct assertion + a parametrized property sweep
over a dozen synthetic unresolvable model strings, asserting `priced=False`/`total_usd ==
0.0` for every one, and the inverse for every active bundled model), plus the exact
live-verified new-model rates and the realistic-call dollar-magnitude regression above.
`tests/test_price_freshness.py` (new) — `check_price_table_staleness` boundary/retired/
inheritance/fail-closed behavior, plus a regression test that the actual bundled table is
fresh as of its own `as_of`. `tests/test_server_tool_warning.py` (new) — detection,
propagation through `TurnCost`/`SessionCost`/`ThreeAxisResult`, and the `[NOT PRICED: ...]`
report line. `tests/test_cost_approximate.py`, `tests/test_price_override.py` updated for
the new fail-closed behavior and the bumped `as_of`.

## [0.10.1] — Apache-2.0 dual-license the cost module — LIVE on PyPI

**Published:** https://pypi.org/project/tracegauge/0.10.1/ — uploaded 2026-08-13
(confirmed via PyPI's JSON API). **Git tag:** `v0.10.1` at commit `0704388`. This entry
originally said "_pending_" — stale by the time of the 0.11.0 release; corrected here.

**What changed, and why:** `tes/cost.py` and `tes/_digest.py` are now additionally available
under Apache-2.0 (see `LICENSE-APACHE`, a file that 0.16.0 replaced when the whole project moved to Apache-2.0), on top of this project's
AGPL-3.0-only license — everything else in the repo stays AGPL-3.0-only. Standard SPDX
dual-license header (`AGPL-3.0-only OR Apache-2.0`) on both files, no bespoke terms. This
exists so a downstream package —
[adk-tracegauge](https://github.com/gaurav-gandhi-2411/adk-tracegauge), a per-invocation
cost evaluator for Google's Agent Development Kit built on this module — can depend on the
cost-computation code as a library without inheriting AGPL's copyleft. Scope of the grant
confirmed via a dependency-graph check: `cost.py` imports only `tes._digest` (plus stdlib);
`_digest.py` imports nothing from this package at all — nothing else is pulled into the
permissive grant.

**Packaging fix bundled with the same change:** `pyproject.toml`'s `license-files` only
listed `LICENSE` (the AGPL text) — the built wheel/sdist would have shipped without
`LICENSE-APACHE` in its own `dist-info/licenses/`, leaving the dual-license disclosure only
in the two source files' header comments, not in the distribution's license metadata. Fixed
by adding `LICENSE-APACHE` to `license-files`. Verified twice via a fresh `uv build`:
`tracegauge-0.10.0.dist-info/licenses/` (pre-bump) contains both `LICENSE` and
`LICENSE-APACHE`.

**No functional/behavioral changes.** `tes/cost.py`'s and `tes/_digest.py`'s actual code is
byte-for-byte unchanged except for the added header comments — every existing cost
computation produces identical output. Patch version bump reflects that.

**Publish checklist (see commands below):** build → publish → post-publish verify (fresh
env, real PyPI index, confirm `tes --version` reports `0.10.1` and
`tracegauge-0.10.1.dist-info/licenses/LICENSE-APACHE` is present in the installed package) →
tag `v0.10.1` only after verification passes, per this project's established discipline of
tagging after publish confirms, not before.

## [0.10.0] — Live Monitor & Cost Alarm — LIVE on PyPI (habit coach built, HELD)

**Published:** https://pypi.org/project/tracegauge/0.10.0/ — `pip install tracegauge` →
0.10.0. **Git tag:** `v0.10.0` at commit `704f009` — tagged AFTER PyPI post-publish
verification confirmed (tag discipline correct).

**Post-publish verification (real PyPI, 2026-07-04, `tes-postpublish-0100`,
`--no-default-packages`, a fresh env distinct from the pre-publish `tes-verify-0100`):**
- numpy/tracegauge confirmed absent before install; `pip install --no-cache-dir
  tracegauge==0.10.0` resolved cleanly from the real index (not a local wheel).
- `tes.__file__` from site-packages (not repo), confirmed from a neutral cwd.
  `tes --version` → `tes 0.10.0` ✓
- `tes monitor` fired on the actual currently-active session from the installed wheel
  (~$43.93 estimated, ~2.21M context tokens, 98% re-send, above its own p75). The same real
  below-p75 session used in the pre-publish proof, run through the published wheel's
  `check_alarm`, stayed SILENT (247,339 vs. p75 447,157 tokens, 96% resend) — no-cry-wolf
  confirmed SHIPPED, not just built.
- `tes budget --window-days 60` → labeled self-trend projection from the installed wheel,
  N/window stated, non-forecast caveat present.
- `tes serve --alarm --plan max` printed `Alarm: ON — plan=max` on startup; `/budget` and
  `/monitor` both returned 200 from a live running instance of the published wheel.
- `tes coach` → `invalid choice` (exit 2); `/coach` → 404 from the live server — the held
  feature is genuinely absent from what a user gets, confirmed on the actual shipped
  surface, not just the repo.
- Regression: `tes patterns` → footer stamped `tracegauge 0.10.0`, 3 archetypes; `tes ask`
  answered from local Ollama unchanged; `tes score` (no path) frictionless auto-select still
  works with honest scope-floor framing; `/`, `/session/<id>`, `/trends`, `/baseline-status`
  all 200.
- Developer's base conda env reconfirmed at the real `tracegauge==0.8.0` before, during, and
  after this entire verification pass — the `source activate` incident from pre-publish
  verification (see below) was fully remediated and did not recur.

## [0.10.0 pre-publish build record]

**This release ships live monitor + cost alarm + budget/pace. The habit coach is BUILT
but HELD — not shipped this release.** Say what it does, not more: `tes coach` and the
`/coach` dashboard route do not exist in this release. `tes/coach.py` and
`web/templates/coach.html` are in the repo, tested, but deliberately unwired, pending a
fix (see below). Do not describe this release as shipping a coach.

**Design reviewed before code** (`research/13_coach_alarm_honesty_design.md`): coaching and
alerting are where tools most often over-claim, so the grounding/data-gating/flat-plan
design was written up and approved before `coach.py`/`alarm.py` existed. Two findings
changed scope from the original spec wording, documented in that file:

- No confirmed compaction-event marker exists in Claude Code's local transcript format
  (checked real session JSONLs on this machine — every "compact" hit was a false positive).
  The spec's flagship habit example ("sessions where you compacted earlier cost less") is
  **deferred** — never built, rather than fabricate detection.
- No rate-limit signal exists locally either (same check, same result). The "rate-limit-
  proximity" framing for flat-plan users is replaced with context-size-relative-to-your-
  own-history, which is honestly buildable from data already computed.

**Pre-publish real-data review found the coach's surviving habits (H1-H3) were thin — held.**
Before publish, ran `tes coach` against the real store specifically to check whether the
remaining habits (after H4 was deferred above) were genuinely useful or filler that only
looked fine because H4 was removed. They were thin:
- H1 (high context re-send ratio) never fired on the real store — not a low-N issue: every
  scoreable session across all 5 task types was above the fixed 60% resend threshold, so
  there was no low-resend comparison group to contrast against at all. The fixed threshold
  doesn't discriminate for this (common) heavy-usage pattern.
- H3 (above-baseline-band sessions cost more) fires but its message hides the real finding:
  above-band sessions are disproportionately less $-efficient per token, not just bigger
  (measured: 5.9x more tokens but 8.6x more expensive for ml-eval; 8.6x tokens but 10.7x cost
  for infra-deploy) — real signal, but the shipped message just says "cost more... no action
  attached," which reads as filler.
- H2 (recurring RR/RFR waste) is the one genuinely specific, actionable habit found, but tiny
  in raw $ terms on the real store (~$2.60) and ranked last (6th of 6) under pure-$-impact
  sorting — invisible under the default top-3 a user would actually see.

A thin coach is a credibility risk specifically because it's the piece a developer judges
the whole tool by — five generic "cost more" tips would read as the whole tool being shallow
and contaminate trust in the honest diagnostic + alarm underneath it. Holding it (rather than
shipping behind a flag) protects the product; a curious user finds `tes coach` regardless of
a flag, so "hidden" isn't materially different from "shipped" from a credibility standpoint.
Full addendum with the fix needed (state the disproportionate-$/token finding explicitly;
rank by actionability not raw $; explore a resend threshold relative to the user's own
distribution instead of a fixed 60%) is in `research/13_coach_alarm_honesty_design.md`.

**What's shipped (643/643 tests green, up from 601 in 0.9.0; 43 new tests, including
`test_coach_grounded.py` for the held module):**
- `tes/live_monitor.py` — scores the ACTIVE (in-progress) CC session incrementally, reusing
  the frozen attribution/cost engine against whatever the file currently contains. Every
  figure is labeled "estimated, in progress" — never presented as final/billed.
- `tes/alarm.py` — fires ONLY on a two-part measured AND gate (same shape as
  `intelligence/anomaly.py`'s per-cluster Tukey fence): (1) the live session's token count
  already exceeds the user's OWN self-baseline p75 for that task_type, AND (2) context
  re-send is the dominant cost driver (so `/compact` is actually relevant). Silent whenever
  the self-baseline for that type is still "building" — no cry-wolf, no arbitrary global
  threshold. `AlarmConfig` is OFF by default (opt-in, `--alarm` on `tes serve`). Flat-plan
  aware: the message ALWAYS shows both $ and token framings; `plan=max` reorders emphasis
  (tokens lead, dollar figure becomes a labeled "API-equivalent" parenthetical) but never
  hides the dollar figure outright. Module docstring now carries a caveat (found during
  real-data verification): on heavy-usage stores gate 2 can be near-universally true, so
  gate 1 (the user's own p75) may be doing most of the real gating — still correct, just not
  always independently load-bearing.
- `tes/budget.py` — `tes budget` + dashboard Budget view: rolling-window (default 7 days)
  spend tracking with an honest self-trend projection, always labeled "based on your last N
  days, not a forecast of future work." Silent (returns `None`) when there's nothing in the
  window to project, rather than fabricating a $0 projection.
- `tes/watcher.py` — extended (additively) with `alarm_enabled`/`plan_type` fields; checks
  the live monitor + alarm once per scan cycle when enabled, printing to stderr on fire.
- Dashboard: `/budget`, `/monitor` routes + templates, honest labels throughout (no `/coach`
  route — see above); all prior routes (`/`, `/session/<id>`, `/trends`, `/baseline-status`,
  `/patterns`, `/ask`) regression-confirmed unchanged.
- `tests/test_alarm_measured.py`, `test_coach_grounded.py`, `test_projection_labeled.py`,
  `test_live_cost_estimated.py`, `test_prior_features_intact.py` — cover the no-cry-wolf
  proof (silent on a normal/building/non-resend-dominant session), the N-gate silence
  property, flat-plan-safe message construction, and full regression. The coach tests stay
  green even though the CLI/dashboard wiring is held — they test the module directly.

**Live proof against REAL sessions, not just synthetic fixtures (this machine, 2026-07-04):**
- Alarm fired on the actual currently-active background session: *"This session is at
  ~$13.61 (estimated, in progress) and ~918,044 context tokens (estimated, in progress), 97%
  of which is re-sent context (measured) — well above your own typical debug-fix session
  (p75: 503,086 tokens). Consider `/compact`."*
- Alarm stayed silent on two REAL completed sessions run through the identical pipeline: one
  clearly below its own p75 (247,339 vs. 447,157 tokens) even at 96% resend ratio — proving
  gate 1 alone blocks it, not just gate 2 riding along; one exactly at the p75 boundary
  (silent, confirming the `<=` boundary is correct).
- Flat-plan (`plan=max`) text on the same firing session: *"~918,044 context tokens
  (estimated, in progress), 97% of which is re-sent context (measured) — well above your own
  typical debug-fix session (p75: 503,086 tokens). (API-equivalent: ~$13.61 (estimated, in
  progress), not necessarily what you're billed on a flat plan.) Consider `/compact`."* —
  tokens lead, dollar figure present but demoted and explicitly labeled.
- `tes budget --window-days 60` on the real store (the default 7-day window was honestly
  silent — this machine's scoring store hasn't run since 2026-06-15, not a bug): *"At this
  pace (~$4838.91 so far across 800 sessions, 25.8 of 60 days) you're trending toward
  ~$11243.18 over a 60-day window — based on your last 25.8 days, not a forecast of future
  work; work volume varies."*

**Non-negotiables held:** `git diff --exit-code tes/_waste_detectors.py` empty throughout;
self-baseline/attribution/cost math untouched (consumed, not altered); import-closure green
(zero new dependencies — alarm/budget/live_monitor use stdlib + existing tes internals only,
per the approved zero-dep default); local-only (live monitor reuses the watcher's file tail,
no new egress); dormant 0.9.0 corpus untouched and still dormant.

**Clean-room verified (2026-07-04, `tes-verify-0100`, `--no-default-packages`):** built the
`0.10.0` wheel, confirmed numpy/tracegauge absent before install, installed from the wheel,
confirmed `tes.__file__` resolves to site-packages (not repo) from a neutral cwd. `tes
--version` → `tes 0.10.0`. `tes coach` correctly absent (`invalid choice` error); `tes budget
--help`/`tes monitor --help` present with correct text. All 6 dashboard routes (4 prior + 2
new) returned 200 from the installed wheel, not just the repo copy.

**Incident during clean-room verification (self-caught, fixed):** `source activate <env>`
silently no-op'd in the Bash tool and a wheel install briefly landed in the base conda
environment instead of the isolated verify env, replacing the real `tracegauge==0.8.0`
(the developer's daily-driver install) with the unpublished dev build. Caught via `pip show`
showing the wrong location, remediated by reinstalling `tracegauge==0.8.0` from PyPI into
base, and reconfirmed clean from a neutral cwd before and after the actual clean-room test
(done correctly the second time via the verify env's `python.exe` by full path).

**Published 2026-07-04** — see the post-publish verification entry at the top of this
`[0.10.0]` section. The habit coach is built and tested but not part of this release — see
the addendum in `research/13_coach_alarm_honesty_design.md` for what a future fix needs
before it ships.

## [0.9.0] — Community Corpus (built and tested; NOT published — corpus stays dormant)

**Decision (2026-07-02):** this release's headline feature — the opt-in community corpus —
is fully built, tested (601/601 passing), and proven (RLS proof, send-time byte-grep proof,
clean-room install proof — see below), but the Supabase corpus itself has deliberately **not
been provisioned**. With zero contributors, a live corpus of one delivers no user value, and
crossing the transmission boundary before there's anyone to receive value from it is
premature. `pyproject.toml` carries `version = "0.9.0"` and this code is committed to the
repository, but **it is not published to PyPI** — publishing a release whose headline
feature is inert adds nothing for users and would make PRIVACY.md describe infrastructure
that doesn't exist yet. The next publish happens when either (a) a corpus is provisioned and
activated (see `CURRENT_STATE.md` and `corpus/setup.md` for the exact activation steps —
Supabase project → schema.sql → Edge Function → three env vars → round-trip proof → publish),
or (b) a different user-facing improvement justifies a release on its own.

Everything below is real, tested code sitting dormant behind an unconfigured destination —
not a description of something currently happening to any user's data.
**Engine, detectors, reports, and self-baseline scoring are byte-for-byte unchanged; this
release is a new opt-in data path plus its presentation, not a change to how anything is
scored.**

### Added

- **`tes corpus contribute`** — builds the same 14-field content-free payload as
  `export-contribution` (0.8.0's P7 builder, reused unchanged), shows the exact real row
  plus full consent screen (fields sent, what's never sent, destination named as a third
  party, use, contributor_id, withdrawal warning), and on explicit `y` would send it to the
  tracegauge community corpus (Supabase, `eu-west-1`) — **once one is provisioned**. Today,
  with no corpus configured, it prints `[NOT SENT] the community corpus is not configured on
  this install` and makes no network call, regardless of consent.
- **Send-time content-free re-verification (`tes/corpus_client.py`)** — the ACTUAL
  serialized POST body (not the in-memory payload) is independently re-checked immediately
  before the network call: every row's keys must be exactly the 14 allowed fields, and every
  value is checked against what's legitimate for its field (known-value sets, format
  patterns, numeric-type checks, a 30-character cap on any other string). A failure raises
  `ContentLeakGuardError`, aborts the send before any network call, and writes a local-only
  `~/.tes/contribution_blocked.log` entry — the guard runs whether or not the payload came
  from the already-tested builder, so a future bug elsewhere cannot silently reach the wire.
- **`tes corpus withdraw`** — deletes every row tied to your `contributor_id` via a
  service-role Edge Function (the only path that can delete anything — the client's `anon`
  role has insert-only RLS access, no select/update/delete). Also deletes the local
  `~/.tes/contributor_id.txt` on confirmed success, so a future contribution starts under a
  fresh, unlinked ID.
- **`tes corpus reset-id`** — generates a new `contributor_id` locally (no network); prior
  rows become unlinked from future contributions.
- **`corpus/schema.sql` + `corpus/edge_functions/withdraw-contributor/`** — the Supabase
  table (columns exactly matching the 14-field allow-list) and RLS policy (anon: insert
  only, no select/update/delete — enforced independently of the client's own content-free
  guarantee), and the withdrawal Edge Function (validates `contributor_id` as UUIDv4 before
  the service-role-authenticated delete). `corpus/setup.md` documents reproducible setup.
- **`tes/community_baseline.py`** — offline batch computation of per-task-type percentile
  statistics from pooled contributed rows (published as a data file, fetched the same way
  the bundled self-baseline ships today), and client-side scoring against it. A task_type
  below a minimum-contributor floor (5 distinct contributors) is reported but marked
  unscoreable rather than shown as a misleadingly precise percentile. Every scored result
  carries a domain-of-validity string: contributor/session counts, self-selection bias, and
  content-free coarseness — there is no path that shows a community percentile without it.
- **`PRIVACY.md`** — replaced the "nothing is transmitted" contribution section with the
  full transmission disclosure: what/where (Supabase, `eu-west-1`, GDPR)/retention (until
  withdrawn)/deletion path/anonymity and its limits (a linkability caveat is disclosed, not
  hidden)/the corrected content-free mechanism description (allow-list + length checks, not
  pattern-matching — described as what the code does, not a stronger claim)/hosting-chain
  disclosure naming Supabase's technical access alongside a link to Supabase's own privacy
  policy.

### Non-negotiables held (verified by tests)

- Default install transmits nothing — `tes corpus contribute`/`withdraw` are the only code
  paths that call `tes.corpus_client`, and both are unconditionally gated on explicit
  consent/confirmation, checked before any network call (`test_transmit_optin.py`).
- Content-free payload re-verified on the actual POST bytes at send time, proven by
  byte-grepping the real payload and by planting a secret in every field type — a string
  field, the `waste_detectors_fired` list, and a numeric-as-string attempt — and confirming
  the guard catches each (`test_send_content_free.py`).
- Withdrawal works and is honest when it can't: missing/malformed `contributor_id.txt`
  refuses rather than guessing (`test_withdrawal.py`).
- RLS proof: `corpus/schema.sql` has exactly one policy (anon insert-only), no
  select/update/delete policy for anon anywhere in the file, and its columns match
  `ALLOWED_FIELDS` exactly (`test_corpus_rls.py`).
- `git diff tes/_waste_detectors.py` empty; self-baseline scoring path untouched by this
  release; import-closure green (no new dependency — `httpx` was already declared).
- Clean-room proof: built the `0.9.0` wheel, installed into a fresh `--no-default-packages`
  conda env, ran from a neutral cwd (outside the repo, so the installed wheel was actually
  exercised, not the local source tree). `tes corpus contribute` with explicit `y` and no
  `TES_CORPUS_*` env vars set → `[NOT SENT] the community corpus is not configured on this
  install` — a fresh install cannot transmit even under forced consent.

### Deliberately not done

- **No Supabase project provisioned; not published to PyPI.** See the decision note at the
  top of this entry. The RLS proof above is static (schema-text assertions against
  `corpus/schema.sql`) — there is no live Supabase project to test against, by choice, not
  by omission. The end-to-end round-trip (contribute → row appears → withdraw → gone) against
  a real project is the last proof before activation, and it happens when a corpus is
  actually provisioned — see `corpus/setup.md` for the exact steps.

## [0.8.0] — 2026-06-15 — Dashboard Intelligence + Sortable Session List

Brings the Session Intelligence features (ML archetypes, natural-language Q&A) from the CLI
into the browser dashboard, and makes the session list sortable. **Surfacing + sorting only —
the engine, ML, chat grounding, cost math, and detectors are byte-for-byte unchanged.**

### Added

#### Part B — Sortable session list

- **Clickable column headers** on the session list: sort by **Cost**, **Date**, **Tokens**,
  **Waste**, or **Token verdict** via server-side `?sort=&dir=` query params. No SPA, no
  browser storage — pure server-rendered Jinja2 and `ORDER BY` on query params.
- **Active-column sort arrow** (↑/↓) rendered in the header that reflects the current sort key
  and direction. Default sort: date descending (most recent first, matching the pre-0.8.0 order).
- **New Cost and Tokens columns** in the session list table, surfacing what was previously only
  visible inside session detail.
- **`list_sessions()` sort params:** `order_by` and `direction` added with a strict 5-key
  whitelist (`date/cost/waste/tokens/verdict` → actual DB column names). Unknown keys fall
  back to `scored_at DESC`. SQL injection surface: zero — the column name never comes from
  user input; only the whitelisted value reaches the query.
- **Honesty elements all survive sorting:** the domain-of-validity caveat, UNAVAILABLE-neutral
  trajectory badge, "not a score" price provenance, baseline-source badge — all verified by
  test assertions on every sort permutation.

#### Part A — Dashboard Intelligence

- **`/patterns` page (nav item):** Session archetypes as visual cards (bar charts for context
  re-send, context growth, output, has-waste; dominant-feature labels; task-type mix). Validity
  header (k, silhouette, session count, computed timestamp). Anomaly count and pct. Domain-of-
  validity caveat. "Descriptive only — not predictive, not quality labels" framing — identical
  to `tes patterns` CLI. Small-corpus floor honored by construction: below 30 content sessions,
  shows "Not enough sessions yet" message and no archetype cards.
- **LLM / judge status chips** in the Patterns page header: green "Ollama running — local
  inference, no egress" / grey "Ollama not detected" / amber "ANTHROPIC_API_KEY set — consent
  required before use". Read-only status indicators only — no one-click API-judge enable.
- **Web Ask panel** on the Patterns page: text input → POST `/ask` → grounded answer rendered
  in the browser. Carries **identical guards to `tes ask`**, enforced by construction:
  - Same `ask_local()` / `ask_api()` functions as the CLI (not a copy — the same code path).
  - Same `CHAT_SYSTEM_PROMPT` object: "I don't predict future behavior" fires in-browser.
  - Same `build_chat_context()`: metrics-only, no raw session content/code/paths structurally.
  - API consent: checkbox shown in UI (only when Ollama absent + API key detected); route
    returns `{needs_consent: true}` error without calling `ask_api` unless `api_consent=True`.
  - Question length capped at 500 chars server-side before any LLM call.
  - Small-corpus floor: below-floor Ask works (corpus stats provided); no invented archetypes.
  - Local-first routing: Ollama answer used; `ask_api` never called when local succeeds.

### Fixed

- **Route-registration gap (closes "tests-pass-but-real-server-fails" class):**
  `test_route_registration.py` imports `create_app` / `start_server` via the same path `tes serve`
  uses, inspects `url_map` directly, and asserts `/patterns` + `/ask` are registered. The
  previous test gap: a fixture-level GET test passes even when the installed artifact lacks the
  route (analogous to the numpy gap caught by `test_dep_closure.py` in 0.7.1).

### Tests

543 green (+70 new across four files):
- `tests/test_session_sort.py` (23): whitelist unit tests, sort-key ordering, Flask route with
  honesty-element assertions on every sort permutation.
- `tests/test_web_patterns.py` (17): floor honored (no archetype grid below 30), validity stats
  shown, DOV + descriptive caveat, judge status chips, Ask panel rendered.
- `tests/test_web_ask_guards.py` (21): G1 metrics-only egress, G2 identical backend (same
  functions as CLI), G3 "I don't predict" pass-through, G4 "not measured" pass-through, G5
  floor no-crash, G6 consent gate (no network without `api_consent=True`), G7 `ask_api` gate
  inherited, G8 question length cap, G9 local-first routing.
- `tests/test_route_registration.py` (9): url_map assertions via same path as `tes serve`,
  GET /patterns → 200, POST /ask empty → 400, GET /ask → 405 not 404.

Detectors frozen. Import closure green (no new Python deps — Ask-panel JS is vanilla inline
script, no framework, no CDN).

---

## [0.7.1] — 2026-06-15 — Hotfix: missing ML dependencies

`tes patterns` and `tes ask` crashed with `ModuleNotFoundError: No module named 'numpy'`
on every clean install of 0.7.0. The Session Intelligence code (tes/intelligence/) imports
`numpy` and `scikit-learn` but those packages were never declared in `pyproject.toml`.
They were present in the development environment, so the pre-publish gate missed the gap.

### Fixed

- **Missing ML dependencies declared:** `numpy>=1.24,<3` and `scikit-learn>=1.3,<2` added
  to `[project.dependencies]` in `pyproject.toml`. A clean `pip install tracegauge` now
  installs everything `tes patterns` and `tes ask` need. No pandas is imported — only
  numpy and scikit-learn were missing.
- **Judge HTTP 500 graceful handling:** Ollama returning HTTP 500 (model OOM or inference
  failure) now prints `Judge unavailable: Ollama returned HTTP 500 (model error or OOM) —
  trajectory UNAVAILABLE` instead of a raw exception message. `httpx.HTTPStatusError` is
  now caught explicitly before the broader `httpx.HTTPError` catch.
- **Dashboard judge model name corrected:** `session_detail.html` suggested `qwen3:8b` for
  the trajectory judge setup — wrong model. Fixed to `qwen3:30b-a3b` (~18 GB VRAM), matching
  `judge.py`'s `JudgeConfig` default and `report.py`'s setup hint.

### Tests

- `tests/test_dep_closure.py` (1 test): walks `tes/intelligence/*.py` via AST, extracts
  all absolute top-level imports, and asserts every external import is either stdlib or
  in `DECLARED_IMPORT_NAMES`. Fails immediately if a new undeclared import slips in —
  guards against the 0.7.0 clean-install regression.

### Gate lesson

The 0.7.0 clean-room gate ran in conda base (numpy pre-installed). The gate must use a
truly isolated environment with only declared deps. Starting with 0.7.1, the gate uses
`conda create --no-default-packages` so only `pip install tracegauge` deps are present.

---

## [0.7.0] — 2026-06-15 — Session Intelligence

Two composing features that do work the deterministic engine cannot: unsupervised
ML finding recurring patterns across the whole corpus, and a conversational layer
to ask about sessions in plain language. The engine, attribution math, detectors,
and reports are **byte-for-byte unchanged** — this adds analytics on top of what's
already measured.

### Added

#### ML Foundation (`tes/intelligence/`)

- **Validated KMeans clustering** over the session corpus (k=2..8, n_init=30).
  k selected by silhouette score; stability checked across 10 seeds (CV < 0.15
  required). Silhouette thresholds: ≥ 0.20 meaningful, 0.10–0.20 weak, < 0.10
  no structure. Live corpus: k=3, silhouette=0.466, stability CV=0.000.
- **Feature engineering** — 8 features: 4 attribution percentages
  (context_resend_pct, context_growth_pct, output_pct, waste_pct), 3 log-scale
  size features (log_real_tokens, log_turn_count, log_cost), 1 binary (has_waste).
  `task_type` deliberately **excluded** from the feature vector: including it
  produced k=7 where 5/7 clusters re-discovered known folder labels (silhouette
  0.37); excluding it gives k=3, silhouette 0.466 with genuinely cross-type
  behavioral archetypes — structure not already present in the folder names.
- **Named archetypes** from centroid dominant features, with evaluative terms
  forbidden. Live archetypes: `medium high context re-send sessions` (64.7%),
  `small active context-building sessions` (24.3%),
  `medium with detected waste sessions` (11.1%).
- **Per-cluster Tukey outer fence anomaly detection** (Q3 + 1.5×IQR on centroid
  distance). Top-3 deviating features reported per anomaly. Live: 10 anomalies /
  235 sessions (4.3%).
- **Persistent ML cache** at `~/.tes/intelligence_cache.json` — stamped with
  `tracegauge_version + session_count + computed_at`. Invalidated on version
  bump or when session count changes by > 5. Minimum corpus floor: 30 content
  sessions; below it, an honest "not enough sessions for stable patterns yet"
  message is returned instead of clustering noise.
- **`tes patterns`** — CLI command showing archetypes, cluster validity metrics,
  and anomaly summary for the whole corpus.

#### Conversational Layer

- **`tes ask "<question>"`** — plain-language Q&A about sessions. Local Ollama
  first (auto-picks best available model); falls back to offering the Anthropic
  API behind the existing consent gate. Default local model: `qwen3:8b`.
- **Metrics-only egress** — the chat context contains ONLY computed numbers
  (corpus stats, cluster centroids, anomaly counts). Raw session content, code,
  tool inputs/outputs, and file paths are never sent to any model.
- **Constrained system prompt** (7 rules): answer only from context; "I don't
  have that measured" for absent facts; "I don't predict future behavior" for
  forecasting questions; cite the metric source for every number; do not
  dramatize archetypes into personalities.
- **Unambiguous context labels** — all binary flags use YES/NO (never 0/1);
  all attribution percentages name the denominator ("of billed tokens"); all
  session fractions name the reference ("of corpus"). The Q2 regression: `waste=1`
  misread as "1.0% waste rate" is guarded by `TestContextFormatUnambiguous`.
- **Honest small-corpus path** — `tes ask` and `tes patterns` both gate on the
  minimum corpus floor; a new user sees "not enough sessions yet" rather than
  archetypes that don't statistically exist.

### Tests (470 total, up from 377)

- `tests/test_cluster_validity.py` (31) — feature extraction, clustering validity,
  no-stable-clusters path, evaluative terms not in archetype names.
- `tests/test_anomaly_threshold.py` (12) — Tukey threshold is per-cluster, every
  anomaly exceeds threshold, top-3 deviating features valid and sorted.
- `tests/test_chat_grounding.py` (31) — system prompt constraints, context
  structure, no-code egress, API consent gate; **+ 5 context-format regression
  tests** (`TestContextFormatUnambiguous`).
- `tests/test_chat_no_code_egress.py` (19) — user message builder, API payload
  inspection, egress notice accuracy.
- `tests/test_small_corpus_honest_path.py` (12) — cache layer, format layer, and
  chat layer all return honest "not enough sessions" messages below the clustering
  floor; above the floor, valid clustering is produced.

### Methodology

`research/12_session_intelligence.md` — feature choices, task_type-exclusion
reasoning, cluster-validity results, anomaly-threshold rationale. The portfolio
artifact documenting what the ML actually found and why the method is sound.

### Unchanged (non-negotiable)

- `tes/_waste_detectors.py` is **byte-frozen** (confirmed: `git diff` empty).
- Reports 01–11 immutable. Attribution engine, cost math, judge path, consent
  gate — all unchanged.
- API consent gate is **unconditional** on all paths, including the new chat API
  path. `consent_given=False` → `None` + zero network calls.

---

## [0.6.0] — 2026-06-13 — Frictionless UX

Ergonomics only. The scoring engine, numbers, attribution, cost math, detectors,
and honesty surfacing are **byte-for-byte unchanged** from `0.5.0` — this release
only changes how the tool is invoked and how it guides setup. The detectors stay
frozen; reports 01–11 stay immutable.

### Added
- **Bare `tes` launches the dashboard.** Running `tes` with no subcommand now
  starts the localhost dashboard (`tes serve`), the obvious default. `tes --help`
  still shows help; `tes <unknown>` still errors helpfully.
- **`tes score` needs no path.** With no argument it scores your most recent
  session (newest `.jsonl` under `~/.claude/projects` by mtime) and prints which
  one it chose. `tes score --pick` shows a numbered list of recent sessions to
  choose from. An explicit `PATH` still works. Resolution order: explicit PATH >
  `--pick` > newest.
- **Clean `--judge` on-switch.** `tes score --judge` now works — it previously
  failed with `ambiguous option: --judge could match --judge-model,
  --judge-endpoint`. `--judge` means "use the trajectory judge."
- **Judge auto-detect + guide.** `--judge` detects a running local Ollama judge
  and uses it; if none is found but `ANTHROPIC_API_KEY` is set, it *offers* the
  API judge behind the existing per-session consent screen; if neither is
  available it prints the single simplest setup step instead of failing cryptically.
- **First-run orientation line** on `tes serve` / bare `tes` (session count found,
  dashboard URL) and on `tes score` (which session was auto-selected).

### Unchanged (non-negotiable boundaries)
- **API-judge consent stays the egress gate.** Auto-detecting an API key does
  **not** authorize sending data — every byte of egress still requires an explicit
  `y` on the per-session consent screen. `detect_env_api_key()` performs zero
  network activity.
- Judge stays **OFF by default in the background watcher** (GPU/cost footgun guard).
- No scoring / attribution / judge / cost / detector logic changed — same numbers,
  same honesty. `tes/_waste_detectors.py` is byte-frozen; reports 01–11 immutable.

### Tests
- `tests/test_cli_ergonomics.py` — bare-`tes`-serves, no-path-scores-newest,
  `--pick` selection, explicit-path-wins, `--judge`-not-ambiguous, flag conflicts.
- `tests/test_judge_autodetect.py` — Ollama-preferred, API-offered-on-key,
  consent-declined-means-no-egress, consent-accepted-passes-config,
  guide-when-nothing, and an unconditional no-silent-egress guard.

## [0.5.0] — 2026-06-13 — Consolidated current release

The complete, feature-complete release. Bundles the entire validated toolchain
(B1–B5 research arc) and every shipped phase (P1–P9) into one published artifact.

### Added
- **Diagnostic dashboard redesign (P9).** Polished CSS visual system across all
  dashboard views (session list, session detail, baseline status, trends). All
  honesty elements survive the restyle, guarded by a dedicated regression test
  (`test_ui_honesty_survives.py`, 20 assertions): domain-of-validity caveats on
  every axis, UNAVAILABLE rendered as a calm/neutral state (never an error),
  relative "your own lean baseline" framing, baseline-source labels, waste proof
  turns, API-judge egress warning, and no composite/blended score.
- **README Features section** at the top so a new user sees the tool is
  feature-complete regardless of the version number.

### Feature set in this release (cumulative)
- **Self-baseline token scoring (P4)** — scores against your own lean, waste-free
  sessions per task type; bundled reference corpus as fallback.
- **Dollar cost attribution (P8)** — six reconciling buckets (B1–B6); token% and
  cost% side by side; cache-read divergence made visible.
- **Deterministic waste detection (B4/P6)** — frozen observable-invariant
  detectors (repeated-failed-retry, redundant-read) with proof turns and per-event
  wasted cost.
- **Trajectory judge (P1/P8)** — local Ollama judge ($0, GPU) or opt-in API judge
  (explicit per-session consent); UNAVAILABLE is a complete, expected state.
- **Localhost dashboard + watcher (P2)** — `tes serve`, `127.0.0.1`-only,
  auto-scores finished sessions, SQLite store with WAL.
- **Content-free local contribution export (P7)** — `tracegauge export-contribution`;
  redacted local file you inspect and control; nothing transmitted by tracegauge.

### Unchanged / guaranteed
- Detectors frozen (`tes/_waste_detectors.py` byte-verbatim with the validated
  research copy). Research reports 01–11 immutable.
- Local by default — scoring and the dashboard make zero external network calls.
  The only egress is the opt-in API judge (your key, your consent).

## [0.4.0] — built internally, not published

Token attribution (six-bucket) + opt-in API judge (P8). Folded into 0.5.0.

## [0.2.0] — built internally, not published

Content-free contribution export + watcher/dashboard hardening (P2/P7). Folded
into 0.5.0.

## [0.1.0] — 2026-06-08 — First PyPI release

- Installable CLI + SDK: `pip install tracegauge`, `tes score <path>`.
- Three-axis scoring (token economy + trajectory quality + deterministic waste),
  tiered judge, secret redaction on by default, AGPL-3.0.
- Published to PyPI: https://pypi.org/project/tracegauge/0.1.0/

[0.5.0]: https://pypi.org/project/tracegauge/0.5.0/
[0.1.0]: https://pypi.org/project/tracegauge/0.1.0/
