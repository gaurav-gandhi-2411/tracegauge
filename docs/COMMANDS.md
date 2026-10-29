# Commands, SDK and roadmap

The long-form usage that used to be in the README. The methodology and every caveat are in
[METHODOLOGY.md](METHODOLOGY.md); the README keeps the short command table.

## Try it right now — no local judge, no API key, no network call

```bash
pip install tracegauge
tracegauge quickstart
```

## Quick start

The tool already knows where your sessions live (`~/.claude/projects`). You don't type paths or memorize flags — **just run `tracegauge`**.

`tgauge` is a short alias for `tracegauge`. Coming from the old `tes` name? It was removed in 0.18; see [docs/MIGRATING_FROM_TES.md](MIGRATING_FROM_TES.md).

```bash
pip install tracegauge
# Optional: pip install "tracegauge[patterns]" adds numpy + scikit-learn, needed only for
# `tracegauge patterns`, `tracegauge ask` and the dashboard's Patterns/Ask pages

# Just run it — bare `tracegauge` launches the localhost dashboard (http://127.0.0.1:4747/)
tracegauge

# Score your most recent session — no path needed
tracegauge score

# Pick from a list of your recent sessions
tracegauge score --pick

# Run the trajectory judge — auto-detects a local Ollama judge or an API key,
# and guides you to the single simplest setup step if neither is present
tracegauge score --judge
```

Subagent spend is broken out by subagent type (0.17.0): `tracegauge score` prints a line per type
under the cost note ("subagents: 60% of this session's $X"), and `tracegauge cost` prints the subagent
share of the period spend and tokens plus a per-type table. Sessions scored before 0.17.0 are listed
as "not recorded" in `tracegauge cost` until the transcript is re-scored (`tracegauge score <path>`); an unpriced type is
shown as `unpriced`, never `$0.00`, a price under half a cent as `<$0.01`, and `>=` marks a lower bound.
When any usage is unpriced, the dollar share is over the priced dollars only (marked "priced part"); the
token share counts all tokens, in real-token units, and so do the per-type token counts shown in the text
(the JSON also carries billed tokens per type).
Fields: [JSON_OUTPUT.md](JSON_OUTPUT.md).

That's the whole frictionless path. Power-user / scripting forms still work:

```bash
tracegauge serve                                  # same as bare `tracegauge`, with flags (--port, --cc-path, …)
tracegauge score <path>.jsonl                     # score a specific file
tracegauge score ~/.claude/projects/<project>/    # score every session in a directory
tracegauge score <path> --json                    # machine-readable output (also cost, budget, monitor, impact, patterns)
tracegauge --version
```

Bare `tracegauge` (and `tracegauge serve`) start two things: a background scan loop that auto-scores finished Claude Code sessions (token economy + deterministic waste, judge OFF by default), and a web dashboard on `http://127.0.0.1:4747/` where scores accumulate. Session resolution for `tracegauge score` is: explicit PATH > `--pick` > most recent session.

## `tracegauge serve` — always-available local service

```bash
tracegauge serve [--port PORT] [--scan-interval SECONDS] [--stability-window SECONDS] \
          [--cc-path PATH] [--db-path PATH] [--background-judge]
```

- **Watcher**: scans `~/.claude/projects` every 2 minutes (configurable), scores any session file stable for 5+ minutes (token + waste; judge OFF by default).
- **Dashboard**: `http://127.0.0.1:4747/` — session list, per-session three-axis detail with domain-of-validity notes inline, trend views.
- **Store**: SQLite at `~/.tes/tes.db` (WAL mode; watcher writes and dashboard reads concurrently without locks).
- **Manual scores share the dashboard**: `tracegauge score <path>` results also write to the store.

Moat properties: binds `127.0.0.1` only (never exposed to external interfaces), redaction on by default at ingestion, no external network calls.

To enable the trajectory judge in the background watcher:
```bash
tracegauge serve --background-judge
# WARNING: runs qwen3:30b-a3b (~18 GB VRAM) on your GPU for every new session continuously.
```

## SDK usage

```python
from pathlib import Path

from tracegauge import load_baselines, score_session, JudgeConfig
from tracegauge.adapt import adapt_session
from tracegauge.baselines import BUNDLED_BASELINES_PATH
from tracegauge.waste import detect_repeated_failed_retry, detect_redundant_read, build_waste_entry

baselines = load_baselines(BUNDLED_BASELINES_PATH)
record = adapt_session(Path("path/to/session.jsonl"))  # secrets redacted at ingestion

session_id = record["session_id"]
turns = record["digest"]["turns"]
waste_entry = build_waste_entry(session_id, turns)

# Optional: trajectory judge (returns None → UNAVAILABLE when no local judge)
from tracegauge.judge import score_trajectory

judge_entry = score_trajectory(record)

result = score_session(record, baselines, judge_entry=judge_entry, waste_entry=waste_entry)
print(result.band_verdict)  # "within_band" | "above_p75" | "below_p25" | "unavailable"
print(result.judge_verdict)  # "BETTER" | None
print(result.waste_event_count)  # int
print(result.token_domain_of_validity)  # caveat string, always populated
```

## Roadmap

## Roadmap

- **Corpus de-biasing:** `tracegauge export-contribution` (P7) writes a local content-free digest for voluntary contribution. Server-side aggregation, pooled baselines, and legal review are follow-on work.
- **Smaller judge:** a laptop-runnable quantized model for the trajectory axis (requires a new B3-equivalent corroboration run, not a swap).
- **Cross-agent support:** adapters for OpenCode, Codex, Aider once tool_result data is available for re-validation.
- **`tracegauge install-hook`:** explicit opt-in SessionEnd hook for zero-latency scoring (modifies `~/.claude/settings.json` only on user request).
