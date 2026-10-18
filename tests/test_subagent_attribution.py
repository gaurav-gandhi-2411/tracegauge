# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Per-subagent-type attribution (0.17.0): adapter, cost, score text/JSON, store, cost period.

SYNTHETIC fixtures only. Every pin has a negative control: a test that fails when the pinned
property is broken (so the pin is shown to bite, not merely to pass).
"""

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest
from tracegauge._digest import reconstruct_digest
from tracegauge.adapt import adapt_session, collect_subagent_usage, read_agent_type
from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.cost import compute_session_cost, load_price_table
from tracegauge.cost_period import compute_period_cost, resolve_period
from tracegauge.report import format_human, format_json
from tracegauge.score import build_subagents_summary, score_session
from tracegauge.store import get_session, open_db, upsert_session

from tests.test_subagent_rollup import PRICED, UNPRICED, _assistant, _usage, _write

SUBAGENTS_KEYS = {"count", "tokens", "usd", "usd_is_floor", "share_of_session_usd", "by_type"}
BY_TYPE_KEYS = {
    "type",
    "files",
    "requests",
    "tokens",
    "real_tokens",
    "usd",
    "usd_is_floor",
    "share_of_session_usd",
}

# (agent id, raw meta.json text or None for "no meta file", model)
AgentSpec = tuple[str, str | None, str]


def _meta(agent_type: str) -> str:
    return json.dumps({"agentType": agent_type, "description": "synthetic"})


def _make_session(root: Path, agents: list[AgentSpec], sid: str = "sid-1") -> Path:
    """A parent with one main response and one subagent transcript per spec."""
    parent = root / "proj" / f"{sid}.jsonl"
    _write(
        parent,
        [
            {"type": "user", "message": {"role": "user", "content": "do the thing"}},
            _assistant("m1", PRICED, _usage(10, 0, 1_000, 100), sidechain=False),
        ],
    )
    sub_dir = root / "proj" / sid / "subagents"
    for n, (agent_id, meta, model) in enumerate(agents):
        _write(
            sub_dir / f"agent-{agent_id}.jsonl",
            [_assistant(f"s{n}", model, _usage(5 + n, 100, 2_000, 50 + n), sidechain=True)],
        )
        if meta is not None:
            (sub_dir / f"agent-{agent_id}.meta.json").write_text(meta, encoding="utf-8")
    return parent


def _score(parent: Path) -> tuple[dict[str, Any], Any, Any]:
    rec = adapt_session(parent)
    cost = compute_session_cost(reconstruct_digest(rec["digest"]), load_price_table())
    result = score_session(
        rec, load_baselines(BUNDLED_BASELINES_PATH), session_cost=cost, attribution=None
    )
    return rec, cost, result


THREE_TYPES: list[AgentSpec] = [
    ("a1", _meta("executor"), PRICED),
    ("a2", _meta("executor"), PRICED),  # same type as a1: must merge into one row
    ("a3", _meta("verifier"), PRICED),
    ("a4", _meta("Explore"), PRICED),
]


# ------------------------------------------------------------------ adapter


def test_two_files_of_the_same_type_merge(tmp_path: Path) -> None:
    usage = collect_subagent_usage(_make_session(tmp_path, THREE_TYPES))
    assert usage is not None
    by_type = usage["by_type"]
    assert set(by_type) == {"executor", "verifier", "Explore"}
    assert by_type["executor"]["file_count"] == 2
    assert by_type["executor"]["message_count"] == 2
    # The per-type roll-up reconciles with the existing session totals (nothing re-derived).
    for key in ("file_count", "message_count", "real_tokens", "billed_tokens"):
        assert sum(v[key] for v in by_type.values()) == usage[key]
    # Negative control: dropping a type breaks the reconciliation, so the check above bites.
    partial = sum(v["billed_tokens"] for k, v in by_type.items() if k != "verifier")
    assert partial != usage["billed_tokens"]


@pytest.mark.parametrize(
    "meta",
    [
        None,  # older Claude Code: no meta file at all
        "{not json",
        "[]",  # valid JSON, not a dict
        '"executor"',
        json.dumps({"agentType": 7}),  # non-string type
        json.dumps({"agentType": None}),
        json.dumps({"agentType": ""}),
        json.dumps({"other": "x"}),
        json.dumps({"agentType": "\x00\x07\n"}),  # nothing printable left after cleaning
    ],
)
def test_missing_or_bad_meta_is_unknown(tmp_path: Path, meta: str | None) -> None:
    parent = _make_session(tmp_path, [("a1", meta, PRICED)])
    usage = collect_subagent_usage(parent)
    assert usage is not None
    assert set(usage["by_type"]) == {"unknown"}
    assert {t["agent_type"] for t in usage["turns"]} == {"unknown"}


def test_good_meta_is_not_unknown_negative_control(tmp_path: Path) -> None:
    usage = collect_subagent_usage(_make_session(tmp_path, [("a1", _meta("executor"), PRICED)]))
    assert usage is not None
    assert "unknown" not in usage["by_type"]


def test_agent_type_is_capped_and_stripped_of_control_characters(tmp_path: Path) -> None:
    sub = tmp_path / "agent-x.jsonl"
    sub.write_text("", encoding="utf-8")
    sub.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "ev\x1b[31mil\n" + "x" * 200}), encoding="utf-8"
    )
    cleaned = read_agent_type(sub)
    assert len(cleaned) == 64
    assert cleaned.isprintable()
    assert cleaned.startswith("ev[31mil")


# ------------------------------------------------------------------ cost


def test_cost_types_sum_to_subagent_totals(tmp_path: Path) -> None:
    rec, cost, _ = _score(_make_session(tmp_path, THREE_TYPES))
    by_type = cost.subagent_by_type
    assert set(by_type) == {"executor", "verifier", "Explore"}
    assert sum(t.usd for t in by_type.values()) == pytest.approx(cost.subagent_usd)
    assert sum(t.tokens for t in by_type.values()) == rec["subagent_usage"]["billed_tokens"]
    assert sum(t.requests for t in by_type.values()) == rec["subagent_usage"]["message_count"]
    assert sum(t.files for t in by_type.values()) == rec["subagent_usage"]["file_count"]
    assert by_type["executor"].files == 2
    assert not any(t.usd_is_floor for t in by_type.values())
    # Negative control: the sum is not trivially satisfied by any subset of the types.
    without_executor = sum(t.usd for k, t in by_type.items() if k != "executor")
    assert without_executor != pytest.approx(cost.subagent_usd)


def test_unpriced_model_is_a_floor_never_zero(tmp_path: Path) -> None:
    agents: list[AgentSpec] = [
        ("a1", _meta("executor"), PRICED),
        ("a2", _meta("verifier"), UNPRICED),
        ("a3", _meta("mixed"), PRICED),
        ("a4", _meta("mixed"), UNPRICED),
    ]
    _, cost, result = _score(_make_session(tmp_path, agents))
    by_type = cost.subagent_by_type
    assert by_type["verifier"].usd_is_floor and not by_type["verifier"].usd_known
    assert by_type["mixed"].usd_is_floor and by_type["mixed"].usd_known  # priced part only
    assert not by_type["executor"].usd_is_floor and by_type["executor"].usd_known

    rows = {r["type"]: r for r in result.subagents["by_type"]}
    assert rows["verifier"]["usd"] is None  # JSON null, never 0.0
    assert rows["verifier"]["usd_is_floor"] is True
    assert rows["mixed"]["usd"] > 0 and rows["mixed"]["usd_is_floor"] is True
    assert rows["executor"]["usd"] > 0 and rows["executor"]["usd_is_floor"] is False
    assert result.subagents["usd_is_floor"] is True

    text = format_human(result)
    verifier_line = next(ln for ln in text.splitlines() if ln.strip().startswith("verifier:"))
    assert "unpriced" in verifier_line and "$0.00" not in verifier_line
    mixed_line = next(ln for ln in text.splitlines() if ln.strip().startswith("mixed:"))
    assert ">= " in mixed_line and "unpriced" not in mixed_line
    # Negative control: a fully priced session never claims a floor or an unpriced type.
    clean = _score(_make_session(tmp_path, THREE_TYPES, sid="sid-clean"))[2]
    assert "unpriced" not in "\n".join(
        ln for ln in format_human(clean).splitlines() if ln.strip().startswith("executor:")
    )
    assert clean.subagents["usd_is_floor"] is False


# ------------------------------------------------------------------ score text / JSON


def test_json_key_set_is_pinned(tmp_path: Path) -> None:
    _, _, result = _score(_make_session(tmp_path, THREE_TYPES))
    doc = json.loads(format_json(result))
    subagents = doc["subagents"]
    assert set(subagents) == SUBAGENTS_KEYS
    assert all(set(r) == BY_TYPE_KEYS for r in subagents["by_type"])
    # existing keys are untouched (additive only)
    for key in ("subagent_tokens", "subagent_cost_usd", "subagent_count", "session_cost_usd"):
        assert key in doc
    # sorted by usd desc, then type
    keys = [(-(r["usd"] or 0.0), r["type"]) for r in subagents["by_type"]]
    assert keys == sorted(keys)
    assert subagents["count"] == doc["subagent_count"] == 4
    assert subagents["share_of_session_usd"] == pytest.approx(
        doc["subagent_cost_usd"] / doc["session_cost_usd"]
    )
    # Negative control: the pin rejects an added or a missing key.
    assert set(dict(subagents, extra=1)) != SUBAGENTS_KEYS
    assert set({k: v for k, v in subagents.items() if k != "usd"}) != SUBAGENTS_KEYS


def test_session_without_subagents_has_the_same_fixed_shape(tmp_path: Path) -> None:
    _, _, result = _score(_make_session(tmp_path, []))
    subagents = json.loads(format_json(result))["subagents"]
    assert set(subagents) == SUBAGENTS_KEYS
    assert subagents["by_type"] == [] and subagents["count"] == 0
    assert subagents["share_of_session_usd"] == 0.0
    assert "subagents:" not in format_human(result)


def test_share_is_null_when_session_total_is_unknown() -> None:
    assert build_subagents_summary(None)["share_of_session_usd"] is None
    assert build_subagents_summary(None)["usd"] is None


def test_text_lists_types_largest_first_with_share_and_caps_at_six(tmp_path: Path) -> None:
    agents: list[AgentSpec] = [(f"a{i}", _meta(f"type{i}"), PRICED) for i in range(8)]
    _, cost, result = _score(_make_session(tmp_path, agents))
    text = format_human(result)
    share = cost.subagent_usd / cost.total_usd
    assert f"subagents: {share * 100:.0f}% of this session's ${cost.total_usd:.2f}" in text
    type_lines = [ln for ln in text.splitlines() if ln.strip().startswith("type")]
    assert len(type_lines) == 6
    assert "2 more type(s)" in text
    assert "% of session" in type_lines[0]
    usd_order = [r["usd"] for r in result.subagents["by_type"]]
    assert usd_order == sorted(usd_order, reverse=True)
    # Negative control: three types fit, so no "more" line appears.
    few = _score(_make_session(tmp_path, THREE_TYPES, sid="sid-few"))[2]
    assert "more type(s)" not in format_human(few)


# ------------------------------------------------------------------ store


def test_migration_adds_column_and_old_rows_read_as_not_recorded(tmp_path: Path) -> None:
    parent = _make_session(tmp_path, THREE_TYPES)
    _, _, result = _score(parent)
    db = tmp_path / "old.db"
    conn = open_db(db)
    conn.execute("ALTER TABLE sessions DROP COLUMN subagent_by_type_json")  # a 0.16 database
    conn.commit()
    conn.close()
    for _ in range(2):  # idempotent
        conn = open_db(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert "subagent_by_type_json" in cols
        conn.close()
    conn = open_db(db)
    upsert_session(conn, result, str(parent), 1.0, "h")
    row = get_session(conn, result.session_id)
    assert row is not None
    stored = json.loads(row["subagent_by_type_json"])
    assert {e["type"] for e in stored} == {"executor", "verifier", "Explore"}

    # A row scored before the column existed is NULL ("not recorded"), not "[]" ("no subagents").
    conn.execute("UPDATE sessions SET subagent_by_type_json = NULL")
    conn.commit()
    assert get_session(conn, result.session_id)["subagent_by_type_json"] is None  # type: ignore[index]

    # Scored with no subagents: "[]". A result without a summary stays NULL (negative control).
    none_parent = _make_session(tmp_path, [], sid="sid-none")
    none_result = _score(none_parent)[2]
    upsert_session(conn, none_result, str(none_parent), 1.0, "h2")
    assert get_session(conn, none_result.session_id)["subagent_by_type_json"] == "[]"  # type: ignore[index]
    bare = dataclasses.replace(none_result, session_id="bare", subagents={})
    upsert_session(conn, bare, str(none_parent), 1.0, "h3")
    assert get_session(conn, "bare")["subagent_by_type_json"] is None  # type: ignore[index]
    conn.close()


# ------------------------------------------------------------------ cost period


def _store_three_sessions(tmp_path: Path) -> Path:
    """Two sessions with a recorded split (one with subagents, one without) + one NULL row."""
    db = tmp_path / "tes.db"
    conn = open_db(db)
    now = time.time()
    specs = [("sid-a", THREE_TYPES), ("sid-b", [("b1", _meta("executor"), PRICED)]), ("sid-c", [])]
    for sid, agents in specs:
        parent = _make_session(tmp_path, agents, sid=sid)
        result = _score(parent)[2]
        upsert_session(conn, result, str(parent), now - 60, f"h-{sid}")
    conn.execute("UPDATE sessions SET subagent_by_type_json = NULL WHERE session_id = 'sid-c'")
    conn.commit()
    conn.close()
    return db


def test_period_aggregates_over_recorded_sessions_and_counts_null_rows(tmp_path: Path) -> None:
    db = _store_three_sessions(tmp_path)
    conn = open_db(db)
    start, end, label = resolve_period(week=True)
    report = compute_period_cost(conn, start, end, period_label=label)
    sub = report.subagents
    assert sub.sessions_recorded == 2
    assert sub.sessions_not_recorded == 1
    by_type = {t.type: t for t in sub.by_type}
    assert set(by_type) == {"executor", "verifier", "Explore"}
    assert by_type["executor"].files == 3  # 2 in sid-a + 1 in sid-b
    # Types reconcile with the recorded sessions' subagent spend.
    assert sum(t.usd or 0.0 for t in sub.by_type) == pytest.approx(sub.usd)
    rows = {r["session_id"]: r for r in conn.execute("SELECT * FROM sessions")}
    recorded_total = rows["sid-a"]["session_cost_usd"] + rows["sid-b"]["session_cost_usd"]
    # The NULL row is in neither the numerator nor the denominator.
    assert sub.recorded_total_usd == pytest.approx(recorded_total)
    assert sub.share_of_total_usd == pytest.approx(sub.usd / recorded_total)
    assert 0 < sub.share_of_total_tokens < 1  # type: ignore[operator]
    # Negative control: counting the NULL row's total would change the denominator.
    assert sub.recorded_total_usd != pytest.approx(
        recorded_total + rows["sid-c"]["session_cost_usd"]
    )
    conn.close()


def test_cost_command_text_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import tracegauge.cli as cli

    db = _store_three_sessions(tmp_path)
    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(db))
    monkeypatch.setattr("sys.argv", ["tracegauge", "cost", "--week"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "1 session not recorded" in text
    assert "% of spend" in text and "% of tokens" in text
    assert "executor" in text and "verifier" in text
    assert "$0.00" not in "\n".join(ln for ln in text.splitlines() if "executor" in ln)

    monkeypatch.setattr("sys.argv", ["tracegauge", "cost", "--week", "--json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    doc = json.loads(capsys.readouterr().out)
    sub = doc["subagents"]
    assert set(sub) == {
        "sessions_recorded",
        "sessions_not_recorded",
        "usd",
        "usd_is_floor",
        "share_of_total_usd",
        "tokens_real",
        "share_of_total_tokens",
        "by_type",
    }
    assert (sub["sessions_recorded"], sub["sessions_not_recorded"]) == (2, 1)
    assert all(
        set(t)
        == {
            "type",
            "files",
            "requests",
            "tokens",
            "real_tokens",
            "usd",
            "usd_is_floor",
            "share_of_total_usd",
        }
        for t in sub["by_type"]
    )
    assert "subagents" in doc and "by_project" in doc  # existing keys are still there


@pytest.mark.parametrize(
    "bad",
    [
        '[{"type": "x"}]',
        '{"type": "x"}',
        "42",
        '["x"]',
        '[{"type": "x", "files": "n", "requests": 1, '
        '"tokens": 1, "usd": null, "usd_is_floor": false}]',
    ],
)
def test_wrong_shape_stored_split_counts_as_not_recorded_never_crashes(
    tmp_path: Path, bad: str
) -> None:
    db = _store_three_sessions(tmp_path)
    conn = open_db(db)
    conn.execute("UPDATE sessions SET subagent_by_type_json = ? WHERE session_id = 'sid-a'", (bad,))
    conn.commit()
    start, end, label = resolve_period(week=True)
    sub = compute_period_cost(conn, start, end, period_label=label).subagents
    assert sub.sessions_not_recorded == 2  # sid-a (unreadable) + sid-c (NULL)
    assert sub.sessions_recorded == 1  # sid-b only: nothing from sid-a leaked into the totals
    assert {t.type for t in sub.by_type} == {"executor"}
    conn.close()


def test_sub_cent_price_is_not_shown_as_zero_dollars() -> None:
    from tracegauge.report import _fmt_sub_usd

    assert _fmt_sub_usd(0.003525, False) == "<$0.01"
    assert _fmt_sub_usd(0.003525, True) == ">= <$0.01"
    assert _fmt_sub_usd(0.0, False) == "$0.00"  # a genuine zero stays $0.00
    assert _fmt_sub_usd(0.02, False) == "$0.02"
    assert _fmt_sub_usd(None, False) == "unpriced"
