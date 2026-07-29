"""Automatic conversation-cost control: context tracking and session handoff."""

from dataclasses import dataclass
from typing import Any

from config.settings import Settings

HANDOFF_MEMO_PROMPT = (
    "Write a concise handoff memo so a fresh session can continue this work "
    "without the conversation history. Include: current state, decisions made, "
    "open questions, relevant file paths, and the immediate next step. "
    "Reply with the memo text only."
)

HANDOFF_SEED_HEADER = "Continue from this handoff memo written by the previous session:"

_CONTEXT_USAGE_KEYS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _usage_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the usage dict from a CLI event (top-level or under message/result)."""
    for container in (event, event.get("message"), event.get("result")):
        if isinstance(container, dict):
            usage = container.get("usage")
            if isinstance(usage, dict):
                return usage
    return None


def extract_context_tokens(event: Any) -> int | None:
    """Return the request context size (input + cache tokens) from a CLI event, or None."""
    if not isinstance(event, dict):
        return None
    usage = _usage_from_event(event)
    if usage is None:
        return None

    total = 0
    found = False
    for key in _CONTEXT_USAGE_KEYS:
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value < 0:
            continue
        total += int(value)
        found = True
    return total if found else None


def build_handoff_prompt(memo: str, user_text: str) -> str:
    """Build the first prompt of a fresh session seeded with a handoff memo."""
    return f"{HANDOFF_SEED_HEADER}\n\n{memo}\n\n---\n\nNew request:\n{user_text}"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Threshold policy for automatic session handoff (disabled by default)."""

    enabled: bool = False
    threshold_tokens: int = 80_000
    memo_max_chars: int = 4_000

    @classmethod
    def from_settings(cls, settings: Settings) -> ContextBudget:
        """Build a budget from application settings."""
        return cls(
            enabled=settings.auto_handoff_enabled,
            threshold_tokens=settings.auto_handoff_threshold_tokens,
            memo_max_chars=settings.auto_handoff_memo_max_chars,
        )

    def should_handoff(self, context_tokens: int | None) -> bool:
        """Return whether a session at this context size should be handed off."""
        return (
            self.enabled
            and context_tokens is not None
            and context_tokens >= self.threshold_tokens
        )

    def clip_memo(self, memo: str) -> str:
        """Trim a memo to the configured maximum length."""
        return memo[: self.memo_max_chars]

    def describe(self) -> str:
        """Human-readable one-line summary for status commands."""
        if not self.enabled:
            return "off"
        return f"on (threshold {self.threshold_tokens:,} tokens)"
