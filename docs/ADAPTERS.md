# Writing an adapter for another coding agent

The commands support Claude Code only. 0.18.0 adds an experimental, library-only reader for Codex CLI
rollouts (`tracegauge.codex_adapt.adapt_codex_session`, section (c)); the commands do not use it yet. An adapter reads one agent's session files and produces the
digest that the rest of tracegauge scores. This page says what an adapter has to produce, how the Claude
Code adapter does it, what is known about six other agents, how to test an adapter without a real
transcript, and what a pull request for one should contain. The plan and its order are in
[ROADMAP.md](../ROADMAP.md).

## How much of this to trust

The notes on other agents were read from each agent's public source and a few web pages on **2026-10-10**.
**No agent was run, and no transcript from any of them was read,** with one later exception: four real
Codex CLI rollouts were read on 2026-10-29, and what they showed is tagged OBSERVED in the Codex status
subsection. Every other claim about another agent carries one of two tags:

- **VERIFIED**: read in that agent's own source at the commit pinned below, with the file and line given.
- **UNVERIFIED**: everything else. That includes claims taken from a third-party parser or a web page,
  inferences, and claims that had a source reference but were not marked verified when the notes were
  written. Treat an UNVERIFIED claim as a lead to check against a real file, not as a fact.

A line number is only good for the commit it was read at. Agents change their formats without a schema
version, so a claim can be stale by the time you read it; check the agent's current source before you rely
on one.

| Agent | Repository | Pinned commit |
|---|---|---|
| Codex CLI | `openai/codex` | `2f761ae8210082c21cdd471131fa2118680a6059` |
| Gemini CLI | `google-gemini/gemini-cli` | `fa2531d457208ef92029a11dd454bf56a3cd21ee` |
| OpenCode | `anomalyco/opencode` | `388406238bd5ca15564a762840a2362c3a45bd9c` |
| Aider | `Aider-AI/aider` | `5dc9490bb35f9729ef2c95d00a19ccd30c26339c` |
| Copilot CLI | `github/copilot-cli` | `dcdc0273c0250d8b32677ca99bc0e1f5006daa89` |
| Cline | `cline/cline` | `d20b337eb591d661b8927e288bb0034d53f1fabb` |
| ccusage (a third-party usage reader, used as a secondary source) | `ryoppippi/ccusage` | `c4fd2e8897713749b954d8c5b75c74f1c1afe69d` |

Paths such as `codex-rs/...` below are relative to the repository of the agent in the section.

## (a) What an adapter must produce

An adapter is a function from one session file to a plain dict: today
`tracegauge.adapt.adapt_session(path) -> dict`. The CLI calls it directly (`tracegauge/cli.py`); there is
no adapter registry or dispatch by agent yet, and file discovery (`tracegauge/discovery.py`) only looks
under `~/.claude/projects`. The first new adapter therefore also has to decide how a path is routed to it;
open an issue to agree on that shape before writing code.

The dict is the "layer 1 record" in [digest_schema.md](digest_schema.md), which is the reference for the
base fields and for what the judge reads. The dataclasses are `SessionDigest` and `TurnDigest` in
`tracegauge/_digest.py`. The tables below list what an adapter fills and what each field is used for.
They add the cost-related turn fields that `digest_schema.md` does not list yet.

### Per turn (`TurnDigest`)

| Field | Fill with | Used for |
|---|---|---|
| `turn_index` | 0, 1, 2, ... in file order | Naming proof turns in waste findings and cache-churn events. |
| `role` | `"ai"` for a model response, `"user"` for a human message, `"tool"` for a tool result | Waste detectors (they pair an `ai` turn that called a tool with the `tool` turn that answered it) and the judge text. |
| `tool_names` | Names of the tools the response called, `[]` if none | Waste detectors (`Read`, the shell and write tools are matched by name in `tracegauge/_waste_detectors.py`) and the judge text. |
| `content_snippet` | First 300 characters of the text, secrets redacted | Waste detectors compare snippets of tool results; the judge cites them. |
| `token_count_input` | For an `ai` turn: fresh input + cache-creation + cache-read tokens. `0` for other roles. | Total and real tokens; cost. |
| `token_count_output` | Output tokens of the response (include reasoning tokens only if the agent bills them as output) | Total and real tokens; cost. |
| `cache_read` | Cache-read tokens (a subset of `token_count_input`) | Real tokens (`token_count_input - cache_read + token_count_output`), cache hit rate, cost. |
| `cache_creation` | Cache-write tokens (a subset of `token_count_input`) | Cost. |
| `cache_creation_1h` | Part of `cache_creation` written with the 1-hour lifetime | Cost: 1-hour writes are priced above 5-minute writes. |
| `cache_creation_tier_known` | How many of the `cache_creation` tokens the 1-hour/5-minute split accounts for | Cost: writes of unknown tier are priced at the 5-minute rate, a floor. |
| `model` | The model id string of this turn's response, unmodified | Cost (price lookup; a model not in the price table is shown as unpriced, never `$0`). |
| `server_tool_use` | Counts such as `{"web_search_requests": 2}` or `None` | Cost (web search is priced; kinds that cannot be priced produce a warning). |
| `speed`, `inference_geo` | Per-request billing modifiers: `"fast"` / `"us"`, else `""` | Cost: they scale the whole request. |
| `request_count` | `1` for a real turn; more only for an aggregated subagent turn | Cost for models priced per request tier. |
| `h2_duplicate` | `False` | Reserved; no adapter sets it. |

### Per session (`SessionDigest` and the surrounding record)

| Field | Fill with | Used for |
|---|---|---|
| `session_id` | The file's stem or the agent's own id | Store key, display. |
| `task_description` | First real user message, first 800 characters, secrets redacted | Task-type classification (`tracegauge/classify.py`) and the judge. |
| `turns` | Every turn above, in order | Everything. |
| `total_tokens` | Sum over AI turns of fresh input + cache-creation + cache-read + output | Display and the record's `total_tokens`. |
| `turn_count` | `len(turns)` | Scope gates in the token-economy verdict. |
| `cache_hit_rate` | `sum(cache_read) / max(1, billed input)` | Display. |
| `subagent_turns`, `subagent_count` | Usage of subagent files, kept out of `turns` | Cost and attribution add them on top; `real_tokens` and the verdict stay main-chain. |
| `usage_records`, `usage_records_deduped` | Records that carried usage, and the number of distinct responses among them | The "usage counted once" check shown to the user. |
| `edit_operations` (record level) | Per-file additions/deletions from edit tool calls | `tracegauge impact`. |
| `adapter_version`, `cost_version` (record level) | Integers owned by the adapter | The store uses them to tell rows produced by older accounting from current ones and to re-score them. Bump `ADAPTER_VERSION` when the token accounting of an adapter changes. |
| `labeler_model` | `"not_applicable"` | **Never `"missing"`**: the judge pipeline silently drops a record with that value. |
| `domain_id`, `test_outcome`, `p25_token_ratio`, `h2_duplicate_count` | `"unknown"`, `False`, `1.0`, `0` | Placeholders for fields the scoring math expects; a real session has no resolved/unresolved label. |
| `scaffold`, `token_economy_available`, `domain_inferred` | The agent's name, `False`, `"fallback_unknown"` | Carried in the stored record; nothing in `tracegauge/` reads them today. |

### Invariants

These are the properties the tests of the Claude Code adapter pin. A new adapter has to hold the same ones.

1. **One usage record per API response, counted once.** If the agent writes one record per content block
   and repeats the response's usage on each, add it once. If it writes streamed partials whose output
   count grows, take the final (maximum) value. If it writes cumulative totals, difference them, and
   ignore a repeated total. If it replays a parent session's usage into a fork, do not count it twice.
   Before the fix in 0.15.0, counting once per record made token totals about 2.4 times and costs about
   2.3 times too high ([CHANGELOG.md](../CHANGELOG.md)).
2. **Keep the turn structure when you dedupe usage.** The waste detectors work on one turn per
   transcript record. Charge the response's usage to one carrier turn (the Claude Code adapter chooses the
   last record that holds a tool call, else the last record) and give its sibling turns zero usage, rather
   than merging them.
3. **Cache reads and cache writes are separate, and writes carry their lifetime.** Report reads, writes,
   and the 5-minute/1-hour split of the writes. If the agent does not say, leave `cache_creation_tier_known`
   at `0` and let the cost module price the write at the 5-minute rate, which is the documented floor.
   Clamp a malformed split so it can never exceed the total written.
4. **The model is per turn.** A session can switch models. Copy the model string as written, even if
   it is not in the price table.
5. **Subagent or sidechain usage never inflates the main chain.** Keep it out of `turns`, roll it up
   separately, and make sure a response that appears in two files is counted once.
6. **A fresh-input figure that already includes cached tokens must be split.** `token_count_input` is
   fresh + write + read; if an agent's own `input` already includes the cached part, subtract it before
   you add the read and write fields, or cached tokens are counted twice. (The notes below report that
   Gemini CLI may behave this way; UNVERIFIED.)
7. **Timestamps stay in the transcript.** The digest has no timestamp field. The cache-churn detector
   (`tracegauge/cache_churn.py`) reads Claude Code records directly (`timestamp`, `isSidechain`,
   `message.id`, falling back to `requestId`, then `uuid`). A new adapter does not get a cache-churn section
   unless that detector is extended for its format, which is a separate piece of work.
8. **Fail closed on money.** An unknown model, a missing usage block or an agent that records usage only at
   the end of a session must produce an unpriced or partial result that says so, never a guessed or zero
   figure presented as a price.
9. **Redact secrets and bound text.** Snippets pass through `_redact_secrets` and are cut to 300 characters;
   the task description to 800.
10. **Tolerate a live file.** An agent may be writing the file while it is read: skip a blank or truncated
    line instead of failing.

## (b) How the Claude Code adapter does it

Everything is in `tracegauge/adapt.py`.

- **File layout.** One JSONL file per session under `~/.claude/projects/<project-folder>/`. Each line is a
  JSON record with a `type` (`user`, `assistant`, ...). `adapt_session` reads the file and skips lines that
  are not valid JSON (with a warning on stderr).
- **Usage deduplication.** Claude Code writes one assistant record per content block (thinking, text,
  tool use) and each repeats the response's `usage`. `_message_dedup_key` identifies the response by
  `message.id`, falling back to `requestId`, then `uuid` (which is unique, so such a record is never merged).
  `_collapse_main_chain_usage` takes the per-field maximum over a response's records (equal on real data, and
  the right choice for streamed partials) and returns one "carrier" record per response, plus the counters
  behind `usage_records` and `usage_records_deduped`.
- **Reading the numbers.** `_parse_usage` reads input, cache-creation, cache-read and output tokens.
  `_parse_cache_tiers` reads the 5-minute/1-hour split and clamps it. `_parse_server_tool_use` and
  `_parse_billing_modifiers` read the fields the price table needs.
- **Model per turn.** The turn's `model` is `message.model` of the record.
- **Sidechains.** Records with `isSidechain: true` are skipped in the main loop (`_is_main_chain`). Subagent
  transcripts live in `<session-id>/subagents/agent-<id>.jsonl`, are recognised by path
  (`tracegauge.discovery.is_subagent_path`, which also keeps discovery from scoring them as sessions), and
  `collect_subagent_usage` rolls their usage up with the same per-response dedupe, aggregated per file, model,
  speed and region. The parent's own transcript does not contain the subagent's token usage, so adding the
  files does not double count. The directory is read flat: a file nested deeper is ignored.
- **Subagent type comes from a sidecar file (0.17.0).** Next to each `agent-<id>.jsonl` Claude Code writes
  `agent-<id>.meta.json` with an `agentType`; `read_agent_type` reads it (missing, unreadable or non-string
  values become `unknown`) so cost and tokens can be broken down per subagent type. Nothing else in the
  adapter reads a metadata file.
- **Text, tools and edits.** Tool names come from `tool_use` blocks, snippets are redacted by
  `_redact_secrets`, and `_extract_edit_operations_from_content` pulls edit/write operations in the same pass.
- **Known limits of the reference adapter** (found by running it with synthetic input): `adapt_session`
  raises on a line that is valid JSON but not an object, and on a record whose `message` is not an object,
  while it skips a line that is not valid JSON. A new adapter should skip or report such lines the same way
  as malformed ones. A file that is not valid UTF-8 also makes `adapt_session` raise; that one is the
  documented contract (`score` exits 4, see [EXIT_CODES.md](EXIT_CODES.md)), whereas `_read_jsonl`, used for
  subagent files, replaces the bad bytes. Pick one behaviour for your adapter and document it.

Tests to read first: `tests/test_usage_dedupe.py`, `tests/test_subagent_rollup.py`,
`tests/test_cost_cache_tiers.py`, `tests/test_adapt_billing_modifiers.py`.

## (c) Other agents

Only Codex CLI has an implementation, and it is library-only (its status subsection says what was checked). The
first three are the planned order (Codex, OpenCode, Aider); the other three have been read but are not scheduled.

| Agent | Store | Where token usage lives |
|---|---|---|
| Codex CLI | JSON Lines, one file per session | `token_count` events, cumulative and per call |
| Gemini CLI | JSON Lines (legacy single `.json`) | Per message |
| OpenCode | SQLite database (legacy JSON tree) | Per assistant message and per step |
| Aider | Markdown per project | A line of English; JSON only if an analytics log was enabled |
| Copilot CLI | JSON Lines per session | At session shutdown only; per call only in an optional OpenTelemetry export |
| Cline | One JSON document per session | Per message |

### Codex CLI (`openai/codex` @ `2f761ae`)

Location and format
- VERIFIED: the home directory is `$CODEX_HOME` if set (it must exist and be a directory), else `~/.codex`
  (`codex-rs/utils/home-dir/src/lib.rs:6-46`).
- VERIFIED: sessions go to `<home>/sessions/YYYY/MM/DD/`, the date taken from local time
  (`codex-rs/rollout/src/recorder.rs:1744-1757`); the file is `rollout-YYYY-MM-DDThh-mm-ss-<uuid>.jsonl`, and a
  reverted thread appends `_<rolloutId>` (`codex-rs/rollout/src/rollout_file_name.rs:44-62`). An
  `archived_sessions` directory also exists (`codex-rs/rollout/src/lib.rs:86-87`).
- Each line is `{timestamp, ordinal?, type, payload}` (`codex-rs/history/src/lib.rs:365-371`; OBSERVED in
  real files, see the status subsection).
  The `type` values are `session_meta`, `response_item`, `inter_agent_communication`, `compacted`,
  `turn_context`, `token_usage_record`, `world_state`, `retained_context`, `security_risk_score`,
  `event_msg` and `realtime_item` (`codex-rs/history/src/rollout_payload.rs:33-76`).
- `session_meta` carries `cli_version`, `model_provider`, `cwd`, `originator` and `forked_from_id`
  (`codex-rs/protocol/src/protocol.rs:3243-3290`). The first four are OBSERVED in real files.
  `forked_from_id` is still UNVERIFIED: it was absent from the unforked files. There is no schema-version field, so
  `cli_version` is the only version marker.
- VERIFIED: there are two history modes, `Legacy` (the default) and `Paginated`
  (`codex-rs/protocol/src/protocol.rs:878-884`). Under `Paginated` the legacy `event_msg` message types are
  not persisted and `ItemCompleted` events are used instead (`codex-rs/rollout/src/policy.rs:133-150`). A
  parser written only for `Legacy` may miss `Paginated` sessions. UNVERIFIED: how common `Paginated`
  sessions are.
- VERIFIED: a compression worker turns files at least 7 days old into `*.jsonl.zst`
  (`codex-rs/rollout/src/compression.rs:364`, suffix at `:30`), gated by the `local_thread_store_compression`
  feature, which is under development and off by default (`codex-rs/features/src/lib.rs:1268-1272`). VERIFIED:
  no age-based deletion was found in the rollout crate.

Tool calls and reasoning
- VERIFIED: persisted response items include messages, reasoning, local shell calls, function calls and
  their outputs, custom tool calls and outputs, and web search calls (`codex-rs/rollout/src/policy.rs:80-98`).
  Tool results are `*_output` response items.
- VERIFIED: persisted command output and MCP results are cut at 64 KiB (`policy.rs:12-14`).
- VERIFIED: the `Reasoning` item has `summary`, optional `content` and `encrypted_content`
  (`codex-rs/protocol/src/models.rs:1051-1059`); raw reasoning is often available only encrypted.

Token usage
- VERIFIED: an `event_msg` whose `payload.type` is `token_count` carries `info.total_token_usage`
  (cumulative), `info.last_token_usage` (the last call) and `model_context_window`
  (`codex-rs/protocol/src/protocol.rs:2396-2400`, `:2463-2466`); it is always persisted (`policy.rs:133`).
- VERIFIED: `TokenUsage` has `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
  `output_tokens`, `reasoning_output_tokens` and `total_tokens` (`protocol.rs:2361-2376`).
- VERIFIED: a newer `token_usage_record` item adds `response_id`, `usage`, `turn_token_usage` and
  `thread_token_usage` (`protocol.rs:~2379-2392`, persisted at `policy.rs:44`).
- VERIFIED: the model id is in `turn_context.model` (`protocol.rs:3415+`), not in `token_count`; attribute
  usage to a model by tracking the latest `turn_context`.
- Settled from source in the status subsection below: `input_tokens` includes `cached_input_tokens`
  (invariant 6). Still worth confirming against a real file.

Pitfalls
- Both a cumulative total and a per-call value exist: use one consistently and never add them.
- UNVERIFIED (third-party parser, `rust/adapters/codex/src/parser.rs:484-490`): duplicate `token_count`
  events whose cumulative total did not change should be ignored.
- UNVERIFIED (same parser, `parser.rs:96-135`): a forked session replays the parent's usage into the child
  file, so fork usage is double counted unless the replayed prefix is recognised.
- UNVERIFIED (same parser, `parser.rs:406-424`, `:510-530`): that parser falls back when `turn_context` is
  missing and has dedicated handling for compaction.
- The model is in `turn_context`, not in the token event.
- `Paginated` history changes which event types exist (VERIFIED above).
- `.jsonl.zst` files can exist when compression is enabled (VERIFIED above), so a reader that must see
  compressed files has to handle them.

Status: experimental library adapter, shipped in 0.18.0

`tracegauge/codex_adapt.py` has `adapt_codex_session(path)`, which returns the layer-1 record for a
Codex rollout. It is **not** wired into the CLI or discovery (routing needs an agreed design, see
section (a)). It has been run on hand-written synthetic files and on four real rollouts (next list).

UNVERIFIED, because checking them needs a paid OpenAI-hosted model and every real file so far came from a
local model:
- cache-write tokens (`cache_write_input_tokens`), including whether `input_tokens` contains them (the
  adapter assumes it does; see "Settled from source" below);
- reasoning tokens (`reasoning_output_tokens`), including whether `output_tokens` contains them;
- any session from an OpenAI-hosted model: its model ids, its usage fields and its prices (the bundled
  price table has no OpenAI models, so every real Codex session is reported as unpriced).

Checked against real rollouts (2026-10-29). Codex CLI 0.165.0 and 0.162.1 (`@openai/codex` from npm),
`codex exec` on a synthetic task with a local model (`qwen3:8b` via Ollama), in a throwaway environment.
Scenarios: one turn; one turn plus two `codex exec resume --last` follow-ups; one turn with `--ephemeral`.
Two of the files, shortened, are test fixtures (`tests/fixtures/codex/real_*.jsonl`). OBSERVED below means
seen in those files.
- OBSERVED: every line is `{timestamp, ordinal, type, payload}`; some lines also have `metadata`.
  `ordinal` was present on every line and ran 0..n-1.
  `session_meta` carries `cli_version`, `model_provider`, `cwd`, `originator` (`codex_exec`), `source`,
  `history_mode` and `id`. `forked_from_id` was absent; none of these sessions was forked.
- OBSERVED: `codex exec` wrote `history_mode: "paginated"` in both versions. Under it, `response_item`
  messages, reasoning, `function_call` and `function_call_output` items are still in the rollout, and so
  are `token_count` events. The legacy `user_message` / `agent_message` events are absent;
  `item_completed`, `thread_settings_applied` and `world_state` lines appear instead. Besides
  `session_meta` (fork marker) and `turn_context` (model), the adapter reads only the `response_item` and
  `token_count` lines, so it handles these files.
- OBSERVED: within one model call the order is call, (reasoning), `token_usage_record`, call output, then
  `token_count`. A final assistant message comes before its `token_count`.
  - Adapter version 2 charges a `token_count` that follows a call output to the ai turn holding the latest
    such call.
  - Version 1 charged it to a new, empty ai turn: 11 ai turns for 7 model calls.
  - Session totals are the same in both versions.
- OBSERVED: every `token_count` total grew by exactly its `last_token_usage`: no repeated or falling totals
  in 27 events. `total_tokens == input_tokens + output_tokens`, so cached input is inside `input_tokens`
  (invariant 6). Each `token_usage_record.usage` equals the `last_token_usage` of the next `token_count`,
  and they sum to the final cumulative total. Ignoring `token_usage_record` is therefore what avoids
  double counting.
- OBSERVED: on all four files, the adapter's input, cache-read, output and total equal Codex's final
  `total_token_usage`.
- OBSERVED: `resume --last` appends to the same rollout file, with a new `turn_context` per turn.
  `--ephemeral` wrote no rollout in either version.
- OBSERVED: `<environment_context>` is injected as a user message before the task, and the adapter skips
  it. Instructions (`<skills_instructions>`, `<permissions instructions>`) arrive as `developer` messages,
  which the adapter does not read as user turns.
- Not covered by these files: cache writes and reasoning tokens (both 0 with Ollama), an OpenAI-hosted
  model, forks, compaction, `AGENTS.md` / `<user_instructions>`, and Legacy-mode files.

Settled from source at `2f761ae` (VERIFIED unless a bullet says INFERRED):
- A rollout line is `{"timestamp": str, "ordinal"?: int, "type": <snake_case variant>, "payload": {...}}`:
  the envelope is `RolloutLine` (`codex-rs/history/src/lib.rs:365-371`, `item` flattened) and the
  `type`/`payload` pair is the adjacently-tagged `RolloutItemWire`
  (`codex-rs/history/src/rollout_payload.rs:32-76`).
- A usage event is a line with `type: "event_msg"` and `payload`
  `{"type": "token_count", "info": {"total_token_usage": T, "last_token_usage": T,
  "model_context_window": int|null} | null, "rate_limits": {...}|null}` (`protocol.rs:1459-1462`,
  `:1526`, `:2396-2400`, `:2463-2466`), where `T` is `TokenUsage` (`protocol.rs:2361-2376`). `info`
  may be `null`; the adapter skips such an event.
- `input_tokens` INCLUDES `cached_input_tokens` (invariant 6): non-cached input is
  `input_tokens - cached_input_tokens` (`protocol.rs:2550-2552`).
- INFERRED, not verified: `input_tokens` also includes `cache_write_input_tokens`. The only evidence is
  that the Responses-API parser reads both out of `input_tokens_details`
  (`codex-rs/codex-api/src/sse/responses.rs:130-134`) and a serde parse test uses input 100, cached 40,
  written 60, total 110 (`responses.rs:846-867`); those are numbers chosen for the test, and
  `non_cached_input` subtracts only the cached part. The adapter subtracts the write as well. If the
  write is actually additive, fresh input is understated by the written tokens and cost is understated
  with it. Confirm against a real file.
- A malformed record with `input_tokens` below cached + write gets a billed input of cached + write
  (fresh 0), so the turn is internally consistent and no cached token is dropped. Huge counts are not
  capped.
- `output_tokens` already includes `reasoning_output_tokens`: the same test has output 10, reasoning 5,
  total 110, and the CLI prints reasoning as a parenthesised part of output (`protocol.rs:2622-2631`).
  The adapter does not add it again.
- The model id is `turn_context.model`, a required string (`protocol.rs:3416`, `:3438`); the adapter
  uses the latest one seen before a `token_count`.
- `*.jsonl.zst` is refused with `CodexCompressedSessionError` (no zstd dependency).

Still UNVERIFIED or not done:
- Item order is settled for 0.162.1 and 0.165.0 (above), but not for other builds or for Legacy mode.
- `token_usage_record` items (per response, with `response_id`) are ignored, so a response is not
  counted twice next to its `token_count`. If a build writes only those, usage is read as missing.
- Forked sessions: a `session_meta.forked_from_id` only adds a caveat and a warning; replayed parent
  usage is not recognised or removed.
- Cache writes carry no lifetime, so `cache_creation_tier_known` is 0. The bundled price table also has
  no OpenAI models, so every real Codex session prices as unpriced until entries are added.
- A session with no `token_count` gets `usage_available: false`, a caveat and `models_seen`, and its ai
  turns have `model` blanked to `""`. `tracegauge.cost` cannot resolve an empty model, so it reports the
  session as approximate ("empty model string") instead of a confident $0; cost.py itself is unchanged.
- Dedupe: within one counter the cumulative total only grows (`append_last_usage`, `protocol.rs:2431`),
  so an event whose total equals that of the last counted event is ignored. A total BELOW it is treated
  as a counter reset (restart, compaction): the event's `last_token_usage` is counted, the baseline moves
  down, `usage_dedupe.decreasing_total_events` is incremented, a warning is printed and a caveat says
  usage may be over-counted if the drop was a replay (so an A, B, A replay is over-counted, flagged).
  Ignoring it instead would silently drop all later usage. If `total_tokens` is missing or not a number,
  the event is compared on (total, input, output) with the previous event only, with a warning.
- Not done:
  - `edit_operations` (apply_patch) are not extracted.
  - `item_completed` lines are not read. In the files above they hold typed items (`UserMessage`,
    `AgentMessage`, `Reasoning`, `CommandExecution`) for the same turns. UNVERIFIED: whether they ever
    hold something the `response_item` lines do not.
  - Cache-churn and subagent roll-up do not exist for Codex.
- Of the injected-context prefixes, `<environment_context>` is OBSERVED; `<user_instructions>` and
  `# AGENTS.md` are still UNVERIFIED.

### Gemini CLI (`google-gemini/gemini-cli` @ `fa2531d`)

Location and format
- VERIFIED: the home directory honours `GEMINI_CLI_HOME`, else the OS home
  (`packages/core/src/utils/paths.ts:19-28`); the global directory is `<home>/.gemini`
  (`packages/core/src/config/storage.ts:54-60`); under macOS Seatbelt the runtime directory is
  `~/.cache/.gemini` (`storage.ts:92-107`).
- VERIFIED: chats live in `<runtime>/tmp/<projectIdentifier>/chats/`
  (`storage.ts:195-197`, `:230-234`, `:416`). The identifier is a short slug from `projects.json`, not a hash;
  old hash directories are migrated (`storage.ts:267-310`).
- VERIFIED: the file name is `session-<YYYY-MM-DDTHH-MM>-<first 8 characters of the session id>.jsonl`
  (`packages/core/src/services/chatRecordingService.ts:803-821`).
- UNVERIFIED: subagents write `<sessionId>.jsonl` in a folder named after the parent session.
- UNVERIFIED: the file is append-only JSON Lines holding a metadata header (`sessionId`, `projectHash`,
  `startTime`, `lastUpdated`, `kind`), message records, `{$set: ...}`, `{$rewindTo: id}` and `{$patch: ...}`
  (`packages/core/src/services/chatRecordingTypes.ts:92-175`). Legacy `.json` files are converted to `.jsonl`
  (`chatRecordingService.ts:732-733`, `:970-973`). A message with the same `id` can appear more than once,
  and the latest version wins. There is no schema-version field.
- VERIFIED: the default retention is `sessionRetention.enabled = true` with `maxAge = '30d'`, so chats older
  than 30 days are deleted automatically (`packages/cli/src/config/settingsSchema.ts:389-410`).

Tool calls and reasoning
- VERIFIED: a `gemini` message has `toolCalls[]` with `id`, `name`, `args`, `result`, `status` and
  `timestamp` (`chatRecordingTypes.ts:54-66`); `thoughts[]` holds thought summaries (`:79`);
  `MAX_TOOL_OUTPUT_SIZE` is 50 KB (`:14`).

Token usage
- VERIFIED: a `gemini` message has `tokens: {input, output, cached, thoughts?, tool?, total}` taken from
  that response's `usageMetadata` (`chatRecordingTypes.ts:19-26`, `chatRecordingService.ts:1137-1160`), and
  the `model` on the same message (`:80`). It is per message, not cumulative.
- UNVERIFIED: a message first written without tokens may be re-appended with the same `id` once the usage
  arrives (`chatRecordingService.ts:1152-1157`).

Pitfalls
- The same message `id` appears more than once; keep the last (UNVERIFIED, above).
- UNVERIFIED (same parser, `parser.rs:400-416`): when `total == input + output + thoughts + tool`, `input`
  includes the cached tokens, and the overlap has to be subtracted.
- `$rewindTo` and `$patch` records exist, so rewound turns may still be counted by a naive reader. Decide
  whether a rewound turn was billed (it was) or part of the final conversation (it is not) and document the
  choice.
- A relocated home (`GEMINI_CLI_HOME`) moves the whole tree, so honour that variable.
- Files older than 30 days are gone by default (VERIFIED above), so a history reader sees only recent
  sessions.
- A legacy single-document `.json` form exists alongside `.jsonl`.
- UNVERIFIED: the successor product, Antigravity CLI, is not open source, and the third-party parser
  describes its conversations as SQLite with protobuf blobs, a different format that this section does not cover.

### OpenCode (`anomalyco/opencode` @ `3884062`)

Location and format
- VERIFIED: the database is `<Global.Path.data>/opencode.db`
  (`packages/core/src/database/database.ts:43-56`); `OPENCODE_DB` overrides it
  (`packages/core/src/flag/flag.ts:47`); for channels other than latest, beta and prod the file is
  `opencode-<channel>.db` unless `OPENCODE_DISABLE_CHANNEL_DB` is set. `Global.Path.data` is
  `xdgData/opencode` using the `xdg-basedir` package (`packages/core/src/global.ts:3-12`).
- UNVERIFIED: on Windows and macOS the default location is not established (the third-party parser uses
  `~/.local/share`).
- UNVERIFIED: the database is SQLite in WAL mode (`database.ts:25-31`) with tables `session`,
  `message(id, session_id, time_*, data JSON)` and `part(id, message_id, session_id, data JSON)`
  (`packages/core/src/session/sql.ts:22-98`), a newer `session_message(id, session_id, type, seq, data)`
  (`sql.ts:119-135`), and `event`, `event_sequence` and `todo`.
- UNVERIFIED: a legacy JSON store `storage/session/{info,message,part}/...` was migrated into the database
  (`packages/opencode/src/storage/storage.ts:82-200`); the database migrates itself with drizzle
  (`database.ts:33`). No retention policy was found.

Tool calls and reasoning
- VERIFIED: a `tool` part has `callID`, `tool` and `state` (`packages/schema/src/v1/session.ts:315-322`); a
  completed state has `input`, `output` (a string), `title`, `metadata` and `time.{start,end,compacted?}`
  (`session.ts:273-290`); a `reasoning` part has `text` and `time` (`session.ts:118-128`).
- UNVERIFIED: `time.compacted` suggests old tool output can be pruned after compaction.

Token usage
- VERIFIED: an assistant message carries `modelID`, `providerID`, `cost` and
  `tokens {total?, input, output, reasoning, cache{read,write}}` (`session.ts:452-481`), and each
  `step-finish` part also carries `cost` and `tokens` (`session.ts:240-256`).

Pitfalls
- The message totals and the `step-finish` totals overlap; summing both double counts (an inference from the
  two VERIFIED facts above, UNVERIFIED as observed behaviour).
- UNVERIFIED (third-party parser): installs from before and after the move to SQLite coexist, so a reader needs
  both the legacy JSON tree and the database, and must dedupe by message id between them
  (`rust/adapters/opencode/src/loader.rs:106-180`, `:546-560`).
- UNVERIFIED (same parser, `loader.rs:615-730`): sessions forked in the newer format copy their parent's
  leading `session_message` rows, so inherited assistant usage has to be dropped.
- Channel-suffixed database file names (VERIFIED above).
- UNVERIFIED: tool output may be pruned after compaction.

### Aider (`Aider-AI/aider` @ `5dc9490`, last commit 2026-05-22)

Location and format
- VERIFIED: the default chat history is `<git-root>/.aider.chat.history.md` (or the working directory) and
  the input history is `.aider.input.history` (`aider/args.py:271-288`); both can be moved with
  `--chat-history-file` and `--input-history-file`.
- VERIFIED: it appends markdown. A session starts with `# aider chat started at <time>` (`aider/io.py:335-336`);
  user input is written with a `####` prefix (`io.py:779-789`); assistant output is plain text
  (`io.py:793-795`); every `tool_output` is written as a `> ` blockquote (`io.py:995-999`), including the
  `Aider v<version>` banner and the model names (`aider/coders/base_coder.py:209`).
- VERIFIED: an optional raw log, `--llm-history-file`, writes `TO LLM <iso ts>` and `LLM RESPONSE <iso ts>`
  blocks of formatted text, not JSON (`args.py:299`, `io.py:754-765`, `base_coder.py:1793`, `:1823-1826`).
- There is no schema version and no rotation.

Tool calls and reasoning
- UNVERIFIED: edits are written inside the assistant text; no log holds structured tool-call or tool-result
  records, and reasoning is not recorded as a separate record. Whether shell output reaches the markdown
  depends on `tool_output` and is not established.

Token usage
- VERIFIED: usage appears as a blockquote line such as `> Tokens: 1.2k sent, 340 received.` plus a cost line
  (`base_coder.py:2023-2068`, shown at `:2102-2109`). It is per user message, accumulated across reflections
  and then reset (`:2124-2126`); cache write and hit tokens are appended when present.
- VERIFIED: the structured form is the analytics event `message_send` with `prompt_tokens`,
  `completion_tokens`, `total_tokens`, `cost`, `total_cost` and `main_model` (`base_coder.py:2113-2122`),
  written as JSON Lines to `--analytics-log` (`aider/analytics.py:213-249`). It has no message content, and
  it exists only if the user passed that flag.
- UNVERIFIED: the model id appears only in the printed banner and in the analytics `main_model`, which is
  redacted for models that are not registered (`analytics.py:~219`). Without a `usage` object from the
  provider, Aider estimates tokens itself (`base_coder.py:2016-2019`).

Pitfalls
- There are no tool records, so waste detection by tool name does not apply.
- Tokens are English text in markdown, in a format that can change with the wording.
- The file is per project, not per user.
- Per-message totals include reflections.

### Copilot CLI (`github/copilot-cli` @ `dcdc027`)

- VERIFIED: the repository contains only `LICENSE.md`, `README.md`, `changelog.md` and `install.sh`. The
  client is a distributed binary, so its format cannot be read from source and everything below is
  UNVERIFIED.
- UNVERIFIED (third-party parser, `rust/adapters/copilot/src/paths.rs:32-47`, `:56-62`): sessions are in
  `~/.copilot/session-state/<session-id>/events.jsonl`, and `COPILOT_HOME` overrides `~/.copilot`.
- UNVERIFIED: an optional OpenTelemetry file export goes to `~/.copilot/otel/*.jsonl` or the path in
  `COPILOT_OTEL_FILE_EXPORTER_PATH` (`paths.rs:5`, `:24-30`, `:76`).
- UNVERIFIED: the event types seen in that project's test fixtures are `session.start` and
  `session.shutdown`; the schemas of tool-call and tool-result events are not known.
- UNVERIFIED (`parser.rs:136-262`): usage appears only on `session.shutdown`, as
  `data.modelMetrics.<model>.usage {inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens,
  reasoningTokens}` and `requests.count`. A session that crashes or is killed has no usage at all.
- UNVERIFIED: per-call usage exists only in the OpenTelemetry export, as `gen_ai.usage.input_tokens`,
  `output_tokens`, `cache_read.input_tokens`, `cache_creation.input_tokens` and `reasoning.output_tokens`
  (`parser.rs:331-352`).

Pitfalls (all UNVERIFIED): usage only at shutdown; the OpenTelemetry export has four overlapping sources (a
chat span, an inference log, an agent-turn log and a summary span) that have to be deduplicated by response id
(`parser.rs:560+`); the changelog mentions resuming sessions with "recoverable corruption", so a truncated
last line is plausible; and because the source is closed, the schema can change without notice.

### Cline (`cline/cline` @ `d20b337`)

The repository now has an SDK-based format and a legacy VS Code format; a reader needs both.

- VERIFIED (SDK): the base directory is `CLINE_DIR`, else `~/.cline`
  (`sdk/packages/shared/src/storage/paths.ts:159-168`); the data directory is `CLINE_DATA_DIR`, else
  `<base>/data` (`:186-192`); the sessions directory is `CLINE_SESSION_DATA_DIR`, else `<data>/sessions`
  (`:194-200`).
- VERIFIED (SDK): per session, `<sessions>/<id>/<id>.messages.json`, `<id>.compaction.json` and a manifest
  `<id>.json` (`sdk/packages/core/src/services/session-artifacts.ts:69-95`), plus `sessions.index.json`
  (`session/services/file-session-service.ts:63`). The messages file is a single pretty-printed JSON
  document rewritten as a whole (`session/stores/atomic-file.ts`), shaped `{version: 1, updated_at, agent,
  sessionId, origin, messages[], system_prompt?}` (`services/session-data.ts:334-358`). `version: 1` is the
  only schema marker found. Subagent artifacts are in the same folder, one `messages.json` per child
  (`session-artifacts.ts:130-145`).
- VERIFIED (legacy VS Code): `<data>/tasks/<taskId>/api_conversation_history.json` and `ui_messages.json`,
  with `<data>/state/taskHistory.json` (`apps/vscode/src/sdk/legacy-state-reader.ts:42-65`; file names at
  `apps/vscode/src/core/storage/disk.ts:17-20`). UNVERIFIED: the older
  `globalStorage/saoudrizwan.claude-dev` location at this commit. UNVERIFIED: rotation and retention (none
  found).
- VERIFIED: content blocks are `text`, `file`, `image`, `tool_use`, `tool_result`, `thinking` and
  `redacted_thinking` (`sdk/packages/shared/src/llms/messages.ts:19-125`), so tool calls, results and
  thinking are all recorded.
- VERIFIED: `MessageWithMetadata` has `modelInfo {id, provider, family?}` and
  `metrics {inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens, cost}`, per assistant message
  (`messages.ts:140-165`; attached by `withLatestAssistantTurnMetadata`, `session-data.ts:122-150`).
- UNVERIFIED: in the legacy format the `api_req_started` UI message carries `tokensIn`, `tokensOut`,
  `cacheWrites`, `cacheReads` and `cost` (`apps/vscode/src/shared/ExtensionMessage.ts:369-378`); the type is
  known, the emitting code was not checked.

Pitfalls: two formats; the SDK file is replaced as a whole, so a reader must tolerate it changing mid-read
(a consequence of the atomic rewrite, VERIFIED above).

## (d) Testing an adapter

**Never use a real transcript.** Not as a fixture, not trimmed, not "anonymised": real sessions hold code,
paths and keys, and `tests/test_synthetic_fixtures.py` and `tests/test_public_text.py` exist to catch them.
Write a small file by hand, in the agent's real record shape, from the field names in this page and from
the agent's own source.

1. **Build the fixture.** A few lines is enough. Use obviously synthetic content ("synthetic task: read the
   config"), made-up ids, and token counts you can add up in your head. Put it under
   `tests/fixtures/<agent>/`. The reference is `tests/fixtures/usage_dedupe/multi_block_session.jsonl`; for a
   subagent layout, see how `tests/test_subagent_rollup.py` writes its files with `tmp_path`.
2. **Write the golden test.** Compute the expected totals by hand and put the arithmetic in the test, as
   `tests/test_usage_dedupe.py` does (`EXPECTED_REAL = (2 + 5000 + 600) + (3 + 200)`), so a reader can check
   the number against the fixture. Assert the session totals, `turn_count`, each AI turn's `model`,
   `cache_read` and `cache_creation`, and the `usage_dedupe` counters. A number with no derivation in the
   test is not a golden value.
3. **Cover the usage pitfalls** with one small fixture each:
   - one response written as several records (usage counted once, turn structure unchanged);
   - a streamed response whose later record has a larger output count (final value wins);
   - the same response in two files (counted once);
   - for a cumulative counter: a repeated total (ignored) and a total that goes down after compaction or a fork;
   - cached tokens included in, or separate from, the input figure (invariant 6);
   - a model switch inside one session (model per turn);
   - a response with no usage (zero, not an error, and not priced as free);
   - subagent usage (kept out of the main chain, counted once);
   - the 5-minute/1-hour split present, absent, and larger than the total written.
4. **Cover hostile input:** a blank line, a truncated last line, a line that is valid JSON but not an object,
   a record with an unexpected shape, a non-UTF-8 byte (skip it, or fail with a documented exit code), an
   empty file. The adapter must skip or report, not crash with a traceback.
5. **Cost.** Run the adapted turns through `tracegauge.cost.compute_session_cost` as
   `tests/test_cost_cache_tiers.py` does, with a model that is in the price table and one that is not.
6. **Run the repository checks** that gate documentation and fixtures:

```bash
uv run pytest tests/test_public_text.py tests/test_synthetic_fixtures.py tests/test_spdx_headers.py -q
uv run pytest tests/ -q
```

## (e) Checklist for an adapter pull request

- [ ] An issue exists and the maintainer agreed how the adapter is routed (file location, format detection).
- [ ] The agent's version and the source commit you read are written in the module docstring, with the
      file and line of each format claim; anything you did not read in source is marked UNVERIFIED.
- [ ] The fixture is synthetic and in `tests/fixtures/<agent>/`; no real prompts, paths, ids or keys.
- [ ] Every invariant in section (a) has a test, and the golden values are derived in the test.
- [ ] Usage is counted once per response; cumulative, forked and re-appended records are handled.
- [ ] Cache reads, cache writes and their lifetime are separate; unknown tier is left unknown.
- [ ] The model is per turn and copied unmodified; an unknown model stays unpriced.
- [ ] `ADAPTER_VERSION` handling is defined for the new adapter, and stored rows can be told apart.
- [ ] Hostile-input tests pass: blank, truncated, non-object, non-UTF-8, empty.
- [ ] New `.py` files start with the SPDX header (`python scripts/spdx_headers.py --check`).
- [ ] Docs describe the new agent in this page and in the README's "Supported agents" line, with the
      limits stated (what is not counted, which versions were checked).
- [ ] A validation run is described: what was measured, on what synthetic or public data, and with what
      source; a number without a source is not added.
- [ ] Commits are signed off (`git commit -s`) and the pull request is one change.
