"""
CLI Session Manager for Multi-Instance Claude CLI Support

Manages a pool of CLISession instances, each handling one conversation.
This enables true parallel processing where multiple conversations run
simultaneously in separate CLI processes.
"""

import asyncio
import uuid

from loguru import logger

from .session import CLISession


class CLISessionManager:
    """
    Manages multiple CLISession instances for parallel conversation processing.

    Each new conversation gets its own CLISession with its own subprocess.
    Replies to existing conversations reuse the same CLISession instance.

    Sessions go through two ID stages:

    1. **Pending** — created with a caller-supplied or auto-generated ``temp_id``
       before the CLI process has emitted its real session UUID.
    2. **Active** — promoted to ``_sessions`` via :meth:`register_real_session_id`
       once the real UUID is known, with a bidirectional mapping kept between the
       two IDs so that either can be used for lookup.
    """

    def __init__(
        self,
        workspace_path: str,
        api_url: str,
        allowed_dirs: list[str] | None = None,
        plans_directory: str | None = None,
        claude_bin: str = "claude",
        *,
        log_raw_cli_diagnostics: bool = False,
        log_messaging_error_details: bool = False,
    ):
        """
        Initialize the session manager.

        Args:
            workspace_path: Working directory for CLI processes.
            api_url: API URL for the proxy.
            allowed_dirs: Directories the CLI is allowed to access.
            plans_directory: Directory for Claude Code CLI plan files (passed via --settings).
            claude_bin: Path or name of the ``claude`` binary to spawn.
            log_raw_cli_diagnostics: Forward raw CLI stdout/stderr to the logger.
            log_messaging_error_details: Include exception messages (not just type)
                in session-stop error logs.
        """
        self.workspace = workspace_path
        self.api_url = api_url
        self.allowed_dirs = allowed_dirs or []
        self.plans_directory = plans_directory
        self.claude_bin = claude_bin
        self._log_raw_cli_diagnostics = log_raw_cli_diagnostics
        self._log_messaging_error_details = log_messaging_error_details

        self._sessions: dict[str, CLISession] = {}
        self._pending_sessions: dict[str, CLISession] = {}
        self._temp_to_real: dict[str, str] = {}
        self._real_to_temp: dict[str, str] = {}
        self._lock = asyncio.Lock()

        logger.info("CLISessionManager initialized")

    async def get_or_create_session(
        self, session_id: str | None = None
    ) -> tuple[CLISession, str, bool]:
        """Get an existing session or create a new one.

        If ``session_id`` is provided it is resolved through ``_temp_to_real``
        first, so callers can pass either a temp or real ID and hit the same
        session.  When no match is found — or ``session_id`` is ``None`` — a
        fresh :class:`CLISession` is created and placed in ``_pending_sessions``
        under a temp ID (the supplied ``session_id``, or a generated one).

        Returns:
            ``(session, effective_id, is_new)`` — ``is_new`` is ``True`` only
            when a session was just created.
        """
        async with self._lock:
            if session_id:
                lookup_id = self._temp_to_real.get(session_id, session_id)

                if lookup_id in self._sessions:
                    return self._sessions[lookup_id], lookup_id, False
                if lookup_id in self._pending_sessions:
                    return self._pending_sessions[lookup_id], lookup_id, False

            temp_id = session_id if session_id else f"pending_{uuid.uuid4().hex[:8]}"

            new_session = CLISession(
                workspace_path=self.workspace,
                api_url=self.api_url,
                allowed_dirs=self.allowed_dirs,
                plans_directory=self.plans_directory,
                claude_bin=self.claude_bin,
                log_raw_cli_diagnostics=self._log_raw_cli_diagnostics,
            )
            self._pending_sessions[temp_id] = new_session
            logger.info(f"Created new session: {temp_id}")

            return new_session, temp_id, True

    async def register_real_session_id(
        self, temp_id: str, real_session_id: str
    ) -> bool:
        """Promote a pending session to active once the real CLI session UUID is known.

        Moves the session from ``_pending_sessions`` to ``_sessions`` and records
        the bidirectional ``temp_id`` ↔ ``real_session_id`` mapping so that
        subsequent lookups with either ID reach the same session.

        Returns ``False`` (without raising) if ``temp_id`` is not in
        ``_pending_sessions``, which can happen if the session was removed
        concurrently.
        """
        async with self._lock:
            if temp_id not in self._pending_sessions:
                logger.warning(f"Temp session {temp_id} not found")
                return False

            session = self._pending_sessions.pop(temp_id)
            self._sessions[real_session_id] = session
            self._temp_to_real[temp_id] = real_session_id
            self._real_to_temp[real_session_id] = temp_id

            logger.info(f"Registered session: {temp_id} -> {real_session_id}")
            return True

    async def remove_session(self, session_id: str) -> bool:
        """Stop and remove a session, accepting either a temp or real ID.

        Cleans up both directions of the ``temp_id`` ↔ ``real_session_id``
        mapping when removing an active session.  Returns ``False`` if the ID
        is not found in either ``_sessions`` or ``_pending_sessions``.
        """
        async with self._lock:
            if session_id in self._pending_sessions:
                session = self._pending_sessions.pop(session_id)
                await session.stop()
                return True

            if session_id in self._sessions:
                session = self._sessions.pop(session_id)
                await session.stop()
                temp_id = self._real_to_temp.pop(session_id, None)
                if temp_id is not None:
                    self._temp_to_real.pop(temp_id, None)
                return True

            return False

    async def stop_all(self):
        """Stop every active and pending session and reset all internal state.

        Errors from individual :meth:`CLISession.stop` calls are caught and
        logged rather than propagated, so one broken session cannot prevent
        the others from being cleaned up.
        """
        async with self._lock:
            all_sessions = list(self._sessions.values()) + list(
                self._pending_sessions.values()
            )
            for session in all_sessions:
                try:
                    await session.stop()
                except Exception as e:
                    if self._log_messaging_error_details:
                        logger.error(
                            "Error stopping session: {}: {}",
                            type(e).__name__,
                            e,
                        )
                    else:
                        logger.error(
                            "Error stopping session: exc_type={}",
                            type(e).__name__,
                        )

            self._sessions.clear()
            self._pending_sessions.clear()
            self._temp_to_real.clear()
            self._real_to_temp.clear()
            logger.info("All sessions stopped")

    def get_stats(self) -> dict:
        """Get session statistics."""
        return {
            "active_sessions": len(self._sessions),
            "pending_sessions": len(self._pending_sessions),
            "busy_count": sum(1 for s in self._sessions.values() if s.is_busy),
        }
