# tracegauge

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/gaurav-gandhi-2411/tracegauge/raw/master/assets/brand/lockup-dark.svg">
    <img alt="tracegauge" src="https://github.com/gaurav-gandhi-2411/tracegauge/raw/master/assets/brand/lockup-light.svg" height="64">
  </picture>
</p>

tracegauge reads your Claude Code session transcripts on your own machine and shows where the tokens and dollars went, which retries were pure waste, and which cache re-writes cost you money.

[![CI](https://github.com/gaurav-gandhi-2411/tracegauge/actions/workflows/ci.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/tracegauge/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://pypi.org/project/tracegauge/)
[![PyPI](https://img.shields.io/pypi/v/tracegauge.svg)](https://pypi.org/project/tracegauge/)

<p align="center">
  <img alt="Terminal recording of tracegauge quickstart on the bundled synthetic sample session: a token economy verdict, one deterministic waste finding and the cost breakdown" src="https://github.com/gaurav-gandhi-2411/tracegauge/raw/master/assets/demo/quickstart.svg">
</p>

## Install and try it

```bash
pip install tracegauge
tracegauge quickstart   # scores a bundled synthetic session: no network, no files of yours read
tracegauge score        # scores your most recent Claude Code session
```

## What it finds

Real output of this version on data in the repository. Lines are selected from the report, never edited. [`scripts/gen_readme_demo.py`](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/scripts/gen_readme_demo.py) regenerates both blocks and the recording above, and `tests/test_readme_demo.py` fails when this README or the recording drifts from what the command prints.

`tracegauge quickstart` on the bundled sample, a synthetic session shipped in the package. It gets a token verdict against the research-recon band, one deterministic waste finding with its proof turns, and the cost split by where the money went:

```text
tracegauge quickstart -- no local judge, no network call, no files of yours read.
── TOKEN ECONOMY ───────────────────────────────────────────────────────────
  Real tokens: 531,840
  Verdict:     UNAVAILABLE
── TRAJECTORY QUALITY ──────────────────────────────────────────────────────
  Verdict:     UNAVAILABLE (no local judge configured)
── DETERMINISTIC WASTE ─────────────────────────────────────────────────────
  Events:  1 event(s)
  [1] REPEATED-FAILED-RETRY — 2 retries, turns: 61→62, 64→65  ~$0.041
── COST BREAKDOWN ──────────────────────────────────────────────────────────
  Context re-send (cache reads)                       $1.89   51.3%
  Context growth (cache writes)                       $0.33    8.8%
  Output                                              $0.80   21.8%
  Fresh input                                         $0.63   17.0%
  Detected waste (redundant reads, retry loops)       $0.04    1.1%
  Total (priced)                                      $3.69
```

The cache-churn detector, on a second synthetic session with three planted cache re-writes (an idle gap past the cache lifetime, a model switch and a compaction; seeded generator, `tests/churn_synth.py`). It reports what each re-write cost above reading the cache back, and says the cause is a rule on transcript markers and timestamps, not a verified fact. Method and limits: [docs/CACHE_CHURN.md](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/docs/CACHE_CHURN.md).

```text
── CACHE CHURN ─────────────────────────────────────────────────────────────
  3 event(s) where cached context was re-written instead of read: $0.38
  above what reading it would have cost. Main chain $0.38 (3), subagents
  $0.00 (0).
    TTL gap (idle past the cache lifetime)      1       $0.06
    model switch                                1       $0.16
    compaction                                  1       $0.17
  Plausibly avoidable (a judgement): $0.21; not avoidable by a setting (idle
  over an hour, compaction): $0.17.
    [main] turn 29→30: compaction, gap 78s vs 300s TTL, 35,615 tokens re-
    written, $0.17
    [main] turn 19→20: model switch, gap 53s vs 300s TTL, 32,860 tokens re-
    written, $0.16
    [main] turn 9→10: ttl gap, gap 2,632s vs 300s TTL, 23,145 tokens re-
    written, $0.06
  Cause = a rule on transcript markers and timestamps, not a verified fact.
```

Every figure in this block is rounded independently, and totals use the unrounded values. So the three cause rows add to $0.39 while the $0.38 total is the sum of the unrounded amounts, and the $0.21 "plausibly avoidable" figure sits beside model-switch and TTL-gap rows ($0.16 + $0.06) that add to $0.22.

## Commands

`tgauge` is a short alias for `tracegauge`. The old `tes` command still works until 0.18 ([migration guide](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/docs/MIGRATING_FROM_TES.md)). Full usage and the SDK: [docs/COMMANDS.md](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/docs/COMMANDS.md).

| Command | What it does |
|---|---|
| `tracegauge quickstart` | Scores the bundled sample session. No network, none of your files read. |
| `tracegauge score [PATH]` | Scores a session file, a directory, or (no path) your most recent session. `--pick` chooses from a list, `--json` prints machine-readable output, `--judge` adds the optional trajectory judge. |
| `tracegauge` / `tracegauge serve` | Starts the localhost-only dashboard (`127.0.0.1:4747`) and a watcher that scores finished sessions. |
| `tracegauge cost` | Spend for a period (`--week`, `--month`, `--since DATE`); `--roi` compares it with your plan cost. |
| `tracegauge budget` | Where your spend pace is heading, from your own trailing window. |
| `tracegauge monitor` | One-shot check of the session being written now; exits 3 if the cost/context alarm fires. |
| `tracegauge impact` | Lines added and removed and the most-edited files, from edit tool calls. |
| `tracegauge patterns`, `tracegauge ask` | Behavioural archetypes over your sessions and an explainer over them (need `pip install "tracegauge[patterns]"`). |
| `tracegauge rescore`, `tracegauge backfill-waste` | Re-score sessions stored by older versions. |
| `tracegauge export-contribution` | Writes a redacted, content-free local file. Nothing is sent. |

## Supported agents

Claude Code is supported. Other agents are planned; none is supported today. Each needs its own transcript adapter and a fresh validation run, not a flag.

## Methodology

Three readings, never blended into one score. Token economy compares a session's tokens (each API response counted once, cache re-reads excluded) with the p25 to p75 band of your own lean, waste-free sessions of the same task type, which exists once you have 10 of them; until then a task type shows `building (n/10)` and no verdict is borrowed from another population. "Above the band" means heavier than your own efficient runs, not wasteful. Beside it, `score` prints a labelled context line, the session's tokens per turn as a percentile of other developers' sessions of the same task type ([how it is built](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/docs/MULTIDEV_REFERENCE.md)); it is not a verdict. Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat), which is made available under the [ODC Attribution License 1.0](https://opendatacommons.org/licenses/by/1-0/). Trajectory quality is an optional LLM judge: it is `UNAVAILABLE` without one, it was checked against a reference model and not against human labels, and its negative verdicts are model-dependent. The waste detectors catch two observable patterns only (identical failed retries and re-reads of unchanged files) and miss judgment-of-progress waste by design. Cache churn is a separate section of the score output, not a third waste detector: it finds turns where a cached prefix that the previous turn had read was written again, and prices each re-write above what reading it back would have cost; its cause label is a rule over transcript markers and timestamps, not a measurement of the vendor's cache, and the transcript cannot settle the cause ([docs/CACHE_CHURN.md](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/docs/CACHE_CHURN.md)). Costs are API-equivalent rates from a bundled, dated price table, and a model missing from it is shown as unpriced, never as $0. Scoring makes no external network call (the optional local Ollama endpoint aside); the only data that can leave your machine is an opt-in API judge you consent to per session. Figures whose source data is not in this repository are labelled as the maintainer's unpublished local measurement. Everything, with every caveat and where each number comes from: [docs/METHODOLOGY.md](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/docs/METHODOLOGY.md). Privacy: [PRIVACY.md](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/PRIVACY.md).

---

[Apache-2.0](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/LICENSE) from 0.16.0 onward. Releases 0.15.0 and earlier on PyPI were published under AGPL-3.0-only and remain under that license for anyone who received them; the change applies to 0.16.0 and later only. Third-party fonts bundled in the package keep their own SIL OFL 1.1 license, and third-party datasets in the repository keep theirs; see `NOTICE` and [DATA_SOURCES.md](https://github.com/gaurav-gandhi-2411/tracegauge/blob/master/DATA_SOURCES.md).
