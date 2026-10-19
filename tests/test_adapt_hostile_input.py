# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""A transcript line that is valid JSON but not the shape Claude Code writes must be skipped or defaulted,
never abort the session. Two real failures motivated this: a SWE-chat session with
``"cache_creation_input_tokens": null`` (the whole file failed with ``int() argument ... NoneType``), and a
line that is a bare ``null`` / number / list. A seeded structural fuzz covers the rest of the shape space."""

import copy
import json
import random
from pathlib import Path
from typing import Any

import pytest
import tracegauge
from tracegauge.adapt import adapt_session

SAMPLE = Path(tracegauge.__file__).parent / "data" / "quickstart_sample_session.jsonl"


def _rows() -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write(tmp: Path, name: str, lines: list[str]) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _digest(p: Path) -> dict[str, Any]:
    rec = adapt_session(p)
    return json.loads(json.dumps(rec["digest"], sort_keys=True, default=str))


def test_a_null_token_count_counts_as_zero_and_does_not_abort(tmp_path: Path) -> None:
    rows = _rows()
    nulled = copy.deepcopy(rows)
    zeroed = copy.deepcopy(rows)
    for r_null, r_zero in zip(nulled, zeroed, strict=True):
        if r_null.get("type") == "assistant":
            r_null["message"]["usage"]["cache_creation_input_tokens"] = None
            r_zero["message"]["usage"]["cache_creation_input_tokens"] = 0
    a = _digest(_write(tmp_path / "a", "s.jsonl", [json.dumps(r) for r in nulled]))
    b = _digest(_write(tmp_path / "b", "s.jsonl", [json.dumps(r) for r in zeroed]))
    assert a == b


@pytest.mark.parametrize("value", [None, True, "x", "", [], {}, [1], 1e30, "9" * 40])
def test_any_odd_value_in_a_usage_count_is_survivable(tmp_path: Path, value: Any) -> None:
    rows = _rows()
    for r in rows:
        if r.get("type") == "assistant":
            r["message"]["usage"]["output_tokens"] = value
            r["message"]["usage"]["cache_creation"] = {
                "ephemeral_5m_input_tokens": value,
                "ephemeral_1h_input_tokens": value,
            }
    adapt_session(_write(tmp_path, "s.jsonl", [json.dumps(r) for r in rows]))


@pytest.mark.parametrize(
    "junk",
    [
        "null",
        "42",
        '"text"',
        "[1, 2]",
        "true",
        "[" * 5000 + "]" * 5000,
        '{"type": "assistant", "message": null}',
        '{"type": "assistant", "message": "str"}',
        '{"type": "user", "message": [1]}',
        '{"type": "assistant", "message": {"usage": [1], "model": 5}}',
    ],
)
def test_a_junk_line_is_skipped_and_the_rest_of_the_session_still_scores(
    tmp_path: Path, junk: str
) -> None:
    lines = [json.dumps(r) for r in _rows()]
    clean = _digest(_write(tmp_path / "clean", "s.jsonl", lines))
    mixed = _digest(_write(tmp_path / "mixed", "s.jsonl", [lines[0], junk, *lines[1:]]))
    # the junk line adds at most itself as a record; every real turn is still there
    assert len(mixed["turns"]) >= len(clean["turns"])


def test_a_non_object_line_is_reported_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = [json.dumps(r) for r in _rows()]
    adapt_session(_write(tmp_path, "s.jsonl", [lines[0], "null", "7", *lines[1:]]))
    err = capsys.readouterr().err
    assert err.count("not a JSON object") == 2


def test_a_seeded_structural_fuzz_never_raises(tmp_path: Path) -> None:
    """150 mutations of the bundled sample with hostile values (null, wrong types, empty containers, huge numbers)."""
    hostile = [
        None,
        0,
        -1,
        3.5,
        1e30,
        "x",
        "",
        [],
        {},
        [1, 2],
        [None],
        {"a": None},
        True,
        "9" * 40,
        ["text"],
    ]
    rng = random.Random(42)

    def paths(obj: Any, prefix: tuple[Any, ...] = ()) -> Any:
        yield prefix
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from paths(v, (*prefix, k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from paths(v, (*prefix, i))

    base = _rows()
    for i, r in enumerate(base):
        r.setdefault("uuid", f"u{i}")
        if r.get("type") == "assistant":
            r["message"].setdefault("id", f"msg_{i}")
            r["message"]["usage"].setdefault(
                "cache_creation", {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}
            )
            r["message"]["usage"].setdefault("server_tool_use", {"web_search_requests": 0})
    for n in range(150):
        recs = copy.deepcopy(base)
        for _ in range(rng.choice([1, 2, 3])):
            r = rng.randrange(len(recs))
            p = rng.choice([q for q in paths(recs[r]) if q])
            parent: Any = recs[r]
            for k in p[:-1]:
                parent = parent[k]
            if rng.random() < 0.2 and isinstance(parent, dict):
                parent.pop(p[-1], None)
            else:
                parent[p[-1]] = rng.choice(hostile)
        if rng.random() < 0.1:
            recs.insert(rng.randrange(len(recs) + 1), rng.choice(hostile))
        adapt_session(_write(tmp_path / f"m{n}", "s.jsonl", [json.dumps(x) for x in recs]))
