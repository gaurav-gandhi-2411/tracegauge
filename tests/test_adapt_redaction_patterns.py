# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_adapt_redaction_patterns.py — Cover every label in tracegauge.adapt._SECRET_PATTERNS.

tests/test_redaction_default_on.py exercises `scripts/adapters/claudecode_adapter.py`'s copy of
`_redact_secrets`, not `tracegauge.adapt`, the module that actually ships in the wheel; and most of
the 18 labels in `_SECRET_PATTERNS` had no test at all. This file closes both gaps: a parametrised
case per label against the shipped module, plus a structural check that the two pattern lists stay
equal so a future divergence fails loudly instead of silently.

Every fake value below is built by string concatenation, as the existing adapter tests do, so no
single literal in this file reads as a real secret to the gitleaks pre-commit hook / CI job.
"""

import sys
from pathlib import Path

import pytest
from tracegauge.adapt import _SECRET_PATTERNS as _SHIPPED_PATTERNS
from tracegauge.adapt import _redact_secrets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "adapters"))

from claudecode_adapter import _SECRET_PATTERNS as _SCRIPT_PATTERNS  # noqa: E402

# One obviously-fake value per label in tracegauge.adapt._SECRET_PATTERNS, built so the
# matching value never appears as a single contiguous literal in this source file.
_FAKE_SECRETS_BY_LABEL: dict[str, str] = {
    "groq_key": "gsk_" + "A" * 30,
    "anthropic_key": "sk-ant-" + "B" * 30,
    "openrouter_key": "sk-or-" + "C" * 30,
    "openai_style_key": "sk-" + "D" * 45,
    "github_pat": "ghp_" + "E" * 25,
    "github_fine_grained_pat": "github_pat_" + "F" * 25,
    "github_actions_token": "ghs_" + "G" * 25,
    "huggingface_token": "hf_" + "H" * 25,
    "google_api_key": "AIzaSy" + "I" * 35,
    "aws_access_key_id": "AKIA" + "J" * 16,
    "wandb_api_key": "wandb_v1_" + "K" * 25,
    "slack_bot_token": "xoxb-" + "L" * 25,
    "slack_user_token": "xoxp-" + "M" * 25,
    "slack_webhook": "hooks.slack.com/services/" + "N" * 25,
    "generic_key_assignment": "API_KEY" + "=" + "O" * 20,
    "database_url": "postgresql://user:" + "P" * 10 + "@db.example.invalid/mydb",
    "jwt_session_token": '"token"' + ": " + '"eyJ' + "Q" * 25 + '"',
    "private_key_header": "-----BEGIN RSA PRIVATE KEY" + "-----",
}


def test_fake_secrets_cover_every_label() -> None:
    """Guard against this file's own table silently drifting from the real pattern list."""
    expected_labels = {label for _, label in _SHIPPED_PATTERNS}
    assert set(_FAKE_SECRETS_BY_LABEL) == expected_labels


@pytest.mark.parametrize("label", sorted(_FAKE_SECRETS_BY_LABEL))
def test_each_pattern_label_is_redacted(label: str) -> None:
    secret = _FAKE_SECRETS_BY_LABEL[label]
    text = f"here is a value: {secret} -- end"
    result = _redact_secrets(text)
    assert secret not in result
    assert "<SECRET_REDACTED>" in result


def test_clean_sentence_is_returned_unchanged() -> None:
    text = "this is a normal message with no secrets in it at all"
    assert _redact_secrets(text) == text


def test_shipped_and_script_pattern_lists_are_equal() -> None:
    """tests/test_redaction_default_on.py exercises the scripts/ copy, not the shipped one.

    Both copies are kept in sync by hand today; this test fails loudly the moment they diverge,
    instead of the two adapters silently disagreeing on what counts as a secret.
    """
    assert _SCRIPT_PATTERNS == _SHIPPED_PATTERNS
