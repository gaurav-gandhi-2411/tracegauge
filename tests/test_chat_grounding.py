# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_chat_grounding.py — Chat grounding and out-of-scope guard tests.

Verifies that:
1. build_chat_context() returns structured context with real metrics (no made-up numbers)
2. The system prompt contains the "not measured" and "I don't predict" constraints
3. The context contains only metrics (no raw session content, no file paths, no code)
4. ask_api() with consent_given=False returns None immediately (no network call)
5. Simulated out-of-scope responses: the system prompt is correctly constraining
"""

import json
import re
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
from tracegauge.intelligence.chat import (
    CHAT_EGRESS_NOTICE,
    CHAT_SYSTEM_PROMPT,
    ChatApiConfig,
    ask_api,
    build_chat_context,
)

# ---------------------------------------------------------------------------
# System prompt content tests (honesty enforced at the prompt level)
# ---------------------------------------------------------------------------


class TestSystemPromptConstraints:
    def test_not_measured_constraint_present(self):
        assert (
            "not measured" in CHAT_SYSTEM_PROMPT.lower()
            or "don't have that measured" in CHAT_SYSTEM_PROMPT.lower()
        )

    def test_no_prediction_constraint_present(self):
        assert (
            "don't predict" in CHAT_SYSTEM_PROMPT.lower()
            or "i don't predict" in CHAT_SYSTEM_PROMPT.lower()
        )

    def test_no_quality_judgment_constraint_present(self):
        """Must instruct model not to rate session quality."""
        prompt_lower = CHAT_SYSTEM_PROMPT.lower()
        assert "quality" in prompt_lower or "good/bad" in prompt_lower

    def test_answer_only_from_context_constraint(self):
        """Must constrain model to context-only answers."""
        prompt_lower = CHAT_SYSTEM_PROMPT.lower()
        assert "only" in prompt_lower and ("context" in prompt_lower or "provided" in prompt_lower)

    def test_cite_source_constraint(self):
        """Model must cite source of numbers."""
        assert (
            "where it comes from" in CHAT_SYSTEM_PROMPT.lower()
            or "cite" in CHAT_SYSTEM_PROMPT.lower()
            or "from your" in CHAT_SYSTEM_PROMPT.lower()
        )

    def test_archetype_framing_constraint(self):
        """Must not let model dramatize archetypes — modest variation framing required."""
        assert (
            "modest" in CHAT_SYSTEM_PROMPT.lower()
            or "mainly differ" in CHAT_SYSTEM_PROMPT.lower()
            or "homogeneous" in CHAT_SYSTEM_PROMPT.lower()
        )

    def test_consent_notice_non_empty(self):
        assert len(CHAT_EGRESS_NOTICE) > 100
        assert (
            "metrics only" in CHAT_EGRESS_NOTICE.lower()
            or "metrics-only" in CHAT_EGRESS_NOTICE.lower()
        )


# ---------------------------------------------------------------------------
# Context structure tests (metrics-only, no raw content)
# ---------------------------------------------------------------------------


class TestChatContextStructure:
    @pytest.fixture(scope="class")
    @classmethod
    def context(cls):
        return build_chat_context("What kind of sessions do I run?")

    def test_context_has_required_keys(self, context):
        assert "question" in context
        assert "intelligence" in context
        assert "corpus_stats" in context

    def test_corpus_stats_are_numbers(self, context):
        cs = context["corpus_stats"]
        assert isinstance(cs["total_sessions_in_store"], int)
        assert isinstance(cs["content_sessions"], int)
        assert cs["content_sessions"] >= 0

    def test_corpus_stats_no_content(self, context):
        """Corpus stats must not contain session content, code, or file paths."""
        cs_json = json.dumps(context["corpus_stats"])
        # Should not contain file paths (Windows or Unix style)
        assert "\\.claude\\projects" not in cs_json
        assert "/.claude/projects" not in cs_json
        # Should not contain source code patterns
        assert "def " not in cs_json
        assert "import " not in cs_json

    def test_intelligence_in_context(self, context):
        intel = context["intelligence"]
        assert "valid" in intel

    def test_no_session_key_without_uuid_in_question(self, context):
        """Without a UUID in the question, no specific session data should be fetched."""
        assert context.get("session") is None

    def test_session_lookup_with_fake_uuid(self):
        """With a fake UUID, session lookup should safely return None (not crash)."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        ctx = build_chat_context(f"Tell me about session {fake_uuid}")
        assert ctx.get("session") is None  # fake UUID not in store

    def test_question_preserved_in_context(self, context):
        assert context["question"] == "What kind of sessions do I run?"

    def test_waste_metrics_are_aggregates_not_content(self, context):
        waste = context["corpus_stats"]["waste"]
        assert isinstance(waste["sessions_with_waste"], int)
        assert isinstance(waste["pct_of_content"], float)
        # No session-level content should be in aggregate waste stats
        assert "content_snippet" not in json.dumps(waste)
        assert "proof_turns" not in json.dumps(waste)


# ---------------------------------------------------------------------------
# No-code-egress tests (the context builder must never include raw content)
# ---------------------------------------------------------------------------


class TestNoCodeEgress:
    @pytest.mark.parametrize(
        "question",
        [
            "What kind of sessions do I run?",
            "What is my most common waste pattern?",
            "Which sessions are outliers?",
            "What is my typical session cost?",
        ],
    )
    def test_context_contains_no_code(self, question):
        ctx = build_chat_context(question)
        ctx_json = json.dumps(ctx)
        # Python code markers
        assert "def " not in ctx_json
        assert "import " not in ctx_json
        assert "class " not in ctx_json
        # Tool call content
        assert "content_snippet" not in ctx_json
        assert "tool_use" not in ctx_json
        # Session content fields
        assert "task_description" not in ctx_json

    def test_session_summary_no_file_paths(self):
        """_summarize_session must not include source_path."""
        from tracegauge.intelligence.chat import _summarize_session

        fake_row = {
            "session_id": "abcd1234-5678-9012-3456-789012345678",
            "task_type": "debug-fix",
            "scored_at": "2026-06-15T10:00:00",
            "real_tokens": 500000,
            "turn_count": 100,
            "band_verdict": "within_band",
            "scope_status": "in_scope",
            "session_cost_usd": 5.0,
            "judge_verdict": None,
            "judge_score": None,
            "waste_event_count": 0,
            "waste_events": [],
            "baseline_source": "self",
            "source_path": "/home/user/.claude/projects/secret-project/session.jsonl",
        }
        summary = _summarize_session(fake_row)
        summary_json = json.dumps(summary)
        assert "source_path" not in summary_json
        assert "secret-project" not in summary_json
        assert "/home/user" not in summary_json

    def test_session_summary_uses_partial_id(self):
        """Session ID in context should be truncated (not full UUID)."""
        from tracegauge.intelligence.chat import _summarize_session

        fake_row = {
            "session_id": "abcd1234-5678-9012-3456-789012345678",
            "task_type": "debug-fix",
            "scored_at": "2026-06-15T10:00:00",
            "real_tokens": 500000,
            "turn_count": 100,
            "band_verdict": "within_band",
            "scope_status": "in_scope",
            "session_cost_usd": 5.0,
            "judge_verdict": None,
            "judge_score": None,
            "waste_event_count": 0,
            "waste_events": [],
            "baseline_source": "self",
        }
        summary = _summarize_session(fake_row)
        # Full UUID must not be in the summary
        assert "abcd1234-5678-9012-3456-789012345678" not in json.dumps(summary)
        # Partial ID should be present
        assert "abcd1234" in json.dumps(summary)


# ---------------------------------------------------------------------------
# API consent gate tests (zero network calls without consent)
# ---------------------------------------------------------------------------


class TestApiConsentGate:
    def test_ask_api_consent_false_returns_none_no_network(self):
        """ask_api with consent_given=False must return None immediately, zero network."""
        config = ChatApiConfig(api_key="test-key-123")
        with patch("httpx.post") as mock_post:
            result = ask_api(
                "What are my session patterns?",
                config,
                consent_given=False,
            )
        assert result is None
        mock_post.assert_not_called()

    def test_ask_api_empty_key_returns_none(self):
        """ask_api with empty api_key returns None even with consent=True."""
        config = ChatApiConfig(api_key="")
        with patch("httpx.post") as mock_post:
            result = ask_api("question", config, consent_given=True)
        assert result is None
        mock_post.assert_not_called()

    def test_ask_api_with_consent_calls_anthropic(self):
        """ask_api with consent_given=True should attempt an API call."""
        config = ChatApiConfig(api_key="sk-ant-test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [
                {
                    "text": "From your measured data, your sessions primarily show high context re-send."
                }
            ]
        }
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = ask_api(
                "What kind of sessions do I run?",
                config,
                consent_given=True,
            )
        mock_post.assert_called_once()
        # Verify the call went to Anthropic
        call_url = mock_post.call_args[0][0]
        assert urlparse(call_url).hostname == "api.anthropic.com"

    def test_ask_api_context_contains_no_code(self):
        """The payload sent to Anthropic must not contain raw session content."""
        config = ChatApiConfig(api_key="sk-ant-test-key")
        captured_payload: list[dict] = []

        def capture_and_mock(*args, **kwargs):
            captured_payload.append(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"content": [{"text": "answer"}]}
            return mock_resp

        with patch("httpx.post", side_effect=capture_and_mock):
            ask_api("What are my session patterns?", config, consent_given=True)

        assert captured_payload, "No API call was made"
        payload_str = json.dumps(captured_payload[0])
        # Must not contain raw session content
        assert "content_snippet" not in payload_str
        assert "def " not in payload_str
        assert "import " not in payload_str
        assert ".jsonl" not in payload_str
        assert ".claude/projects" not in payload_str

    def test_ask_local_returns_none_when_ollama_down(self):
        """ask_local should return None gracefully when Ollama is not reachable."""
        from tracegauge.intelligence.chat import ask_local

        with patch("tracegauge.intelligence.chat._probe_ollama_models", return_value=[]):
            result = ask_local("What are my session patterns?")
        assert result is None


# ---------------------------------------------------------------------------
# Context format unambiguity tests — the Q2 regression guard
#
# Root cause of Q2 bug: format_intelligence_summary used waste=1 (a binary flag
# formatted as a number), which the model misread as "1.0% waste rate".
# These tests guard that the format remains unambiguous after any future change.
# ---------------------------------------------------------------------------


class TestContextFormatUnambiguous:
    """Guard that format_intelligence_summary uses unambiguous labels.

    The Q2 incident: has_waste=1 was displayed as waste=1, misread as "1.0% waste rate".
    Rule: any value that is a binary flag MUST appear as a word (YES/NO), never as 0/1.
    Any value that is a percentage of something MUST name the denominator.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def intel_summary(cls) -> str:
        """UU1: format_intelligence_summary() is a pure dict -> str function --
        this class is a Q2-regression guard on its FORMATTING contract, which
        needs no real corpus at all. It used to call get_or_compute_intelligence()
        against the live default DB (no db_path), which (a) made these tests
        depend on this machine's real session history -- always failing on a
        fresh checkout/CI runner with no history, and silently skipped there via
        ci.yml's --deselect rather than actually exercised -- and (b) is exactly
        the shape of write-boundary bug UU2 hardens against (a compute call with
        no explicit db_path). A synthetic, always-valid cache dict (same shape
        build_cache_from_results() produces) tests the same formatting contract
        deterministically, everywhere, with no DB access at all.
        """
        from tracegauge.intelligence.cache import format_intelligence_summary

        synthetic_cache = {
            "valid": True,
            "k": 2,
            "silhouette": 0.42,
            "silhouette_stability_mean": 0.42,
            "silhouette_stability_cv": 0.05,
            "stable": True,
            "status": "silhouette=0.420 (meaningful). stable (CV=0.050).",
            "domain_of_validity": "descriptive of this corpus only, not predictive",
            "n_sessions": 40,
            "archetypes": [
                {
                    "cluster_id": 0,
                    "name": "medium high context re-send sessions",
                    "size": 25,
                    "fraction": 0.625,
                    "centroid": {
                        "context_resend_pct": 0.95,
                        "context_growth_pct": 0.02,
                        "output_pct": 0.02,
                        "waste_pct": 0.01,
                        "has_waste": 0,
                    },
                    "task_type_counts": {"debug-fix": 15, "feature-build": 10},
                    "dominant_features": [],
                },
                {
                    "cluster_id": 1,
                    "name": "small waste-flagged sessions",
                    "size": 15,
                    "fraction": 0.375,
                    "centroid": {
                        "context_resend_pct": 0.90,
                        "context_growth_pct": 0.03,
                        "output_pct": 0.03,
                        "waste_pct": 0.04,
                        "has_waste": 1,
                    },
                    "task_type_counts": {"ml-eval": 15},
                    "dominant_features": [],
                },
            ],
            "anomaly_count": 2,
            "anomaly_pct": 5.0,
        }
        return format_intelligence_summary(synthetic_cache)

    def test_has_waste_is_yes_no_not_numeric(self, intel_summary: str) -> None:
        """has_waste must appear as YES or NO — never as 0, 1, -0, or any digit."""
        # Must contain the word-form label
        assert "has_waste: YES" in intel_summary or "has_waste: NO" in intel_summary
        # Must NOT contain the numeric forms that caused the Q2 misread
        bad = re.findall(r"\bwaste\s*=\s*-?\d", intel_summary)
        assert not bad, f"Numeric waste flag found (Q2 regression): {bad}"

    def test_percentages_name_denominator(self, intel_summary: str) -> None:
        """Token attribution percentages must name 'of billed tokens' so the model
        cannot confuse the denominator (billed tokens != total tokens != turns)."""
        assert "of billed tokens" in intel_summary

    def test_session_fractions_name_corpus(self, intel_summary: str) -> None:
        """Session-count fractions in archetypes must say '% of corpus'."""
        assert "of corpus" in intel_summary

    def test_no_bare_numeric_flags_in_archetype_lines(self, intel_summary: str) -> None:
        """Archetype lines must not contain field=<single digit> patterns (flag risk)."""
        archetype_lines = [l for l in intel_summary.split("\n") if l.strip().startswith("[")]
        for line in archetype_lines:
            # Pattern: a word boundary, then something=<single digit with no decimal>
            # Catches waste=1, waste=0, waste=-0 but not n=152 or silhouette=0.466
            bad = re.findall(r"\b\w+=\s*-?\d\b(?!\.\d)", line)
            # Exclude known-safe patterns: n=<number> is unambiguous (count, not flag)
            bad = [b for b in bad if not b.strip().startswith("n=")]
            assert not bad, f"Bare single-digit field value in archetype line: {bad!r} in {line!r}"

    def test_small_corpus_summary_no_archetype_claims(self) -> None:
        """format_intelligence_summary on an invalid cache must not mention archetypes."""
        from tracegauge.intelligence.cache import format_intelligence_summary

        small_cache = {
            "valid": False,
            "reason": "not_enough_sessions",
            "n_sessions": 10,
            "n_content_sessions_needed": 30,
            "status": (
                "Not enough content sessions for pattern analysis yet "
                "(10 < 30 needed). Patterns will be available as your session corpus grows."
            ),
            "domain_of_validity": "n/a — minimum corpus size not reached",
        }
        summary = format_intelligence_summary(small_cache)
        assert "archetype" not in summary.lower()
        assert "cluster" not in summary.lower()
        assert (
            "not enough" in summary.lower() or "minimum" in summary.lower() or "10 < 30" in summary
        )
