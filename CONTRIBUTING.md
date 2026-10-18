# Contributing

`tracegauge` (import name `tracegauge`; the old `tes` name is a deprecated alias) is a solo-maintained portfolio project —
contributions are welcome, but the bar is the same one the maintainer
holds their own commits to: real tests, honest documentation, no
fabricated numbers.

## Quickstart for your first contribution

```bash
git clone https://github.com/gaurav-gandhi-2411/tracegauge.git
cd tracegauge
uv sync --frozen                                  # exact locked dependencies, dev tools included
uv run pytest tests/test_usage_dedupe.py -v       # one test file, under a second
uv run pytest tests/ -q                           # the full suite (a few minutes)
uv run tracegauge quickstart                      # scores a bundled synthetic session; no network, none of your files read
git commit -s -m "docs: what you changed"         # -s adds the DCO sign-off every commit needs
```

Good places to start: the issues labelled `good first issue`, a "Limits" section in `docs/` that says
something is not covered, or [`docs/ADAPTERS.md`](docs/ADAPTERS.md) if you want to help another coding
agent get supported.

**Finding your own Claude Code transcripts.** Claude Code writes one `<session-id>.jsonl` per session
under `~/.claude/projects/<project-folder>/` (on Windows, `%USERPROFILE%\.claude\projects\`). To see what
tracegauge makes of one, run `uv run tracegauge score <path-to-file.jsonl>`, or `score` a whole project
folder. They are your data and can contain code and secrets: read them, never commit them.

**The rules that are easy to miss**

- Fixtures are synthetic. Never commit real session text, prompts, file paths, ids or keys, not even
  trimmed or "anonymised"; write a small file in the real record shape instead (the existing ones are in
  `tests/fixtures/`, for example `tests/fixtures/usage_dedupe/multi_block_session.jsonl`).
- Every new `.py` file starts with the SPDX header; `python scripts/spdx_headers.py` adds it.
- Sign off every commit (`git commit -s`), see "Licence and sign-off" below.
- A number in docs, code comments or a pull request description comes with its source: the command
  that printed it, the file it was read from, or the vendor page and the date you read it. A figure from
  data that is not in this repository is called what it is, an unpublished local measurement.
- `tests/test_public_text.py` fails on private paths and internal labels in public text; run it if you
  touch docs.
- A pull request carries one change; the pull request template lists what to fill in.

## Dev setup

```bash
git clone https://github.com/gaurav-gandhi-2411/tracegauge.git
cd tracegauge
uv sync --frozen   # installs the exact locked dependency set, incl. dev deps
```

`uv` is the only supported package manager for this repo. `uv.lock` is
committed; never hand-edit it, and never run `uv sync` without `--frozen`
in CI or when verifying a change.

## Running tests and lint

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
```

**`ruff` is currently informational-only in CI** (`.github/workflows/ci.yml`
— this repo never had it enforced before that workflow existed, and a large
share of `scripts/` predates it; see the workflow's own comments). It is
NOT yet a merge-blocking gate for the whole tree. This is a known,
deliberate, temporary state, not an oversight.

**Set up the pre-commit hook anyway, so new/touched files stay clean going
forward, even though CI doesn't enforce it repo-wide yet:**

```bash
uv run pre-commit install
```

After that, every `git commit` runs `ruff check --fix` and `ruff format`
against the files you're actually committing (not the whole repo) via
`uv run ruff` (`language: system` in `.pre-commit-config.yaml`,
deliberately not the `astral-sh/ruff-pre-commit` mirror repo — that mirror
pins its own `ruff` version separately from `uv.lock`, a second source of
truth that can silently drift; `uv run ruff` always resolves to the exact
version this repo has locked). Because it only checks staged files, it
will not block you on pre-existing debt elsewhere in the repo — only on
files your own commit touches. If you touch an already-non-compliant file,
you'll be asked to bring it into compliance as part of your change,
consistent with this project's "fix debt in code you're already touching"
convention.

**Windows note**: if `pre-commit install`/`pre-commit run` fails with a
traceback mentioning `pip._vendor.rich.markup` or similar during "Installing
environment," that's a corrupted `virtualenv` seed-wheel cache, not a
problem with this repo's config — clear
`%LOCALAPPDATA%\pypa\virtualenv\Cache` and `%USERPROFILE%\.cache\pre-commit`
and retry.

## Licence and sign-off

From 0.16.0 tracegauge is licensed under the Apache License 2.0 (see `LICENSE`); releases up to
0.15.0 remain AGPL-3.0. By contributing you agree your contribution is licensed under Apache-2.0
(inbound = outbound). There is no CLA. Instead every commit must be signed off under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/), which says you wrote the
change or have the right to submit it under this licence. Add the sign-off with `-s`:

```bash
git commit -s -m "fix(scope): what changed"   # appends: Signed-off-by: Your Name <you@example.org>
git rebase --signoff origin/master            # add it to commits you already made
```

The name and email in the trailer must match the commit's author (or committer) email. CI
(`dco` job in `.github/workflows/ci.yml`, `scripts/check_dco.py`) fails a pull request if any commit
in it lacks a valid `Signed-off-by` trailer; merge commits and bot commits (dependabot) are skipped.
Run it locally with `python scripts/check_dco.py --base origin/master --head HEAD`.

The certificate text, version 1.1 (copied from developercertificate.org):

    Developer Certificate of Origin
    Version 1.1

    Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

    Everyone is permitted to copy and distribute verbatim copies of this
    license document, but changing it is not allowed.


    Developer's Certificate of Origin 1.1

    By making a contribution to this project, I certify that:

    (a) The contribution was created in whole or in part by me and I
        have the right to submit it under the open source license
        indicated in the file; or

    (b) The contribution is based upon previous work that, to the best
        of my knowledge, is covered under an appropriate open source
        license and I have the right under that license to submit that
        work with modifications, whether created in whole or in part
        by me, under the same open source license (unless I am
        permitted to submit under a different license), as indicated
        in the file; or

    (c) The contribution was provided directly to me by some other
        person who certified (a), (b) or (c) and I have not modified
        it.

    (d) I understand and agree that this project and the contribution
        are public and that a record of the contribution (including all
        personal information I submit with it, including my sign-off) is
        maintained indefinitely and may be redistributed consistent with
        this project or the open source license(s) involved.

New Python files start with the two-line header added by `scripts/spdx_headers.py`
(`python scripts/spdx_headers.py` adds it, `--check` verifies it; a test enforces it).

## Releasing

See `RELEASING.md` for the full tag-triggered release flow.
