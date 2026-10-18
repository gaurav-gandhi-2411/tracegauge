# Cache churn

When Claude Code resumes a prompt it normally *reads* the already-cached prefix at a fraction of the
input price. Sometimes the prefix is not found and the whole thing is *written* again. Those
re-writes are the "cache churn". `tracegauge score` finds them in a session transcript, says how much more
they cost than reading would have, and says what the transcript lets it say about why.

Nothing here is stored: the analysis is computed from the session's JSONL (and its `subagents/`
transcripts) each time you run `tracegauge score` or open the session in the dashboard.

## What counts as an event

The unit is a **chain**: one cache lineage, i.e. the main transcript or ONE subagent transcript. A
subagent's first request does not read its parent's cache, so chains are never mixed. Inside a chain,
turn `t` (one API response, de-duplicated by `message.id`) is an event when all of these hold:

1. `C(t-1) >= 5,000`, where `C = cache_read + cache_creation` of the previous turn (a prefix worth
   preserving existed);
2. `cache_read(t-1) > 0` and `cache_read(t) < 0.5 * cache_read(t-1)` (the read collapsed);
3. `cache_creation(t) > 0.2 * prompt(t)`, where `prompt = input + cache_creation + cache_read` (a
   large share of this prompt was written again).

Turn 0 of a chain has no predecessor and is never an event. A big read of a new file with the old
prefix intact fails condition 2 and is not an event: that is growth, not churn. The constants are
named in `tracegauge/cache_churn.py` (`MIN_PREFIX_TOKENS`, `READ_DROP_RATIO`, `REWRITE_SHARE`). On the
maintainer's own sessions the ratio `cache_read(t) / C(t-1)` was bimodal (nothing between 0.3 and
0.9), so the event set barely moves with the thresholds (the maintainer's unpublished local
measurement: -2.1% / +0.2% events when all three were made stricter / looser together).

**Churn tokens** are `min(cache_creation(t), max(0, C(t-1) - cache_read(t)))`: only the part of the
old prefix that had to be written again, not new conversation content. **Churn dollars** are those
tokens times (the turn's own blended cache-write rate minus the model's cache-read rate), priced with
the same price table `tracegauge cost` uses (per-model read multiplier, the 5-minute / 1-hour write tiers,
long-context tiers). A model that is not in the price table is **unpriced**: its events are counted,
shown as unpriced, and left out of the dollar total, which is then a floor. It is never `$0.00`.

## Causes

Each event is given at most one cause, from the first rule that matches:

| Cause | Rule |
|---|---|
| `COMPACTION` | A compaction marker (`system` record of subtype `compact_boundary`, or a `user` record flagged `isCompactSummary`) lies between the two turns. |
| `MODEL_SWITCH` | The model of turn `t` differs from turn `t-1` (each model has its own cache). |
| `TTL_GAP` | The gap between the two requests exceeded the cache lifetime in force. |

Everything else is reported in an **`UNEXPLAINED`** bucket with its dollars and **no cause claim**.
Other things that can invalidate a prefix (a changed tool set, image eviction, server-side context
handling) leave no marker in the transcript, so the tool does not guess.

The gap is measured start to start (the timestamp of the user or tool-result record that triggered
each request, falling back to the assistant record's own timestamp). The lifetime in force is
inferred from the most recent turn that wrote to the cache and records the 5-minute / 1-hour split
of its writes (`usage.cache_creation`): 1 hour if at least half of its writes were 1-hour writes,
otherwise 5 minutes. Turns without that split are never assumed to be 5-minute writes; if the chain
has no turn with the split at all, the lifetime is unknown (see Limits). A gap is over the lifetime when it is strictly greater than
it (`TTL_GRACE_S` in `tracegauge/cache_churn.py` can add a margin; the default is 0, the documented
boundary). Transcript timestamps are when records were written, not when requests arrived, so the
5-minute edge is blurred over roughly 300 to 360 seconds: on the maintainer's snapshot a 30 s grace
would move some `TTL_GAP` events with a gap in (300, 330] s to `UNEXPLAINED` (the maintainer's
unpublished local measurement; the count is not stated here).

## Avoidable or not (a judgement)

The output separates a *plausibly avoidable* part from the rest. This is a judgement, not a
measurement, and the output says so:

* plausibly avoidable: a `TTL_GAP` of 5 minutes to 1 hour on a chain whose cache lifetime is 5
  minutes (a longer lifetime would have kept it), and any `MODEL_SWITCH`;
* not avoidable by a setting: a gap of more than 1 hour, and `COMPACTION`;
* neither: `UNEXPLAINED`.

## TTL advice, priced per session

Claude Code's documentation names the controls for the cache lifetime: `promptCacheTtl` /
`CLAUDE_CODE_PROMPT_CACHE_TTL` for the main conversation and `subagentPromptCacheTtl` /
`CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` for everything else (subagents, compaction, forks), each `5m`
or `1h`, in Claude Code 2.1.242 and later (same page, retrieved from the vendor documentation; the exact retrieval date is not recorded). A 1-hour lifetime is not free: 1-hour writes cost 2.0x the
input price instead of 1.25x.

So tracegauge never prints a generic "use 1h". For each session it prices the counterfactual:
`premium` = every 5-minute write in the scope repriced at the 1-hour rate, `saved` = the churn of the
plausibly-avoidable 5-minute-tier `TTL_GAP` events (assumed to become reads), `net = saved - premium`.
The advice appears only when `net > 0` for that session, states the net dollars and its assumptions,
and is withheld when any model in the scope is unpriced. It does not model second-order effects
(another request refreshing an entry, sibling subagents sharing one).

On the maintainer's 81 sessions the aggregate answer for subagents was negative: repricing all 5-minute
subagent writes cost about $480.72 against about $316.76 of avoided re-writes (net about -$163.96), and
the net was positive in only 3 of the 45 sessions that had subagent writes (the maintainer's
unpublished local measurement; a single user and machine). That is why the advice is per session.

## What the dashboard and JSON show

* `tracegauge score`: a short CACHE CHURN section after the waste section, only when there are events.
* `tracegauge score --json`: a `cache_churn` object with a fixed key set, see [JSON_OUTPUT.md](JSON_OUTPUT.md).
* The dashboard session page: a "Cache churn" card with the totals, the split by cause and by scope
  (main chain vs subagents), the largest events with their two proof turns, and any priced advice. It
  says so when the transcript is not available.
* `tracegauge cost` is unchanged.

Every event carries its two proof turns (`turn k-1` and `turn k`): `cache_read`, `cache_creation`,
`cache_creation_1h`, the model, the transcript record position and the API message id, plus the gap in
seconds and the lifetime used. No transcript text is ever included.

## Limits

* The transcript has no per-request lifetime, no cache breakpoints and no system prompt or tool list.
  Changes there show up only as `UNEXPLAINED`.
* Timestamps are record write times; see the blur above.
* The summarisation request of a compaction, and other helper requests that are not written to the
  transcript (titles, forks), are invisible, so compaction cost is understated: only the rebuild
  after it is seen.
* The cache is shared by parallel sessions in the same directory, so a chain's reads can be refreshed
  by a session the transcript does not show. A gap-based cause is a proxy.
* The 1-hour main / 5-minute subagent pattern is what a subscription session writes. A session that
  uses an API key writes 5-minute entries everywhere and will show a different mix.
* Records without the `usage.cache_creation` split (older Claude Code versions, other writers) have
  an unknown cache tier. Their writes are carried as "unsplit" (`unsplit_cache_write_tokens` in the
  JSON), are left out of the 5-minute premium of the advice and out of the lifetime inference. When
  a chain has the split on some turns, the tier comes from those turns (the nearest earlier one, else
  the first later one). When a chain has it on none, the lifetime is unknown: a gap over 5 minutes is
  reported as `UNEXPLAINED` with avoidability `unknown` and a note saying why, never as a `TTL_GAP`
  and never as plausibly avoidable, and no TTL advice is built from it. The dollar figure of such an
  event still prices the re-write at the 5-minute rate, so it is a lower bound if those writes were
  1-hour writes.
* Subagent transcripts are the files `<session>/subagents/agent-*.jsonl`, the same flat set `tracegauge cost`
  reads; a file nested deeper under `subagents/` is in neither the cost nor the churn figures.
* A message that appears in two transcript files of one session is counted once.
* The `/usage` command of Claude Code adds a `Prompt cache (main)` line with the session hit ratio
  and miss count, and that line names a `likely cause` for the last miss when Claude Code can
  identify one (the likely-cause text needs Claude Code v2.1.260 or later; source: [How Claude Code
  uses prompt caching](https://code.claude.com/docs/en/prompt-caching.md), retrieved from the vendor documentation; the exact retrieval date is not
  recorded).
  It covers the last miss of the main conversation. tracegauge works from the transcript instead
  and adds the history across sessions, dollars per session, subagent chains and the priced
  break-even above.

## What the detector cannot explain

The share of events that end up `UNEXPLAINED` does **not** generalise from one user to others.

* On the maintainer's own sessions, 3.2% of churn events (32 of 993, main and subagent chains
  together) were `UNEXPLAINED`.
* Over a cross-developer corpus of public transcripts (1,053 transcripts from the SWE-chat dataset,
  which tracegauge does not redistribute), 449 of 1,318 events (34.1%) were `UNEXPLAINED` (the maintainer's unpublished local measurement). The
  cluster-bootstrap 95% interval for that 34.1% is [26.4, 44.2] (the maintainer's unpublished local measurement), resampling developers as clusters
  with each session that carries no user id as its own cluster.

Both are the maintainer's unpublished local measurements: they are not reproducible from this
repository, each is a single run, and the second corpus is public transcripts from other
developers' sessions, not a controlled experiment.

No mechanism was identified for the larger residual. The best candidate, a human prompt arriving
inside the cache lifetime, only describes *when* the loss happens: it covers 77% of those events
(the maintainer's unpublished local measurement), but only about 5% (the maintainer's unpublished
local measurement) of the inside-lifetime pairs with a human prompt are events, and no documented
mechanism says why a human prompt would lose the prefix. Eleven invalidators that are visible in a
transcript (permission or plan mode change, thinking setting change, Claude Code version change,
microcompact, image in the prompt, history branch, working directory or git branch change, API
error, local command, many new content blocks, model change) together explain only 34% of the 449 `UNEXPLAINED` events (153 of 449; the
maintainer's unpublished local measurement; the 449 is the SWE-chat figure above). The model-change check is one of the eleven but cannot
be an `UNEXPLAINED` event by construction, so the union covers ten invalidators within
`UNEXPLAINED`. Transcripts hold usage counters, not requests: the system prompt, tool list, cache
breakpoints and routing are not in them, so the transcript cannot settle the question.

The shipped detector therefore labels every such event `UNEXPLAINED`, prices it, and never asserts a
cause for it. Read an `UNEXPLAINED` figure as "the cache read collapsed and nothing recorded in the
transcript says why", not as a defect of any particular setting.

## Controlled runs

Two small runs of unmodified `claude -p` (Claude Code 2.1.302 for the second, Sonnet 5.5, a Claude
subscription login, a separate working directory so no other session shares the cache) were made
to see what the transcript lets a rule say. Both are the maintainer's unpublished local
measurements: one user, one machine, one run each, 16 calls in all, not reproducible from this
repository. Dollars are the cache numbers priced with the tool's own price table, not a bill.

**Lifetime (6 calls, 2026-10-11).** Two series of three calls on the same session: a seed, a
resume inside the lifetime, a resume past it. On the 5-minute tier (`CLAUDE_CODE_PROMPT_CACHE_TTL=5m`)
the resume after 6 minutes read 16,280 tokens (the system and tool prefix only) and wrote 53,753; on
the 1-hour tier the resume after 65 minutes read 16,155 and wrote 49,099. So the `TTL_GAP` rule
reproduced in 2 of 2 series, and the lifetime in force was the one the `usage.cache_creation` split
reported.

**Settings changed inside the lifetime (10 calls, 2026-10-18).** One session, resumed nine times,
each call about 41 to 44 seconds after the previous one (start to start). The only difference we
introduced from the call before is the one listed. Every resume also adds a reminder attachment and
runs the maintainer's own session-start hook, whose text changes, so the conditions are similar,
not identical, and each variant was run once. "Cache read before" is the previous request's read.

| Call | Change | Cache read before → after | Written | Read share of previous prefix | Collapse? |
|---|---|---|---|---|---|
| 2 | none (control A) | 16,405 → 45,247 | 19,369 | 0.70 | partial, see below |
| 3 | none (control B) | 45,247 → 64,479 | 296 | 1.00 | no |
| 4 | plan mode on | 64,479 → 64,638 | 2,932 | 1.00 | no |
| 5 | plan mode off | 64,638 → 64,797 | 2,969 | 0.96 | no |
| 6 | `MAX_THINKING_TOKENS` 8000 (no effect on the request, see below) | 64,797 → 67,592 | 333 | 1.00 | no |
| 7 | `MAX_THINKING_TOKENS` 0 (no effect on the request) | 67,592 → 67,788 | 296 | 1.00 | no |
| 8 | hook output added | 67,788 → 67,947 | 318 | 1.00 | no |
| 9 | hook output changed | 67,947 → 68,106 | 347 | 1.00 | no |
| 10 | none (control C; same settings as call 9) | 68,106 → 68,287 | 354 | 1.00 | no |

Findings:

* None of the changes that were actually made collapsed the read (plan mode on and off, a hook
  output added and then changed; one run each, one machine, short gaps). The transcript shows a
  `plan_mode` / `plan_mode_exit` attachment and a `hook_success` attachment between the turns, and
  the request shape hash was identical in all ten calls, so these changes appended text to the
  conversation rather than altering the prefix; that is consistent with the cache staying warm, not
  a tested mechanism.
* The thinking toggle did not change the request: the transcript's request record shows the same
  adaptive thinking parameter in all ten calls, so that row says nothing about thinking. Whether a
  real thinking change collapses the read was **not** tested.
* The first resume of a `-p` session read only 70% of the previous prefix and wrote 19,369 tokens
  again (about $0.082, against about $0.008 for the controls). It happened in both series of the
  first run too (read 44,473 of 62,873 and 44,348 of 62,748, tokens), so 3 of 3 first resumes. The
  transcript carries no marker that tells it apart from the controls that followed, and 0.70 is above
  the detector's 0.5 read-drop threshold, so it is not counted as an event and is not an
  `UNEXPLAINED` event. It is described here because it is the largest rewrite the runs produced that
  no marker explains (the 5-minute lifetime expiry above rewrote more, but that one has its cause);
  it was seen only on `--resume` of a `-p` session and says nothing about interactive use.
* A cause rule needs a mechanism that reproduces in 2 of 2 runs and is identifiable from transcript
  markers. None met that bar, so no cause rule was added and every such event stays `UNEXPLAINED`.

**A marker worth knowing about.** Claude Code 2.1.300 and later write an `api-request` record per
request with a hash of the request's shape (system prompt, tools, parameters) and of each message.
If those records become common, a change in the shape hash between two turns would identify a
prefix change the older transcripts cannot show. Of the maintainer's 77 local main transcripts only 9
(versions 2.1.300 to 2.1.302, one of them the controlled run above) carry them, and none of those
has an `UNEXPLAINED` event, so there is nothing yet to test a rule on and none is shipped.

## Validation status

* **Measured from usage fields:** whether an event happened, its tokens and its dollars are read
  directly from the `usage` numbers in the transcript.
* **Not yet validated:** the *cause*. `TTL_GAP`, `MODEL_SWITCH` and `COMPACTION` are rules applied to
  markers and timestamps. On the maintainer's snapshot all 40 eligible post-compaction turns were events
  and the event rate steps sharply at the documented lifetimes (the maintainer's unpublished local
  measurement), which is consistent with the rules but is not a controlled test. The `TTL_GAP` cause
  has a small controlled run behind it (next section); a labelled sample for the other causes is still
  to be done.
* **Cause check by three language models (LLM-consensus agreement, not ground truth):** on 160 sampled
  events three language models, shown only derived numbers and never the detector's label, agreed with the
  detector's cause on 158 (98.8%, Wilson 95.6 to 99.7), Fleiss kappa 0.865. Per judge and per cause below.
  The cards hold the same facts the rules use, so this checks that the rules follow from the documented
  mechanisms, not that the physical cause is right. See [Per-judge agreement](#per-judge-agreement).
* **Controlled runs (the maintainer's unpublished local measurement, 16 calls in total):** see
  [Controlled runs](#controlled-runs) below. They confirm the lifetime rule; the few settings that
  were changed did not make the cache collapse inside the lifetime (thinking was not really varied).
* **Single user:** all numbers above come from one person's sessions on one machine. They show that
  the detector fires and what it costs there, not how common churn is for other users.
* **Tests:** the unit tests use synthetic transcripts with injected gaps, switches and compactions
  (`tests/test_cache_churn.py`). The seeded-injection recall there is a regression check of the code
  on clear-cut synthetic cases: the generator shares the detector's ideas and constants and has no
  near-misses, so a high recall is not an accuracy estimate and says nothing about real sessions.
* **Harder synthetic cases (observations, not tuned):** the same test module plants 240 borderline
  cases per mode (60 seeded chains, 4 each) and records what the detector does. A cache lost after
  an idle gap of 300 to 360 s on a 5-minute chain: 240 of 240 are found and all 240 are `TTL_GAP`
  (every such gap is strictly over 300 s); with `ttl_grace_s=30` they split 118 `TTL_GAP` and 122
  `UNEXPLAINED`, which is the blur of the timestamps showing, not an error rate. A read kept at
  0.40 to 0.499 of the previous read (just below the 0.5 ratio): 240 of 240 found, all
  `UNEXPLAINED` (no marker explains a partial loss). A read kept at 0.50 to 0.60 (just above): 0 of
  240 found. The last two show the threshold, which is a definition: a partial loss on either side
  of 0.5 is the same physical event, and only one side is counted. In the maintainer's real
  sessions no pair fell between 0.3 and 0.9 of the previous prefix, so this edge is rarely
  exercised there (the maintainer's unpublished local measurement).

## Per-judge agreement

The sample is 160 events (40 per cause, at most 2 per session within a cause, seed 42) from the maintainer's
local sessions (29) and 1,051 public SWE-chat sessions (131); the maintainer's unpublished local measurement
for the local part. Each judge saw the same cards in its own order, with the label order shuffled, at
temperature 0, and had first labelled at least 90% of 40 seeded unambiguous cards correctly (Claude 40/40,
gpt-oss-120b 40/40, qwen3:8b with thinking on 40/40; with thinking off it scored 30/40 and was not used). Wilson 95%
intervals; the intervals treat events as independent although up to 4 come from one session.

| Judge | `TTL_GAP` | `MODEL_SWITCH` | `COMPACTION` | `UNEXPLAINED` | All |
|---|---|---|---|---|---|
| Claude (`claude -p`) | **25/40 = 62.5%** (47.0 to 75.8) | 40/40 = 100% (91.2 to 100) | 40/40 = 100% (91.2 to 100) | 38/40 = 95.0% (83.5 to 98.6) | 143/160 = 89.4% (83.6 to 93.3) |
| gpt-oss-120b | 40/40 = 100% (91.2 to 100) | 38/40 = 95.0% (83.5 to 98.6) | 39/40 = 97.5% (87.1 to 99.6) | 39/40 = 97.5% (87.1 to 99.6) | 156/160 = 97.5% (93.7 to 99.0) |
| qwen3:8b (thinking) | 38/40 = 95.0% (83.5 to 98.6) | 37/40 = 92.5% (80.1 to 97.4) | 40/40 = 100% (91.2 to 100) | 40/40 = 100% (91.2 to 100) | 155/160 = 96.9% (92.9 to 98.7) |
| **Majority of the three** | 40/40 = 100% (91.2 to 100) | 38/40 = 95.0% (83.5 to 98.6) | 40/40 = 100% (91.2 to 100) | 40/40 = 100% (91.2 to 100) | **158/160 = 98.8%** (95.6 to 99.7) |

Pairwise Cohen kappa: Claude with gpt-oss 0.826, Claude with qwen3 0.818, gpt-oss with qwen3 0.950. All three
judges agreed with each other on 136 of the 160 events. A judge's invalid answers count as disagreement for that
judge (gpt-oss 1, qwen3 4); a vote with no majority counts as a miss for the majority.

**Why Claude is at 62.5% on `TTL_GAP`.** It labelled 15 of the 40 `TTL_GAP` events `UNEXPLAINED`, including events
whose gap was hours against a one-hour lifetime. Asked to add a reason for six of those cards (a diagnostic, not part of the
sample), on four it gave the same reason (on the other two it labelled `TTL_GAP` when asked again, so single answers are not perfectly repeatable): the current request still read part of the prefix from the cache (for
example 31,740 tokens), which it took to be inconsistent with the whole prefix having expired. The card
did not say that a short shared prefix (the system prompt, the tool definitions) can still be read while the
longer conversation part after it is written again, so this is most likely a gap in the card rather than a Claude error. That rests on the reasons given on four
cards and was not tested separately; the other judges did not stumble on it.

**The card change and the partial re-run (not used in the figures).** One neutral sentence about partial reads was
added to the glossary of the cards (a short shared prefix such as the system prompt and tool definitions can still be
read while the longer conversation part after it is written again), and Claude was re-run on the same cards. The
re-run was stopped by the system after 60 of the 160 cards (low memory), and it has deliberately not been completed or
resumed, so it is a partial result. The same 60 cards, Claude's answers agreeing with the detector, original cards
then card with the sentence:

| Cause (detector label) | Original cards | Card with the sentence |
|---|---|---|
| `TTL_GAP` | 11/19 | 19/19 |
| `MODEL_SWITCH` | 16/16 | 16/16 |
| `COMPACTION` | 11/11 | 11/11 |
| `UNEXPLAINED` | 13/14 | 9/14 |
| All 60 | 51/60 | 55/60 |

12 of the 60 answers changed. The sentence fixed the `TTL_GAP` misses and also pushed Claude to call some
no-mechanism events `TTL_GAP` (it gave `TTL_GAP` 24 times against 19 events with that detector label), so it
trades one error for another. The 60 are the first 60 cards of a seeded shuffle of the 160, so they are a
random subset, but 11 to 19 events per cause is too few for a per-cause precision estimate. All figures in the
table above, and the consensus 158/160, are for the cards as originally used.
