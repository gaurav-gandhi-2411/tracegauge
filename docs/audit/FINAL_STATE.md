# Final State Report — adk-tracegauge + token-efficiency-scorer

Session date: 2026-08-20. Orchestrator run covering AI1–AI4 of the release/audit queue.
All claims below are VERIFIED via git/PyPI/gh CLI evidence captured during this session,
independently re-checked by a blind verifier subagent that did not see the release
executor's own output. Nothing here is stated from memory.

---

## AI1 — Both releases

### 1.1 Pre-flight sync

- `token-efficiency-scorer` release PR (`chore/release-0.12.2`) merged to `master` @
  `58d3f940878e63b020bfd76647f9ef5fc42206a9`. Local checkout fast-forwarded from `4b8b0cd`.
- `adk-tracegauge` release PR (`chore/release-0.5.1`) merged to `main` @
  `e06af2e487dfec1395fd6535f8a9bc5247e847da`. Local checkout fast-forwarded from `f76df6f`.
- Both repos confirmed clean (`git status --porcelain` empty) and `HEAD` matching the SHAs
  above at end of session.

### 1.2 — adk-tracegauge 0.5.1

**Pre-tag artifact check — VERIFIED.** `uv build` succeeded; clean venv installed the built
wheel (`<scratch-venv>`, short path — the default scratch path hit Windows `WinError 206`
filename-too-long via `litellm`'s `vertex_ai_partner_models` subpackage, a real instance of
this project's own documented MAX_PATH trap). Both independent version reads returned
`0.5.1`:
- `import adk_tracegauge; adk_tracegauge.__version__` → `0.5.1`
- `importlib.metadata.version('adk-tracegauge')` → `0.5.1`

**Tag/push — one transient retry.** `git tag v0.5.1` on `e06af2e`; first `git push` failed
with a GitHub-side `500 Internal Server Error` (confirmed via `git ls-remote` returning
empty — nothing landed, no partial state). This was a transport-layer failure, not a release
gate failure (build, version, and tag content had all already passed). A single plain retry
of the identical, already-correct push succeeded: `[new tag] v0.5.1 -> v0.5.1`.

**CI — VERIFIED success.** Run `32391172284`: build, `twine check`, PyPI publish (Trusted
Publishing/OIDC), and `gh release create` all green, ~27s total.

**Independent blind verification (separate subagent, fresh venv, no access to the release
executor's claims):**

| Check | Result |
|---|---|
| PyPI `info.version` | `0.5.1` — VERIFIED |
| PyPI README (`info.description`) contains exit-code-4 docs | VERIFIED — see verbatim snippet below |
| `v0.5.1` tag on GitHub → `e06af2e...` | VERIFIED |
| GitHub Release `v0.5.1` exists, published, not draft | VERIFIED (published `2026-08-20T16:18:05Z`) |
| Fresh PyPI install (`<scratch-venv>`, new venv) reports `0.5.1` | VERIFIED |

**Published-artifact verbatim (from PyPI's JSON API `info.description`, the actual rendered
README on the live PyPI project page):**

> `adk-tracegauge check` runs a percentile bootstrap on the difference in mean cost and exits
> with a **real, distinguishable exit code**: `0` pass, `1` regression, `3` insufficient data,
> `4` pass but underpowered (two-sample mode only — see "Known limitations" below; a real,
> non-zero exit code your CI should distinguish from a hard failure if it treats any non-zero
> exit as build-failing).

Extended discussion of exit code 4's runtime-detection mechanism and power analysis also
confirmed present in the "Known limitations" section of the published page.

### 1.3 / 1.4 — tracegauge 0.12.2

This release exists specifically to fix a real packaging gap surfaced by an earlier PR: `web/static/
brand/*.svg` and `web/static/fonts/*.woff2` were never declared in `[tool.setuptools.
package-data]`. A prior version installed from a real wheel (not the editable checkout used
to build the feature) would 404 on the dashboard's favicon and fall back to system fonts.
Both the pre-tag and post-publish checks specifically exercised the **installed wheel**, not
the checkout, per the standing instruction that testing against the checkout would not catch
a regression of this exact bug.

**Pre-tag artifact check — VERIFIED**, clean venv `<scratch-venv>`:
- `tes.exe --version` → `tes 0.12.2`; `importlib.metadata.version('tracegauge')` → `0.12.2`.
- dist-info license files present: `licenses/LICENSE`, `licenses/LICENSE-APACHE`.
- On-disk site-packages brand/font files confirmed present (2 SVGs, 5 woff2s).
- `tes serve` started with `TES_DB_PATH` pointed at an isolated scratch DB; favicon and font
  URLs (read from `base.html`) both returned HTTP 200 with correct content-type.
- **Real `~/.tes/tes.db` confirmed never written** — mtime unchanged before/during/after
  every server run this session (`2026-08-17 18:54:27`, stable throughout). One cosmetic
  false alarm caught and correctly investigated in-flight: the `tes serve` startup banner
  prints the literal string `~/.tes/tes.db` regardless of `TES_DB_PATH`, which is a display
  bug in `tes/cli.py` (~line 505), not an actual write-path bug — the real write path was
  confirmed correct via `store.resolve_db_path` and the scratch DB's own file growth. Filed
  as a new minor finding below (not one of the three known open issues).

**Tag/push — clean, no retries needed.** `v0.12.2` tagged on `58d3f94`, pushed successfully
first attempt.

**CI — VERIFIED success, log-confirmed (not just checkmark).** Run `32400917796`; log contains
the genuine upload confirmation `Successfully verified SCT...` and
`View at: https://pypi.org/project/tracegauge/0.12.2/`.

**GitHub Release — created manually** (this repo's `release.yml`, unlike adk-tracegauge's,
has no auto-create step): `gh release create v0.12.2 --generate-notes`, confirmed via
`gh release view v0.12.2`.

**Independent blind verification (separate subagent, fresh venv `<scratch-venv>`, no access
to the release executor's claims):**

| Check | Result |
|---|---|
| PyPI `info.version` | `0.12.2` — VERIFIED |
| `v0.12.2` tag on GitHub → `58d3f94...` | VERIFIED |
| GitHub Release `v0.12.2` exists, published, not draft | VERIFIED (`2026-08-20T18:02:47Z`) |
| Fresh PyPI install reports `0.12.2` (`tes.exe --version`) | VERIFIED |
| Brand SVGs + font woff2s present in fresh install's site-packages | VERIFIED (2 SVGs, 5 woff2s, all non-empty) |
| Real `~/.tes/tes.db` untouched during blind verifier's own `tes serve` run | VERIFIED — exact mtime match before/after |
| Favicon URL → HTTP 200, `image/svg+xml` | VERIFIED |
| Font URL → HTTP 200, `application/octet-stream` | VERIFIED |

**On the screenshot requirement (1.4):** no browser/screenshot automation tool is available
in this session (checked via tool search — nothing beyond `WebFetch`, which cannot render or
screenshot). `playwright`/`chromium` are not declared dependencies of this repo
(`pyproject.toml`/`uv.lock` grepped, no match) despite a changelog line from an earlier
session claiming a headless-Chromium visual check — that check is not reproducible in this
environment. In its place, the release executor performed a byte-level equivalent: it
byte-compared (`diff` + `md5sum`, identical hashes) the HTTP response body of the served
favicon and a font file against the corresponding on-disk file inside the installed venv's
`site-packages`, proving the running server was genuinely serving the installed package's own
asset bytes, not a fallback or a stale copy. This is offered as the verified substitute, not
a claimed equivalent to a visual screenshot — flagged explicitly rather than silently assumed
equivalent.

### 1.5 — GitHub Releases

- adk-tracegauge: auto-created by `release.yml`'s `gh release create` step. VERIFIED live.
- tracegauge: manually created (`gh release create v0.12.2 --generate-notes`) since this
  repo's workflow has no auto-create step. VERIFIED live.

---

## AI2 — Corpus consent, verified from the published artifact

Installed `tracegauge==0.12.2` fresh from PyPI (blind verifier's own venv, `<scratch-venv>`,
independent of the release executor's earlier install). Ran both commands directly.

**`tes corpus contribute` — verbatim output:**

```
[NOT AVAILABLE] No community corpus is currently operated — `tes corpus contribute` has
nowhere to send data yet. The code path is built and tested (see PRIVACY.md); it activates
once a corpus is provisioned. Nothing is sent by this command today.
```

**`tes corpus withdraw` — verbatim output:**

```
[NOT AVAILABLE] No community corpus is currently operated — there is nothing to withdraw
from yet. This command will work once a corpus is provisioned; see PRIVACY.md.
```

Both commands exited immediately with the `[NOT AVAILABLE]` message and asked **no**
interactive question — confirms the fix: the availability check now runs before any consent
prompt, closing the gap where a user answering "y" to "Send to the community corpus?"
believed data was transmitted when it never could be.

**`tes corpus --help` — verbatim matching text:**

```
NOT YET ACTIVE: no public corpus is currently operated, so these subcommands print
[NOT AVAILABLE] and do nothing until one is provisioned — see PRIVACY.md.
```

Per-subcommand help also confirmed:

```
contribute   Preview + consent + send content-free session aggregates to the community
             corpus. NOT YET ACTIVE — no corpus is operated.
withdraw     Delete every row tied to your contributor_id from the community corpus.
             NOT YET ACTIVE — no corpus is operated.
```

AI2 fully VERIFIED — 2.2 and 2.3 both pass.

---

## AI3 — Task list integrity

**3.1/3.2 — Reconciled against actual state, not memory.** Every release-related item was
checked against PyPI/git directly this session rather than trusted from any prior list:

- tracegauge 0.12.0, 0.12.1 were, in fact, already live on PyPI before this session started
  (confirmed via tag history `v0.12.0`, `v0.12.1` present and PyPI JSON API's version history
  — not re-verified in depth this session since they were not in scope, but their tags exist
  and 0.12.2 published cleanly on top of them with no gaps).
- adk-tracegauge 0.5.0 was live; 0.5.1 was the only pending item, now shipped.
- No stray tags, no tag/PyPI-version mismatches found in either repo.

**3.3 — Why completions weren't propagating: found a real, distinct root cause, not the one
implied by the prompt.** The specific claim "Q5 (tracegauge 0.12.1) shown as open after
being tagged/published/verified" does not correspond to any persisted artifact in this repo
— grepped `PLAN.md`, `CURRENT_STATE.md`, `NEXT_PHASE.md`, `CHANGELOG.md`, and all of
`docs/audit/*.md` for the literal string `Q5`: zero matches anywhere. "Q5" was very likely an
ephemeral in-session task list (e.g. a `TodoWrite`-style list) from a single prior CC session,
which by design does not persist across sessions — so it cannot be inspected now, and its
staleness (if real) left no durable trace to audit.

**A separate, independently-verified propagation gap was found and is more actionable:**
this project's own session-memory notes file stops at the **0.10.0** release
(2026-07-04) and was never updated through 0.11.0, 0.11.1, 0.12.0, 0.12.1, or 0.12.2 —
five shipped releases with zero corresponding memory update, despite `CHANGELOG.md`
documenting all five. `MEMORY.md`'s index line for this file still reads *"0.10.0 LIVE on
PyPI: live monitor+alarm+budget shipped, coach HELD"* as of this session's start — actively
misleading for any future session that trusts it without re-verifying against PyPI, a failure
mode this project has already hit once (a stale local checkout read after a restart). This
memory file has been corrected as part of this session's work (see below) — the durable version of
the "stale task list" problem is now fixed; the ephemeral-list version cannot be, by
construction, and the fix for that class of gap is procedural (checkpoint into PLAN.md more
consistently), not something further auditing here will surface.

---

## AI4 — Final state

### 4.1 — Published versions, confirmed live and installable

| Package | Version | PyPI | Fresh-venv install confirmed |
|---|---|---|---|
| `adk-tracegauge` | 0.5.1 | https://pypi.org/project/adk-tracegauge/0.5.1/ | VERIFIED (`<scratch-venv>`) |
| `tracegauge` | 0.12.2 | https://pypi.org/project/tracegauge/0.12.2/ | VERIFIED (`<scratch-venv>`, `<scratch-venv>`) |

### 4.2 — Repo state

Both `main`/`master` clean, branch-protected (required status checks, `enforce_admins: true`,
required PR reviews), and in sync with the tagged release commits. Zero open PRs in either
repo as of this session's end.

**Open issues:**

adk-tracegauge: none open.

token-efficiency-scorer:
- `CorpusNotConfigured` exception exported but never raised (dead code).
- `backfill_turn_counts()` has zero callers (dead code, self-documented, confirmed still true).
- two dead `sqlite3` imports in near-identical `cli.py` try blocks.

**New minor finding this session (not yet filed as an issue):** `tes/cli.py`'s `serve`
startup banner (~line 505) prints the literal string `~/.tes/tes.db` for the "Database:"
line regardless of whether `TES_DB_PATH` is set — cosmetic only (the actual resolved write
path, via `tes/store.py`'s `resolve_db_path`, is correct), but misleading output that cost
real verification time this session (required tracing actual file mtimes to rule out a real
violation of the never-write-to-real-`~/.tes/` rule). Worth a one-line fix in a future patch.

### 4.3 — External PRs against Google repos

Two of the six have changed status since this queue was written; both re-verified via `gh pr
view` including comments, not assumed from title alone.

| Repo | PR | Status | Detail |
|---|---|---|---|
| google/adk-docs | [#2128](https://github.com/google/adk-docs/pull/2128) | **CLOSED, declined** | Maintainer (`joefernandez`) closed with explicit feedback: *"We would like to see this integration grow more adoption and maturity before we list it as an ADK integration."* Not a bug, not abandoned — a deliberate pass on inclusion in the docs catalog. |
| google/adk-python | [#6739](https://github.com/google/adk-python/pull/6739) | OPEN | `fix(evaluation): honor each metric's own eval_status in AgentEvaluator.evaluate()` — active review discussion. |
| google/adk-python | [#6740](https://github.com/google/adk-python/pull/6740) | OPEN | `fix(cli): adk eval process exit code now reflects PASSED/FAILED`. |
| google/adk-python | [#6710](https://github.com/google/adk-python/pull/6710) | OPEN | `fix(evaluation): record NOT_EVALUATED instead of dropping invocations with zero auto-rater samples` — reviewer comment from a second-party auditor ("mycroft") present, unresolved. |
| google/adk-python | [#6682](https://github.com/google/adk-python/pull/6682) | OPEN | `fix(evaluation): NOT_EVALUATED metric no longer masked by a passing one`. |
| google/adk-python | [#6681](https://github.com/google/adk-python/pull/6681) | **MERGED** (via Copybara) | GitHub shows `state: CLOSED`, `mergedAt: null` because Google imports external PRs internally via Copybara rather than a native GitHub merge — but the change is genuinely live: verified commit `023f45c3e5846c3e72525b53f16ef018b5ecdaa6` exists on `google/adk-python`'s `main`, commit message reads `Merge https://github.com/google/adk-python/pull/6681`, carries a `PiperOrigin-RevId`. Confirmed via `gh api repos/google/adk-python/commits/023f45c`. |

### 4.4 — What each package does, measures, and does not claim

**adk-tracegauge** is a CI cost-regression gate for Google ADK agent evaluations: it runs a
two-sample percentile-bootstrap test comparing an agent's per-eval-case token cost between a
baseline and a candidate run, and exits with a distinguishable code (`0` pass, `1`
regression, `3` insufficient data, `4` pass-but-underpowered) so a CI pipeline can gate merges
on cost regressions the same way it gates on test failures. It measures cost only — token
counts converted to USD via a maintained pricing table — not output quality, correctness, or
task success; those remain the job of ADK's own `AgentEvaluator`. It explicitly does not
claim its bootstrap test has validated statistical power for every possible eval-case count
(exit code 4 exists precisely to flag when a "pass" verdict is underpowered rather than
silently reporting a false confidence), and it does not claim compatibility with
`AgentEvaluator`'s `App`/plugin-based custom-metric path — that integration route was found,
live-verified, and documented not to work against `google-adk==2.6.3` (see the adk-docs #2128
history above), with a hand-rolled `Runner` harness documented as the actually-working
integration path today.

**tracegauge** (import name `tes`) is a three-axis efficiency scorer for Claude Code coding
sessions: token economy (cost/waste relative to a self-baseline), trajectory quality (via an
optional LLM judge), and deterministic waste detection (context-resend, redundant reads,
repeated-failed-retries, and similar patterns detected without any LLM call). It is local-only
by default — no server, no telemetry, nothing transmitted unless the user explicitly opts in
per-feature (the API judge, or the local contribution export). It explicitly does not claim
predictive power ("I don't predict" is a hardcoded chat-layer boundary for future-looking
questions), does not claim validated accuracy against human judgment for the trajectory-
quality axis outside its existing calibration work, and does not operate a community corpus
today — that capability is fully built and tested but deliberately dormant (see 4.5).

### 4.5 — Deliberately not built, with revisit triggers

- **Community-corpus baseline** (`tes corpus contribute`/`withdraw`, cross-developer
  percentile baselines): code, RLS policy, content-free send-time guard, and tests are all
  complete (`docs/audit` and `CURRENT_STATE.md`'s 0.9.0 section). Not activated because zero
  contributors exist and provisioning live transmission infrastructure before anyone can use
  it is premature. **Revisit trigger:** the moment a real user asks for cross-developer
  baselines, or the maintainer decides contributor acquisition is worth pursuing —
  activation is a documented ~20-minute checklist (`CURRENT_STATE.md` lines 116–136), not a
  rebuild.
- **OG-preview automation** (uploading a social-preview image to GitHub's repo settings):
  `assets/brand/og-preview.svg` (1200×630) exists but only as SVG — GitHub's social-preview
  upload slot requires a raster PNG/JPG, and no GitHub API exists for this upload (it's a
  Settings-UI-only action). **Revisit trigger:** none needed technically — this is purely a
  manual step blocked on API absence, not a design decision; route to the maintainer (below).
- **Q6 — dashboard trajectory timeline + sub-agent spawn trees:** design-only
  (`docs/design/DASHBOARD_TRAJECTORY_TIMELINE.md`), explicitly routed for review before any
  implementation starts, per the doc's own header. **Revisit trigger:** design sign-off from
  the maintainer.
- **Cost-quality Pareto frontier / changepoint detection on cost trends:** feasible (per
  `docs/audit/COMPETITIVE_GAP_ANALYSIS.md`'s Phase 8 "rigor + demand" test), but killed
  alongside multi-source adapter support (§2.4) for the identical reason: real feasibility
  without validated demand is architecture for its own sake. **Revisit trigger:** an
  unprompted external user request for either, per the same test used to kill "Option C" and
  HH2/HH3.1-2.
- **Option C — engine consolidation:** killed under Phase 8's demand test in an earlier
  phase, cited again in the Phase-9-era competitive-gap analysis as the reference case for
  "feasible, zero demand." **Revisit trigger:** same as above — a validated demand signal, not
  a feasibility argument.

---

## ROUTE TO THE MAINTAINER

**PRs awaiting merge:** none. Zero open PRs in either repo as of this session's end.

**Venvs/paths the sandbox would not let this session delete** (all outside the repos, all
inert — no running processes, none referenced by either repo, safe to delete at your
convenience):
- two adk-tracegauge pre-tag/post-publish verification venvs
- six tracegauge pre-tag/post-publish/blind-verify venvs and their isolated scratch DB
  directories

**Paid Gemini validation run (~$0.0065)** — unchanged from the prior session's report
(`adk-tracegauge/docs/audit/AUTONOMOUS_RUN.md`, "ROUTE TO THE MAINTAINER" section, R1), restated here
since it remains un-run and this queue asked it be carried forward: confirms whether
Ollama's local 7B-model cost-variance measurement is representative of a real hosted model.
Steps: swap `LiteLlm(model="ollama_chat/qwen2.5:7b")` for `model="gemini-2.5-flash-lite"` in
the existing 36-case evalset (no synthetic price table needed — real published rate).
Estimated cost ≈$0.0065 total (mean 107.8 input + 423.2 output tokens × 36 cases), a few
cents even with a 3–5x safety margin. Requires a real `GOOGLE_API_KEY`, which this
environment does not have. Full detail in `docs/audit/AD2_REAL_CV_MEASUREMENT.md` §2.3.

**OG-preview PNG upload to GitHub's social-preview slot:** `assets/brand/og-preview.svg`
(1200×630) exists and is ready; needs conversion to PNG/JPG and manual upload via each
repo's Settings → Social Preview — no GitHub API exists for this action.

---

## Memory correction made this session

`project_context.md` (auto-memory) updated to reflect 0.11.0 through 0.12.2 having shipped —
it previously stopped at 0.10.0, a five-release gap with no corresponding memory entry. See
AI3 above for the full finding.
