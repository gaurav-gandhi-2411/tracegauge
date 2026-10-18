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

Both populations are the maintainer's unpublished local measurement, taken on master (commit 8f2b50d) on
2026-10-18 and recomputed the same day after the maintainer's sessions from three projects were removed from
the local population; the commands are `tracegauge score <path> --no-judge --json`, one process per session.

| Population (sessions with priced usage) | n | T = $0.10 | **T = $0.50** | T = $1 | T = $5 |
|---|---|---|---|---|---|
| One developer's local Claude Code sessions | 47 | 68.1% | **68.1%** (32 of 47; Wilson 53.8 to 79.6) | 61.7% | 0.0% |
| SWE-chat Claude Code sessions (public data, 337 developer clusters) | 1,051 | 35.8% | **18.7%** (Wilson 16.5 to 21.2; developer-cluster bootstrap 13.9 to 27.6) | 7.2% | 0.5% |

Read these with the caveats:

* **The first row is one person on one machine** (12 projects, the sessions that survived local retention). It
  says what that user sees, not how common the finding is. A project-cluster bootstrap gives 52.5 to 80.6%
  but rests on 12 clusters; leaving out any one project keeps the headline between 64.3 and 71.1%. Of the 51
  local sessions scored, 4 are not in the denominator (3 with no usage at all, 1 with no price).
* **The second row has clustered sessions.** One developer holds 25% of the SWE-chat sessions, and 280
  sessions with no user id are treated as separate clusters, which makes the bootstrap interval somewhat too
  narrow. Use the bootstrap interval, not the Wilson one, for that row. Leaving out any one cluster keeps the
  headline between 18.0 and 22.6%.
* **Almost all of it is cache churn.** At T = $0.50 the one developer's 32 qualifying sessions all qualify through
  churn (at least 157 churn events of $0.50 or more, no waste event; the output lists only the ten largest events per session, so this is a lower bound); in SWE-chat 197 sessions qualify, all through churn
  (2 of them also have a waste event). The waste detectors are deterministic and conservative; in these sessions
  they rarely reach $0.50. A waste finding of an identical-read pair (RR-A) is priced at $0 by definition.
* **"Actionable" is not "avoidable".** The count includes churn the tool itself labels not avoidable by a setting
  (a gap of more than an hour, a compaction). The avoidable share of these sessions was not measured, so 68%
  means "has a cache-churn event of at least $0.50", not "has something you can fix". See
  [CACHE_CHURN.md](CACHE_CHURN.md).
* Waste dollars (the cost of the redundant turns) and churn dollars (the read-to-write premium) are different
  quantities, summed only to decide whether a session crosses the threshold.

## Do independent readers agree with the cause the tool names?

`tracegauge score` gives each churn event a cause (`TTL_GAP`, `MODEL_SWITCH`, `COMPACTION` or `UNEXPLAINED`)
from rules over the transcript. To see whether the rules match a reader's reading of the documented
mechanisms, three language models from different families were each given only derived numbers and markers of
an event (the gap, the cache lifetime in force, the cache read and write of both requests, the two models,
whether a compaction marker lies between; no transcript text, path or id) and a neutral glossary, never the
tool's label, and asked for one of the four labels. The label order and the card order were shuffled per judge,
temperature 0.

* **Judges:** Claude through an unmodified `claude -p`; `openai/gpt-oss-120b` on a free tier; local qwen3:8b with
  thinking on. Each had to label at least 90% of 40 seeded, unambiguous cards correctly before it was used: all
  three scored 40/40 (qwen3:8b with thinking off scored 30/40 on the same cards and was not used in that mode).
* **Sample:** 160 events, 40 per cause, at most 2 per session within a cause (so up to 4 per session across the
  four causes; 100 sessions contribute one event, 14 two, 8 three and 2 four), drawn at random (seed 42) from the
  1,926 events of the two populations above (131 from SWE-chat, 29 from the local sessions).

| Cause the tool named | Events | Majority of the judges agreed | Wilson 95% |
|---|---|---|---|
| `TTL_GAP` | 40 | 40 (100%) | 91.2 to 100 |
| `MODEL_SWITCH` | 40 | 38 (95%) | 83.5 to 98.6 |
| `COMPACTION` | 40 | 40 (100%) | 91.2 to 100 |
| `UNEXPLAINED` | 40 | 40 (100%) | 91.2 to 100 |
| All | 160 | 158 (98.8%) | 95.6 to 99.7 |

Fleiss kappa across the three judges is 0.865 (pairwise Cohen kappa 0.818 to 0.950). Each judge alone agreed
with the tool on 143 (Claude), 156 (gpt-oss-120b) and 155 (qwen3:8b) of the 160. The one visible difference: Claude
labelled 15 of the 40 `TTL_GAP` events `UNEXPLAINED`, including events whose gap was hours against a one-hour
lifetime; the other two judges agreed on 40 and 38 of them. All three judges agreed on 136 of the 160 events.

What this shows: the causes the tool names follow from the facts it displays, as three readers who never saw the
tool's label read them. What it does not show: that the physical cause is right. The cards hold the same facts
the rules use, so agreement is close to a consistency check of the rules; `UNEXPLAINED` means the card holds no
mechanism, and the judges found none either. On the 6 events where two mechanisms apply at once the majority
agreed with the tool on 4. The intervals treat events as independent although up to 4 come from one session.
This is LLM-consensus agreement, not ground truth, and the tool's cause remains "a rule on transcript markers
and timestamps, not a verified fact".

## Repeat it

```bash
tracegauge score <session.jsonl> --no-judge --json   # waste_events (with wasted_cost_usd) and cache_churn.events
```

Count the sessions where the largest priced finding is at least your threshold. `score --json` lists the ten
largest churn events per session, which is enough to decide whether a session crosses a threshold but not to
sum its dollars.
