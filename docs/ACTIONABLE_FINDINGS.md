# How often does a session have a finding worth acting on?

Two questions a first-time reader asks: "will `tracegauge score` tell me anything about my sessions?" and "is
that finding real money?". This page measures the first, says what it does and does not show about the
second, and states how it was measured so you can repeat it on your own sessions.

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
> which is made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/).
> See [DATA_SOURCES.md](../DATA_SOURCES.md).

## Definition

A session has an **actionable finding** at threshold T when it has at least one finding of at least T dollars
that comes with its proof turns:

* a **waste event** from the deterministic detectors (redundant reads, retry loops), with the dollar
  annotation `tracegauge score` prints (the cost of the redundant turns only); or
* a **cache-churn event**, priced, with its two proof turns (the dollars are the premium of writing the prefix
  again over reading it, see [CACHE_CHURN.md](CACHE_CHURN.md)).

Unpriced events do not count. A session counts once however many findings it has. "At least" means `>=`.
The headline threshold is T = $0.50. The dollars are API-equivalent list prices from the bundled price table,
not what a subscription bills.

## Result

Both populations are the maintainer's unpublished local measurement on master (commit 8f2b50d) at the
time of writing (2026-10-18); the commands are `tracegauge score <path> --no-judge --json`, one process per session.

| Population | Sessions (priced) | T = $0.10 | **T = $0.50** | T = $1 | T = $5 |
|---|---|---|---|---|---|
| One developer's local Claude Code sessions | 67 | 67.2% | **64.2%** (Wilson 52.2 to 74.6) | 58.2% | 0.0% |
| SWE-chat Claude Code sessions (public data, 337 developer clusters) | 1,051 | 35.8% | **18.7%** (Wilson 16.5 to 21.2; developer-cluster bootstrap 13.9 to 27.6) | 7.2% | 0.5% |

Read these with the caveats:

* **The first row is one person on one machine** (15 projects, the sessions that survived local retention). It
  says what that user sees, not how common the finding is. A project-cluster bootstrap gives 54.1 to 73.1%
  but rests on 15 clusters.
* **The second row has clustered sessions.** One developer holds 25% of the SWE-chat sessions, and 280
  sessions with no user id are treated as separate clusters, which makes the bootstrap interval somewhat too
  narrow. Use the bootstrap interval, not the Wilson one, for that row. Leaving out any one cluster keeps the
  headline between 18.0 and 22.6%.
* **Almost all of it is cache churn.** At T = $0.50 the one developer's 43 qualifying sessions are all churn (395 churn events, no
  waste event); in SWE-chat 197 sessions qualify, all through churn (2 of them also have a waste event). The waste detectors are
  deterministic and conservative; in these sessions they rarely reach $0.50. A waste finding of an
  identical-read pair (RR-A) is priced at $0 by definition.
* **"Actionable" is not "avoidable".** The count includes churn the tool itself labels not avoidable by a setting
  (a gap of more than an hour, a compaction). The avoidable share of these sessions was not measured, so 64%
  means "has a cache-churn event of at least $0.50", not "has something you can fix". See
  [CACHE_CHURN.md](CACHE_CHURN.md).
* Waste dollars (the cost of the redundant turns) and churn dollars (the read-to-write premium) are different
  quantities, summed only to decide whether a session crosses the threshold.

## Do independent readers agree with the cause the tool names?

`tracegauge score` gives each churn event a cause (`TTL_GAP`, `MODEL_SWITCH`, `COMPACTION` or `UNEXPLAINED`)
from rules over the transcript. To see whether the rules match a reader's reading of the documented
mechanisms, three local language models of different families were each given only the transcript-visible
numbers of an event (gap, cache lifetime in force, the cache read and write of both requests, the models, whether
a compaction marker lies between) and a neutral glossary, never the tool's label, and asked for one of the four
labels. The sample is 60 events (20 `TTL_GAP`, 20 `COMPACTION`, 10 `MODEL_SWITCH`, 10 `UNEXPLAINED`), temperature 0.

| Judge | Agreement with the tool's label | Note |
|---|---|---|
| llama3.1 8B | 20/60 = 33% (Wilson 22.7 to 45.9) | answered `TTL_GAP` on every card |
| gemma3 4B | 20/60 = 33% (22.7 to 45.9) | `TTL_GAP` on 50 cards, `UNEXPLAINED` on 10 |
| qwen2.5 14B | 58/60 = 97% (88.6 to 99.1) | uses all four labels |

Fleiss kappa across the three is -0.11: the two small models do not read the cards, so the three-family
majority vote is uninformative and is not reported as support. A fourth, Mistral-family model run afterwards
agreed on 50/60: all of the `TTL_GAP`, `COMPACTION` and `MODEL_SWITCH` cards and none of the `UNEXPLAINED`
ones. What this shows: the three rule-defined causes are mechanically readable from the facts. What it does
not show: that the physical cause is right. The tool's labels are rule outputs from the same facts the judges
see, and `UNEXPLAINED` is exactly where competent judges split, because the card holds no cause. This is
LLM-consensus agreement, not ground truth.

## Repeat it

```bash
tracegauge score <session.jsonl> --no-judge --json   # waste_events (with wasted_cost_usd) and cache_churn.events
```

Count the sessions where the largest priced finding is at least your threshold. `score --json` lists the ten
largest churn events per session, which is enough to decide whether a session crosses a threshold but not to
sum its dollars.
