# Hotfix procedure

How to ship a fixed release quickly and safely, and how to take a broken one out of circulation. The normal
release flow is in [RELEASING.md](../RELEASING.md); this page is the short path for a fix, plus the yank
procedure. Nothing here is automatic: the maintainer merges and approves each step. The timings and the
problems below come from a real hotfix, 0.17.1 (October 2026), not from a rehearsal.

## When to ship a hotfix

* The published version crashes or gives a wrong answer for a class of users, or exposes something it
  should not: ship a new patch version (`0.17.0` to `0.17.1`).
* The problem is cosmetic or only affects a corner nobody has hit: put the fix on master and wait for the
  next release.
* A broken release is never repaired in place. PyPI does not allow re-uploading a version, and a moved tag
  would no longer match what was published.

## Steps

1. **Fix on a branch** from `master` (`fix/<what>`), with the test that fails before the fix (a bug fix
   ships with the test that would have caught it). Merge it like any change.
2. **Bump the version** on a separate branch: `pyproject.toml`, `uv lock`, and the `CHANGELOG.md` entry with
   the release date. `tests/test_version_consistency.py` and `scripts/check_release_version.py` fail if the
   version sources disagree. CI runs the full matrix (Windows, macOS, Linux; Python 3.10 to 3.14).
3. **Check the canary first.** The release workflow refuses to publish unless the latest *scheduled*
   `pypi-canary` run on `master` is green and at most 3 days old (see "A new repository cannot release on
   day one" below). Look before tagging:
   `gh run list --workflow pypi-canary.yml --event schedule --limit 1`.
4. **Tag the merged commit** and push the tag (`git tag vX.Y.Z`, `git push origin vX.Y.Z`). The tag push
   starts `release.yml`: the version-sources check, the canary gate, two independent builds, the
   reproducibility comparison and the artifact check.
5. **Approve the `pypi` environment** on the run page. Publishing uses PyPI Trusted Publishing (OIDC); no
   token exists anywhere. The `publish` job uploads the files the checked preflight produced; it never
   rebuilds.
6. **Verify from a fresh environment against the real index:** install the exact version into a new venv,
   `tracegauge --version`, `tracegauge quickstart`, score one session; compare the sha256 of both files on
   `https://pypi.org/pypi/tracegauge/X.Y.Z/json` with the sums the CI preflight printed; check every image,
   badge and link of the PyPI page; re-run the input that crashed.
7. **Publish the GitHub Release** for the tag with the changelog entry as its notes.

## Timings of the 0.17.1 hotfix

Measured from the run's own timestamps (UTC):

| Step | Time |
|---|---|
| Tag pushed, release run started | 2026-10-20 04:32:05 |
| Run finished: every check green except the canary gate, which refused; nothing published | 04:35:22 (3 min 17 s) |
| First scheduled canary run on the new repository (green) | 2026-10-20 11:53 |
| Run re-run on the same tag (no re-tag) | 2026-10-22 21:16:32 |
| All checks green, waiting for the `pypi` approval | 21:18:47 (2 min 15 s) |
| Approved; files on PyPI (`View at:` line) | 21:19:59 |

The machine time of a release is about 3.5 minutes of checks plus under a minute of publishing. The elapsed
time of this hotfix was 2 days 16 hours, almost all of it waiting: first for the canary gate (below), then for
the maintainer to be available to re-run and approve. Post-publish checks on 0.17.1: both file hashes on PyPI
equal the CI preflight's sums; the PyPI page's 6 images and badges and 18 links all return 200; the input that
crashed 0.17.0 (a `null` token count plus non-JSON-object lines) now scores with exit code 0 and two warnings;
300 random public SWE-chat v2 sessions (seed 42; the session that made 0.17.0 exit 4 is one of them) all score
with exit code 0, no traceback and valid JSON (median 0.72 s, 90th percentile 1.09 s, slowest 2.42 s per
session on one Windows laptop).

## A new repository cannot release on day one

The canary gate counts only *scheduled* `pypi-canary` runs on `master`, so that a manual run cannot turn it
green without anything being fixed. A repository that has just been created (or recreated) has no scheduled
run at all, so its first release is refused until the daily schedule has fired once. GitHub registers a
scheduled workflow only after a push changes the workflow file on the default branch, so a repository whose
workflows were pushed with Actions disabled waits until that has happened too. 0.17.1 hit exactly this: the
tag was pushed 7 hours before the first scheduled run, and the run had to be re-run afterwards.

What to do: after creating a repository, make sure the workflow files are registered (`gh workflow list`
shows `PyPI Canary` as active) and wait for one scheduled canary run before tagging. A refused run is
harmless: nothing is uploaded, and the same run can be re-run on the same tag once the canary is green
(`gh run rerun <run-id>`). Do not re-tag and do not move the tag.

**Bootstrap exception:** while **no scheduled canary run exists at all**, the gate judges the latest
`workflow_dispatch` canary run on `master` instead, by the same rule (green, at most 3 days old), and the job
summary says "BOOTSTRAP EXCEPTION USED". So on a new repository, run the canary by hand
(`gh workflow run pypi-canary.yml --ref master`), wait for it to pass, then tag. Once any scheduled run
exists, in any state, only scheduled runs count again: a manual run never overrides a red or stale scheduled
run. The rule is in `scripts/canary_gate.py`, with tests in `tests/test_canary_gate.py`.

## Yanking a release

A yank ([PEP 592](https://peps.python.org/pep-0592/)) hides a version from every install that does not name
it exactly: `pip install tracegauge` and version ranges skip it, `pip install tracegauge==X.Y.Z` still works
and prints a warning. It is reversible.

Yank only when the version cannot be installed or imported for a class of users, or it has a security
problem. Do **not** yank for a wrong number or a cosmetic bug, and do not yank a version because a newer one
fixes a crash on some inputs (0.17.0 was not yanked): ship the fixed version and say so in the changelog.

1. Ship the fixed version first if you can; a yank without a replacement leaves users nothing to move to.
2. On pypi.org: the project, *Manage*, the release, *Options*, *Yank*, with a reason that names the problem
   and the fixed version, for example `ImportError on Python 3.10 (datetime.UTC); fixed in 0.14.0`.
3. Add a line to the note at the top of `CHANGELOG.md` (yanked on PyPI, the reason, the date checked against
   the real index) and edit the GitHub Release of that version to say so.
4. To undo: *Options*, *Unyank*.

**Never delete a release.** Deletion burns the version number permanently and breaks pinned installs. The
three versions yanked so far (0.12.3, 0.12.4 and 0.13.0, for the Python 3.10 import error fixed in 0.14.0) are
the model. RELEASING.md step 7 says the same: never delete, and yank only in the cases above.

## If the release workflow itself fails

* A check is red: read the job and fix the cause; do not bypass it. The version check, the canary gate and
  the artifact checks each exist because they once caught a real mistake.
* The `publish` job fails after approval: nothing was uploaded unless the log shows a
  `View at: https://pypi.org/project/tracegauge/X.Y.Z/` line. Fix the cause and re-run the failed job; the
  tag stays where it is.
* The two builds differ: stop. Do not publish; the build is not reproducible and something in the
  environment changed.
