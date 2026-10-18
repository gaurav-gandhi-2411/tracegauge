# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Public text must not point at the maintainer's private working files or use internal work-item
jargon. Two scans:

* README.md, CHANGELOG.md and docs/ (prose a reader follows): no private paths or jargon.
* The code and configuration that ships or runs in public: tracegauge/ (including the dashboard
  templates), the tes compatibility package, scripts/, .github/, pyproject.toml, MANIFEST.in and
  _build/. Comments and docstrings there keep the *reason* for a decision but not the internal
  ticket, wave or audit label that prompted it, and nothing names a private machine path or
  cloud project.

A claim in public text has to be reproducible from the repo, or be stated as an unpublished local
measurement; a path on the maintainer's disk or the name of an internal report is neither."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tracked_only(paths: list[Path]) -> list[Path]:
    """Keep only the files git tracks, so ignored local data (for example SWE-chat-derived files
    kept out of the repository) is never scanned: a clone and a working copy see the same files.
    Without a usable git checkout (an unpacked sdist) every path is kept, never none."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return paths
    tracked = {ROOT / name for name in out.decode("utf-8").split("\0") if name}
    return [p for p in paths if p in tracked] if tracked else paths


# Built from pieces so this file does not trip its own scan (it lives under tests/, which is not
# scanned, but a grep over the repo for the markers should find the rule, not the data).
FORBIDDEN = ("D:" + "\\", "W1" + "A", "W1" + "B", "q-" + "step")

# Names of the maintainer's private or unreleased projects (and the cloud ids named after them).
# Public text uses generic placeholders ("project-a") instead. The published sibling projects are
# deliberately not listed: adk-tracegauge, agentgauge / agentgauge-harness and the public portfolio
# site (gg-portfolio). "Warmer" is an ordinary English word, so it cannot be a marker.
OTHER_PROJECT_NAMES = re.compile(
    r"aether[-_ ]?art|expense[-_ ]?tracker|triage[-_ ]?iq|review[-_ ]?iq|style[-_ ]?maitri"
    r"|mind[-_ ]?meld|deal[-_ ]?hunter|agentic[-_ ](?:travel|shopping)|iconic[-_ ]reactor"
    r"|ml[-_ ]portfolio|promising[-_ ]life|fr[-_ ]en[-_ ]transformer|ship[d]oc|intent[-_ ]router"
    r"|sam[i]dha|tes-cc-validati[o]n|gold[-_ ]rate[-_ ]tracker",
    re.I,
)

# --- code and configuration scan -------------------------------------------------------------
SOURCE_MARKERS = {
    "work-package label": re.compile(r"\bW1[AB]\b|\bW3\b|\bD[0-9]\b|\bitem [0-9]\b"),
    "audit label": re.compile(r"audit D[0-9]|\bT[0-9] fix\b|q-" + "step"),
    "phase label": re.compile(
        r"\b[Pp]hase[ -]?[0-9]|phase0|\bset B\b|c2-selection|\b[Ww]ave [0-9]+"
    ),
    "ticket code": re.compile(r"\b(?!CC0\b)[A-Z]{2}[0-9](?:\.[0-9])?\b"),
    "private rule or doc": re.compile(r"CLAUDE\.md|\brule [0-9]+[a-z]?\b|GG decision"),
    "role name": re.compile(r"\borchestrator\b|\bverifier\b"),
    "other project name": OTHER_PROJECT_NAMES,
    "private path or project": re.compile(
        r"\b[CDEFG]:\\(?:Users\\|ml-projects)|\bD:\\|aethe[r]art|\bgaur[a]\b"
    ),
}
SOURCE_SUFFIXES = {".py", ".html", ".yml", ".yaml", ".toml", ".sh", ".in", ".md", ".json"}
SOURCE_ROOTS = ("tracegauge", "tes", "scripts", ".github", "_build")
SOURCE_FILES = (
    "pyproject.toml",
    "MANIFEST.in",
    "NOTICE",
    "PRIVACY.md",
    "DATA_SOURCES.md",
    "SECURITY.md",
    "RELEASING.md",
    "CONTRIBUTING.md",
)
# Where a marker is the file's own subject rather than a leaked internal label. Each entry is
# (path, marker name) with the reason; a stale entry (nothing matches any more) fails the test, so
# this list cannot quietly grow into a blanket exemption.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("scripts/generalization_run.py", "phase label"): (
        "the script's own pipeline stages are called Phase 0..7 in its output and comments"
    ),
}


# --- private-reference scan over prose and config that the two scans above do not reach ----------
# docs/audit, docs/design, research/, CURRENT_STATE.md and the pre-commit config were audit and
# working notes; they stay in the repository but must not name a private path, a cloud project, an
# internal rule file or the maintainer's initials.
PRIVATE_REFERENCE_MARKERS = {
    "private path": re.compile(r"[A-Za-z]:\\(?:Users|ml-projects|adk|tg)|/c/Users/|\bgaur[a]\b"),
    "cloud project or id": re.compile(
        r"aethe[r]art|review[-_ ]?iq-prod|tes-cc-validati[o]n|org_[0-9a-z]{20,}"
    ),
    "internal rule file": re.compile(r"CLAUDE\.md|\brule [0-9]{2,3}[a-z]?\b|\bGG\b"),
    "other project name": OTHER_PROJECT_NAMES,
}
PRIVATE_REFERENCE_FILES = (
    "README.md",
    "CHANGELOG.md",
    "CURRENT_STATE.md",
    "PLAN.md",
    ".pre-commit-config.yaml",
)
PRIVATE_REFERENCE_DIRS = ("docs", "research")


def _private_reference_files() -> list[Path]:
    files = [ROOT / name for name in PRIVATE_REFERENCE_FILES]
    for d in PRIVATE_REFERENCE_DIRS:
        files += sorted(
            p for p in (ROOT / d).rglob("*") if p.is_file() and p.suffix in {".md", ".txt"}
        )
    return _tracked_only(files)


def test_audit_design_research_and_config_notes_name_no_private_reference() -> None:
    hits: list[str] = []
    for path in _private_reference_files():
        rel = path.relative_to(ROOT).as_posix()
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for name, rx in PRIVATE_REFERENCE_MARKERS.items():
                if rx.search(line):
                    hits.append(f"{rel}:{n}: {name}: {line.strip()[:100]}")
    assert not hits, "private reference in prose or config notes:\n" + "\n".join(hits)


def test_private_reference_scan_reaches_the_audit_design_research_and_config_files() -> None:
    rels = {p.relative_to(ROOT).as_posix() for p in _private_reference_files()}
    for expected in (
        "docs/audit/FINAL_STATE.md",
        "docs/design/DASHBOARD_TRAJECTORY_TIMELINE.md",
        "CURRENT_STATE.md",
        ".pre-commit-config.yaml",
        "research/07-ingestion.md",
    ):
        assert expected in rels, expected


def test_private_reference_markers_fire_on_what_they_exist_for() -> None:
    samples = {
        "private path": "C:" + "\\Users\\x",
        "cloud project or id": "--project=aether" + "art-1",
        "internal rule file": "per CLAUDE" + ".md rule 39c",
    }
    for name, text in samples.items():
        assert PRIVATE_REFERENCE_MARKERS[name].search(text), (name, text)
    assert PRIVATE_REFERENCE_MARKERS["internal rule file"].search("route to G" + "G")


def test_other_project_names_are_caught_and_the_sibling_product_is_not() -> None:
    for name in (
        "aether" + "art",
        "Aether" + "Art",
        "expense_" + "tracker",
        "Triage" + "IQ",
        "review" + " iq",
        "Style" + "Maitri",
        "mind" + "meld",
        "Deal" + "Hunter",
        "agentic-" + "travel",
        "iconic" + "-reactor",
        "ml-" + "portfolio",
        "promising" + "-life",
        "fr-en-" + "transformer",
        "ship" + "doc-extract",
        "intent" + "-router",
    ):
        assert OTHER_PROJECT_NAMES.search(f"see the {name} repo"), name
    for ok in (
        "adk-tracegauge",
        "agentgauge",
        "agentgauge-harness",
        "gg-portfolio",
        "gaurav-gandhi.vercel.app",
        "a warmer colour",
        "tracegauge",
        "the project-a repo",
    ):
        assert not OTHER_PROJECT_NAMES.search(ok), ok


def _public_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    files += sorted(
        p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in {".md", ".txt"}
    )
    return _tracked_only(files)


def _source_files() -> list[Path]:
    files = [ROOT / name for name in SOURCE_FILES]
    for root in SOURCE_ROOTS:
        files += sorted(
            p
            for p in (ROOT / root).rglob("*")
            if p.is_file()
            and p.suffix in SOURCE_SUFFIXES
            and "__pycache__" not in p.parts
            and "node_modules" not in p.parts
        )
    return _tracked_only(files)


def test_public_text_has_no_private_paths_or_internal_jargon() -> None:
    hits: list[str] = []
    for path in _public_files():
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for needle in FORBIDDEN:
                if needle in line:
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {needle!r}")
    assert not hits, "private path or internal jargon in public text:\n" + "\n".join(hits)


def test_the_scan_sees_the_files_it_is_meant_to_scan() -> None:
    names = {p.name for p in _public_files()}
    assert {"README.md", "CHANGELOG.md", "UPGRADING.md", "EXIT_CODES.md"} <= names


def _scan_source() -> tuple[list[str], set[tuple[str, str]]]:
    hits: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in _source_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in SOURCE_MARKERS.items():
                if rx.search(line):
                    if (rel, name) in ALLOWLIST:
                        used.add((rel, name))
                    else:
                        hits.append(f"{rel}:{n}: {name}: {line.strip()[:100]}")
    return hits, used


def test_shipped_code_and_config_carry_no_internal_labels() -> None:
    hits, _ = _scan_source()
    assert not hits, (
        "internal label, private path or private project name in code/config; keep the reason, "
        "drop the label:\n" + "\n".join(hits)
    )


def test_tests_and_manifest_name_no_other_project() -> None:
    files = [
        p
        for p in sorted((ROOT / "tests").rglob("*"))
        if p.is_file()
        and p.suffix in {".py", ".json", ".jsonl", ".md", ".txt"}
        and p.name != Path(__file__).name
        and "__pycache__" not in p.parts
    ]
    files += [p for p in sorted((ROOT / ".portfolio").rglob("*")) if p.is_file()]
    files += [p for p in sorted((ROOT / "data").glob("*.jsonl")) if p.is_file()]
    hits = [
        f"{p.relative_to(ROOT).as_posix()}:{n}"
        for p in _tracked_only(files)
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if OTHER_PROJECT_NAMES.search(line)
    ]
    assert not hits, "another project's name in tests, data or the manifest:\n" + "\n".join(hits)


def test_every_allowlist_entry_still_matches_something() -> None:
    _, used = _scan_source()
    stale = sorted(set(ALLOWLIST) - used)
    assert not stale, f"allowlist entries that no longer match anything (delete them): {stale}"


def test_source_scan_covers_the_shipped_surfaces() -> None:
    rels = {p.relative_to(ROOT).as_posix() for p in _source_files()}
    assert "tracegauge/cli.py" in rels
    assert "tracegauge/web/templates/base.html" in rels
    assert "tes/__init__.py" in rels
    assert "pyproject.toml" in rels
    assert ".github/workflows/ci.yml" in rels
    assert any(r.startswith("scripts/") for r in rels)


def test_markers_do_fire_on_the_labels_they_exist_for() -> None:
    samples = {
        "work-package label": "# done for W1" + "A D6",
        "audit label": "(audit D" + "8, 2026-10-07)",
        "phase label": "Phase " + "7 scope",
        "ticket code": "# R" + "R1: persisted",
        "private rule or doc": "per CLAUDE" + ".md rule 39c",
        "role name": "the orchestra" + "tor polls",
        "private path or project": "C:" + "\\Users\\x",
    }
    for name, text in samples.items():
        assert SOURCE_MARKERS[name].search(text), (name, text)
    assert not SOURCE_MARKERS["ticket code"].search("licence CC0 1.0")


def test_scans_skip_untracked_local_files_but_keep_tracked_ones() -> None:
    ignored = ROOT / "data" / "swechat_noncc_adapted.jsonl"  # .gitignore'd, local-only
    tracked = ROOT / "README.md"
    assert tracked in _tracked_only([tracked, ignored])
    assert ignored not in _tracked_only([tracked, ignored])


def test_no_paragraph_repeats_a_sentence() -> None:
    """A merge once left a README paragraph with a block of itself pasted twice."""
    root = Path(__file__).resolve().parent.parent
    for rel in ("README.md", "CHANGELOG.md", "docs/METHODOLOGY.md"):
        for n, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
            sentences = [s.strip() for s in line.split(". ") if len(s.strip()) > 60]
            dup = {s for s in sentences if sentences.count(s) > 1}
            assert not dup, f"{rel}:{n} repeats a sentence: {sorted(dup)[0][:80]}"


def test_ratio_of_sums_interval_is_a_bootstrap_not_wilson() -> None:
    """The subagent share is a ratio of sums, so a Wilson (binomial) interval does not apply to it."""
    paths = [
        "README.md",
        "CHANGELOG.md",
        "DATA_SOURCES.md",
        *sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "docs").rglob("*.md")),
    ]
    for rel in paths:
        # A hard-wrapped bullet or paragraph is one block: the interval may sit on a later line than the claim.
        block: list[str] = []
        blocks: list[str] = []
        for line in [*(ROOT / rel).read_text(encoding="utf-8").splitlines(), ""]:
            if not line.strip() or line.lstrip().startswith(("- ", "|", "#")):
                blocks.append(" ".join(block))
                block = [line] if line.strip() else []
            else:
                block.append(line)
        for text in blocks:
            low = text.lower()
            if "53.8% of api-equivalent" in low or "subagent share" in low:
                assert "wilson" not in low, f"{rel} labels a ratio-of-sums interval as Wilson"
