# Installing tracegauge

The package is `tracegauge` on PyPI. It installs two equivalent commands, `tracegauge` and the short alias `tgauge` (plus the deprecated `tes`, removed in 0.18).
Python 3.10 or newer. Nothing leaves your machine unless you opt in to something that says so.

## Fastest: run it without installing (uvx)

```bash
uvx tracegauge quickstart            # scores a bundled synthetic session, prints a verdict + a finding
uvx tracegauge score                 # scores your most recent real Claude Code session
```

`uvx` (from [uv](https://docs.astral.sh/uv/)) builds a cached throwaway environment for the core
install, so the second run is fast.

## Persistent install

```bash
pipx install tracegauge              # isolated, on PATH
uv tool install tracegauge           # same idea with uv
pip install tracegauge               # into the active environment (use a venv)
```

## Extras

| Install | Adds | Needed for |
|---|---|---|
| `tracegauge` (core) | flask, httpx (and their dependencies) | `quickstart`, `score`, `cost`, `budget`, `monitor`, `impact`, `serve` (dashboard), waste detection, lever hint |
| `tracegauge[patterns]` | numpy, scikit-learn (scipy comes with it) | `tracegauge patterns`, `tracegauge ask`, the dashboard `/patterns` page and `/ask` box |

Without the extra, those commands exit 1 with one line on stderr:

```text
[ERROR] Pattern analysis needs the patterns extra: pip install "tracegauge[patterns]" (adds numpy, scikit-learn, scipy).
```

and the dashboard shows an "extra not installed" page for `/patterns` and a JSON `501`
(`{"error": ..., "needs_extra": "patterns"}`) for `/ask`; every other page keeps working.
Nothing at import time or at CLI start-up needs the extra (pinned by `tests/test_core_without_extras.py`,
which blocks numpy, scipy and sklearn in a subprocess and runs the core commands).

## Measured facts

Everything below was measured on 2026-10-11 against the published `tracegauge==0.15.0` from PyPI, on one
Windows 11 machine with CPython 3.13.5, in fresh throw-away venvs. These are the maintainer's local
measurements, not CI output, and **the machine was not idle**: `Win32_Processor.LoadPercentage` was 65% before and
77% after the pip runs, and 45% before and 33% after the uvx runs. Treat them as an order of magnitude on a busy
laptop, not a guarantee.

**Install size, one measure used everywhere in this project:** the on-disk size of every file listed in the
RECORD of each installed distribution, excluding pip, setuptools and wheel (they are in every venv and are not
tracegauge's footprint). Reproduce it with `python scripts/measure_install_size.py`, run with the venv's own
interpreter.

| | core | core + `[patterns]` |
|---|---|---|
| installed package files (measure above) | 11.3 MiB (788 files, 16 distributions; re-measured 2026-10-20, see below) | 236.6 MiB (6,931 files, 22 distributions) |
| `pip install` wall time, three fresh venvs, first with an empty pip cache | 38.3 s, 9.8 s, 10.7 s | 90.6 s, 63.4 s, 64.1 s |
| installed `tracegauge quickstart`, n=5 | median 0.41 s (0.39 to 0.52 s) | not measured |
| installed `tracegauge --version`, n=5 | median 0.38 s | not measured |

The first `[patterns]` install reused the pip cache the core installs had filled for flask, httpx and their
dependencies, so only numpy, scikit-learn and scipy were downloaded cold. Moving numpy and scikit-learn to the
extra removes about 226 MiB from the default install. The core install is a little over a quarter of a 40 MB budget.

The core size row was re-measured on 2026-10-20 against the published `tracegauge==0.17.0` (`pip install` into a fresh
CPython 3.11 venv on Windows, then `scripts/measure_install_size.py` with that venv's interpreter): 11,804,085 bytes,
788 files, 16 distributions. An independent re-run the same day got 11,807,205 bytes (dependencies float, so the
exact figure moves by a few KB); the reason for the rise from 10.1 MiB was not isolated. The other cells of the table are still the 2026-10-11 measurements
on 0.15.0 (the `[patterns]` column and all timings were not repeated).

`uvx tracegauge@0.15.0 quickstart` wall time, n=5 each. Cold means a brand-new empty uv cache for every run (flask,
httpx and the rest are downloaded from PyPI); warm means a populated cache after one uncounted priming run. Every
run exited 0 and printed the REPEATED-FAILED-RETRY finding, so this is time-to-first-finding.

| | runs (s) | median | min / max |
|---|---|---|---|
| cold | 4.14, 2.53, 3.03, 2.80, 4.40 | 3.03 s | 2.53 / 4.40 |
| warm | 0.65, 0.49, 0.50, 0.50, 0.51 | 0.50 s | 0.49 / 0.65 |

Cold time depends on the network and on machine load; these are single-machine numbers with network and
antivirus variance, and an idle-machine run has not been measured.
