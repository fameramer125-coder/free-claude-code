"""Abstract base class for messaging platforms."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import (
    Any,
    Protocol,
    runtime_checkable,
)

from ..models import IncomingMessage


@runtime_checkable
class CLISession(Protocol):
    """Protocol for CLI session - avoid circular import from cli package."""

    def start_task(
        self, prompt: str, session_id: str | None = None, fork_session: bool = False
    ) -> AsyncGenerator[dict, Any]: ...

    @property
    def is_busy(self) -> bool: ...


@runtime_checkable
class SessionManagerInterface(Protocol):
    """Protocol for session managers (avoids circular import from cli package)."""

    async def get_or_create_session(
        self, session_id: str | None = None
    ) -> tuple[CLISession, str, bool]:
        """Return (session, session_id, is_new_session)."""
        ...

    async def register_real_session_id(
        self, temp_id: str, real_session_id: str
    ) -> bool:
        """Register the real session ID from CLI output."""
        ...

    async def stop_all(self) -> None:
        """Stop all sessions."""
        ...

    async def remove_session(self, session_id: str) -> bool:
        """Remove a session from the manager."""
        ...

    def get_stats(self) -> dict:
        """Get session statistics."""
        ...


class MessagingPlatform(ABC):
    """Base class for messaging platform adapters (Telegram, Discord, etc.)."""

    name: str = "base"

    @abstractmethod
    async def start(self) -> None:
        """Initialize and connect to the messaging platform."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and cleanup resources."""
        pass

    @abstractmethod
    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to: str | None = None,
        parse_mode: str | None = None,
        message_thread_id: str | None = None,
    ) -> str:
        """Send a message and return its message ID."""
        pass

    @abstractmethod
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        """Edit an existing message in place."""
        pass

    @abstractmethod
    async def delete_message(
        self,
        chat_id: str,
        message_id: str,
    ) -> None:
        """Delete a message from a chat."""
        pass

    @abstractmethod
    async def queue_send_message(
        self,
        chat_id: str,
        text: str,
        reply_to: str | None = None,
        parse_mode: str | None = None,
        fire_and_forget: bool = True,
        message_thread_id: str | None = None,
    ) -> str | None:
        """Enqueue a message; returns None immediately if fire_and_forget, else waits for message ID."""
        pass

    @abstractmethod
    async def queue_edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        parse_mode: str | None = None,
        fire_and_forget: bool = True,
    ) -> None:
        """Enqueue a message edit; returns immediately if fire_and_forget."""
        pass

    @abstractmethod
    async def queue_delete_message(
        self,
        chat_id: str,
        message_id: str,
        fire_and_forget: bool = True,
    ) -> None:
        """Enqueue a message deletion; returns immediately if fire_and_forget."""
        pass

    async def queue_delete_messages(
        self,
        chat_id: str,
        message_ids: list[str],
        *,
        fire_and_forget: bool = True,
    ) -> None:
        """Delete many messages; loops queue_delete_message by default (override for bulk delete)."""
        for mid in message_ids:
            await self.queue_delete_message(
                chat_id, mid, fire_and_forget=fire_and_forget
            )

    @abstractmethod
    def on_message(
        self,
        handler: Callable[[IncomingMessage], Awaitable[None]],
    ) -> None:
        """Register the handler called for each incoming message."""
        pass

    @abstractmethod
    def fire_and_forget(self, task: Awaitable[Any]) -> None:
        """Execute a coroutine without awaiting it."""
        pass

    @property
    def is_connected(self) -> bool:
        """Check if the platform is connected."""
        return False
