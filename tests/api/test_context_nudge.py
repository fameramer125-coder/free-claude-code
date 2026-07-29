"""Tests for the proxy-level context-size nudge (direct CLI/IDE clients)."""

from unittest.mock import MagicMock

import pytest

from api.context_nudge import (
    build_nudge_text,
    should_nudge,
    wrap_stream_with_context_nudge,
)
from api.models.anthropic import Message, MessagesRequest
from api.services import ClaudeProxyService
from config.settings import Settings
from core.anthropic.stream_contracts import (
    assert_anthropic_stream_contract,
    parse_sse_text,
    text_content,
)


def _settings(**overrides) -> Settings:
    settings = Settings()
    settings.context_nudge_enabled = overrides.get("context_nudge_enabled", False)
    settings.context_nudge_threshold_tokens = overrides.get(
        "context_nudge_threshold_tokens", 100_000
    )
    return settings


class TestShouldNudge:
    def test_disabled_by_default(self):
        assert should_nudge(_settings(), 10_000_000) is False

    def test_enabled_below_threshold(self):
        settings = _settings(
            context_nudge_enabled=True, context_nudge_threshold_tokens=1_000
        )
        assert should_nudge(settings, 999) is False

    def test_enabled_at_threshold(self):
        settings = _settings(
            context_nudge_enabled=True, context_nudge_threshold_tokens=1_000
        )
        assert should_nudge(settings, 1_000) is True


class TestBuildNudgeText:
    def test_includes_formatted_token_count(self):
        text = build_nudge_text(123_456)
        assert "123,456" in text
        assert "/compact" in text


async def _agen(items):
    for item in items:
        yield item


class TestWrapStreamWithContextNudge:
    @pytest.mark.asyncio
    async def test_injects_block_before_message_delta_whole_event_chunks(self):
        chunks = [
            'event: message_start\ndata: {"type": "message_start"}\n\n',
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n',
            'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}\n\n',
            'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n',
            'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}\n\n',
            'event: message_stop\ndata: {"type": "message_stop"}\n\n',
        ]
        out = [
            piece
            async for piece in wrap_stream_with_context_nudge(_agen(chunks), "nudge!")
        ]
        text = "".join(out)

        events = parse_sse_text(text)
        assert_anthropic_stream_contract(events)
        assert "nudge!" in text_content(events)

        names = [e.event for e in events]
        # The injected block (start+stop) must appear before message_delta.
        assert names.index("content_block_start") < names.index("message_delta")
        # Injected block uses the next free index (1), not reusing index 0.
        injected_start = [
            e
            for e in events
            if e.event == "content_block_start"
            and e.data.get("content_block", {}).get("text") == "nudge!"
        ]
        assert len(injected_start) == 1
        assert injected_start[0].data["index"] == 1

    @pytest.mark.asyncio
    async def test_injects_block_when_chunked_per_line(self):
        # Simulate providers/anthropic_messages.py's "line" stream_chunk_mode,
        # which yields one physical line per chunk instead of whole events.
        whole = (
            'event: message_start\ndata: {"type": "message_start"}\n\n'
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "hi"}}\n\n'
            'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'
            'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}\n\n'
            'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        )
        per_char_chunks = list(whole)  # worst-case: one character at a time

        out = [
            piece
            async for piece in wrap_stream_with_context_nudge(
                _agen(per_char_chunks), "nudge!"
            )
        ]
        text = "".join(out)

        events = parse_sse_text(text)
        assert_anthropic_stream_contract(events)
        assert "nudge!" in text_content(events)

    @pytest.mark.asyncio
    async def test_no_message_delta_means_no_injection(self):
        chunks = [
            'event: message_start\ndata: {"type": "message_start"}\n\n',
            'event: error\ndata: {"type": "error", "error": {"type": "api_error", "message": "boom"}}\n\n',
        ]
        out = [
            piece
            async for piece in wrap_stream_with_context_nudge(_agen(chunks), "nudge!")
        ]
        text = "".join(out)
        assert "nudge!" not in text

    @pytest.mark.asyncio
    async def test_multiple_content_blocks_get_next_free_index(self):
        chunks = [
            'event: message_start\ndata: {"type": "message_start"}\n\n',
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}\n\n',
            'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n',
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "hi"}}\n\n',
            'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 1}\n\n',
            'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}\n\n',
            'event: message_stop\ndata: {"type": "message_stop"}\n\n',
        ]
        out = [
            piece
            async for piece in wrap_stream_with_context_nudge(_agen(chunks), "nudge!")
        ]
        events = parse_sse_text("".join(out))
        assert_anthropic_stream_contract(events)

        injected = [
            e
            for e in events
            if e.event == "content_block_start"
            and e.data.get("content_block", {}).get("text") == "nudge!"
        ]
        assert injected[0].data["index"] == 2


async def _collect_body(response) -> str:
    parts = [
        chunk if isinstance(chunk, str) else chunk.decode("utf-8")
        async for chunk in response.body_iterator
    ]
    return "".join(parts)


def _fake_provider(events: list[str]) -> MagicMock:
    provider = MagicMock()

    async def fake_stream(*_a, **_kw):
        for e in events:
            yield e

    provider.stream_response = fake_stream
    return provider


_BASIC_EVENTS = [
    'event: message_start\ndata: {"type": "message_start"}\n\n',
    'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n',
    'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}\n\n',
    'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n',
    'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}\n\n',
    'event: message_stop\ndata: {"type": "message_stop"}\n\n',
]


class TestCreateMessageIntegration:
    @pytest.mark.asyncio
    async def test_nudge_appended_when_enabled_and_over_threshold(self):
        settings = _settings(
            context_nudge_enabled=True, context_nudge_threshold_tokens=1
        )
        provider = _fake_provider(_BASIC_EVENTS)
        service = ClaudeProxyService(settings, provider_getter=lambda _: provider)
        request = MessagesRequest(
            model="claude-3-haiku-20240307",
            max_tokens=10,
            messages=[Message(role="user", content="hello there")],
        )

        response = service.create_message(request)
        body = await _collect_body(response)

        events = parse_sse_text(body)
        assert_anthropic_stream_contract(events)
        assert "Context is large" in text_content(events)

    @pytest.mark.asyncio
    async def test_no_nudge_when_disabled(self):
        settings = _settings(
            context_nudge_enabled=False, context_nudge_threshold_tokens=1
        )
        provider = _fake_provider(_BASIC_EVENTS)
        service = ClaudeProxyService(settings, provider_getter=lambda _: provider)
        request = MessagesRequest(
            model="claude-3-haiku-20240307",
            max_tokens=10,
            messages=[Message(role="user", content="hello there")],
        )

        response = service.create_message(request)
        body = await _collect_body(response)

        assert "Context is large" not in body
        events = parse_sse_text(body)
        assert_anthropic_stream_contract(events)

    @pytest.mark.asyncio
    async def test_no_nudge_when_under_threshold(self):
        settings = _settings(
            context_nudge_enabled=True, context_nudge_threshold_tokens=1_000_000
        )
        provider = _fake_provider(_BASIC_EVENTS)
        service = ClaudeProxyService(settings, provider_getter=lambda _: provider)
        request = MessagesRequest(
            model="claude-3-haiku-20240307",
            max_tokens=10,
            messages=[Message(role="user", content="hello there")],
        )

        response = service.create_message(request)
        body = await _collect_body(response)

        assert "Context is large" not in body
