"""CLI event handling for a single queued node (transcript + session + errors)."""

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from .cli_event_constants import TRANSCRIPT_EVENT_TYPES, get_status_for_event
from .platforms.base import SessionManagerInterface
from .safe_diagnostics import text_len_hint
from .session import SessionStore
from .transcript import TranscriptBuffer
from .trees.queue_manager import MessageState, MessageTree


async def handle_session_info_event(
    event_data: dict[str, Any],
    tree: MessageTree | None,
    node_id: str,
    captured_session_id: str | None,
    temp_session_id: str | None,
    *,
    cli_manager: SessionManagerInterface,
    session_store: SessionStore,
) -> tuple[str | None, str | None]:
    """Promote a pending session to active when the CLI emits a ``session_info`` event.

    The CLI emits ``session_info`` early in a stream to announce the real session UUID.
    This function registers it with the manager (temp → real mapping), updates the
    tree node state, and returns the new ``(captured_session_id, temp_session_id)``
    pair — with ``temp_session_id`` cleared to ``None`` so the caller stops passing
    the temp ID to subsequent operations.

    Non-``session_info`` events are ignored and the IDs are returned unchanged.
    """
    if event_data.get("type") != "session_info":
        return captured_session_id, temp_session_id

    real_session_id = event_data.get("session_id")
    if not real_session_id or not temp_session_id:
        return captured_session_id, temp_session_id

    await cli_manager.register_real_session_id(temp_session_id, real_session_id)
    if tree and real_session_id:
        await tree.update_state(
            node_id,
            MessageState.IN_PROGRESS,
            session_id=real_session_id,
        )
        session_store.save_tree(tree.root_id, tree.to_dict())

    return real_session_id, None


async def process_parsed_cli_event(
    parsed: dict[str, Any],
    transcript: TranscriptBuffer,
    update_ui: Callable[..., Awaitable[None]],
    last_status: str | None,
    had_transcript_events: bool,
    tree: MessageTree | None,
    node_id: str,
    captured_session_id: str | None,
    *,
    session_store: SessionStore,
    format_status: Callable[..., str],
    propagate_error_to_children: Callable[[str, str, str], Awaitable[None]],
    log_messaging_error_details: bool = False,
) -> tuple[str | None, bool]:
    """Apply one parsed CLI event to the transcript and drive UI/state transitions.

    Returns the updated ``(last_status, had_transcript_events)`` pair so the
    caller can thread them across the event loop without mutation.

    Side effects (in dependency order):

    - Applies transcript-mutating events to ``transcript``.
    - Calls ``update_ui`` for status-bearing or block-stop events.
    - On ``complete``: updates node state and persists the tree.
    - On ``error``: updates UI, marks the node, and propagates to children.
    """
    ptype = parsed.get("type") or ""

    if ptype in TRANSCRIPT_EVENT_TYPES:
        transcript.apply(parsed)
        had_transcript_events = True

    status = get_status_for_event(ptype, parsed, format_status)
    if status is not None:
        await update_ui(status)
        last_status = status
    elif ptype == "block_stop":
        await update_ui(last_status, force=True)
    elif ptype == "complete":
        if not had_transcript_events:
            transcript.apply({"type": "text_chunk", "text": "Done."})
        logger.info("HANDLER: Task complete, updating UI")
        await update_ui(format_status("✅", "Complete"), force=True)
        if tree and captured_session_id:
            await tree.update_state(
                node_id,
                MessageState.COMPLETED,
                session_id=captured_session_id,
            )
            session_store.save_tree(tree.root_id, tree.to_dict())
    elif ptype == "error":
        error_msg = parsed.get("message", "Unknown error")
        if log_messaging_error_details:
            logger.error("HANDLER: Error event received: {}", error_msg)
        else:
            em = error_msg if isinstance(error_msg, str) else str(error_msg)
            logger.error(
                "HANDLER: Error event received: message_chars={}",
                text_len_hint(em),
            )
        logger.info("HANDLER: Updating UI with error status")
        await update_ui(format_status("❌", "Error"), force=True)
        if tree:
            await propagate_error_to_children(node_id, error_msg, "Parent task failed")

    return last_status, had_transcript_events
