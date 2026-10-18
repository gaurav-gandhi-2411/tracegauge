# Dashboard screenshots

Every image here shows **synthetic data**: a seeded, invented Claude Code project tree
(`demo-api`, `demo-web`) built by `scripts/gen_dashboard_demo.py`. No real transcript was read.
The ids, prompts, file names and token counts all come from that script (seed 42).

These replace the PR-evidence screenshots removed in the 0.16.0 history scrub. Before/after pairs
cannot be recreated (the old builds are gone), so each image is the current build in one state.

- Captured on: 2026-10-18
- master commit: `ca26b2075fad057f9992b89de118f2e25075666c` (pyproject version 0.16.0, unreleased 0.17.0 work)
- Browser: Chrome headless, 1280 px wide, light theme (the dashboard has no dark theme)

## How they were produced

```bash
# 1. the synthetic tree (deterministic: two runs write identical bytes, tests/test_gen_dashboard_demo.py)
python scripts/gen_dashboard_demo.py <tree>

# 2. serve it with a scratch HOME and database (nothing under the real ~/.tes is touched).
#    The tree was mounted as a drive root (Windows `subst X: <tree>`) so the source path the
#    session page prints reads X:\projects\demo-api\<id>.jsonl instead of a scratch path.
tracegauge serve --port 4747 --cc-path X:\projects --db-path <scratch>\serve.db \
    --stability-window 1 --scan-interval 30

# 3. one screenshot per page (height chosen to fit the page, then trailing background cropped)
chrome.exe --headless=new --screenshot=<file> --window-size=1280,<h> --hide-scrollbars <url>
```

The cache-churn "transcript unavailable" image was taken after moving that session's `.jsonl`
out of the tree (the page reads the transcript on request); the file was moved back afterwards.
`cache-churn-events.png` was palette-quantised (128 colours) to stay under 300 KB.

## Index

| File | What it shows | URL path |
|---|---|---|
| `home.png` | Session list with the brand lockup, ten synthetic sessions, one with waste, two unpriced | `/` |
| `session-output-heavy.png` | Output lever finding (87% of cost), cost breakdown, three subagent transcripts in the cost note | `/session/9e574f7a-a0ee-49ae-9453-dd324b0dbb41` |
| `session-context-heavy.png` | Large re-sent context: 68% of cost in the breakdown, and no finding (context share is not a lever by design) | `/session/0822e8f3-6c03-4199-972a-846916419f82` |
| `session-unpriced.png` | Model id not in the price table: breakdown "Not computed", costs shown as unpriced | `/session/a65ed389-b74d-4fb1-b2e7-06298fadc1a6` |
| `session-partly-unpriced.png` | Half the turns unpriced: "priced part only" note, output lever with the unpriced caveat | `/session/6dadd6c7-95a7-4d79-bf3c-4c06434308bc` |
| `session-waste-redundant-reads.png` | Waste lever finding ($0.30, 14%) from six redundant reads | `/session/8fb5d27b-beb7-4919-bf22-faf823bed01d` |
| `cache-churn-events.png` | Cache-churn card with three events (TTL gap, model switch, compaction) | `/session/1745d6d8-7e57-4ddf-8270-50a82369b584` |
| `cache-churn-empty.png` | Cache-churn card with no events | `/session/3eb13b90-4668-4257-bdd6-40fb06671ad1` |
| `cache-churn-unavailable.png` | Cache-churn card when the transcript is no longer on disk | `/session/ff5e9ff0-ff50-4de4-b825-67b85cabcc97` |
| `trends.png` | Trends page (cold-start gate not met) | `/trends` |
| `baseline-status.png` | Baseline-status page | `/baseline-status` |

## Known artefacts of the current build (not of the data)

- Every session is listed as an "empty/stub session" (`Sessions with content: 0`, `10 (100.0%)`):
  the watcher stores `turn_count = 0` because `watcher.py` calls `upsert_session` without it.
  This is how master behaves, so the images show it.
- All sessions carry the capture date (the store dates a session by when it was scored, not by its
  transcript timestamps), so the trends page cannot show a history.
