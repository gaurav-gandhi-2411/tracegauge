# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tracegauge/baselines.py — Token baseline loader and real-token calculator.

Self-contained implementation (no scripts/ import) so the installed wheel
works without repo access.

Public API:
    load_baselines(path) -> dict
    compute_real_tokens(record) -> int
    BUNDLED_BASELINES_PATH: Path
"""

import json
from pathlib import Path

# Bundled baseline — inside tracegauge/data/ so the installed package is self-contained.
BUNDLED_BASELINES_PATH: Path = Path(__file__).parent / "data" / "cc_baselines.json"
# Developer-clustered percentiles of other developers' sessions: CONTEXT only, never a verdict
# (tracegauge.context; built by scripts/build_multidev_bands.py).
MULTIDEV_BASELINES_PATH: Path = Path(__file__).parent / "data" / "multidev_bands.json"


def load_baselines(path: Path | str = BUNDLED_BASELINES_PATH) -> dict:
    """Load cc_baselines.json and return as a dict.

    Parameters
    ----------
    path:
        Path to cc_baselines.json. Defaults to the bundled tracegauge/data/cc_baselines.json.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compute_real_tokens(record: dict) -> int:
    """Return real_tokens for an adapted pool session.

    real_tokens = sum over AI turns of (token_count_input - cache_read) + token_count_output

    Excludes cache_read re-accumulation. Only AI turns (role == 'ai') are
    counted; user turns carry zero meaningful token cost in this context.

    Parameters
    ----------
    record:
        Adapted session record (from an adapted-session pool or adapt_session).
    """
    total = 0
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    for turn in turns:
        if turn.get("role") != "ai":
            continue
        inp: int = turn.get("token_count_input", 0)
        out: int = turn.get("token_count_output", 0)
        cache: int = turn.get("cache_read", 0)
        total += (inp - cache) + out
    return total


__all__ = [
    "BUNDLED_BASELINES_PATH",
    "MULTIDEV_BASELINES_PATH",
    "compute_real_tokens",
    "load_baselines",
]
