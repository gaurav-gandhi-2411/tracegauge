# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""A small seeded synthetic history for ``tracegauge quickstart``.

The bundled sample session is research-recon. A token verdict needs 10 of the user's own sessions of
the type, which a first-time user does not have, so the demo scores the sample against a fixed list of
12 invented research-recon sessions and says so on every line that mentions them. The history is built
in memory as a state object: it is never written to the user's store, and nothing reads the store (the
quickstart's "no files of yours read" promise).
"""

from tracegauge.self_baseline import (
    MIN_OWN_SESSIONS,
    TypeBaseline,
    building_state,
    quartiles,
)

SAMPLE_TASK_TYPE = "research-recon"
# random.Random(42): sorted(round(r.lognormvariate(12.7, 0.55), -3) for _ in range(12)). Fixed here so
# the demo is identical on every machine and Python.
SYNTHETIC_TOKENS: tuple[int, ...] = (
    172_000, 192_000, 249_000, 274_000, 304_000, 375_000,
    447_000, 628_000, 654_000, 750_000, 852_000, 958_000,
)  # fmt: skip
assert len(SYNTHETIC_TOKENS) >= MIN_OWN_SESSIONS


def synthetic_state(b2_baselines: dict):
    """A self-baseline state in which only the sample's task type has the synthetic history."""
    state = building_state(b2_baselines)
    old = state.by_type[SAMPLE_TASK_TYPE]
    vals = sorted(SYNTHETIC_TOKENS)
    p25, median, p75 = quartiles(vals)
    state.by_type[SAMPLE_TASK_TYPE] = TypeBaseline(
        task_type=SAMPLE_TASK_TYPE,
        source="self",
        p25=p25,
        median=median,
        p75=p75,
        own_n=len(vals),
        sessions_needed=0,
        scope_floor=old.scope_floor,
        domain_of_validity=(
            f"SYNTHETIC HISTORY: {len(vals)} invented {SAMPLE_TASK_TYPE} sessions shipped for this "
            "demo stand in for your own history; they are not your sessions and are not stored. "
            f"With your own sessions the reading is relative to YOUR OWN {SAMPLE_TASK_TYPE} sessions "
            "(10 needed). Not an absolute efficiency verdict."
        ),
        values=[(f"synthetic-{i:02d}", v) for i, v in enumerate(vals)],
        synthetic=True,
    )
    return state
