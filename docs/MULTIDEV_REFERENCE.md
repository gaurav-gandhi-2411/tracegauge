# The multi-developer context line

`tracegauge score` prints one line of **context** under the token reading (illustrative values):

```
Context: 1,038 tokens per turn is about the 62nd percentile of 271 developers' debug-fix sessions
(1,879 sessions; SWE-chat v2, public repositories, 2026-01-15 to 2026-09-04; each developer weighted
equally). Not a verdict. Contains information from SALT-NLP/SWE-chat, made available under the ODC
Attribution License 1.0.
```

It answers "is this session unusually heavy or light per turn compared with other developers' sessions of
the same kind?". It is **not a verdict**. The token verdict (`above_p75`, `within_band`, `below_p25`) comes from
your own sessions only, once you have 10 of them; until then it reads `building (n/10)`. Nothing here is
borrowed as a verdict, and the line never says "above" or "below" a band.

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
> which is made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/).
> Baumann et al., 2026, arXiv:2604.20779. See [DATA_SOURCES.md](../DATA_SOURCES.md).

## Where it appears and where it is hidden

* `score` text, `score --json` (`context`, a fixed-key object, `null` when hidden; see
  [JSON_OUTPUT.md](JSON_OUTPUT.md)) and the dashboard's session page.
* Hidden when the task type has no published cell, when the session has no recorded turn count, and
  **always for `feature-build`** (the maintainer's decision: the classifier returns it for every session that
  matches no other rule, so it is not a coherent comparison group; 4,411 sessions were measured and none is published).
* A cell is published only if it has at least 5 developers and 10 sessions, and its p25, median and p75 each
  move by at most 30% when any one developer is left out. An unpublished cell carries no numbers.

## The shipped table

The table is a percentile table only: per task type a grid of percentiles (5 to 95 in steps of 5) of tokens per
turn, the p25 / median / p75 with 95% bootstrap intervals, the number of developers and sessions, and the
leave-one-developer-out shift. It holds no developer id, no session id, no text and no per-developer value.
`tracegauge/data/multidev_bands.json`; rebuilt by `scripts/build_multidev_bands.py` from a local, gitignored
per-session extract (`scripts/extract_swechat_sessions.py`).

**Shipped: SWE-chat v2** (maintainer's unpublished local measurement; the raw data stays on the maintainer's
disk). 8,367 Claude Code sessions from 495 developers, created 2026-01-15 to 2026-09-04. Left out: 3,641
sessions with no developer id (they cannot be clustered) and 261 partial sessions (207 with no usage, 31 that
begin with a continuation summary, 23 with fewer API responses than the dataset recorded).

| Task type | Developers | Sessions | p25 / median / p75 (tokens per turn) | Median, 95% interval | Largest move when one developer is left out |
|---|---|---|---|---|---|
| debug-fix | 271 | 1,879 | 686 / 1,009 / 1,460 | 940 to 1,074 | 0.4% |
| infra-deploy | 155 | 516 | 648 / 974 / 1,480 | 897 to 1,060 | 2.9% |
| ml-eval | 97 | 659 | 809 / 1,117 / 1,694 | 990 to 1,263 | 0.9% |
| research-recon | 211 | 902 | 689 / 1,088 / 1,862 | 997 to 1,176 | 0.8% |
| feature-build | 432 | 4,411 | not published | | |

### v1 against v2, built under identical rules

The same rules were run on the first release of the dataset (v1: 57 developers, 766 sessions, created
2026-02-11 to 2026-04-15) and on v2.

| Task type | v1 developers / sessions | v1 median (interval) | v2 developers / sessions | v2 median (interval) |
|---|---|---|---|---|
| debug-fix | 33 / 162 | 793 (722 to 918) | 271 / 1,879 | 1,009 (940 to 1,074) |
| infra-deploy | 15 / 45 | 645 (463 to 964) | 155 / 516 | 974 (897 to 1,060) |
| ml-eval | 8 / 19 | **not published** (p25 moved 30.5% when one developer was left out) | 97 / 659 | 1,117 (990 to 1,263) |
| research-recon | 19 / 42 | 886 (502 to 1,259) | 211 / 902 | 1,088 (997 to 1,176) |

**Recommendation, followed here: ship v2.** It has about nine times the developers (495 against 57), publishes
the ml-eval cell that v1 had to reject, and is more stable: the median's interval is 0.16 of the median on average
(v1: 0.55, about 3.4 times wider), and the largest leave-one-developer-out move of a published cell is 2.9%
(v1: 9.4%). v1 is the replication, and it agrees in direction but not exactly: the v2 medians are 1.2 to 1.5 times
the v1 medians, and a v2 median falls inside the v1 interval for research-recon only (debug-fix and infra-deploy
fall just outside). The bands differ between dataset releases; the later, larger sample is the better estimate of
that dataset's population, which is not a claim about yours.

## Why tokens per turn

Three ways to normalise a session were compared **before** the table was built, on the same developer-weighted
data, by how much developers differ from one another (smaller is a more stable reference): the largest
leave-one-developer-out shift of the median, the inter-quartile range across developer medians divided by their
median, and the width of the median's bootstrap interval divided by the median. Mean over the five task types:

| Dataset | Normaliser | Largest leave-one-out shift | IQR of developer medians / median | Interval width / median |
|---|---|---|---|---|
| v1 | tokens per session | 20.9% | 1.58 | 1.07 |
| v1 | **tokens per turn** | **11.7%** | **0.70** | **0.55** |
| v1 | tokens per tool call | 14.5% | 1.11 | 0.78 |
| v2 | tokens per session | 2.2% | 2.04 | 0.43 |
| v2 | **tokens per turn** | **1.2%** | **0.79** | **0.16** |
| v2 | tokens per tool call | 1.7% | 0.89 | 0.22 |

Tokens per turn was the most stable on all three measures in both datasets. A whole session is dominated by how
long it ran; per turn removes that, and unlike per tool call it needs nothing but the turn count the tool
already records.

## What it does not tell you

* It is the percentile among **public repositories of developers who use the Entire CLI**, not all Claude Code
  users, and the population is its own: a heavy user can sit high on it without anything being wrong.
* Each developer is weighted equally within a task type, so a prolific developer cannot set it. Sessions of one
  developer are not independent; the intervals resample developers, not sessions.
* The task type is the tool's own rule (`classify_session`), applied to both populations. A session that the rule
  puts in another type is compared with that type.
* A high percentile means more tokens per turn than most of that population, not waste; a low one is not
  efficiency. Use the waste and cost readings, and your own baseline, to decide.

## Reproduce

```bash
python scripts/extract_swechat_sessions.py --raw <swechat dir> --out /d/scratch/extract.jsonl
python scripts/build_multidev_bands.py --extract /d/scratch/extract.jsonl --reference-id swechat-v2 --dataset-version v2
```

The table does not depend on the developer pseudonyms: two independent extracts of the same data give a
byte-identical file (`tests/test_build_multidev_bands.py`).
