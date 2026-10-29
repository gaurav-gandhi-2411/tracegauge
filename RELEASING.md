# Releasing tracegauge

Every release from `0.1.0` through `0.10.0` was published by hand — a manual `uv build` +
upload with a locally-held PyPI API token, nothing about the process written down. Starting
with `0.10.1`, releases are tag-triggered and published by CI over PyPI Trusted Publishing
(OIDC) — no token generated, handled, or stored anywhere, by anyone, ever again.

This document describes the actual flow, written after running it for real for `0.10.1`
(2026-08-13) — not a plan for how it should work.

## The flow

1. **Bump the version** in `pyproject.toml` (`[project].version`), then run `uv lock` so
   `uv.lock`'s own entry follows, and add the `CHANGELOG.md` entry. `tests/test_version_consistency.py`
   fails if pyproject, the installed metadata, `uv.lock` and the CHANGELOG's newest release heading
   disagree. (Until 0.14.0 `tests/test_packaging.py` asserted the version as a literal string that
   silently went stale; that literal is gone, so there is nothing to hunt for. Still search `tests/`
   for any new hardcoded version before releasing.)
   - Add a `CHANGELOG.md` entry documenting what shipped and why, following the existing
     format (a note at the top of the file tracks which versions were actually published to
     PyPI vs. built-but-held).
2. **README must document any user-facing command or flag this release adds.** The
   CHANGELOG alone is not sufficient — it tells *existing* users what changed since their
   last install; the README is what a *prospective* user (or PyPI's own rendered project
   page) reads to learn the tool exists at all. A feature with a CHANGELOG entry but no
   README section is invisible to anyone who hasn't already installed the package.

   **Real incident, not hypothetical:** `0.11.0` (`tracegauge cost --week/--month/--since`) shipped
   to PyPI with a correct, detailed CHANGELOG entry and zero mentions of `tracegauge cost` anywhere
   in the README — caught only after the release was already published and permanently
   locked into that version's PyPI page (PyPI does not allow re-uploading a version's
   metadata). Fixed in the next release, `0.11.1`, but the gap in `0.11.0`'s own published
   page is permanent. The same sweep also found the README's top-of-file version banner
   still claimed `0.10.2` was "not yet published" a full two releases after it actually
   shipped — a stale claim, not a new one, but the same root cause: nothing in the release
   process re-checks the README against reality. Checklist for every release:
   - Every new subcommand and flag has a README section with **real captured output from
     the published artifact**, not an invented example.
   - Any documented behavioral rationale (a filtering-column choice, a known gap) is stated
     plainly, not left to the CHANGELOG alone.
   - Any "not yet published" / "pending" language anywhere in the README is re-checked
     against the real PyPI state before this release ships — it goes stale the moment the
     thing it's describing actually publishes, and nothing else catches that.
   - Verify the *published* README (`curl -s https://pypi.org/pypi/tracegauge/<version>/json`,
     string-search the `description` field) contains the new command/flag names — not just
     that the local `README.md` file does; a build/publish step could in principle diverge.
   - **Every checkable figure in `README.md` and `CURRENT_STATE.md`** (test counts, session/
     corpus counts, model names, version numbers) is re-verified against current reality
     before tagging — not just the newly-added ones. Test counts drift silently every time
     tests are added without a release (e.g. a `README.md` line still citing an old total
     while the suite has since grown); re-run `uv run pytest tests/ -q` and compare against
     any hardcoded count in prose. `CURRENT_STATE.md`'s per-milestone historical counts
     (364/377/472/543/601/…) are point-in-time records of that milestone and should NOT be
     changed to the current total — only fix a figure there if it misstates what was true
     *at that milestone*, not because the repo has grown since.
   - **Every doc code block in `README.md` presented as copy-pasteable actually runs, verbatim,
     against a real session file** — not just read for syntax correctness. Real incident: the
     "SDK usage" block called `adapt_session("path/to/session.jsonl")` with a bare string;
     `adapt_session`'s real signature takes a `Path` and calls `.stem` on it internally, so the
     block crashed with `AttributeError: 'str' object has no attribute 'stem'` on the very
     first real line — found via a cross-repo examples/config-block audit, not by anyone
     actually running the block before it shipped.
   - **Any pin of THIS package's version inside a sibling repo's docs is not this repo's job to
     catch, but this repo's own version pins (in reproduction instructions, quick-start blocks,
     the SDK example) get exactly this same re-check every time.** Real cross-repo incident:
     `agentgauge`'s two academic papers both cited `agentgauge-harness==0.5.2` in their
     reproduction instructions a full release after `0.5.3` shipped — same failure mode this
     bullet exists to prevent here, just in a different repo.
3. **Commit and open a PR.** CI (`ci.yml`) runs the normal lint/test suite against the
   version-bumped code. Merge once green.
4. **Tag the merged commit and push the tag:**
   ```bash
   git checkout master && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
5. **`release.yml` takes it from there** — triggered by the `v*` tag push, it checks out
   that exact tag (never a branch head), builds with `python scripts/build_dist.py dist` in the preflight,
   sanity-checks the artifact with `twine check`, and publishes the preflight's own files via `pypa/gh-action-pypi-publish@release/v1` using the
   `pypi` GitHub Environment's OIDC token. Confirm it actually succeeded — don't just trust
   a green checkmark, read the log for the real upload confirmation:
   ```bash
   gh run list --repo gaurav-gandhi-2411/tracegauge --workflow release.yml --limit 1
   gh run view <run-id> --repo gaurav-gandhi-2411/tracegauge --log | grep -A1 "View at"
   ```
   A genuine publish ends with a Sigstore `Successfully verified SCT...` line and
   `View at: https://pypi.org/project/tracegauge/X.Y.Z/` — that URL is the actual proof, not
   the workflow's green checkmark alone (a green run that never reached the upload step
   would look identical in the checks UI).

   **Reproducible builds.** `scripts/build_dist.py` runs the stock setuptools backend through
   `uv build` with `SOURCE_DATE_EPOCH` set to the commit time of `HEAD`, then rewrites only the
   sdist's tar and gzip metadata (setuptools honours the epoch for the wheel but not the sdist).
   `.gitattributes` forces LF in every checkout. `release.yml` builds the commit twice on separate
   runners and its `reproducible` job refuses to publish unless the wheel and sdist sha256 values
   match; each build also prints them in its job summary. Each build also uploads its `dist/` as the
   `dist-a` / `dist-b` artifact; the `verify-artifact` job (which also runs on `workflow_dispatch`) and the
   `publish` job download it and run `scripts/verify_dist.py`, which refuses unless the directory holds
   exactly the wheel and sdist whose sha256 values are in both checked `SHA256SUMS` files. `publish` uploads
   those downloaded files; it never rebuilds. The build backend is pinned (`setuptools==84.0.0` in
   `[build-system].requires`, the version that built the published 0.15.0 wheel) and `build_dist.py` raises
   if the wheel's `Generator:` line names another version: bump the pin deliberately, in its own commit. Releases are built in CI only: a build on
   another OS can differ in the wheel (zip `create_system`, `METADATA` line endings), so compare
   hashes only against a CI build of the same commit. A plain `uv build` is not reproducible.
6. **Post-publish verify from a fresh environment against the real index** — see below.
   Also confirm (step 2's checklist) that the published README actually rendered the new
   commands/flags, via PyPI's JSON API — not just that CI succeeded.
7. **Never delete a published version**, including a superseded one. Deletion burns the
   version number permanently and breaks every pinned install of it. If a release has a real
   problem, ship a new version that supersedes it — that's what versioning is for. Yank
   ([PEP 592](https://peps.python.org/pep-0592/)) only a version that harms a class of users: it
   cannot be installed or imported for them, or it has a security problem (0.12.3 to 0.13.0 were
   yanked for that reason). A yank is reversible; the procedure is in
   [docs/HOTFIX.md](docs/HOTFIX.md).

## Pre-release price check (local, needs real transcripts)

CI cannot see real transcripts, so before tagging run the price and usage-field checks locally:

```
python scripts/check_transcript_models_priced.py     # every model id in ~/.claude/projects must price
python scripts/check_usage_fields_priced.py            # every usage key path/value in ~/.claude/projects must have a rule
python scripts/check_price_table_vs_vendor.py        # table vs Anthropic's page (needs network)
```

The first exits 1 and lists any model id found in your transcripts that `tracegauge/data/prices.json`
cannot resolve (exit 2 = nothing could be scanned; `<synthetic>` is excluded by design). Fix a
miss by adding the model from the official pricing page, then add its id to
`tests/fixtures/transcript_models/` so the always-on CI test keeps guarding it. Not modelled by
design, because a transcript cannot show them: fast mode, `inference_geo` data-residency uplift,
the Batch discount and server-tool charges. Haiku 5.5's over-100k-prompt tier is exact for main
turns and reported as a floor for aggregated subagent usage.

## The tag-must-match-pyproject gotcha

`pyproject.toml` has no dynamic versioning (no `setuptools-scm`, no `hatch-vcs`) — the
published version comes entirely from `[project].version`, not from the git tag name.
**The tag is a trigger, not a version source.** If you push `vX.Y.Z` while
`pyproject.toml` still says the previous version, `release.yml` builds and publishes the
*previous* version under the *new* tag — a real, permanent mismatch, since "never delete"
above means it can't be un-published. Always confirm the version landed on `master` and merged
*before* tagging:

```bash
git show origin/master:pyproject.toml | grep "^version"
```

This gotcha bit the `adk-tracegauge` sibling package's `0.1.0rc1` release before it ever got
tagged — caught in review before the tag was pushed, not after. Same repo pattern, same
fix: bump-then-merge-then-tag, in that order, every time.

## Post-publish verification

Manual, deliberately — not automated (see the backlog note in this repo's memory/planning
docs: adding an automated post-publish step to `release.yml` is planned as one dedicated
change across all PyPI-published repos in this portfolio, not built yet).

```bash
# Fresh venv at a SHORT path -- see the MAX_PATH trap below for why "short" is load-bearing
uv venv --python 3.11 C:\tg-verify
uv pip install --no-cache tracegauge==X.Y.Z --index-url https://pypi.org/simple/ --python C:\tg-verify\Scripts\python.exe

C:\tg-verify\Scripts\tracegauge.exe --version   # confirm it reports X.Y.Z

# Confirm the license files actually shipped in the distribution's own metadata --
# not just the SPDX header comments in source, the dist-info itself
find C:\tg-verify\Lib\site-packages\tracegauge-X.Y.Z.dist-info -iname "*LICENSE*"
# expect: .../licenses/LICENSE, .../licenses/NOTICE and, in the package itself, tracegauge/web/static/fonts/OFL-*.txt

rm -rf C:\tg-verify
```

## The MAX_PATH trap (Windows) — presents as a package defect, isn't one

Verifying `adk-tracegauge` (a downstream package that depends on `tracegauge` plus
`google-adk[eval]`, whose transitive dependency tree includes `google-cloud-aiplatform` —
notoriously deep, long file paths) from a venv created under a deeply-nested temp/scratch
directory produced a real, reproducible `ModuleNotFoundError` on import — a file that
genuinely existed on disk, at a path 264 characters long, one past Windows' classic
260-character `MAX_PATH` limit. The traceback looks exactly like a broken/corrupted install
or a real packaging bug in the dependency — it is neither. Re-running the identical install
and import from a short path (`C:\tg-rc1-test` rather than a `...\AppData\Local\Temp\...`
scratch path several directories deep) passed cleanly on the same machine, same Python,
same package versions.

**Lesson:** always create post-publish verification venvs at a short path
(`C:\<name>`, not a deeply nested temp directory) on Windows, especially for any package
whose dependency tree touches `google-cloud-aiplatform` or similar deep-namespace packages.
If a fresh-venv verification fails with a `ModuleNotFoundError` for a module that
demonstrably exists on disk, check the full resolved path length before concluding the
package itself is broken.
