# Migrating from `tes` to `tracegauge`

The tool has one name now: **tracegauge**. Before 0.16 the PyPI distribution was already called
`tracegauge`, but the import package, the command and the environment variables were `tes`. The old
names kept working, with a deprecation notice, in 0.16 and 0.17, and **were removed in 0.18**.

| | Before | Now | Old name |
|---|---|---|---|
| Python import | `import tes` | `import tracegauge` | removed in 0.18 (`ModuleNotFoundError`) |
| Command | `tes score` | `tracegauge score` (short alias: `tgauge score`) | removed in 0.18 (not installed) |
| Module run | `python -m tes` | `python -m tracegauge` | removed in 0.18 (`No module named tes`) |
| Environment variables | `TES_DB_PATH`, ... | `TRACEGAUGE_DB_PATH`, ... | removed in 0.18; one set alone stops the command (see below) |
| Store directory | `~/.tes/` | `~/.tes/` (unchanged, see below) | not removed |

## What to change when you upgrade to 0.18

1. Replace `tes` with `tracegauge` (or `tgauge`) in scripts, shell aliases and CI jobs.
2. Replace `import tes` / `from tes...` with `import tracegauge` / `from tracegauge...`.
3. Rename every `TES_*` variable to its `TRACEGAUGE_*` twin (table below). To find them:
   `env | grep '^TES_'` (PowerShell: `Get-ChildItem env:TES_*`).
4. Remove `TRACEGAUGE_NO_DEPRECATION_NOTICE` if you set it; it no longer does anything (setting it is harmless).

Nothing else changes: the store stays where it is, and scores, JSON field names and exit codes are the same.

The sections below describe how the old names behaved in 0.16 and 0.17, for anyone still on those versions.

## Python imports

```python
# before
import tes
from tes.adapt import adapt_session

# after
import tracegauge
from tracegauge.adapt import adapt_session
```

In 0.16 and 0.17, `import tes` worked and emitted one `DeprecationWarning` per process ("tes is deprecated, import
tracegauge; removed in 0.18."), pointing at the line that imported it. It was an alias, not a copy:
`tes.cost is tracegauge.cost`, so monkeypatching and `isinstance` checks behaved the same through
either name. Python hides `DeprecationWarning` outside `__main__` by default; run with
`python -W error::DeprecationWarning` (or `-W default`) to find every import that still says `tes`.
`import tracegauge` never warns.

## Command line

`tracegauge` is the command and `tgauge` is a short alias for it; both behave identically and
`--version` prints `tracegauge <version>`. In 0.16 and 0.17 the `tes` command ran but printed one
line to **stderr** first:

```
tes is deprecated; use tracegauge (or tgauge). It will be removed in 0.18.
```

The line goes to stderr only, so `--json` output and pipes are unaffected, and it is printed once per
run, not once per subcommand. Set `TRACEGAUGE_NO_DEPRECATION_NOTICE=1` (or `true` / `yes`) to
silence it, for example in a CI job you have not migrated yet.

## Environment variables

Each `TES_*` variable has a `TRACEGAUGE_*` replacement with the same meaning:

| Old | New |
|---|---|
| `TES_DB_PATH` | `TRACEGAUGE_DB_PATH` |
| `TES_PLAN_PATH` | `TRACEGAUGE_PLAN_PATH` |
| `TES_PRICE_TABLE` | `TRACEGAUGE_PRICE_TABLE` |
| `TES_NO_NOTICE` | `TRACEGAUGE_NO_NOTICE` |
| `TES_CORPUS_URL` | `TRACEGAUGE_CORPUS_URL` |
| `TES_CORPUS_ANON_KEY` | `TRACEGAUGE_CORPUS_ANON_KEY` |
| `TES_CORPUS_WITHDRAW_URL` | `TRACEGAUGE_CORPUS_WITHDRAW_URL` |

**From 0.18** only the `TRACEGAUGE_*` names are read. A `TES_*` variable set (non-empty) without its
`TRACEGAUGE_*` twin makes the command stop with exit code 1 and one stderr line:

```
[ERROR] TES_DB_PATH is no longer read (the TES_ names were removed in 0.18). Rename: TES_DB_PATH -> TRACEGAUGE_DB_PATH. tracegauge stops here rather than silently ignore it (for example, use a different database). See docs/MIGRATING_FROM_TES.md.
```

It stops instead of ignoring the variable because ignoring it would change what the command does
without any sign: a script that isolates its store with `TES_DB_PATH=/tmp/x.db` would read and
write the real `~/.tes/tes.db` instead. When both spellings are set, `TRACEGAUGE_*` is used and the
command runs. The library (`import tracegauge`) reads `TRACEGAUGE_*` only and never stops.

In 0.16 and 0.17, these rules applied so that a half-migrated setup did what you expect:

- If both are set, `TRACEGAUGE_*` wins.
- An empty value counts as unset, in both spellings. An empty `TRACEGAUGE_DB_PATH` therefore falls
  through to `TES_DB_PATH`; it does not mask it.
- If a `TES_*` variable is set without its `TRACEGAUGE_*` twin, the command line prints one stderr
  line naming it ("`TES_DB_PATH` is deprecated; use `TRACEGAUGE_DB_PATH`. ..."). The same
  `TRACEGAUGE_NO_DEPRECATION_NOTICE=1` silences it. Library calls never print.

To support 0.17 and 0.18 side by side, export both spellings with the same value
(`TES_DB_PATH=/tmp/x.db TRACEGAUGE_DB_PATH=/tmp/x.db`): 0.17 prefers the new one and 0.18 runs.

## Where your data lives: `~/.tes/` does not move

The store, the cached intelligence results, the optional `prices.json` and `plan.json`, and the
contribution files all stay under `~/.tes/` (on Windows `%USERPROFILE%\.tes\`). The directory name
is a storage detail, not a product name, and moving it would orphan every user's scored history
(or force a silent copy of session-derived data) for no functional gain. Nothing in tracegauge
writes anywhere else; `tests/test_store_location.py` checks that every default path resolves under
`~/.tes/`. If you want the database somewhere else, set `TRACEGAUGE_DB_PATH` or pass `--db-path`.

## Timeline

| Version | `tes` import / command / `TES_*` |
|---|---|
| 0.16 | work; warn |
| 0.17 | work; warn |
| 0.18 | removed |

## Strings that changed in the output: update anything that parses them

Scores, JSON schemas (field names and types) and exit codes are the same. Human-readable
strings are not all the same, so a script that matches on them must be updated. The three that
scripts are most likely to match are in the table below. The others are rename-only wording in
help text, hints and error messages: the command's argparse description, the `--db-path` help
text, the "No sessions found" hint, the store schema error ("Upgrade tracegauge."), the price
path in the report footer, the `approximate_reason` prose for an unknown model, the legacy-rows
notice hint, and the `corpus`, `ask` and judge hints, which now name `tracegauge` and the
`TRACEGAUGE_*` variables. Match on the field names, not on prose.

| Where | Before | Now |
|---|---|---|
| `--version` | `tes <version>` | `tracegauge <version>` (the program name is fixed, so the `tgauge` and `tes` commands print it too) |
| JSON field `cost_domain_of_validity` (prose) | "... override with TES_PRICE_TABLE env var or ~/.tes/prices.json ..." | "... override with TRACEGAUGE_PRICE_TABLE env var or ~/.tes/prices.json ..." (the field name and its position are unchanged; only the sentence differs) |
| Text report banner | `TOKEN-EFFICIENCY SCORER` | `TRACEGAUGE` |

Match on the field names, not on these sentences. The same variable-name change appears in the
other place that tells you how to override the price table (the "set TRACEGAUGE_PRICE_TABLE ..."
hints in `budget` and `takeaway` output).

## The bundled price table

`tracegauge/data/prices.json` (was `tes/data/prices.json`) holds the same rates, dates and model
entries as 0.15.0. Exactly two strings in it changed, both prose: the table-level `note` (internal
work-item labels removed, the module path `tes/cost.py` became `tracegauge/cost.py`) and the `note`
of the retired `claude-3-haiku` entry (one internal label removed). No rate, `as_of`, `source_url`,
multiplier or retirement flag changed; a user's own `~/.tes/prices.json` override is read exactly
as before. Reproduce: load both files with `json` and compare the parsed objects; only
`/note` and `/models/claude-3-haiku/note` differ.

Nothing else changes in the tool's output.
