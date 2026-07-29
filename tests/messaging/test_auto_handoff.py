"""Handler-level tests for automatic context handoff."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from messaging.context_budget import HANDOFF_SEED_HEADER, ContextBudget
from messaging.handler import ClaudeMessageHandler
from messaging.models import IncomingMessage
from messaging.trees.data import MessageNode, MessageState, MessageTree


def _make_incoming(**kwargs) -> IncomingMessage:
    defaults: dict[str, Any] = {
        "text": "hello",
        "chat_id": "chat_1",
        "user_id": "user_1",
        "message_id": "m1",
        "platform": "telegram",
    }
    defaults.update(kwargs)
    return IncomingMessage(**defaults)


async def _events(events):
    for e in events:
        yield e


async def _wait_for_state(tree, node_id, state, attempts=50):
    for _ in range(attempts):
        node = tree.get_node(node_id)
        if node and node.state == state:
            return
        await asyncio.sleep(0.01)


def _handler(mock_platform, mock_cli_manager, mock_session_store, budget):
    return ClaudeMessageHandler(
        mock_platform,
        mock_cli_manager,
        mock_session_store,
        context_budget=budget,
    )


def _task_events(session_id: str, context_tokens: int) -> list[dict]:
    return [
        {"type": "session_info", "session_id": session_id},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Reply"}]},
        },
        {"type": "result", "usage": {"input_tokens": context_tokens}},
        {"type": "exit", "code": 0, "stderr": None},
    ]


def _memo_events(memo_text: str) -> list[dict]:
    return [
        {"type": "session_info", "session_id": "memo_sess"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": memo_text}]},
        },
        {"type": "exit", "code": 0, "stderr": None},
    ]


class TestNodeSerialization:
    def test_handoff_memo_round_trip(self):
        node = MessageNode(
            node_id="n1",
            incoming=_make_incoming(),
            status_message_id="s1",
            handoff_memo="state: done",
        )
        restored = MessageNode.from_dict(node.to_dict())
        assert restored.handoff_memo == "state: done"

    def test_missing_handoff_memo_defaults_to_none(self):
        node = MessageNode(
            node_id="n1", incoming=_make_incoming(), status_message_id="s1"
        )
        data = node.to_dict()
        del data["handoff_memo"]
        assert MessageNode.from_dict(data).handoff_memo is None


class TestTreeHandoffAccessors:
    @pytest.mark.asyncio
    async def test_set_and_get_parent_handoff_memo(self):
        root = MessageNode(
            node_id="root", incoming=_make_incoming(), status_message_id="s_root"
        )
        tree = MessageTree(root)
        child = await tree.add_node(
            node_id="child",
            incoming=_make_incoming(message_id="child"),
            status_message_id="s_child",
            parent_id="root",
        )
        assert tree.get_parent_handoff_memo(child.node_id) is None

        await tree.set_handoff_memo("root", "memo text")
        assert tree.get_parent_handoff_memo(child.node_id) == "memo text"
        assert tree.get_parent_handoff_memo("root") is None

    @pytest.mark.asyncio
    async def test_set_handoff_memo_missing_node_is_noop(self):
        root = MessageNode(
            node_id="root", incoming=_make_incoming(), status_message_id="s_root"
        )
        tree = MessageTree(root)
        await tree.set_handoff_memo("missing", "memo")
        root_node = tree.get_node("root")
        assert root_node is not None
        assert root_node.handoff_memo is None


class TestAutoHandoffFlow:
    @pytest.mark.asyncio
    async def test_handoff_triggers_over_threshold(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        budget = ContextBudget(enabled=True, threshold_tokens=1_000)
        handler = _handler(mock_platform, mock_cli_manager, mock_session_store, budget)

        session = MagicMock()
        session.start_task.side_effect = [
            _events(_task_events("sess1", context_tokens=5_000)),
            _events(_memo_events("memo: continue from step 3")),
        ]
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "pending_1",
            True,
        )
        mock_platform.queue_send_message.return_value = "s1"

        await handler.handle_message(_make_incoming(text="task", message_id="m1"))
        tree = handler.tree_queue.get_tree_for_node("m1")
        await _wait_for_state(tree, "m1", MessageState.COMPLETED)
        # Let the handoff step (after state update) finish.
        await asyncio.sleep(0.05)

        node = tree.get_node("m1")
        assert node.state == MessageState.COMPLETED
        assert node.handoff_memo == "memo: continue from step 3"

        # Memo run forked from the completed session.
        memo_call = session.start_task.call_args_list[1]
        assert memo_call.kwargs["session_id"] == "sess1"
        assert memo_call.kwargs["fork_session"] is True

        # User was notified with a handoff status message.
        texts = [c.args[1] for c in mock_platform.queue_send_message.call_args_list]
        assert any("♻️" in t for t in texts)

    @pytest.mark.asyncio
    async def test_no_handoff_below_threshold(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        budget = ContextBudget(enabled=True, threshold_tokens=1_000_000)
        handler = _handler(mock_platform, mock_cli_manager, mock_session_store, budget)

        session = MagicMock()
        session.start_task.side_effect = [
            _events(_task_events("sess1", context_tokens=5_000)),
        ]
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "pending_1",
            True,
        )
        mock_platform.queue_send_message.return_value = "s1"

        await handler.handle_message(_make_incoming(text="task", message_id="m1"))
        tree = handler.tree_queue.get_tree_for_node("m1")
        await _wait_for_state(tree, "m1", MessageState.COMPLETED)
        await asyncio.sleep(0.05)

        assert tree.get_node("m1").handoff_memo is None
        assert session.start_task.call_count == 1

    @pytest.mark.asyncio
    async def test_no_handoff_when_disabled(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        handler = _handler(
            mock_platform, mock_cli_manager, mock_session_store, ContextBudget()
        )

        session = MagicMock()
        session.start_task.side_effect = [
            _events(_task_events("sess1", context_tokens=10_000_000)),
        ]
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "pending_1",
            True,
        )
        mock_platform.queue_send_message.return_value = "s1"

        await handler.handle_message(_make_incoming(text="task", message_id="m1"))
        tree = handler.tree_queue.get_tree_for_node("m1")
        await _wait_for_state(tree, "m1", MessageState.COMPLETED)
        await asyncio.sleep(0.05)

        assert tree.get_node("m1").handoff_memo is None
        assert session.start_task.call_count == 1

    @pytest.mark.asyncio
    async def test_memo_error_keeps_resume_behavior(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        budget = ContextBudget(enabled=True, threshold_tokens=1_000)
        handler = _handler(mock_platform, mock_cli_manager, mock_session_store, budget)

        session = MagicMock()
        session.start_task.side_effect = [
            _events(_task_events("sess1", context_tokens=5_000)),
            _events([{"type": "error", "error": {"message": "memo failed"}}]),
        ]
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "pending_1",
            True,
        )
        mock_platform.queue_send_message.return_value = "s1"

        await handler.handle_message(_make_incoming(text="task", message_id="m1"))
        tree = handler.tree_queue.get_tree_for_node("m1")
        await _wait_for_state(tree, "m1", MessageState.COMPLETED)
        await asyncio.sleep(0.05)

        node = tree.get_node("m1")
        assert node.state == MessageState.COMPLETED
        assert node.handoff_memo is None

    @pytest.mark.asyncio
    async def test_memo_is_clipped_to_max_chars(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        budget = ContextBudget(enabled=True, threshold_tokens=1_000, memo_max_chars=4)
        handler = _handler(mock_platform, mock_cli_manager, mock_session_store, budget)

        session = MagicMock()
        session.start_task.side_effect = [
            _events(_task_events("sess1", context_tokens=5_000)),
            _events(_memo_events("long memo text")),
        ]
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "pending_1",
            True,
        )
        mock_platform.queue_send_message.return_value = "s1"

        await handler.handle_message(_make_incoming(text="task", message_id="m1"))
        tree = handler.tree_queue.get_tree_for_node("m1")
        await _wait_for_state(tree, "m1", MessageState.COMPLETED)
        await asyncio.sleep(0.05)

        assert tree.get_node("m1").handoff_memo == "long"

    @pytest.mark.asyncio
    async def test_child_of_handed_off_parent_starts_fresh_seeded_session(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        budget = ContextBudget(enabled=True, threshold_tokens=1_000_000)
        handler = _handler(mock_platform, mock_cli_manager, mock_session_store, budget)
        mock_platform.queue_send_message.return_value = "s_root"

        # Build a completed root with a session id and a handoff memo.
        root_incoming = _make_incoming(text="root task", message_id="root")
        tree = await handler.tree_queue.create_tree(
            node_id="root", incoming=root_incoming, status_message_id="s_root"
        )
        await tree.update_state("root", MessageState.COMPLETED, session_id="old_sess")
        await tree.set_handoff_memo("root", "memo: resume at step 2")
        handler.tree_queue.register_node("s_root", tree.root_id)

        session = MagicMock()
        session.start_task.return_value = _events(
            _task_events("fresh_sess", context_tokens=10)
        )
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "pending_2",
            True,
        )
        mock_platform.queue_send_message.return_value = "s_child"

        await handler.handle_message(
            _make_incoming(
                text="follow-up", message_id="child", reply_to_message_id="root"
            )
        )
        await _wait_for_state(tree, "child", MessageState.COMPLETED)

        # Fresh session: no resume, prompt carries the memo and the new request.
        mock_cli_manager.get_or_create_session.assert_called_with(session_id=None)
        call = session.start_task.call_args
        assert call.kwargs["session_id"] is None
        assert call.kwargs["fork_session"] is False
        prompt = call.args[0]
        assert prompt.startswith(HANDOFF_SEED_HEADER)
        assert "memo: resume at step 2" in prompt
        assert "follow-up" in prompt

    @pytest.mark.asyncio
    async def test_child_without_memo_still_resumes_parent_session(
        self, mock_platform, mock_cli_manager, mock_session_store
    ):
        budget = ContextBudget(enabled=True, threshold_tokens=1_000_000)
        handler = _handler(mock_platform, mock_cli_manager, mock_session_store, budget)
        mock_platform.queue_send_message.return_value = "s_root"

        root_incoming = _make_incoming(text="root task", message_id="root")
        tree = await handler.tree_queue.create_tree(
            node_id="root", incoming=root_incoming, status_message_id="s_root"
        )
        await tree.update_state("root", MessageState.COMPLETED, session_id="old_sess")
        handler.tree_queue.register_node("s_root", tree.root_id)

        session = MagicMock()
        session.start_task.return_value = _events(
            _task_events("sess_child", context_tokens=10)
        )
        mock_cli_manager.get_or_create_session.return_value = (
            session,
            "old_sess",
            False,
        )
        mock_platform.queue_send_message.return_value = "s_child"

        await handler.handle_message(
            _make_incoming(
                text="follow-up", message_id="child", reply_to_message_id="root"
            )
        )
        await _wait_for_state(tree, "child", MessageState.COMPLETED)

        session.start_task.assert_called_with(
            "follow-up", session_id="old_sess", fork_session=True
        )
