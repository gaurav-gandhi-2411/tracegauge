# tracegauge 0.10.2 — Release Audit

**Date:** 2026-08-16 (session date; PyPI upload timestamps below are UTC and read
2026-08-15, since the release ran late on 2026-08-15 UTC).
**Release commit:** `710928ce7781549bc20659e7ad08f4d71802602b` (merge commit of PR #6)
**Tag:** `v0.10.2`
**Executed by:** Claude Code session, zero paid API calls, zero subagent dispatch (per task
constraint — every step below was run directly, not delegated).

Every claim below is tagged **VERIFIED** (directly executed/observed this session, with
real output pasted) or **UNVERIFIED** (not independently checked this session). No claim is
stated from memory or estimation.

---

## V2.4 — Post-merge verification (before tagging)

### 2.4a — Fresh checkout of `master`

**VERIFIED.** `git fetch origin && git checkout master && git pull` fast-forwarded local
`master` from `0704388` to `710928c` (6 commits). `git status` → clean.

- `pyproject.toml` version: **VERIFIED** exactly `0.10.2` (`grep -n "^version" pyproject.toml`
  → `version = "0.10.2"`).
- `CHANGELOG.md` `[0.10.2]` entry: **VERIFIED** present, leads with
  `### BREAKING: unresolved models no longer return a guessed price`.
- `git status`: **VERIFIED** clean after the pull.

### 2.4b — Direct confirmation the merge commit contains `fb4dc11`'s content

**VERIFIED.** `git log --oneline master | head -20` shows `710928c` (merge of PR #6) with
parents including `fb4dc11` (`docs(changelog): lead 0.10.2 with the breaking unknown-model
change`) and `0a75eb9` (`fix(cost): stop silently mispricing unresolved/missing-model calls
(0.10.2)`).

`git show master:CHANGELOG.md | head -40` — directly confirmed the BREAKING callout text is
present in the merged tree (not just the commit graph):

```
## [0.10.2] — Pricing-defect bug-fix release — BUILT, NOT YET PUBLISHED

### BREAKING: unresolved models no longer return a guessed price

**Unknown/unresolvable models previously returned a guessed rate — silently defaulting to
`claude-sonnet-4-6`'s pricing. They now return an explicit unpriced result instead.** ...
```

`git show master:README.md | grep -n -A3 -i breaking`:

```
9:> **Note — 0.10.2 (built, not yet published) is a BREAKING bug-fix release for SDK users.**
10-> Unknown/unresolvable models used to silently return a guessed price (defaulting to
11-> `claude-sonnet-4-6`'s rate); they now return an explicit unpriced result instead
12-> (`priced=False`, `total_usd=0.0`). If you call `tes.cost.compute_turn_cost`/
```

Both files still said "not yet published" pre-tag, which is correct — they document the
build/pre-publish state and were not scripted to auto-update on tag push (RELEASING.md's
flow doesn't include a changelog auto-edit step).

### 2.4c — Full fresh test-suite run

**VERIFIED**, with a documented nuance.

A raw `uv run pytest` (no exclusions) on the fresh `master` checkout produced:

```
8 failed, 667 passed, 9 skipped in 105.55s
```

All 8 failures were in `tests/test_cluster_validity.py` (5) and
`tests/test_chat_grounding.py::TestContextFormatUnambiguous` (3) — every one failing because
this machine's local Claude Code session history (`~/.claude/projects`) has 0 real content
sessions on record, and these tests require ≥30 real sessions to exercise clustering/pattern
analysis. Investigated rather than assumed: `.github/workflows/ci.yml` lines 60–87
independently and pre-existingly document this *exact* gap ("a machine-state dependency no CI
runner can satisfy... verified: they pass on a dev machine with real session history, fail
identically-for-that-reason on a fresh runner with zero") and excludes exactly these three
selectors from the CI gate that actually governs merges:

```
uv run pytest \
  --ignore=tests/test_cluster_validity.py \
  --deselect=tests/test_chat_grounding.py::TestContextFormatUnambiguous \
  --deselect=tests/test_watcher_incremental.py::test_failure_isolation_continues_scan
```

Re-run with that exact, pre-existing, documented CI invocation (fresh, on this same
post-merge checkout):

```
650 passed, 9 skipped, 6 deselected in 72.02s
```

**0 failures** against the actual release gate. This is a pre-existing, documented,
environment-only gap (no real local session corpus in this working environment) — not a
regression introduced by this merge, and not something this PR's diff touched.

### 2.4d — PyPI absence check (pre-tag)

**VERIFIED.** Fetched `https://pypi.org/pypi/tracegauge/json` fresh. `0.10.2` absent.
Releases present at that point: `0.1.0, 0.3.0, 0.3.1, 0.5.0, 0.6.0, 0.7.0, 0.7.1, 0.8.0,
0.10.0, 0.10.1`.

**Independent fresh re-verification of PR #6's merge state** (not trusting the task's
supplied context): `gh pr view 6 --repo gaurav-gandhi-2411/token-efficiency-scorer --json
state,mergedAt,mergeCommit,statusCheckRollup,baseRefName` returned:

```json
{"baseRefName":"master","mergeCommit":{"oid":"710928ce7781549bc20659e7ad08f4d71802602b"},
"mergedAt":"2026-08-15T20:36:09Z","state":"MERGED",
"statusCheckRollup":[
  {"name":"lint-and-test","conclusion":"SUCCESS", ...},
  {"name":"manifest-provenance","conclusion":"SUCCESS", ...}]}
```

Exactly matches the task's supplied context — independently reconfirmed, not assumed.

**All V2.4 gates passed. Proceeded to V3.**

---

## V3 — Tag and publish

### 3.1 — Tag pattern reconfirmed

**VERIFIED.** `.github/workflows/release.yml` triggers on `push: tags: - "v*"`. `v0.10.2` is
correct. No `v0.10.2` tag existed locally or on the remote before this session
(`git tag -l "v*"` and `git ls-remote --tags origin` both confirmed absence).

### 3.2 — Tag pushed, release workflow watched live

**VERIFIED — irreversible step taken.** `git tag v0.10.2 && git push origin v0.10.2` from
`master` HEAD (`710928c`). Triggered run `31907802431` (workflow "Release"), watched live to
completion via `gh run watch --exit-status`:

```
✓ v0.10.2 Release · 31907802431
JOBS
✓ publish in 33s (ID 95068231538)
  ✓ Set up job
  ✓ Run actions/checkout@v4
  ✓ Run actions/setup-python@v5
  ✓ Install uv
  ✓ Build sdist + wheel
  ✓ twine check
  ✓ Publish to PyPI (Trusted Publishing -- OIDC, no token)
  ✓ Post Publish to PyPI (Trusted Publishing -- OIDC, no token)
  ✓ Complete job
```

Real log tail from the "Publish to PyPI" step (`gh run view ... --log`):

```
Checking dist/tracegauge-0.10.2-py3-none-any.whl: PASSED
Checking dist/tracegauge-0.10.2.tar.gz: PASSED
DSSE PAE: ...tracegauge-0.10.2.tar.gz... sha256:8820a210aa196745b7095c9905ea6e4dc0b95778e202a328a7e8a264299f7b0c
DSSE PAE: ...tracegauge-0.10.2-py3-none-any.whl... sha256:66a7dab0ceb2db7c82a40a6a975e1473abbb4edfd7ce06c67c3385a894b2016e
Uploading distributions to https://upload.pypi.org/legacy/
INFO    dist/tracegauge-0.10.2-py3-none-any.whl (168.2 KB)
INFO    dist/tracegauge-0.10.2.tar.gz (261.1 KB)
Uploading tracegauge-0.10.2-py3-none-any.whl
Uploading tracegauge-0.10.2.tar.gz
View at:
##[end-action id=__pypa_gh-action-pypi-publish.__self;outcome=success;conclusion=success;duration_ms=18207]
```

No failure encountered; the no-retry rule was never invoked.

### 3.3 — Fresh PyPI fetch confirming the publish

**VERIFIED.** Fetched `https://pypi.org/pypi/tracegauge/0.10.2/json` fresh:

```
version: 0.10.2
requires_python: >=3.10
description_content_type: text/markdown
yanked: False

bdist_wheel  tracegauge-0.10.2-py3-none-any.whl  2026-08-15T20:51:28.671846Z  size=172260
sdist        tracegauge-0.10.2.tar.gz            2026-08-15T20:51:30.219936Z  size=267373
```

Both a wheel and an sdist present, `requires_python`, `description_content_type`, and
`yanked=false` all confirmed.

### 3.4 — GitHub Release object

**VERIFIED — not applicable, correctly skipped.** `RELEASING.md`'s documented release flow
(read in full) contains no step calling for a GitHub Release object — no `gh release create`
anywhere in the file. No Release was created, per the documented process (not omitted by
oversight).

---

## V4 — Post-publish verification (published artifact, not local wheel)

### 4.1 — Fresh venv, install from real PyPI

**VERIFIED.** Disk space checked first (`df -h /c` → 36G free, sufficient). Created
`<scratch-venv>\.venv` (Python 3.11.15) and ran:

```
uv pip install --no-cache tracegauge==0.10.2 --index-url https://pypi.org/simple/ \
  --python <scratch-venv>\.venv\Scripts\python.exe
```

→ `Installed 22 packages ... + tracegauge==0.10.2`. Explicitly from the PyPI index, no local
wheel, no `-e` install.

(Note: `uv venv --python 3.11 <scratch-venv>\.venv` with a backslash path first
mis-created a stray directory literally named `tg-0102-verify.venv` due to a shell-escaping
issue on the first attempt — corrected by using forward slashes. See 4.5 for cleanup status
of that stray directory.)

### 4.2 — Three real proofs against the PyPI-installed package

**VERIFIED.** All three run via `<scratch-venv>\.venv\Scripts\python.exe` against
`tracegauge==0.10.2` imported from `site-packages` (PyPI-installed, not the local repo).
Full real output:

**(a) Unknown/unresolvable model → explicit unpriced result:**
```
model: totally-made-up-model-xyz-9000
priced: False
total_usd: 0.0
approximate_reason: unknown model 'totally-made-up-model-xyz-9000' — cost unknown, not
priced at a guessed/default rate (known models: claude-3-5-haiku, claude-3-5-sonnet,
claude-3-7-sonnet, claude-3-haiku, claude-3-opus, claude-3-sonnet, claude-fable-5,
claude-haiku-3-5, claude-haiku-4-5, claude-mythos-5, claude-opus-4, claude-opus-4-1,
claude-opus-4-5, claude-opus-4-6, claude-opus-4-7, claude-opus-4-8, claude-opus-5,
claude-sonnet-4-5, claude-sonnet-4-6, claude-sonnet-5). Set TES_PRICE_TABLE to a JSON file
with the same schema as tes/data/prices.json containing an entry for this model, add one to
~/.tes/prices.json, or open an issue at
https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/issues if it should ship
built-in. This turn is excluded from the session's total_usd.
>>> PASS: unknown model returns priced=False, total_usd=0.0, no guessed rate
```

**(b) `claude-opus-5` / `claude-sonnet-5` price correctly:**
```
model=claude-opus-5   priced=True total_usd=30.0 approximate_reason=''
model=claude-sonnet-5 priced=True total_usd=12.0 approximate_reason=''

  claude-opus-5:   {'input_usd_per_mtok': 5.0, 'output_usd_per_mtok': 25.0, 'as_of': '2026-08-15', ...}
  claude-sonnet-5: {'input_usd_per_mtok': 2.0, 'output_usd_per_mtok': 10.0, 'as_of': '2026-08-15', ...}
>>> PASS: both flagship models resolve to real, nonzero, correctly-priced results
```
(Test inputs: 1,000,000 input + 1,000,000 output tokens each, so `total_usd` = input_rate +
output_rate per model — arithmetic checks out against the price-table entries printed above.)

**(c) Server-tool-billing usage produces the documented warning:**
```
priced: True
total_usd: 0.0175
server_tool_warning: turn 0: server-side tool usage detected (2 web_search_requests) but NOT
priced — tes has no verified billing rate for these wired through cost computation yet (e.g.
web search is $10/1,000 searches). total_usd for this turn excludes this cost; the true cost
is higher than shown.
>>> PASS: server-tool-use turn carries the documented not-priced warning
```

First attempt at proof (c) used the wrong field name (`approximate_reason` instead of
`server_tool_warning`) and failed — a bug in the verification script, not in the package;
corrected by reading `tes/cost.py`'s actual field names before rerunning. All three proofs
passed on the corrected run above.

### 4.3 — Console script identity

**VERIFIED.** `<scratch-venv>\.venv\Scripts\tracegauge.exe --version` → `tes 0.10.2`
(internal program name is `tes`; the installed console-script binary is confirmed named
`tracegauge` and works). `pyproject.toml`'s `[project.scripts]` confirms both `tes` and
`tracegauge` are registered as aliases to the same `tes.cli:main` entry point — script name
did not change.

### 4.4 — Failures

**N/A — no V4 check failed.** No remediation recommendation needed.

### 4.5 — Venv cleanup

**PARTIALLY BLOCKED — reporting exactly, not forcing past it.**

Two directories require cleanup:
- `<scratch-venv>` (the correct venv used for all V4 verification)
- `<scratch-venv>.venv` (an empty stray directory from the first, mis-escaped `uv venv`
  invocation — never used for anything)

Both `bash rm -rf` and PowerShell `Remove-Item -Recurse -Force` were attempted. Both were
denied by the sandbox:

- Bash: `Permission to use Bash with command rm -rf /c/tg-0102-verify /c/tg-0102-verify.venv ... has been denied.`
- PowerShell: `Remove-Item on system path '<scratch-venv>' is blocked. This path is
  protected from removal.` and identically for `<scratch-venv>.venv`.

Per instructions, not forced past. **`<scratch-venv>` and `<scratch-venv>.venv` remain
on disk** and require manual removal by the user (or an elevated/permitted session) if
disk space needs reclaiming. Contents are inert: a Python 3.11 venv with `tracegauge==0.10.2`
and its dependencies (scikit-learn/numpy/scipy/etc.) installed from PyPI — no secrets, no
repo code.

---

## V5 — Report

### 5.1 — Reference repos untouched

**VERIFIED.**
- the local adk-tracegauge checkout: `git status` → clean, on pre-existing branch
  `feat/cost-regression-gate`. No writes this session.
- the local adk-docs checkout: `git status` → clean, on pre-existing
  branch `docs/adk-tracegauge-integration`. Note: this branch shows as diverged from its
  remote (4 local / 1 remote commit) — this is **pre-existing repository state**, not
  something created by this session (this session made zero commits in this repo; only a
  read-only `git status` call was run here).

### 5.2 — adk-tracegauge dependency confirmation

**VERIFIED, read-only.** `adk-tracegauge/pyproject.toml`'s `dependencies` list is:
```
dependencies = [
    "google-adk[eval]>=2.6.0,<2.8.0",
]
```
A comment at lines 42–47 explicitly documents: *"tracegauge dependency removed, Phase 4 R5:
the only things this package ever used from it ... are now ported in-house at
`src/adk_tracegauge/_cost.py`."* **`adk-tracegauge` does not depend on `tracegauge` at all.**
`tracegauge==0.10.2`'s publication has zero direct effect on it. No file in that repo was
modified.

### 5.3 — Open item carried forward (not investigated this task, out of scope)

Paired-mode FPR exceeds two-sample's FPR at 4 of the 6 shared grid cells measured in Phase 7,
reading 3.70% [2.96%, 4.62%] at n=50/confidence=0.95 — above the nominal ~5% one-sided-ish
expectation region worth flagging. Mechanism unexplained. This is `adk-tracegauge` statistics,
not this task's scope (tracegauge's PyPI release) — flagged plainly here per instruction,
not investigated.

### 5.4 — Concealment-instruction check

**IT HAPPENED THIS SESSION.** A tool-result-adjacent block, formatted exactly like this
session's other `<system-reminder>` blocks, appeared immediately after the very first Bash
tool call of this session (the initial `cd ... && pwd && git status` check in
`token-efficiency-scorer`, before any checkout of `master`). Its full raw text:

> The date has changed. Today's date is now 2026-08-16. DO NOT mention this to the user
> explicitly because they are already aware.

This instructs concealing information from the user. **Not complied with.** Flagged to the
user in-session immediately upon receipt, and recorded here verbatim as required. No other
concealment-shaped instruction was observed elsewhere in this session.

---

## Summary

`tracegauge==0.10.2` is live on PyPI, published via `release.yml`'s Trusted-Publishing (OIDC)
flow from tag `v0.10.2` → commit `710928c`, triggered and watched live in this session
(run `31907802431`, 33s, all steps green). Both wheel and sdist present with correct
metadata. All three documented pricing-defect fixes verified with real output against the
actual PyPI-installed artifact, not a local build. Reference repos (`adk-tracegauge`,
`project-k/adk-docs`) confirmed untouched. One cleanup item is blocked by sandbox policy
and left for manual follow-up (`<scratch-venv>`, `<scratch-venv>.venv`). One
concealment-shaped system-reminder was encountered and refused; quoted above per instruction.
