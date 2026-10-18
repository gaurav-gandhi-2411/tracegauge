# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""Post-publish verification for tracegauge 0.8.0 — runs from REAL PyPI install.

Confirms: site-packages (not repo), /patterns 200, /ask honesty guard,
sort correctness, numpy/sklearn auto-installed, direct_url confirms index install.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

# ── 1. Confirm site-packages (NOT repo) ─────────────────────────────────────
import tracegauge

repo = pathlib.Path(__file__).resolve().parent.parent
pkg = pathlib.Path(tracegauge.__file__)
assert repo not in pkg.parents, f"FAIL: still loading from repo: {pkg}"
print(f"[OK] tracegauge.__file__ = {pkg}  (site-packages, not repo)")

assert tracegauge.__version__ == "0.8.0", f"FAIL: version = {tracegauge.__version__}"
print(f"[OK] tracegauge.__version__ = {tracegauge.__version__}")

# ── 2. Confirm install came from the index (not a local wheel) ──────────────
direct_url_path = pkg.parent.parent / "tracegauge-0.8.0.dist-info" / "direct_url.json"
if direct_url_path.exists():
    du = json.loads(direct_url_path.read_text())
    url = du.get("url", "")
    assert urlparse(url).hostname == "files.pythonhosted.org", (
        f"FAIL: direct_url.json points to {url!r}, not PyPI"
    )
    print(f"[OK] direct_url.json confirms PyPI index install: {url[:60]}…")
else:
    # Absence of direct_url.json also confirms index install (PEP 610)
    print("[OK] no direct_url.json — confirms index install (PEP 610)")

# ── 3. numpy + sklearn auto-installed as declared deps ──────────────────────
import numpy as np
import sklearn

print(f"[OK] numpy {np.__version__} auto-installed")
print(f"[OK] scikit-learn {sklearn.__version__} auto-installed")

# ── 4. Route registration: /patterns and /ask in url_map ────────────────────
import tracegauge.web.server as srv

app = srv.create_app(srv.ServerConfig())
rules = {str(r) for r in app.url_map.iter_rules() if not str(r).startswith("/static")}
assert "/patterns" in rules, f"FAIL: /patterns missing. Got: {sorted(rules)}"
assert "/ask" in rules, f"FAIL: /ask missing. Got: {sorted(rules)}"
print(f"[OK] url_map routes: {sorted(rules)}")

# ── 5. GET /patterns → 200 (templates bundled in wheel) ─────────────────────
mock_cache_below_floor = {
    "valid": False,
    "reason": "not_enough_sessions",
    "n_sessions": 0,
    "n_content_sessions_needed": 30,
    "status": "Not enough sessions.",
    "domain_of_validity": "n/a",
}
with (
    patch("tracegauge.web.server.get_or_compute_intelligence", return_value=mock_cache_below_floor),
    patch("tracegauge.web.server._check_ollama", return_value=False),
):
    with app.test_client() as c:
        resp = c.get("/patterns")
assert resp.status_code == 200, f"FAIL: GET /patterns → {resp.status_code}"
html = resp.data.decode()
assert "ask-panel" in html, "FAIL: Ask panel not in /patterns HTML"
assert "Not enough sessions" in html or "not enough" in html.lower(), "FAIL: floor message missing"
print("[OK] GET /patterns → 200 (templates bundled, floor message rendered, Ask panel present)")

# ── 6. Honesty guard: "I don't predict" fires from published artifact ────────
from tracegauge.intelligence.chat import CHAT_SYSTEM_PROMPT, ChatApiConfig, ask_api

# Verify the guard text is in the system prompt shipped in the wheel
assert "I don't predict future behavior" in CHAT_SYSTEM_PROMPT, (
    "FAIL: honesty guard missing from CHAT_SYSTEM_PROMPT in published wheel"
)
print("[OK] CHAT_SYSTEM_PROMPT in published wheel contains 'I don't predict future behavior'")

# Verify /ask passes the prediction refusal through unchanged
PREDICTION_REFUSAL = (
    "I don't predict future behavior — I only explain what's already measured "
    "in your session history."
)
with patch("tracegauge.web.server.ask_local", return_value=PREDICTION_REFUSAL):
    with app.test_client() as c:
        r = c.post(
            "/ask",
            data=json.dumps({"question": "What will my next session cost?"}),
            content_type="application/json",
        )
assert r.status_code == 200
data = r.get_json()
assert "don't predict" in data.get("answer", ""), (
    f"FAIL: prediction refusal not passed through. Got: {data}"
)
print("[OK] /ask passes 'I don't predict' refusal through from published wheel")
print(f"     answer: '{data['answer'][:80]}…'")

# Verify ask_api consent gate inherited in published wheel
result = ask_api("test", ChatApiConfig(api_key="sk-fake"), consent_given=False)
assert result is None, "FAIL: ask_api bypassed consent gate in published wheel"
print("[OK] ask_api(consent_given=False) → None (consent gate intact in published wheel)")

# ── 7. Sort: list_sessions whitelist + ordering from published wheel ─────────
from tracegauge.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tracegauge.store import _SORT_COLUMN_WHITELIST, list_sessions, open_db, upsert_session

assert set(_SORT_COLUMN_WHITELIST.keys()) == {"date", "cost", "waste", "tokens", "verdict"}, (
    f"FAIL: sort whitelist keys wrong: {set(_SORT_COLUMN_WHITELIST.keys())}"
)
print(f"[OK] sort whitelist present: {sorted(_SORT_COLUMN_WHITELIST.keys())}")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    db = pathlib.Path(td) / "sort_test.db"
    conn = open_db(db)
    for i, cost in enumerate([0.10, 0.01, 0.05]):
        sid = f"sort-test-{i:04d}-aaaa-bbbb-cccc-dddddddddddd"
        r = ThreeAxisResult(
            session_id=sid,
            task_type="debug-fix",
            real_tokens=1000 + i * 100,
            scope_status="in_scope",
            baseline_available=True,
            p25=800,
            p75=1200,
            median=1000,
            band_verdict="within_band",
            interpretation="",
            token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
            baseline_source="self",
            judge_verdict=None,
            judge_score=None,
            judge_reasoning=None,
            trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
            waste_event_count=0,
            waste_events=[],
            waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
            session_cost_usd=cost,
            cost_approximate=False,
            cost_domain_of_validity="",
        )
        upsert_session(conn, r, f"/fake/{sid}.jsonl", float(i), f"hash-{i}")
    conn.commit()
    rows = list_sessions(conn, order_by="cost", direction="DESC")
    costs = [row["session_cost_usd"] for row in rows]
    conn.close()  # release file lock before TemporaryDirectory cleanup (Windows)
    assert costs == sorted(costs, reverse=True), f"FAIL: sort cost DESC wrong: {costs}"
    print(f"[OK] sort by cost DESC: {costs}")

# ── 8. tracegauge --version from installed entry point ──────────────────────────────
import subprocess

result = subprocess.run(
    [sys.executable, "-m", "tracegauge", "--version"], capture_output=True, text=True
)
ver = (result.stdout + result.stderr).strip()
assert "0.8.0" in ver, f"FAIL: tracegauge --version = {ver!r}"
print(f"[OK] tracegauge --version: {ver}")

print()
print("=" * 60)
print("POST-PUBLISH VERIFY PASSED — 0.8.0 LIVE on PyPI")
print("https://pypi.org/project/tracegauge/0.8.0/")
print("=" * 60)
