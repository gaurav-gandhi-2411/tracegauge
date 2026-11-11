# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Packaging integrity tests.

These tests verify that the installed tracegauge package is self-contained:
- cc_baselines.json is bundled and loadable from the installed location
- __version__ is present and has the correct format
- The entry-point function resolves (importable without error)

They are designed to pass both from a source checkout (pip install -e .)
and from a clean-room wheel install (no repo access). The baselines load
test is the critical gate: it exercises the BUNDLED_BASELINES_PATH
resolution that depends on package-data inclusion in the wheel.
"""

import re


def test_version_present() -> None:
    """__version__ is importable and looks like a semver string."""
    from tracegauge import __version__

    assert isinstance(__version__, str)
    assert __version__ != ""
    # Accepts semver (1.2.3), pre-release (0.1.0a1), or dev fallback (0.0.0.dev0)
    assert re.match(r"^\d+\.\d+\.\d+", __version__), (
        f"__version__ {__version__!r} does not start with X.Y.Z"
    )


def test_baselines_load_from_installed_package() -> None:
    """cc_baselines.json loads from the installed package location (not repo path)."""
    from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines

    assert BUNDLED_BASELINES_PATH.exists(), (
        f"Bundled baselines not found at {BUNDLED_BASELINES_PATH}. "
        "The package-data declaration in pyproject.toml may be missing "
        "or cc_baselines.json was not included in the wheel."
    )

    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    assert isinstance(baselines, dict), "load_baselines() must return a dict"
    assert len(baselines) > 0, "Baselines dict must not be empty"
    # Task types are nested under baselines["types"] — the top-level dict contains
    # metadata keys like "generated", "token_measure", "types", "scope_gates", etc.
    assert "types" in baselines, (
        f"Expected 'types' key in baselines. Found top-level keys: {set(baselines.keys())}"
    )
    expected_types = {"ml-eval", "debug-fix", "infra-deploy", "research-recon", "feature-build"}
    actual_types = set(baselines["types"].keys())
    assert expected_types.issubset(actual_types), (
        f"Missing task types in baselines['types']. Found: {actual_types}"
    )


def test_entry_point_importable() -> None:
    """The CLI entry point function is importable without error."""
    from tracegauge.cli import main  # noqa: F401 — import is the test

    assert callable(main)


def test_package_name_is_tracegauge() -> None:
    """The installed package metadata reports name=tracegauge."""
    from importlib.metadata import metadata

    meta = metadata("tracegauge")
    assert meta["Name"] == "tracegauge"
    # The version is deliberately not asserted here as a literal (it went stale on every release);
    # tests/test_version_consistency.py compares it with pyproject.toml, uv.lock and CHANGELOG.md.
    # PEP 639: setuptools>=77 with license = "Apache-2.0" (SPDX string) emits
    # "License-Expression" in the METADATA file. Fall back to "License" for older
    # build backends that may use the legacy classifier-based field.
    license_expr = meta.get("License-Expression") or meta.get("License") or ""
    assert license_expr == "Apache-2.0", (
        f"Expected 'Apache-2.0' in license metadata. "
        f"License-Expression={meta.get('License-Expression')!r}, "
        f"License={meta.get('License')!r}"
    )


def test_console_scripts_resolve() -> None:
    """tracegauge and tgauge run the CLI; the tes command was removed in 0.18."""
    from importlib.metadata import entry_points

    eps = entry_points(group="console_scripts")
    ep_map = {ep.name: ep.value for ep in eps if ep.name in ("tes", "tgauge", "tracegauge")}

    assert ep_map == {
        "tracegauge": "tracegauge.cli:main",
        "tgauge": "tracegauge.cli:main",
    }
