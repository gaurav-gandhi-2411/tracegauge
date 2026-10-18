# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_chat_no_code_egress.py — Verify metrics-only chat egress.

The chat path must NEVER send raw session content, code, file paths, or tool
inputs/outputs to the LLM. This is a safety property, not a functional one:
these tests check what the code WOULD send, not whether the LLM agrees.

Tests focus on:
1. The user message builder (_build_user_message) produces only metrics
2. No source_path or raw content fields appear anywhere in the payload
3. The CHAT_EGRESS_NOTICE accurately describes what is sent
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from tracegauge.intelligence.chat import (
    CHAT_EGRESS_NOTICE,
    CHAT_SYSTEM_PROMPT,
    ChatApiConfig,
    _build_user_message,
    ask_api,
    build_chat_context,
)

# ---------------------------------------------------------------------------
# Message builder tests
# ---------------------------------------------------------------------------


class TestUserMessageBuilder:
    @pytest.fixture(scope="class")
    @classmethod
    def user_msg(cls):
        ctx = build_chat_context("What kind of sessions do I run?")
        return _build_user_message(ctx)

    def test_message_is_non_empty(self, user_msg):
        assert len(user_msg) > 100

    def test_message_contains_corpus_stats(self, user_msg):
        """Message must reference measured corpus statistics."""
        assert "sessions" in user_msg.lower()
        assert "content session" in user_msg.lower() or "total session" in user_msg.lower()

    def test_message_contains_archetype_or_pattern_info(self, user_msg):
        """Message should contain cluster/archetype information."""
        msg_lower = user_msg.lower()
        assert "archetype" in msg_lower or "cluster" in msg_lower or "pattern" in msg_lower

    def test_message_contains_question(self, user_msg):
        assert "What kind of sessions do I run?" in user_msg

    def test_message_no_source_path(self, user_msg):
        assert "source_path" not in user_msg
        assert ".claude/projects" not in user_msg
        assert "\\.claude\\projects" not in user_msg

    def test_message_no_raw_content_fields(self, user_msg):
        """Session content fields must not appear in the message."""
        forbidden_fields = [
            "content_snippet",
            "tool_use",
            "tool_result",
            "reasoning",
            "task_description",
        ]
        for field in forbidden_fields:
            assert field not in user_msg, f"Forbidden field '{field}' found in user message"

    def test_message_no_python_code(self, user_msg):
        """Python code patterns must not appear in the message."""
        # These patterns indicate raw session content leaked in
        code_patterns = [
            r"\bdef \w+\(",
            r"\bimport \w+",
            r"\bclass \w+:",
            r"from \w+ import",
        ]
        for pattern in code_patterns:
            assert not re.search(pattern, user_msg), (
                f"Code pattern '{pattern}' found in user message"
            )

    def test_message_no_file_paths(self, user_msg):
        """Windows and Unix file paths must not appear."""
        assert not re.search(r"[A-Z]:\\Users\\", user_msg), "Windows path found in user message"
        # Unix home paths (excluding /api/ which is the Anthropic endpoint)
        unix_home = re.findall(r"/home/\w+|/Users/\w+", user_msg)
        assert not unix_home, f"Unix home path found: {unix_home}"

    def test_session_context_only_includes_metrics(self):
        """When a session is in context, it should contain only metrics, not content."""
        from tracegauge.intelligence.chat import _build_user_message, _summarize_session

        fake_session_row = {
            "session_id": "deadbeef-0000-0000-0000-000000000000",
            "task_type": "debug-fix",
            "scored_at": "2026-06-15T10:00:00Z",
            "real_tokens": 750000,
            "turn_count": 200,
            "band_verdict": "above_p75",
            "scope_status": "in_scope",
            "session_cost_usd": 15.50,
            "judge_verdict": "BETTER",
            "judge_score": 0.75,
            "waste_event_count": 0,
            "waste_events": [],
            "baseline_source": "self",
            "source_path": "/secret/path/that/must/not/appear",
        }
        summary = _summarize_session(fake_session_row)
        ctx = {
            "question": "Tell me about this session",
            "intelligence": {"valid": False, "status": "n/a", "n_sessions": 0},
            "corpus_stats": {
                "total_sessions_in_store": 0,
                "content_sessions": 0,
                "task_type_counts": {},
                "cost_usd": {"n": 0, "median": None, "p75": None, "p95": None, "total": None},
                "real_tokens": {"median": None, "p75": None},
                "waste": {"sessions_with_waste": 0, "pct_of_content": 0.0, "total_waste_events": 0},
            },
            "session": summary,
        }
        msg = _build_user_message(ctx)
        assert "/secret/path/that/must/not/appear" not in msg
        # Full UUID must not appear
        assert "deadbeef-0000-0000-0000-000000000000" not in msg


# ---------------------------------------------------------------------------
# API payload inspection tests
# ---------------------------------------------------------------------------


class TestApiPayloadContent:
    @pytest.fixture(scope="class")
    @classmethod
    def captured_payload(cls):
        """Capture the actual JSON payload sent to Anthropic."""
        config = ChatApiConfig(api_key="sk-ant-test-key")
        captured: list[dict] = []

        def capture(*args, **kwargs):
            captured.append(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"content": [{"text": "answer from context"}]}
            return mock_resp

        with patch("httpx.post", side_effect=capture):
            ask_api("What patterns exist in my sessions?", config, consent_given=True)

        return captured[0] if captured else {}

    def test_payload_has_system_and_messages(self, captured_payload):
        assert "system" in captured_payload
        assert "messages" in captured_payload

    def test_payload_system_is_the_constrained_prompt(self, captured_payload):
        assert captured_payload.get("system") == CHAT_SYSTEM_PROMPT

    def test_payload_no_file_paths(self, captured_payload):
        payload_str = json.dumps(captured_payload)
        assert ".claude/projects" not in payload_str
        assert "\\.claude\\projects" not in payload_str
        assert "source_path" not in payload_str

    def test_payload_no_raw_session_content(self, captured_payload):
        payload_str = json.dumps(captured_payload)
        forbidden = ["content_snippet", "tool_use", "task_description", "proof_turns"]
        for field in forbidden:
            assert field not in payload_str, f"'{field}' found in API payload"

    def test_payload_no_python_code(self, captured_payload):
        payload_str = json.dumps(captured_payload)
        assert not re.search(r"\bdef \w+\(", payload_str), "Python function def in API payload"
        assert not re.search(r"\bimport \w+", payload_str), "Python import in API payload"

    def test_payload_contains_metrics(self, captured_payload):
        """Payload SHOULD contain measured metrics (numbers, percentages, counts)."""
        payload_str = json.dumps(captured_payload)
        assert "sessions" in payload_str.lower()


# ---------------------------------------------------------------------------
# Egress notice accuracy tests
# ---------------------------------------------------------------------------


class TestEgressNoticeAccuracy:
    def test_notice_claims_metrics_only(self):
        notice_lower = CHAT_EGRESS_NOTICE.lower()
        assert "metrics" in notice_lower or "statistics" in notice_lower

    def test_notice_claims_no_session_content(self):
        notice_lower = CHAT_EGRESS_NOTICE.lower()
        assert "no session content" in notice_lower or "not" in notice_lower

    def test_notice_names_anthropic(self):
        assert "anthropic" in CHAT_EGRESS_NOTICE.lower()

    def test_notice_no_tracegauge_server(self):
        """Must state that no tracegauge server is involved."""
        assert "no tracegauge server" in CHAT_EGRESS_NOTICE.lower()
