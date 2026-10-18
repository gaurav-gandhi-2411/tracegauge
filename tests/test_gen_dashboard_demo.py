# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""scripts/gen_dashboard_demo.py builds the synthetic tree the dashboard screenshots are taken of.

Three properties matter: it is deterministic (the screenshots can be reproduced), it contains
nothing outside the synthetic vocabulary (they can be published), and every session scores
through the library path the dashboard's watcher uses.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_dashboard_demo as gen  # noqa: E402
from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines  # noqa: E402
from tracegauge.cost import load_price_table  # noqa: E402
from tracegauge.watcher import score_session_file  # noqa: E402

# Built from pieces so this file does not trip a repo-wide grep for the markers it forbids.
PRIVATE = re.compile(
    r"[A-Za-z]:[\\/]|/(?:Users|home)/|[Gg]aur[a]v|[Gg]andh[i]|@[A-Za-z0-9-]+\.[a-z]{2,}"
    r"|CLAUDE" + r"\.md|\.claude\b|" + "|".join(["token" + "-efficiency", "ml" + "-projects"]),
)
MAIN_STATES = {
    "output-heavy",
    "context-heavy",
    "unpriced",
    "partly-unpriced",
    "cache-churn",
    "redundant-reads",
}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.*"))}


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Path]]:
    root = tmp_path_factory.mktemp("demo")
    return root, gen.generate(root)


def test_two_runs_write_identical_bytes(tree: tuple[Path, dict[str, Path]], tmp_path: Path) -> None:
    gen.generate(tmp_path)
    assert _tree_bytes(tmp_path) == _tree_bytes(tree[0])


def test_every_state_is_present_and_layout_is_projects_project_session(
    tree: tuple[Path, dict[str, Path]],
) -> None:
    root, written = tree
    assert set(written) >= MAIN_STATES
    for path in written.values():
        assert path.parent.parent == root / "projects"
        assert path.parent.name in {gen.PROJECT_API, gen.PROJECT_WEB}


def test_output_has_no_private_marker_and_only_synthetic_projects(
    tree: tuple[Path, dict[str, Path]],
) -> None:
    root, _ = tree
    for rel, data in _tree_bytes(root).items():
        text = data.decode("utf-8")
        assert not PRIVATE.search(text), f"{rel} has a private-looking marker"
        # The only path-like strings are the invented project-relative file paths.
        for path in re.findall(r'"file_path": "([^"]+)"', text):
            assert path.split("/")[0] in {gen.PROJECT_API, gen.PROJECT_WEB, "docs"}, path


def test_subagent_transcripts_exist_and_are_sidechains(
    tree: tuple[Path, dict[str, Path]],
) -> None:
    sub = tree[1]["output-heavy"].with_suffix("") / "subagents"
    files = sorted(sub.glob("agent-*.jsonl"))
    assert len(files) == 3
    for f in files:
        recs = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]
        assert recs
        assert all(r["isSidechain"] is True for r in recs)
    assert not list(sub.glob("*.meta.json"))  # this build does not read agent types


def test_every_session_scores_through_the_library_path(
    tree: tuple[Path, dict[str, Path]],
) -> None:
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    prices = load_price_table()
    results = {
        label: score_session_file(path, baselines, use_judge=False, prices=prices)
        for label, path in tree[1].items()
    }
    assert all(r is not None for r in results.values())
    assert results["redundant-reads"].waste_event_count > 0
    assert results["healthy-1"].waste_event_count == 0
    assert gen.UNPRICED not in prices.get("models", prices)  # it must really be unpriced
