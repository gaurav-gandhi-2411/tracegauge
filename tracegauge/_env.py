# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Environment variables: only the ``TRACEGAUGE_*`` names are read.

Every setting is read through :func:`env_value`, which returns the ``TRACEGAUGE_<NAME>`` value if it is
set and non-empty, otherwise ``None``. The old ``TES_*`` names were deprecated in 0.16 and are no
longer read as of 0.18 (docs/MIGRATING_FROM_TES.md).

Removing them must not change behaviour silently: a script that isolates its store with
``TES_DB_PATH=/tmp/x.db`` would otherwise start writing into the real ``~/.tes/tes.db``. So the
command line refuses to run while a ``TES_*`` setting is set without its ``TRACEGAUGE_*`` twin
(:func:`legacy_in_use`, :func:`legacy_refusal`), naming the variable to rename. When both spellings
are set, ``TRACEGAUGE_*`` is the one in effect and nothing is refused (the setup has been migrated).
The library functions never print and never refuse; they read ``TRACEGAUGE_*`` only.
"""

import os
from collections.abc import Mapping

PRIMARY_PREFIX = "TRACEGAUGE_"
LEGACY_PREFIX = "TES_"

#: Every setting the code reads, without its prefix.
SETTINGS = (
    "DB_PATH",
    "PLAN_PATH",
    "PRICE_TABLE",
    "NO_NOTICE",
    "CORPUS_URL",
    "CORPUS_ANON_KEY",
    "CORPUS_WITHDRAW_URL",
    "TEST_USE_AMBIENT_STORE",
)


def env_value(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Value of setting ``name`` (without prefix): TRACEGAUGE_<name> if set and non-empty, else None."""
    env = os.environ if environ is None else environ
    return env.get(PRIMARY_PREFIX + name) or None


def legacy_in_use(environ: Mapping[str, str] | None = None) -> list[str]:
    """Removed ``TES_*`` variables set (non-empty) while their ``TRACEGAUGE_*`` twin is not."""
    env = os.environ if environ is None else environ
    return [
        LEGACY_PREFIX + name
        for name in SETTINGS
        if env.get(LEGACY_PREFIX + name) and not env.get(PRIMARY_PREFIX + name)
    ]


def legacy_refusal(environ: Mapping[str, str] | None = None) -> str | None:
    """The stderr error the command line prints (then exits 1) for removed variables, or None."""
    used = legacy_in_use(environ)
    if not used:
        return None
    renames = ", ".join(f"{u} -> {PRIMARY_PREFIX}{u[len(LEGACY_PREFIX) :]}" for u in used)
    return (
        f"[ERROR] {', '.join(used)} {'is' if len(used) == 1 else 'are'} no longer read (the TES_ names "
        f"were removed in 0.18). Rename: {renames}. tracegauge stops here rather than silently ignore "
        "it (for example, use a different database). See docs/MIGRATING_FROM_TES.md."
    )
