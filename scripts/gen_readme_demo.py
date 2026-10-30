# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Generates the README's "What it finds" blocks and the quickstart demo recording.

Everything comes from running the real CLI on data that ships with, or is generated inside, this
repository:

* ``tracegauge quickstart`` scores the bundled synthetic sample session
  (``tracegauge/data/quickstart_sample_session.jsonl``).
* ``tracegauge score --no-judge`` scores a synthetic session built here, with a seeded generator
  (``tests/churn_synth.py``), that contains three known cache re-writes (an idle gap, a model
  switch, a compaction). Its CACHE CHURN section is the cache-churn detector's real output.

The README blocks are lines of that output, selected but never edited. The recording is a text-based
animated SVG (no raster, no font file, no script): the same selected lines, revealed one by one by
CSS animation. ``tests/test_readme_demo.py`` regenerates all of it and fails if the README or the
SVG drifted from what the CLI prints.

    python scripts/gen_readme_demo.py                       # print the blocks
    python scripts/gen_readme_demo.py --write-svg assets/demo/quickstart.svg
    python scripts/gen_readme_demo.py --check README.md assets/demo/quickstart.svg

The CLI runs in this process with a throw-away home directory and store, so the real ~/.tes is
never read or written.
"""

import argparse
import contextlib
import io
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # tests.churn_synth: the seeded synthetic-transcript builder

#: The recording must stay under this many seconds (the README promises at most 20).
MAX_SECONDS = 20.0
#: Seconds before the first output line, and between lines.
FIRST_LINE_AT = 1.2
LINE_STEP = 0.4

#: Lines of ``tracegauge quickstart`` that the README and the recording show. A line is shown when
#: it starts with one of these and comes before the "Example --" block (an illustration of the
#: judge axis, not output of this run).
_QUICKSTART_KEEP = (
    "tracegauge quickstart --",
    "SYNTHETIC HISTORY:",
    "── TOKEN ECONOMY",
    "  Real tokens:",
    "  Verdict:",
    "  Baseline:",
    "── TRAJECTORY QUALITY",
    "── DETERMINISTIC WASTE",
    "  Events:",
    "  [1] ",
    "── COST BREAKDOWN",
    "  Context re-send",
    "  Context growth",
    "  Output ",
    "  Fresh input",
    "  Detected waste",
    "  Total (priced)",
)

# Logo palette (assets/brand/BRAND.md, "logo B"): dark surface, light ink, accent.
_SURFACE, _INK, _DIM, _ACCENT = "#0E1322", "#DCE3F5", "#8E99B8", "#FF7A9C"


def _run_cli(argv: list[str], home: Path) -> str:
    """Run ``tracegauge <argv>`` in-process; return stdout. Home and store are throw-away."""
    import tracegauge.cli as cli

    old_argv, old_env = sys.argv, dict(os.environ)
    for key in [k for k in os.environ if k.startswith(("TES_", "TRACEGAUGE_"))]:
        del os.environ[key]
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TRACEGAUGE_DB_PATH": str(home / "demo.db"),
            "TRACEGAUGE_NO_NOTICE": "1",
        }
    )
    sys.argv = ["tracegauge", *argv]
    out, err, code = io.StringIO(), io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as exc:
                code = int(exc.code or 0)
    finally:
        sys.argv = old_argv
        os.environ.clear()
        os.environ.update(old_env)
    if code != 0:
        raise RuntimeError(f"tracegauge {' '.join(argv)} exited {code}: {err.getvalue()[:300]}")
    return out.getvalue().replace("\r\n", "\n")


def churn_session(path: Path) -> Path:
    """Write the synthetic session with three planted cache re-writes (seed 42, 40 turns)."""
    from tests.churn_synth import build_chain

    recs = build_chain(random.Random(42), 40, {10: "TTL_GAP", 20: "MODEL_SWITCH", 30: "COMPACTION"})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def quickstart_lines(output: str) -> list[str]:
    """The lines of ``tracegauge quickstart`` that are shown, in order, none edited."""
    body = output.split("Example --")[0]
    return [ln for ln in body.splitlines() if ln.startswith(_QUICKSTART_KEEP)]


def subagent_section(output: str) -> str:
    """The subagent lines of the COST ANNOTATION of a ``tracegauge score`` report, verbatim.

    From the "Includes N subagent transcript(s)" line to the end of that paragraph (the per-type
    lines), so the README block is exactly what a user sees under the cost note.
    """
    lines = output.splitlines()
    start = next(i for i, ln in enumerate(lines) if "subagent transcript(s)" in ln)
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return "\n".join(ln.rstrip() for ln in lines[start:end])


def cache_churn_section(output: str) -> str:
    """The CACHE CHURN section of a ``tracegauge score`` report, verbatim."""
    lines = output.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("── CACHE CHURN"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("── "))
    return "\n".join(ln.rstrip() for ln in lines[start:end]).rstrip("\n")


def _svg(lines: list[str]) -> str:
    """A terminal-style SVG that types the command, then reveals each output line in turn."""
    shown = ["$ tracegauge quickstart", *lines]
    char_w, line_h, pad = 7.9, 19, 20
    width = int(max(len(ln) for ln in shown) * char_w + 2 * pad)
    height = len(shown) * line_h + 2 * pad
    last_at = FIRST_LINE_AT + LINE_STEP * len(lines)
    if last_at + 0.2 > MAX_SECONDS:
        raise ValueError(f"recording would last {last_at + 0.2:.1f}s, over {MAX_SECONDS}s")
    rows = []
    for i, ln in enumerate(shown):
        at = 0.3 if i == 0 else FIRST_LINE_AT + LINE_STEP * (i - 1)
        cls = "cmd" if i == 0 else ("hd" if ln.startswith("──") else "ln")
        y = pad + line_h * (i + 1) - 5
        rows.append(
            f'<text class="{cls}" x="{pad}" y="{y}" style="animation-delay:{at:.2f}s">'
            f"{escape(ln)}</text>"
        )
    body = "\n".join(rows)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-labelledby="t d">\n'
        '<title id="t">tracegauge quickstart</title>\n'
        '<desc id="d">Animated terminal recording of tracegauge quickstart on the bundled '
        "synthetic sample session: token economy verdict against a labelled synthetic history, one deterministic waste finding and the "
        f"cost breakdown. About {last_at + 0.2:.0f} seconds, plays once.</desc>\n"
        "<style>\n"
        f"text{{font-family:ui-monospace,Menlo,Consolas,'DejaVu Sans Mono',monospace;"
        f"font-size:13px;white-space:pre;fill:{_INK}}}\n"
        f".cmd{{fill:{_ACCENT}}}.hd{{fill:{_DIM}}}\n"
        "@keyframes show{from{opacity:0}to{opacity:1}}\n"
        "text{animation:show .01s linear backwards}\n"
        "@media (prefers-reduced-motion:reduce){text{animation:none}}\n"
        "</style>\n"
        f'<rect width="{width}" height="{height}" rx="8" fill="{_SURFACE}"/>\n'
        f"{body}\n"
        "</svg>\n"
    )


def recording_seconds(svg: str) -> float:
    """Seconds until the last line appears, read back from the SVG's own delays."""
    delays = [float(m) for m in re.findall(r"animation-delay:([0-9.]+)s", svg)]
    return max(delays) + 0.01


def generate() -> dict[str, str]:
    """Every generated artifact, keyed by name. Deterministic: seeded data, no clock."""
    # ignore_cleanup_errors: on Windows an antivirus or indexer can briefly lock a file in the
    # throw-away directory and make the cleanup raise after the output was produced. Only the
    # cleanup is tolerated; the output comparison in main() stays strict.
    with tempfile.TemporaryDirectory(
        prefix="tracegauge-readme-demo-", ignore_cleanup_errors=True
    ) as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        quickstart = _run_cli(["quickstart"], home)
        session = churn_session(Path(tmp) / "projects" / "demo-api" / "churn_demo.jsonl")
        scored = _run_cli(["score", str(session), "--no-judge"], home)
        from tests.subagent_synth import build_session

        sub_session = build_session(Path(tmp) / "subagents-demo")
        sub_scored = _run_cli(["score", str(sub_session), "--no-judge"], home)
    lines = quickstart_lines(quickstart)
    return {
        "quickstart_excerpt": "\n".join(lines),
        "cache_churn": cache_churn_section(scored),
        "subagents": subagent_section(sub_scored),
        "svg": _svg(lines),
        "quickstart_full": quickstart,
    }


def _fenced_blocks(markdown: str) -> list[str]:
    return re.findall(r"```text\n(.*?)\n```", markdown.replace("\r\n", "\n"), flags=re.S)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the README demo blocks and recording.")
    parser.add_argument("--write-svg", metavar="PATH", help="write the recording here")
    parser.add_argument(
        "--check", nargs=2, metavar=("README", "SVG"), help="fail unless both match the CLI"
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # box-drawing characters
    out = generate()
    if args.write_svg:
        Path(args.write_svg).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write_svg).write_text(out["svg"], encoding="utf-8", newline="\n")
        return 0
    if args.check:
        readme = Path(args.check[0]).read_text(encoding="utf-8")
        blocks = _fenced_blocks(readme)
        bad = [
            k for k in ("quickstart_excerpt", "cache_churn", "subagents") if out[k] not in blocks
        ]
        svg = Path(args.check[1]).read_text(encoding="utf-8").replace("\r\n", "\n")
        if svg != out["svg"]:
            bad.append("svg")
        for key in bad:
            print(f"{key} does not match the CLI output", file=sys.stderr)
        return 1 if bad else 0
    for key in ("quickstart_excerpt", "cache_churn", "subagents"):
        print(f"===== {key} =====\n{out[key]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
