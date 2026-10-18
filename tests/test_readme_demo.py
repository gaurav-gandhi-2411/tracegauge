# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The README's "What it finds" blocks and the quickstart recording are generated, not typed.

scripts/gen_readme_demo.py runs the real CLI (`quickstart`, and `score` on a seeded synthetic
session with planted cache re-writes). These tests regenerate everything and fail when the README
or the SVG recording has drifted from what the command prints.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_readme_demo as gen  # noqa: E402

README = ROOT / "README.md"
SVG = ROOT / "assets" / "demo" / "quickstart.svg"


@pytest.fixture(scope="module")
def out() -> dict[str, str]:
    return gen.generate()


def _text_blocks(markdown: str) -> list[str]:
    return re.findall(r"```text\n(.*?)\n```", markdown.replace("\r\n", "\n"), flags=re.S)


def test_readme_quickstart_block_equals_the_cli_output(out: dict[str, str]) -> None:
    assert out["quickstart_excerpt"] in _text_blocks(README.read_text(encoding="utf-8")), (
        "README quickstart block is stale: python scripts/gen_readme_demo.py"
    )


def test_readme_cache_churn_block_equals_the_cli_output(out: dict[str, str]) -> None:
    block = out["cache_churn"]
    assert block.startswith("── CACHE CHURN") and "3 event(s)" in block
    assert block in _text_blocks(README.read_text(encoding="utf-8")), (
        "README cache-churn block is stale: python scripts/gen_readme_demo.py"
    )


def test_recording_svg_equals_a_fresh_generation(out: dict[str, str]) -> None:
    on_disk = SVG.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert on_disk == out["svg"], (
        "assets/demo/quickstart.svg is stale: "
        "python scripts/gen_readme_demo.py --write-svg assets/demo/quickstart.svg"
    )


def test_every_recorded_line_is_a_line_the_cli_printed(out: dict[str, str]) -> None:
    printed = set(out["quickstart_full"].splitlines())
    shown = gen.quickstart_lines(out["quickstart_full"])
    assert shown and all(ln in printed for ln in shown)
    svg = SVG.read_text(encoding="utf-8")
    for ln in shown:
        assert gen.escape(ln) in svg, ln


def test_recording_is_at_most_twenty_seconds_and_has_no_script_or_external_reference() -> None:
    svg = SVG.read_text(encoding="utf-8")
    assert gen.recording_seconds(svg) <= 20.0
    assert not re.search(r"<script|<image|href=|url\(|@import|http://(?!www\.w3\.org)", svg)
    assert "\\" not in svg.replace("\\n", "")  # no Windows path in the recording


def test_check_mode_stays_strict_and_exits_nonzero_on_drift(
    out: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--check must still fail on a one-character README or SVG drift (the cleanup of the
    throw-away directory is tolerant, the comparison is not)."""
    readme = README.read_text(encoding="utf-8")
    good_readme, good_svg = tmp_path / "README.md", tmp_path / "q.svg"
    good_readme.write_text(readme, encoding="utf-8")
    good_svg.write_text(SVG.read_text(encoding="utf-8"), encoding="utf-8")
    argv = sys.argv
    try:
        sys.argv = ["gen_readme_demo.py", "--check", str(good_readme), str(good_svg)]
        assert gen.main() == 0
        drifted = readme.replace(
            out["cache_churn"], out["cache_churn"].replace("3 event", "4 event")
        )
        assert drifted != readme
        bad_readme = tmp_path / "bad.md"
        bad_readme.write_text(drifted, encoding="utf-8")
        sys.argv = ["gen_readme_demo.py", "--check", str(bad_readme), str(good_svg)]
        assert gen.main() == 1
        assert "cache_churn does not match" in capsys.readouterr().err
        bad_svg = tmp_path / "bad.svg"
        bad_svg.write_text(
            SVG.read_text(encoding="utf-8").replace("<svg", "<svg ", 1), encoding="utf-8"
        )
        sys.argv = ["gen_readme_demo.py", "--check", str(good_readme), str(bad_svg)]
        assert gen.main() == 1
    finally:
        sys.argv = argv


def test_generation_is_deterministic(out: dict[str, str]) -> None:
    again = gen.generate()
    assert {k: again[k] for k in ("quickstart_excerpt", "cache_churn", "subagents", "svg")} == {
        k: out[k] for k in ("quickstart_excerpt", "cache_churn", "subagents", "svg")
    }


def test_too_long_a_recording_is_refused() -> None:
    with pytest.raises(ValueError):
        gen._svg(["x"] * 60)  # 1.2 s + 60 * 0.4 s is over the 20 s cap


def test_readme_has_the_sections_in_the_promised_order() -> None:
    headings = re.findall(r"^## (.+)$", README.read_text(encoding="utf-8"), flags=re.M)
    assert headings == [
        "Install and try it",
        "What it finds",
        "Commands",
        "Supported agents",
        "Methodology",
    ]
    assert (ROOT / "docs" / "METHODOLOGY.md").is_file()


def _dollar_rows(block: str, start: str, stop: str) -> tuple[float, float]:
    """Sum of the $ column of the rows between two marker lines, and the stated total."""
    lines = block.splitlines()
    rows = lines[next(i for i, ln in enumerate(lines) if ln.strip().startswith(start)) + 1 :]
    amounts = []
    for ln in rows:
        if ln.strip().startswith(stop):
            break
        m = re.search(r"\$(\d+\.\d\d)", ln)
        if m and re.match(r"^\s+\S.*\s\d+\s+\$", ln):
            amounts.append(float(m.group(1)))
    assert amounts, "no $ rows found between the markers"  # a parse failure must not read as 0
    total = float(re.search(r"\$(\d+\.\d\d)", lines[1]).group(1))  # type: ignore[union-attr]
    return round(sum(amounts), 2), total


def _churn_row(block: str, label: str) -> float:
    m = re.search(rf"^\s+{re.escape(label)}.*?\$(\d+\.\d\d)\s*$", block, flags=re.M)
    assert m, label
    return float(m.group(1))


def _churn_mismatches(block: str) -> list[str]:
    """Displayed figures that do not equal the sum of the rows beside them, as `$x.xx` strings.

    Covers the cause rows versus the total, and the "plausibly avoidable" figure versus the
    model-switch and TTL-gap rows it is made of.
    """
    found: list[str] = []
    summed, total = _dollar_rows(block, "3 event(s)", "Plausibly")
    if summed != total:
        found += [f"${summed:.2f}", f"${total:.2f}"]
    avoidable = float(re.search(r"avoidable[^$]*\$(\d+\.\d\d)", block).group(1))  # type: ignore[union-attr]
    parts = round(_churn_row(block, "model switch") + _churn_row(block, "TTL gap"), 2)
    if parts != avoidable:
        found += [f"${parts:.2f}", f"${avoidable:.2f}"]
    return found


def _note_covers(readme: str, figures: list[str]) -> bool:
    """True when nothing is mismatched, or the README says why and names every such figure."""
    if not figures:
        return True
    return "rounded independently" in readme and all(f in readme for f in figures)


def _quickstart_sum(excerpt: str) -> tuple[float, float]:
    """Sum of the cost-split rows of the quickstart excerpt, and its stated priced total."""
    rows = re.findall(r"^  (?!Total)\S.*?\$(\d+\.\d\d)\s+\d+\.\d%$", excerpt, flags=re.M)
    assert rows, "no cost-split rows found in the quickstart excerpt"
    total = re.search(r"Total \(priced\)\s+\$(\d+\.\d\d)", excerpt)
    assert total, "no priced total found in the quickstart excerpt"
    return round(sum(map(float, rows)), 2), float(total.group(1))


def test_readme_says_so_when_displayed_rows_do_not_add_up_to_the_displayed_total(
    out: dict[str, str],
) -> None:
    """Every displayed churn figure that disagrees with its rows must be named in the README note."""
    readme = README.read_text(encoding="utf-8")
    figures = _churn_mismatches(out["cache_churn"])
    assert figures, "the example no longer shows a rounding mismatch; drop the README note"
    assert _note_covers(readme, figures), f"README note must name {figures}"


def test_quickstart_cost_split_rows_add_up_to_the_stated_total(out: dict[str, str]) -> None:
    """The README carries no rounding note for the quickstart block, so its rows must add up."""
    summed, stated = _quickstart_sum(out["quickstart_excerpt"])
    assert summed == stated, f"rows add to ${summed:.2f}, total says ${stated:.2f}"


_CHURN = """\
── CACHE CHURN ──
  3 event(s) where cached context was re-written instead of read: $0.38
  above what reading it would have cost.
    TTL gap (idle past the cache lifetime)      1       $0.06
    model switch                                1       $0.16
    compaction                                  1       $0.17
  Plausibly avoidable (a judgement): $0.21; not avoidable: $0.17.
"""


def test_negative_control_churn_mismatches_are_detected_and_need_a_complete_note() -> None:
    figures = _churn_mismatches(_CHURN)
    assert figures == ["$0.39", "$0.38", "$0.22", "$0.21"]
    assert not _note_covers("nothing here", figures)
    assert not _note_covers("rounded independently, $0.39 vs $0.38", figures)  # avoidable missing
    assert _note_covers("rounded independently $0.39 $0.38 $0.22 $0.21", figures)
    consistent = _CHURN.replace("$0.21;", "$0.22;").replace("$0.38\n", "$0.39\n")
    assert _churn_mismatches(consistent) == []


def test_negative_control_quickstart_sum_mismatch_is_detected() -> None:
    good = "  a   $1.00  50.0%\n  b   $1.00  50.0%\n  Total (priced)   $2.00\n"
    assert _quickstart_sum(good) == (2.0, 2.0)
    summed, stated = _quickstart_sum(good.replace("$2.00\n", "$1.99\n"))
    assert summed != stated


def _subagent_numbers(block: str) -> tuple[int, float, list[tuple[int, float]]]:
    head = re.search(r"Includes \d+ subagent transcript\(s\): ([\d,]+) tokens, \$([\d.]+)", block)
    assert head, block
    rows = re.findall(r"^\s+[\w-]+: \d+ file\(s\), ([\d,]+) tokens, \$([\d.]+)", block, flags=re.M)
    return (
        int(head.group(1).replace(",", "")),
        float(head.group(2)),
        [(int(t.replace(",", "")), float(d)) for t, d in rows],
    )


def test_readme_subagent_block_equals_the_cli_output(out: dict[str, str]) -> None:
    block = out["subagents"]
    assert "subagent transcript(s)" in block and "executor:" in block
    assert block in _text_blocks(README.read_text(encoding="utf-8")), (
        "README subagent block is stale: python scripts/gen_readme_demo.py"
    )


def test_subagent_type_tokens_add_up_to_the_line_above_them(out: dict[str, str]) -> None:
    # The per-type lines must be in the same unit as the "Includes N subagent transcript(s)" line.
    total_tokens, total_usd, rows = _subagent_numbers(out["subagents"])
    assert sum(t for t, _ in rows) == total_tokens
    assert abs(sum(d for _, d in rows) - total_usd) <= 0.015 * len(rows)


def test_readme_says_so_when_subagent_dollar_rows_do_not_add_up(out: dict[str, str]) -> None:
    _, total_usd, rows = _subagent_numbers(out["subagents"])
    summed = round(sum(d for _, d in rows), 2)
    if summed != total_usd:
        readme = README.read_text(encoding="utf-8")
        assert f"${summed:.2f}" in readme and f"${total_usd:.2f}" in readme


def test_negative_control_subagent_unit_mix_is_detected() -> None:
    mixed = (
        "Includes 2 subagent transcript(s): 1,000 tokens, $1.00\n"
        "  executor: 1 file(s), 5,000 tokens, $0.60\n"
        "  verifier: 1 file(s), 1,000 tokens, $0.40"
    )
    total_tokens, _, rows = _subagent_numbers(mixed)
    assert sum(t for t, _ in rows) != total_tokens
