## What and why

<!-- Two to four lines. What changed, and why it is worth changing. Link the issue if there is one. -->

## Changes

<!-- A short bulleted list, grouped by area. Keep one concern per pull request. -->

## Tests run

<!-- The exact commands and what they showed, for example:
     uv run pytest tests/test_usage_dedupe.py -q   -> 7 passed
     uv run pytest tests/ -q                       -> all passed
     A behaviour change comes with the test that would have failed before it. -->

## Numbers and sources

<!-- Every number you add to code, docs or this description comes with where it came from: the
     command that printed it, the file it was read from, or the vendor page and the date you read it.
     A figure from a measurement that is not in this repository is labelled as such, not stated as fact.
     Write "none" if this change adds no numbers. -->

## Screenshots

<!-- A change to the dashboard (tracegauge/web) needs a before and after screenshot, with no real
     paths, session ids or prompt text visible. If there is no visible change, say so and say why.
     Write "n/a" for a change that does not touch the dashboard. -->

## Risk and rollback

<!-- What could this break, who calls it, and how it is reverted. -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`); `python scripts/check_dco.py --base origin/master --head HEAD` passes.
- [ ] `uv run pytest tests/ -q` passes locally.
- [ ] New `.py` files start with the SPDX header (`python scripts/spdx_headers.py --check`).
- [ ] Fixtures are synthetic: no real session text, paths, ids or keys (see PRIVACY.md).
- [ ] No hand edits to `uv.lock`; no new dependency without a stated reason.
