"""Unit tests for messaging.context_budget."""

from messaging.context_budget import (
    HANDOFF_MEMO_PROMPT,
    HANDOFF_SEED_HEADER,
    ContextBudget,
    build_handoff_prompt,
    extract_context_tokens,
)


class TestExtractContextTokens:
    def test_result_event_top_level_usage(self):
        event = {
            "type": "result",
            "usage": {
                "input_tokens": 100,
                "cache_read_input_tokens": 50_000,
                "cache_creation_input_tokens": 2_000,
                "output_tokens": 300,
            },
        }
        assert extract_context_tokens(event) == 52_100

    def test_assistant_event_message_usage(self):
        event = {
            "type": "assistant",
            "message": {"usage": {"input_tokens": 1_234}},
        }
        assert extract_context_tokens(event) == 1_234

    def test_result_nested_usage(self):
        event = {"type": "result", "result": {"usage": {"input_tokens": 7}}}
        assert extract_context_tokens(event) == 7

    def test_output_tokens_are_not_counted(self):
        event = {"type": "result", "usage": {"output_tokens": 999}}
        assert extract_context_tokens(event) is None

    def test_no_usage_returns_none(self):
        assert extract_context_tokens({"type": "assistant", "message": {}}) is None
        assert extract_context_tokens({"type": "exit", "code": 0}) is None

    def test_non_dict_event_returns_none(self):
        assert extract_context_tokens(None) is None
        assert extract_context_tokens("usage") is None
        assert extract_context_tokens(42) is None

    def test_malformed_usage_values_ignored(self):
        event = {
            "type": "result",
            "usage": {
                "input_tokens": "many",
                "cache_read_input_tokens": True,
                "cache_creation_input_tokens": -5,
            },
        }
        assert extract_context_tokens(event) is None

    def test_partial_usage_counts_valid_keys(self):
        event = {
            "type": "result",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": "bad"},
        }
        assert extract_context_tokens(event) == 10

    def test_float_values_truncate_to_int(self):
        event = {"type": "result", "usage": {"input_tokens": 10.9}}
        assert extract_context_tokens(event) == 10


class TestContextBudget:
    def test_default_is_disabled(self):
        assert ContextBudget().should_handoff(1_000_000) is False

    def test_enabled_below_threshold(self):
        budget = ContextBudget(enabled=True, threshold_tokens=100)
        assert budget.should_handoff(99) is False

    def test_enabled_at_threshold(self):
        budget = ContextBudget(enabled=True, threshold_tokens=100)
        assert budget.should_handoff(100) is True

    def test_enabled_none_tokens(self):
        budget = ContextBudget(enabled=True, threshold_tokens=100)
        assert budget.should_handoff(None) is False

    def test_clip_memo(self):
        budget = ContextBudget(memo_max_chars=5)
        assert budget.clip_memo("abcdefgh") == "abcde"
        assert budget.clip_memo("ab") == "ab"

    def test_describe(self):
        assert ContextBudget().describe() == "off"
        on = ContextBudget(enabled=True, threshold_tokens=80_000)
        assert on.describe() == "on (threshold 80,000 tokens)"

    def test_from_settings(self, monkeypatch):
        from config.settings import Settings

        monkeypatch.setitem(Settings.model_config, "env_file", ())
        monkeypatch.setenv("AUTO_HANDOFF_ENABLED", "false")
        monkeypatch.setenv("AUTO_HANDOFF_THRESHOLD_TOKENS", "123")
        monkeypatch.setenv("AUTO_HANDOFF_MEMO_MAX_CHARS", "77")

        budget = ContextBudget.from_settings(Settings())
        assert budget.enabled is False
        assert budget.threshold_tokens == 123
        assert budget.memo_max_chars == 77

    def test_settings_reject_non_positive_limits(self, monkeypatch):
        import pytest
        from pydantic import ValidationError

        from config.settings import Settings

        monkeypatch.setitem(Settings.model_config, "env_file", ())
        monkeypatch.setenv("AUTO_HANDOFF_THRESHOLD_TOKENS", "0")
        with pytest.raises(ValidationError):
            Settings()


class TestBuildHandoffPrompt:
    def test_contains_memo_and_request(self):
        prompt = build_handoff_prompt("memo body", "new task")
        assert prompt.startswith(HANDOFF_SEED_HEADER)
        assert "memo body" in prompt
        assert prompt.endswith("New request:\nnew task")

    def test_memo_prompt_asks_for_memo_only(self):
        assert "memo" in HANDOFF_MEMO_PROMPT
