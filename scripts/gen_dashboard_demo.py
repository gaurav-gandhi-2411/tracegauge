# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Builds a deterministic SYNTHETIC Claude Code project tree to photograph the local dashboard.

The dashboard screenshots in ``docs/screenshots/`` must not show a real session, so they are taken
of ``tracegauge serve --cc-path <this tree>``. Everything here is invented: project names
(``demo-api``, ``demo-web``), file names, prompts, ids and token counts all come from the tables
and the seeded ``random.Random(42)`` below. No real transcript is read.

    python scripts/gen_dashboard_demo.py <output-dir>

writes ``<output-dir>/projects/<project>/<session-id>.jsonl`` (the layout ``--cc-path`` expects is
``<output-dir>/projects``). Sessions, one per state the dashboard has a distinct display for:

* ``output-heavy``     output is more than 40 percent of the priced cost (the output lever fires)
* ``context-heavy``    a large context re-sent every turn (shown in the cost breakdown only; the
                       context share is deliberately not a lever)
* ``unpriced``         every turn uses a model id that is not in the price table
* ``partly-unpriced``  half the turns use that model id, half a priced model
* ``cache-churn``      three planted cache re-writes (tests/churn_synth.py, as gen_readme_demo.py)
* ``healthy``          a plain session that triggers nothing
* ``redundant-reads``  files re-read unchanged (REDUNDANT-READ waste events)
* three more healthy sessions on other days so the trends page has a history

``output-heavy`` also has ``<sid>/subagents/agent-*.jsonl`` (an executor, a verifier and a
general-purpose agent). The ``agent-*.meta.json`` files that name the agent type are not written:
this build of tracegauge does not read them.
"""

import argparse
import json
import os
import random
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # tests.churn_synth: the seeded synthetic-transcript builder

SEED = 42
PRICED = "claude-sonnet-5-5"
#: Deliberately absent from tracegauge/data/prices.json, so its turns are unpriced.
UNPRICED = "claude-demo-model-x"
DAY = 86_400.0
#: Session start times are offsets from this fixed epoch (a Tuesday in Oct 2025).
BASE_EPOCH = 1_760_000_000.0

PROJECT_API = "demo-api"
PROJECT_WEB = "demo-web"

_MODULES = [
    "auth", "billing", "catalog", "orders", "search", "cart", "inventory", "shipping",
    "reports", "notifications", "users", "sessions", "webhooks", "exports", "metrics", "audit",
]  # fmt: skip


def _numbered(path: str) -> str:
    """A file body in the line-numbered form Claude Code's Read tool returns."""
    lines = [
        f"{n:>6}→{'#' + path if n == 1 else '    pass  # synthetic body'}" for n in range(1, 13)
    ]
    return "\n".join(lines) + "\n"


_NOTES = [
    "has a clear entry point and small helpers.",
    "validates input before touching the store.",
    "keeps retry logic in one place.",
    "uses a module-level cache that tests reset.",
    "is covered by two integration tests.",
    "delegates formatting to a shared helper.",
]
_TASKS = {
    "output-heavy": (
        "Draft the full API reference for the demo-api service, one section per module."
    ),
    "context-heavy": "Walk the whole demo-api codebase and list every module that touches orders.",
    "unpriced": "Summarise the demo-web routing layer and list pages without a loading state.",
    "partly-unpriced": "Audit demo-web components for missing labels, then fix the worst three.",
    "healthy": "Add a pagination parameter to the demo-api list endpoints and update the tests.",
    "redundant-reads": "Find where demo-api builds order totals and explain the rounding rules.",
}


def _iso(epoch: float) -> str:
    from tests.churn_synth import iso

    return iso(epoch)


class _Session:
    """Accumulates the JSONL records of one synthetic main transcript."""

    def __init__(self, model: str, start: float, task: str) -> None:
        self.model = model
        self.t = start
        self.ctx = 0  # tokens already in the cached context
        self.call = 0
        self.records: list[dict[str, Any]] = []
        self.records.append(self._user(task))

    def _user(self, content: Any) -> dict[str, Any]:
        return {
            "type": "user",
            "isSidechain": False,
            "timestamp": _iso(self.t),
            "message": {"role": "user", "content": content},
        }

    def _assistant(self, content: Any, usage: dict[str, int], model: str) -> dict[str, Any]:
        return {
            "type": "assistant",
            "isSidechain": False,
            "timestamp": _iso(self.t),
            "message": {"model": model, "content": content, "usage": usage},
        }

    def cycle(
        self,
        tool: str,
        tool_input: dict[str, str],
        result: str,
        note: str,
        *,
        created: int,
        out: int,
        fresh: int = 900,
        model: str | None = None,
        result_tokens: int = 600,
    ) -> None:
        """A tool call, its result, and the assistant's note: three records, two API responses."""
        model = model or self.model
        self.call += 1
        self.t += 4.0
        usage = {
            "input_tokens": fresh,
            "cache_creation_input_tokens": created,
            "cache_read_input_tokens": self.ctx,
            "output_tokens": out,
        }
        block = [{"type": "tool_use", "id": f"call_{self.call}", "name": tool, "input": tool_input}]
        self.records.append(self._assistant(block, usage, model))
        self.ctx += created + out
        self.t += 2.0
        tool_result = [
            {"type": "tool_result", "tool_use_id": f"call_{self.call}", "content": result}
        ]
        self.records.append(self._user(tool_result))
        self.ctx += result_tokens
        self.t += 3.0
        usage2 = {
            "input_tokens": fresh // 2,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": self.ctx,
            "output_tokens": out,
        }
        self.records.append(self._assistant([{"type": "text", "text": note}], usage2, model))
        self.ctx += out

    def finish(self, text: str, out: int, model: str | None = None) -> None:
        self.t += 5.0
        usage = {
            "input_tokens": 1200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": self.ctx,
            "output_tokens": out,
        }
        self.records.append(
            self._assistant([{"type": "text", "text": text}], usage, model or self.model)
        )


def _file(project: str, i: int) -> str:
    return f"{project}/{_MODULES[i % len(_MODULES)]}/handlers_{i // len(_MODULES)}.py"


def _read_cycles(
    s: _Session, project: str, n: int, *, created: int, out: int, fresh: int = 900, offset: int = 0
) -> None:
    for i in range(offset, offset + n):
        path = _file(project, i)
        s.cycle(
            "Read",
            {"file_path": path},
            _numbered(path),
            f"{path}: {_NOTES[i % len(_NOTES)]}",
            created=created,
            out=out,
            fresh=fresh,
        )


def _output_heavy(rng: random.Random, start: float) -> _Session:
    s = _Session(PRICED, start, _TASKS["output-heavy"])
    s.ctx = 6_000
    for i in range(14):
        path = _file(PROJECT_API, i)
        s.cycle(
            "Write",
            {"file_path": f"docs/api/{_MODULES[i % len(_MODULES)]}.md"},
            "ok",
            f"Wrote the reference section for {_MODULES[i % len(_MODULES)]}.",
            created=1_400 + rng.randint(0, 300),
            out=7_000 + rng.randint(0, 1_500),
            fresh=700,
            result_tokens=80,
        )
        s.ctx = min(s.ctx, 40_000)  # context kept small: the cost is the generated text
    s.finish("The API reference is written, one section per module.", 6_000)
    return s


def _context_heavy(rng: random.Random, start: float) -> _Session:
    s = _Session(PRICED, start, _TASKS["context-heavy"])
    s.ctx = 150_000  # a large codebase summary already in context when the work starts
    _read_cycles(s, PROJECT_API, 28, created=2_000 + rng.randint(0, 400), out=500, fresh=1_200)
    s.finish("Seven modules touch orders; the list is above.", 900)
    return s


def _unpriced(rng: random.Random, start: float) -> _Session:
    s = _Session(UNPRICED, start, _TASKS["unpriced"])
    s.ctx = 8_000
    _read_cycles(s, PROJECT_WEB, 16, created=2_200 + rng.randint(0, 300), out=900)
    s.finish("Four pages have no loading state.", 1_100)
    return s


def _partly_unpriced(rng: random.Random, start: float) -> _Session:
    s = _Session(PRICED, start, _TASKS["partly-unpriced"])
    s.ctx = 8_000
    _read_cycles(s, PROJECT_WEB, 9, created=2_200 + rng.randint(0, 300), out=900)
    for i in range(9, 18):
        path = _file(PROJECT_WEB, i)
        s.cycle(
            "Read",
            {"file_path": path},
            _numbered(path),
            f"{path}: {_NOTES[i % len(_NOTES)]}",
            created=2_300,
            out=900,
            model=UNPRICED,
        )
    s.finish("Three labels fixed; the rest are listed.", 1_000, model=UNPRICED)
    return s


def _healthy(rng: random.Random, start: float, project: str, key: str = "healthy") -> _Session:
    s = _Session(PRICED, start, _TASKS[key])
    s.ctx = 9_000
    _read_cycles(s, project, 40, created=2_400 + rng.randint(0, 400), out=200 + rng.randint(0, 100))
    s.finish("Done; the change and its tests are in place.", 1_400)
    return s


def _redundant_reads(rng: random.Random, start: float) -> _Session:
    s = _Session(PRICED, start, _TASKS["redundant-reads"])
    s.ctx = 30_000
    s.ctx = 80_000
    # Each file is read, then read again with identical content two turns later and nothing in
    # between: the REDUNDANT-READ detector's path B (the second read's turn is the waste).
    for i in range(6):
        path = _file(PROJECT_API, i)
        for note in (f"{path}: {_NOTES[i % len(_NOTES)]}", f"{path}: read again; unchanged."):
            s.cycle("Read", {"file_path": path}, _numbered(path), note, created=9_000, out=800)
        _read_cycles(s, PROJECT_API, 3, created=2_400, out=800, offset=10 + 3 * i)
    s.finish("Totals are rounded half-even at the line level.", 1_300)
    return s


def _subagent_records(rng: random.Random, start: float, turns: int) -> list[dict[str, Any]]:
    """One subagent transcript: every record is a sidechain assistant response (priced model)."""
    recs: list[dict[str, Any]] = []
    t, ctx = start, 4_000
    for k in range(turns):
        t += 6.0
        created = 1_800 + rng.randint(0, 300)
        recs.append(
            {
                "type": "assistant",
                "isSidechain": True,
                "timestamp": _iso(t),
                "message": {
                    "id": f"msg_sub_{rng.getrandbits(32):08x}_{k}",
                    "model": PRICED,
                    "content": [{"type": "text", "text": "Synthetic subagent step."}],
                    "usage": {
                        "input_tokens": 800,
                        "cache_creation_input_tokens": created,
                        "cache_read_input_tokens": ctx,
                        "output_tokens": 600 + rng.randint(0, 200),
                    },
                },
            }
        )
        ctx += created + 600
    return recs


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def generate(out_dir: Path) -> dict[str, Path]:
    """Write the tree under ``out_dir / 'projects'``; return session label -> transcript path."""
    from tests.churn_synth import build_chain

    rng = random.Random(SEED)

    def new_id() -> str:
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))

    plan: list[tuple[str, str, float, Any]] = [
        # (label, project, start offset in days, builder)
        ("healthy-1", PROJECT_API, 0.0, lambda st: _healthy(rng, st, PROJECT_API)),
        ("healthy-2", PROJECT_WEB, 1.3, lambda st: _healthy(rng, st, PROJECT_WEB)),
        ("context-heavy", PROJECT_API, 2.4, lambda st: _context_heavy(rng, st)),
        ("healthy-3", PROJECT_API, 3.2, lambda st: _healthy(rng, st, PROJECT_API)),
        ("unpriced", PROJECT_WEB, 4.5, lambda st: _unpriced(rng, st)),
        ("output-heavy", PROJECT_API, 5.1, lambda st: _output_heavy(rng, st)),
        ("redundant-reads", PROJECT_API, 6.3, lambda st: _redundant_reads(rng, st)),
        ("partly-unpriced", PROJECT_WEB, 7.2, lambda st: _partly_unpriced(rng, st)),
        ("healthy-4", PROJECT_WEB, 8.4, lambda st: _healthy(rng, st, PROJECT_WEB)),
    ]
    written: dict[str, Path] = {}
    for label, project, days, build in plan:
        start = BASE_EPOCH + days * DAY
        session = build(start)
        sid = new_id()
        path = out_dir / "projects" / project / f"{sid}.jsonl"
        _write_jsonl(path, session.records)
        os.utime(path, (session.t, session.t))  # mtime = last record, so the trends dates agree
        written[label] = path
        if label == "output-heavy":
            sub = path.with_suffix("") / "subagents"
            for agent, turns in (("executor", 9), ("verifier", 6), ("general-purpose", 4)):
                agent_id = f"a{rng.getrandbits(48):012x}"
                _write_jsonl(sub / f"agent-{agent_id}.jsonl", _subagent_records(rng, start, turns))

    # The planted-churn session: the same builder and seed as scripts/gen_readme_demo.py.
    churn = build_chain(
        random.Random(SEED), 40, {10: "TTL_GAP", 20: "MODEL_SWITCH", 30: "COMPACTION"}
    )
    churn_path = out_dir / "projects" / PROJECT_API / f"{new_id()}.jsonl"
    _write_jsonl(churn_path, churn)
    stamp = BASE_EPOCH + 9.5 * DAY  # after every other session
    os.utime(churn_path, (stamp, stamp))
    written["cache-churn"] = churn_path
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the synthetic dashboard demo tree.")
    parser.add_argument("out_dir", type=Path, help="directory to write; --cc-path is its projects/")
    args = parser.parse_args()
    for label, path in generate(args.out_dir).items():
        print(f"{label:16} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
