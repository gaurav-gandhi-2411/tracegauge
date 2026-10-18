# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Per-session numeric extract of the SWE-chat Claude Code transcripts (local only).

Reads the gitignored raw dataset (``data/swechat_raw/``: ``sessions.parquet`` and ``transcripts/``)
and writes one JSON line per transcript to an output path you choose, for
scripts/build_multidev_bands.py. A row holds numbers, a task type and a salted hash of the
developer id; it holds no text and no path. Keep the output out of the repository (it is derived
from the dataset's sessions, which are covered by the dataset's own terms), for example on another
drive. Needs pandas and pyarrow (the dev extras).

    python scripts/extract_swechat_sessions.py --raw data/swechat_raw --out /d/scratch/v1.jsonl
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SALT = "tracegauge-multidev-v1"


def dev_hash(user_id: object) -> str | None:
    """Stable pseudonym for the grouping only; None when the dataset has no user id."""
    if not isinstance(user_id, str):
        return None
    return hashlib.sha256((SALT + user_id).encode()).hexdigest()[:12]


def session_row(path: Path) -> dict:
    from tracegauge.adapt import adapt_session
    from tracegauge.baselines import compute_real_tokens
    from tracegauge.classify import classify_session

    row: dict = {"stem": path.stem}
    try:
        record = adapt_session(path)
        digest = record.get("digest", {})
        row.update(
            task_type=classify_session(record),
            turn_count=int(record.get("turn_count", 0)),
            real_tokens=int(compute_real_tokens(record)),
            usage_deduped=int(digest.get("usage_records_deduped", 0)),
            tool_calls=sum(
                len(t.get("tool_names") or [])
                for t in digest.get("turns", [])
                if t.get("role") == "ai"
            ),
        )
    except Exception as exc:  # the row is kept and marked, never dropped silently
        row.update(
            task_type="debug-fix", turn_count=0, real_tokens=0, usage_deduped=0, tool_calls=0
        )
        row["error"] = type(exc).__name__
    first_type = None
    with path.open(encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
    try:
        first_type = json.loads(first).get("type")
    except (ValueError, AttributeError):
        first_type = "unparsable"
    row["first_type"] = first_type
    return row


def main(argv: list[str] | None = None) -> int:
    import pandas as pd

    ap = argparse.ArgumentParser(
        description="Per-session numeric extract of SWE-chat (local only)."
    )
    ap.add_argument(
        "--raw", type=Path, required=True, help="data/swechat_raw (sessions.parquet, transcripts/)"
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    # v1 keeps the Claude Code transcripts in transcripts/, v2 splits them over transcripts/ and
    # transcripts1/; only Claude Code files are ever placed in those two directories.
    files = sorted(
        f for d in ("transcripts", "transcripts1") for f in (args.raw / d).glob("*.jsonl")
    )
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(session_row, files, chunksize=8))
    meta = pd.read_parquet(args.raw / "sessions.parquet")
    meta["stem"] = (
        meta["transcript_path"]
        .fillna("")
        .map(lambda p: Path(p).stem if str(p).endswith(".jsonl") else "")
    )
    by_stem = meta.drop_duplicates("stem").set_index("stem")
    with args.out.open("w", encoding="utf-8") as fh:
        for r in rows:
            m = by_stem.loc[r["stem"]] if r["stem"] in by_stem.index else None
            r.pop("stem")  # no ids in the output
            if m is not None:
                r.update(
                    dev=dev_hash(m["user_id"]),
                    created=str(m["created_at"])[:10],
                    ds_api_calls=int(m["api_call_count"]),
                )
            fh.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
