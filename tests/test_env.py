# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""Only TRACEGAUGE_* is read (0.18); a removed TES_* name set alone makes the command line refuse."""

from __future__ import annotations

from pathlib import Path

import pytest
from tracegauge import _env, cli, plan, store
from tracegauge._env import env_value, legacy_in_use, legacy_refusal


def test_only_the_primary_name_set() -> None:
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "a"}) == "a"


def test_the_removed_name_alone_is_not_read() -> None:
    assert env_value("DB_PATH", {"TES_DB_PATH": "b"}) is None


def test_both_set_the_primary_is_used() -> None:
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "a", "TES_DB_PATH": "b"}) == "a"


def test_neither_set() -> None:
    assert env_value("DB_PATH", {}) is None


def test_an_empty_primary_counts_as_unset_and_does_not_fall_back() -> None:
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "", "TES_DB_PATH": "b"}) is None


def test_no_setting_reads_a_tes_name() -> None:
    for name in _env.SETTINGS:
        assert env_value(name, {"TES_" + name: "x"}) is None
        assert env_value(name, {"TRACEGAUGE_" + name: "y", "TES_" + name: "x"}) == "y"


def test_legacy_in_use_lists_only_names_without_a_primary_twin() -> None:
    values = {
        "TES_DB_PATH": "x",
        "TRACEGAUGE_DB_PATH": "y",  # twin set: migrated, nothing to refuse for DB_PATH
        "TES_PLAN_PATH": "p",
        "TES_PRICE_TABLE": "",  # empty is unset
        "TES_UNRELATED": "z",  # not a setting we read
    }
    assert legacy_in_use(values) == ["TES_PLAN_PATH"]


def test_refusal_names_each_variable_and_its_rename() -> None:
    line = legacy_refusal({"TES_DB_PATH": "x"})
    assert line is not None
    assert line.startswith("[ERROR] TES_DB_PATH is no longer read")
    assert "TES_DB_PATH -> TRACEGAUGE_DB_PATH" in line
    assert "docs/MIGRATING_FROM_TES.md" in line
    plural = legacy_refusal({"TES_DB_PATH": "x", "TES_PLAN_PATH": "p"})
    assert plural is not None
    assert "are no longer read" in plural
    assert "TES_PLAN_PATH -> TRACEGAUGE_PLAN_PATH" in plural
    assert "\n" not in plural


def test_no_refusal_when_nothing_or_everything_is_migrated() -> None:
    assert legacy_refusal({}) is None
    assert legacy_refusal({"TRACEGAUGE_DB_PATH": "y"}) is None
    assert legacy_refusal({"TES_DB_PATH": "x", "TRACEGAUGE_DB_PATH": "y"}) is None


def test_the_store_ignores_tes_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "old.db"))
    assert store.resolve_db_path() == tmp_path / "new.db"


def test_plan_path_ignores_tes_plan_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRACEGAUGE_PLAN_PATH", raising=False)
    monkeypatch.setenv("TES_PLAN_PATH", str(tmp_path / "old.json"))
    assert plan.resolve_plan_config_path() != tmp_path / "old.json"
    monkeypatch.setenv("TRACEGAUGE_PLAN_PATH", str(tmp_path / "new.json"))
    assert plan.resolve_plan_config_path() == tmp_path / "new.json"


def test_cli_refuses_a_tes_variable_set_alone_before_doing_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The hazard this guards: a run isolated with TES_DB_PATH must not fall through to the real store.
    monkeypatch.delenv("TRACEGAUGE_DB_PATH", raising=False)
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "isolated.db"))
    monkeypatch.setattr("sys.argv", ["tracegauge", "--version"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.count("TES_DB_PATH is no longer read") == 1


def test_cli_runs_when_the_tes_variable_has_its_twin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setattr("sys.argv", ["tracegauge", "--version"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert out.startswith("tracegauge ")
    assert err == ""
