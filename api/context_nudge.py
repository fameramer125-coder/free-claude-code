"""Proxy-level context-size nudge for direct CLI/IDE clients (session-agnostic)."""

from collections.abc import AsyncIterator

from config.settings import Settings
from core.anthropic.sse import format_sse_event

_CONTENT_BLOCK_START_EVENT = "event: content_block_start"
_MESSAGE_DELTA_EVENT = "event: message_delta"


def should_nudge(settings: Settings, input_tokens: int) -> bool:
    """Return whether this request's context is large enough to warrant a nudge."""
    return (
        settings.context_nudge_enabled
        and input_tokens >= settings.context_nudge_threshold_tokens
    )


def build_nudge_text(input_tokens: int) -> str:
    """Build the inline warning text shown as an extra eager text block."""
    return (
        f"\n\n---\n⚠️ Context is large (~{input_tokens:,} tokens). Consider running "
        "/compact or starting a fresh session to reduce cost.\n"
    )


async def wrap_stream_with_context_nudge(
    stream: AsyncIterator[str], nudge_text: str
) -> AsyncIterator[str]:
    """Inject an extra eager text block with ``nudge_text`` right before ``message_delta``.

    Buffers on blank-line-delimited SSE blocks rather than assuming one event per
    yielded chunk, since providers vary between whole-event, per-line, and raw
    passthrough chunking. The injected block's index is the count of
    ``content_block_start`` events observed so far, which matches the next free
    index because every provider allocates indices sequentially from zero.
    """
    buffer = ""
    block_count = 0
    async for chunk in stream:
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            block += "\n\n"
            if block.startswith(_CONTENT_BLOCK_START_EVENT):
                block_count += 1
            elif block.startswith(_MESSAGE_DELTA_EVENT):
                yield format_sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_count,
                        "content_block": {"type": "text", "text": nudge_text},
                    },
                )
                yield format_sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": block_count},
                )
            yield block
    if buffer:
        yield buffer
