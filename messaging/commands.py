"""Command handlers for messaging platform commands (/stop, /stats, /clear).

Extracted from ClaudeMessageHandler to keep handler.py focused on
core message processing logic.
"""

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from messaging.handler import ClaudeMessageHandler
    from messaging.models import IncomingMessage


async def handle_stop_command(
    handler: ClaudeMessageHandler, incoming: IncomingMessage
) -> None:
    """Handle /stop command.

    Two modes:

    - **Reply-scoped**: replying ``/stop`` to a message cancels only that task.
    - **Global**: a bare ``/stop`` cancels everything in-flight.
    """
    if incoming.is_reply() and incoming.reply_to_message_id:
        reply_id = incoming.reply_to_message_id
        tree = handler.tree_queue.get_tree_for_node(reply_id)
        node_id = handler.tree_queue.resolve_parent_node_id(reply_id) if tree else None

        if not node_id:
            msg_id = await handler.platform.queue_send_message(
                incoming.chat_id,
                handler.format_status(
                    "⏹", "Stopped.", "Nothing to stop for that message."
                ),
                fire_and_forget=False,
                message_thread_id=incoming.message_thread_id,
            )
            handler.record_outgoing_message(
                incoming.platform, incoming.chat_id, msg_id, "command"
            )
            return

        count = await handler.stop_task(node_id)
        noun = "request" if count == 1 else "requests"
        msg_id = await handler.platform.queue_send_message(
            incoming.chat_id,
            handler.format_status("⏹", "Stopped.", f"Cancelled {count} {noun}."),
            fire_and_forget=False,
            message_thread_id=incoming.message_thread_id,
        )
        handler.record_outgoing_message(
            incoming.platform, incoming.chat_id, msg_id, "command"
        )
        return

    count = await handler.stop_all_tasks()
    msg_id = await handler.platform.queue_send_message(
        incoming.chat_id,
        handler.format_status(
            "⏹", "Stopped.", f"Cancelled {count} pending or active requests."
        ),
        fire_and_forget=False,
        message_thread_id=incoming.message_thread_id,
    )
    handler.record_outgoing_message(
        incoming.platform, incoming.chat_id, msg_id, "command"
    )


async def handle_stats_command(
    handler: ClaudeMessageHandler, incoming: IncomingMessage
) -> None:
    """Handle /stats command — reply with active CLI and tree counts."""
    stats = handler.cli_manager.get_stats()
    tree_count = handler.tree_queue.get_tree_count()
    ctx = handler.get_render_ctx()
    msg_id = await handler.platform.queue_send_message(
        incoming.chat_id,
        "📊 "
        + ctx.bold("Stats")
        + "\n"
        + ctx.escape_text(f"• Active CLI: {stats['active_sessions']}")
        + "\n"
        + ctx.escape_text(f"• Message Trees: {tree_count}"),
        fire_and_forget=False,
        message_thread_id=incoming.message_thread_id,
    )
    handler.record_outgoing_message(
        incoming.platform, incoming.chat_id, msg_id, "command"
    )


async def _delete_message_ids(
    handler: ClaudeMessageHandler, chat_id: str, msg_ids: set[str]
) -> None:
    """Best-effort delete messages by ID.

    Numeric IDs are sorted descending so newer messages are deleted first,
    which reduces the chance of the platform blocking deletion of earlier
    messages that are still referenced. Non-numeric IDs follow after.
    Errors are swallowed; a failed delete must never abort the caller.
    """
    if not msg_ids:
        return

    def _as_int(s: str) -> int | None:
        try:
            return int(str(s))
        except Exception:
            return None

    numeric: list[tuple[int, str]] = []
    non_numeric: list[str] = []
    for mid in msg_ids:
        n = _as_int(mid)
        if n is None:
            non_numeric.append(mid)
        else:
            numeric.append((n, mid))
    numeric.sort(reverse=True)
    ordered = [mid for _, mid in numeric] + non_numeric

    try:
        CHUNK = 100
        for i in range(0, len(ordered), CHUNK):
            chunk = ordered[i : i + CHUNK]
            await handler.platform.queue_delete_messages(
                chat_id, chunk, fire_and_forget=False
            )
    except Exception as e:
        logger.debug(f"Batch delete failed: {type(e).__name__}: {e}")


async def _handle_clear_branch(
    handler: ClaudeMessageHandler,
    incoming: IncomingMessage,
    branch_root_id: str,
) -> None:
    """Clear a branch (replied-to node + all descendants).

    Operation order is load-bearing:

    1. Cancel tasks first — stops any in-flight work before touching messages.
    2. Collect and delete platform messages (best-effort).
    3. Remove the branch from the in-memory tree.
    4. Persist the updated tree (or drop it entirely if all nodes are gone).
    """
    tree = handler.tree_queue.get_tree_for_node(branch_root_id)
    if not tree:
        return

    cancelled = await handler.tree_queue.cancel_branch(branch_root_id)
    handler.update_cancelled_nodes_ui(cancelled)

    msg_ids: set[str] = set()
    branch_ids = tree.get_descendants(branch_root_id)
    for nid in branch_ids:
        node = tree.get_node(nid)
        if node:
            if node.incoming.message_id:
                msg_ids.add(str(node.incoming.message_id))
            if node.status_message_id:
                msg_ids.add(str(node.status_message_id))
    if incoming.message_id:
        msg_ids.add(str(incoming.message_id))

    await _delete_message_ids(handler, incoming.chat_id, msg_ids)

    removed, root_id, removed_entire_tree = await handler.tree_queue.remove_branch(
        branch_root_id
    )

    try:
        handler.session_store.remove_node_mappings([n.node_id for n in removed])
        if removed_entire_tree:
            handler.session_store.remove_tree(root_id)
        else:
            updated_tree = handler.tree_queue.get_tree(root_id)
            if updated_tree:
                handler.session_store.save_tree(root_id, updated_tree.to_dict())
    except Exception as e:
        logger.warning(f"Failed to update session store after branch clear: {e}")


async def handle_clear_command(
    handler: ClaudeMessageHandler, incoming: IncomingMessage
) -> None:
    """Handle /clear command.

    Two modes:

    - **Reply-scoped**: replying ``/clear`` to a message removes that node and
      all its descendants from the tree and deletes the associated platform
      messages.  If the replied-to message is a pending voice note (no tree
      node yet), it cancels the transcription instead.
    - **Global**: a bare ``/clear`` stops all tasks, best-effort deletes every
      recorded message in the chat, then resets the session store and tree
      queue.
    """
    from messaging.trees import TreeQueueManager

    if incoming.is_reply() and incoming.reply_to_message_id:
        reply_id = incoming.reply_to_message_id
        tree = handler.tree_queue.get_tree_for_node(reply_id)
        branch_root_id = (
            handler.tree_queue.resolve_parent_node_id(reply_id) if tree else None
        )
        if not branch_root_id:
            cancel_fn = getattr(handler.platform, "cancel_pending_voice", None)
            if cancel_fn is not None:
                cancelled = await cancel_fn(incoming.chat_id, reply_id)
                if cancelled is not None:
                    voice_msg_id, status_msg_id = cancelled
                    msg_ids_to_del: set[str] = {voice_msg_id, status_msg_id}
                    if incoming.message_id is not None:
                        msg_ids_to_del.add(str(incoming.message_id))
                    await _delete_message_ids(handler, incoming.chat_id, msg_ids_to_del)
                    msg_id = await handler.platform.queue_send_message(
                        incoming.chat_id,
                        handler.format_status("🗑", "Cleared.", "Voice note cancelled."),
                        fire_and_forget=False,
                        message_thread_id=incoming.message_thread_id,
                    )
                    handler.record_outgoing_message(
                        incoming.platform, incoming.chat_id, msg_id, "command"
                    )
                    return
            msg_id = await handler.platform.queue_send_message(
                incoming.chat_id,
                handler.format_status(
                    "🗑", "Cleared.", "Nothing to clear for that message."
                ),
                fire_and_forget=False,
                message_thread_id=incoming.message_thread_id,
            )
            handler.record_outgoing_message(
                incoming.platform, incoming.chat_id, msg_id, "command"
            )
            return
        await _handle_clear_branch(handler, incoming, branch_root_id)
        return

    await handler.stop_all_tasks()

    msg_ids: set[str] = set()

    try:
        for mid in handler.session_store.get_message_ids_for_chat(
            incoming.platform, incoming.chat_id
        ):
            if mid is not None:
                msg_ids.add(str(mid))
    except Exception as e:
        logger.debug(f"Failed to read message log for /clear: {e}")

    try:
        msg_ids.update(
            handler.tree_queue.get_message_ids_for_chat(
                incoming.platform, incoming.chat_id
            )
        )
    except Exception as e:
        logger.warning(f"Failed to gather messages for /clear: {e}")

    if incoming.message_id is not None:
        msg_ids.add(str(incoming.message_id))

    await _delete_message_ids(handler, incoming.chat_id, msg_ids)

    try:
        handler.session_store.clear_all()
    except Exception as e:
        logger.warning(f"Failed to clear session store: {e}")

    handler.replace_tree_queue(
        TreeQueueManager(
            queue_update_callback=handler.update_queue_positions,
            node_started_callback=handler.mark_node_processing,
        )
    )
