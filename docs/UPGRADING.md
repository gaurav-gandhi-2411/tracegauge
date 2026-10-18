# Upgrading to 0.15 (legacy rows)

0.15 counts each API response's usage once and prices 1-hour cache writes at their own rate.
Sessions your store scored with an older version (0.14 and earlier) carry numbers that are now
known to be wrong: tokens and dollars ~2x too high (usage was added once per content block), and
1-hour cache writes under-priced. Those rows are **legacy rows**.

**What happens to them.** Nothing is deleted and no stored number is rewritten by the upgrade. A
legacy row is *derived*: `adapter_version` or `cost_version` is empty or older than the running
version (every row of a 0.14-or-earlier store, since those columns did not exist). `tracegauge rescore`
clears it by writing the current versions. The columns are added by the first command that opens
the store; adding them is idempotent, safe to run twice and safe if the watcher and a CLI command
open the store at the same moment.

## What each command does with legacy rows

| Command | Legacy rows |
|---|---|
| self-baselines (verdict bands, `tracegauge score`, dashboard) | excluded. A task type with no current rows is `building` again; its DOV text says how many legacy rows were left out. |
| live alarm (`monitor`, `serve --alarm`) | excluded from the pool the threshold is computed from. |
| `budget` | excluded from the pace and projection. `--json` carries `legacy_rows_excluded` (legacy sessions with a cost in the window); text says how many were left out. If every session in the window is legacy there is no projection, not a legacy one. |
| `cost` (periods, per-project, coverage) | excluded from `total_usd`, `session_count`, `by_project` and token coverage. They appear once, on a separate line labelled `legacy (pre-0.15 accounting, overcounted ~2x)`, never added to the corrected total. `--json`: `legacy_rows_excluded` and a `legacy` object `{label, session_count, total_usd, included_in_total_usd: false}`. |
| `cost --roi` | built on the corrected total only, so the multiple is a floor; it says so when legacy rows were excluded. |
| `export-contribution` (and `tracegauge corpus contribute`, which uses the same builder) | **excluded.** A legacy row would write an overcounted `real_tokens` into a file others may read. The preview and the written-file summary say how many legacy sessions were left out; a store with only legacy rows exports nothing and says so. |
| `impact` | **excluded** from every figure. The edit counts themselves are not affected by the usage double-counting; legacy rows are left out anyway so `impact` describes the same set of sessions as every other command. Text says `N legacy sessions left out`; `--json` has `legacy_rows_excluded`. |
| `patterns` (CLI and the dashboard `/patterns` page) | **excluded** from the clustering (their attribution shares came from overcounted usage). The analysis says how many were left out; `--json` has `legacy_rows_excluded`; a cached analysis built before the count was recorded is recomputed. |
| dashboard `/budget` | **excluded** from the pace and projection, same as `tracegauge budget`. A banner says how many legacy sessions in the window were left out. |
| `ask` (CLI and the dashboard `/ask`) | **excluded** from every corpus number handed to the model (counts, cost and token percentiles, waste), and the model is told how many legacy sessions exist and not to estimate them. A single legacy session you name by id is still described, but labelled `legacy: overcounted, not comparable`. The CLI prints how many were left out after the answer. |
| dashboard session list and session page | **listed, marked.** Every legacy row carries the badge `legacy: overcounted, not comparable`, a banner says how many there are, and a legacy cost is never compared with your baseline. The session page recomputes its attribution from the transcript with the current accounting and says so. |

Only `schema_version` 1 keys were added; none were removed or renamed.

## The one-time notice

The first command that opens an existing store (`score`, `cost`, `budget`, `monitor`, `serve`,
`impact`, `patterns`, `ask`, `export-contribution`, or bare `tracegauge`) prints this to **stderr**
(never into `--json` stdout):

```
tracegauge: 1452 of 1452 stored session(s) were scored by an older version ...
  rescorable (transcript still on disk): 0
  unrecoverable (transcript gone):       1452
```

It is shown once per store per tracegauge version, and again only if more legacy rows appear later.
Silence it with `TRACEGAUGE_NO_NOTICE=1` or `tracegauge --quiet <command>` (the flag goes before the command).
It never creates a store, never raises, waits at most 0.25 s on a locked store, and is skipped by
`rescore` and `backfill-waste`.

## `tracegauge rescore`

```
tracegauge rescore [--dry-run] [--json] [--db-path PATH] [--limit N]
```

Re-scores every legacy row whose transcript is still readable with the current adapter and price
table (real tokens, cost, subagent split, verdict band against the bundled baseline, attribution
fractions, waste events). Judge verdicts are kept. It reports five counts:

* **rescored**: legacy rows re-computed and now current.
* **skipped (source missing)**: the transcript is gone. The row is left exactly as it was and stays legacy.
* **skipped (empty stub)**: the row never recorded a turn or a token (stored `turn_count` 0 and
  `real_tokens` 0) and its transcript carries no usage, so there is nothing to recompute. It is
  marked current (only `adapter_version` and `cost_version` are written; no stored number moves), so
  it stops being legacy and does not keep `rescore` failing on every run.
* **failed (parse error)**: the transcript exists but yielded no usage records although the row had turns or tokens,
  or its cost could not be computed. The row is left exactly as it was and stays legacy (so a damaged file can never
  overwrite a stored number with zero).
* **not attempted (--limit)**: readable rows past `--limit` (most recently written first); run again.

`--dry-run` does the same computation and writes nothing: the store is opened read-only (immutable
when no `-wal` file exists), so it is not migrated and the file is byte-identical afterwards.

It also records the turn count on older rows that were stored without it (`turn_count` 0 with
tokens and a readable transcript; the text output says how many, `--dry-run` says how many would
be). Your own self-baseline only counts rows that carry a turn count, so without this a store written
by an earlier version stayed on "building". The watcher does the same once at start-up. Only
`turn_count` is written, and the `--json` document is unchanged.

Idempotent: a second run rescores 0 rows and changes nothing. It never creates a store. Exit codes:
`0` ok, `1` no store / unopenable store / `--limit < 1`, `4` at least one readable row failed (the
rest were still rescored); see [EXIT_CODES.md](EXIT_CODES.md). `tracegauge backfill-waste` still works as
before (it also refreshes stale rows) and follows the same guard: a transcript with no usage records
is counted as failed, its row is left as it was, and the exit code is 4. Before 0.15.0 it overwrote
such a row with zeros.

If the transcripts are gone (Claude Code deletes old ones), `rescore` cannot help: those rows stay
excluded and your self-baselines and alarm threshold rebuild from new sessions.

# Upgrading to 0.17 (token verdicts come from your own sessions)

Until 0.16 `tracegauge score FILE` compared a session with a bundled one-developer band, while the
watcher and the dashboard showed no token verdict for a task type until about 17 of your own
waste-free sessions existed (a lean half of them, once it passed a stability check). From 0.17 every
surface behaves the same way:

* A task type gets a token verdict (`above_p75`, `within_band`, `below_p25`) only from your own
  sessions: the session's percentile among your other in-scope sessions of that type, with a label
  against their p25 and p75, from 10 of them. Waste is not a filter, empty stubs and sessions below
  the scope floor do not count, and there is no lean subset or stability guard any more.
* Before that it shows `building (n/10)` and the token verdict is `unavailable`. Nothing is
  borrowed from the one-developer table or from any other population. The waste, cost and
  trajectory readings do not depend on the token verdict.
* `tracegauge score FILE` reads your store (when there is one) to know how many sessions of the
  type you have; with no store every type reads `building (0/10)`.
* Stored sessions keep the verdict they were scored with; nothing is rewritten.
* The one-developer table remains in the package (labelled "reference: 1 developer") for the live
  alarm's fallback threshold and as the upper bound of a task type's scope floor. It is never
  shown as a verdict.
