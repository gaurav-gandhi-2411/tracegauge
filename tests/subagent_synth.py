# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""A seeded synthetic Claude Code session with subagents, for the README example.

One main transcript plus ``subagents/agent-*.jsonl`` and ``agent-*.meta.json`` for three subagent
types (``executor`` x3, ``verifier`` x2, ``Explore`` x1). Token counts are generated from a fixed
seed so ``tracegauge score`` prints the same figures every time. No real session content.
"""

import json
import random
from pathlib import Path
from typing import Any

MODEL = "claude-sonnet-4-6"  # priced in the bundled table
SUBAGENTS = (
    ("a1", "executor", 14),
    ("a2", "executor", 10),
    ("a3", "executor", 8),
    ("a4", "verifier", 9),
    ("a5", "verifier", 6),
    ("a6", "Explore", 5),
)


def _assistant(mid: str, usage: dict[str, int], *, sidechain: bool) -> dict[str, Any]:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "uuid": f"{mid}-0",
        "requestId": f"req-{mid}",
        "message": {
            "id": mid,
            "role": "assistant",
            "model": MODEL,
            "content": [{"type": "text", "text": "x"}],
            "usage": usage,
        },
    }


def _chain(rng: random.Random, prefix: str, turns: int, *, sidechain: bool) -> list[dict[str, Any]]:
    """``turns`` responses whose cached context grows, as in a real tool loop."""
    out: list[dict[str, Any]] = []
    context = rng.randint(14_000, 20_000)
    for i in range(turns):
        grow = rng.randint(1_500, 6_000)
        usage = {
            "input_tokens": rng.randint(3, 40),
            "cache_creation_input_tokens": grow,
            "cache_read_input_tokens": context,
            "output_tokens": rng.randint(150, 1_400),
            "cache_creation": {"ephemeral_5m_input_tokens": grow, "ephemeral_1h_input_tokens": 0},
        }
        context += grow
        out.append(_assistant(f"{prefix}{i}", usage, sidechain=sidechain))
    return out


def build_session(root: Path) -> Path:
    """Write the session under ``root`` and return the main transcript path."""
    rng = random.Random(42)
    parent = root / "projects" / "demo-api" / "subagent_demo.jsonl"
    parent.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = [
        {"type": "user", "message": {"role": "user", "content": "synthetic task"}},
        *_chain(rng, "m", 12, sidechain=False),
    ]
    parent.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    sub_dir = parent.parent / parent.stem / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    for agent_id, agent_type, turns in SUBAGENTS:
        recs = _chain(rng, f"{agent_id}-", turns, sidechain=True)
        (sub_dir / f"agent-{agent_id}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8"
        )
        meta = {"agentType": agent_type, "description": "synthetic"}
        (sub_dir / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return parent
