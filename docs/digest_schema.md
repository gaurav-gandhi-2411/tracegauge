# Digest Schema Reference

**Status:** LOCKED — validated in B1 against qwen3:30b-a3b judge. Adapters conform to
this schema; they do not change it. Any field addition or type change is an escalation.

**Source files:**
- `src/token_efficiency/trace_digest.py` — `SessionDigest`, `TurnDigest`, `build_digest()`, `digest_to_text()`
- `src/token_efficiency/layer1_features.py` — `LayerOneFeatures`, `extract_features()`
- `scripts/layer2_judge.py` — `_build_user_prompt()`, `_reconstruct_digest()`

---

## Layer 1 output record (`data/layer1_outputs.jsonl`)

Each line is a JSON object that combines Layer 1 features with the nested SessionDigest.
This is the input format that `layer2_judge.py` reads.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `session_id` | string | **required** | Canonical hex session identifier |
| `domain_id` | string | **required** | Domain bucket (see Domain table below) |
| `test_outcome` | bool | **required** | True = task resolved/passed |
| `total_tokens` | int | **required** | Sum of all token counts across the session |
| `turn_count` | int | **required** | Number of conversation turns |
| `h2_duplicate_count` | int | **required** | Count of turns flagged as H2 duplicate messages |
| `cache_hit_rate` | float | **required** | sum(cache_read) / max(1, sum(input)); range [0.0, 1.0] |
| `p25_token_ratio` | float | **required** | total_tokens / domain_p25_baseline; clamped [0.1, 100.0] |
| `labeler_model` | string | **required** | Model that produced per-turn H2 annotations; "missing" = no annotation (record filtered out by judge pipeline) |
| `scaffold` | string | optional | Source scaffold name (e.g., "swe_agent", "openhands_nebius", "claude_code") |
| `output_tokens_available` | bool | **required** | True when per-turn output tokens are recorded; False for swe_agent (which logs zero output tokens) |
| `digest` | object | **required** | Nested `SessionDigest` (see below) |

### Domain table (from `DOMAIN_RESOLVE_RATE` in `layer1_features.py`)

| Domain ID | Resolve rate | Notes |
|---|---|---|
| `lib_general` | 0.59 | General libraries |
| `type_checker` | 0.14 | Type system / mypy / pyright |
| `unknown` | 0.61 | Fallback for unclassified tasks |
| `data_ml` | 0.42 | Data science / ML |
| `cloud_devops` | 0.48 | Cloud / infra / CI |
| `graph_geo` | 0.95 | Graph / geographic |
| `db_orm` | 0.43 | Databases / ORMs |
| `web_api` | 1.00 | Web frameworks / APIs |
| `testing_ci` | 0.50 | Testing / CI tooling |

Out-of-distribution domains fall back to `CORPUS_MEAN_RESOLVE_RATE = 0.50`.

---

## SessionDigest (`digest` nested field)

Produced by `build_digest()` in `trace_digest.py`. Serialised via `digest_to_dict()`.
Reconstructed by `_reconstruct_digest()` in `layer2_judge.py` before calling the judge.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `session_id` | string | **required** | Same as top-level |
| `domain` | string | **required** | Same as `domain_id` at top level |
| `resolved` | bool | **required** | Same as `test_outcome` at top level |
| `total_tokens` | int | **required** | Session total token count |
| `turn_count` | int | **required** | Number of turns |
| `h2_duplicate_count` | int | **required** | H2 duplicate count |
| `cache_hit_rate` | float | **required** | Cache hit rate |
| `p25_token_ratio` | float | **required** | Token ratio vs domain p25 |
| `output_tokens_available` | bool | **required** | Whether output tokens are logged per-turn |
| `task_description` | string | **required** | First user-role turn content, truncated to 800 chars. Fed directly to judge prompt. |
| `turns` | list[TurnDigest] | **required** | All turns in order; system turns are included but skipped in rendering |
| `usage_records` | int | optional | Main-chain assistant records that carried a `usage` object (0 on digests stored before adapter_version 2) |
| `usage_records_deduped` | int | optional | Distinct API responses among them, each counted once; `usage_records - usage_records_deduped` is the number of repeated content-block records (0 on older digests) |

---

## TurnDigest (element of `turns` list)

| Field | Type | Required | Meaning |
|---|---|---|---|
| `turn_index` | int | **required** | Zero-based sequential index; turns must be in ascending order |
| `role` | string | **required** | `"user"`, `"ai"`, `"system"`, or `"tool"` (tool = ENV_RESULT) |
| `tool_names` | list[str] | **required** | Names of tools called in this turn; empty list if none |
| `content_snippet` | string | **required** | First 300 chars of turn content, stripped; may be empty |
| `token_count_input` | int | **required** | Input tokens for this turn (cumulative context size for AI turns; 0 for env/user turns) |
| `token_count_output` | int | **required** | Output tokens generated in this turn (>0 only for AI turns with output_tokens_available=True) |
| `cache_read` | int | **required** | Cache-read tokens for this turn |
| `h2_duplicate` | bool | **required** | True if this turn was annotated as an H2 duplicate message |

### Role values and rendering

`digest_to_text()` renders turns as follows (system turns are **skipped**):

```
[T{turn_index}] {ROLE_LABEL} — tools: {tool_names} — in: {token_count_input} / out: {token_count_output}
  {content_snippet}
```

Role label mapping for rendering:
- `"tool"` → `"ENV_RESULT"` (execution environment results)
- Any other role → `role.upper()` (e.g., `"user"` → `"USER"`, `"ai"` → `"AI"`)

---

## What the judge actually reads

`_build_user_prompt()` (in `layer2_judge.py`) feeds the judge exactly:

1. `task_description` — first 400 chars of `digest.task_description`
2. `domain` — the domain string
3. `digest_text` — output of `digest_to_text(digest, show_stats=False)`

`show_stats=False` strips these from the rendered text:
- P25 Ratio, Cache Hit, H2 Duplicates (the formula scalars)
- H2 duplicate markers in turn lines

The judge therefore sees: session header (domain, resolved, turn_count,
output_tokens_available), task description, and the TRAJECTORY block with per-turn
lines showing role, tool names, input/output tokens, and content snippet.

### Fields that matter most to the judge (what it cites in reasoning)

From investigation of judge reasoning (report 06 §7.7):
- `content_snippet` — the judge cites specific turn content to identify failure modes
- `tool_names` — cited when tool calls are repeated or results ignored
- `token_count_input` / `token_count_output` — present in every turn line; judge uses these to assess context growth
- `turn_index` — cited directly ("T2, T4, T6, T8 form an empty loop")
- `role` — distinguishes agent action (AI) from environment result (ENV_RESULT)
- `task_description` — anchors judgment; judge rates purposefulness RELATIVE TO the task

### Fields used only by the composite formula (not seen by judge)

- `p25_token_ratio` — in `score.py` formula (efficiency denominator)
- `h2_duplicate_count` — in `score.py` h2_score calculation
- `cache_hit_rate` — in Layer 1 features; not directly in judge prompt
- `test_outcome` / `resolved` — in `score.py` outcome_score; shown in header but judge
  is instructed NOT to assess task success

---

## Required vs optional for a valid judge call

**Minimum required for the judge to produce a valid verdict:**

| Field | Why required |
|---|---|
| `task_description` | Without this the judge cannot anchor on the task goal |
| `domain` | Included in judge prompt; affects rubric context |
| `turns` (non-empty) | The TRAJECTORY section must have at least some AI turns |
| Each turn: `role`, `turn_index`, `content_snippet`, `token_count_input`, `token_count_output` | Constitute the rendered turn line the judge reads |
| Each turn: `tool_names` | "none" is a valid value; must not be missing |

**Fields used only by composite formula (judge can run without them, but score.py needs them):**

| Field | Fallback in score.py |
|---|---|
| `test_outcome` / `resolved` | 0.0 outcome_score — equivalent to "unresolved" |
| `p25_token_ratio` | Clamped to [0.3, 5.0]; formula still runs |
| `h2_duplicate_count` | h2_score becomes 1.0 (perfect, no duplicates) when 0 |
| `domain_id` | Falls back to `CORPUS_MEAN_RESOLVE_RATE = 0.50` for difficulty_norm |

**`labeler_model`:** Set to `"missing"` and the record is filtered out by `_load_records()`
in `layer2_judge.py`. Adapters MUST set this to a non-"missing" value (e.g., `"not_applicable"`)
for real sessions that have no H2 annotation, or the record will be silently skipped.

---

## Constants (do not change without escalation)

```python
_SNIPPET_MAX_CHARS = 300  # content_snippet truncation
_TASK_DESC_MAX_CHARS = 800  # task_description truncation (build_digest)
task_desc_in_prompt = 400  # further truncation in _build_user_prompt
_P25_RATIO_MIN = 0.1  # layer1_features clamping
_P25_RATIO_MAX = 100.0  # layer1_features clamping
P25_RATIO_MIN = 0.3  # score.py clamping (tighter)
P25_RATIO_MAX = 5.0  # score.py clamping (tighter)
```

---

## Subagent fields (tracegauge 0.17.0, `tracegauge/_digest.py`)

Additive, defaulted fields; records stored before they existed deserialise unchanged and no
existing number moves (`ADAPTER_VERSION` / `COST_VERSION` are untouched).

| Field | Type | Meaning |
|---|---|---|
| `TurnDigest.agent_type` | string | Subagent type (`agentType` in the sibling `agent-<id>.meta.json`) on aggregated subagent turns. `""` on main-chain turns and on records stored before this field existed; reported as `unknown`, as is a subagent with a missing/bad meta file. Control characters stripped, capped at 64 chars. |
| `SessionDigest.subagent_files_by_type` | object (string -> int) | Subagent transcripts per type; sums to `subagent_count`. `{}` on older records. |

`collect_subagent_usage` also returns `by_type: {type: {file_count, message_count, real_tokens,
billed_tokens}}` (and `record["subagent_usage"]["by_type"]`).
