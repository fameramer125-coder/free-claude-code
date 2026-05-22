"""CLI session manager: pool of CLISession instances for parallel conversation processing."""

import asyncio
import uuid

from loguru import logger

from .session import CLISession


class CLISessionManager:
    """Pool of CLISession instances; each conversation has its own CLI subprocess."""

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
        """Return ``(session, effective_id, is_new)`` for a temp or real session ID."""
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
            logger.info("Created new session: {}", temp_id)

            return new_session, temp_id, True

    async def register_real_session_id(
        self, temp_id: str, real_session_id: str
    ) -> bool:
        """Promote a pending session to active; returns False if temp_id is not found."""
        async with self._lock:
            if temp_id not in self._pending_sessions:
                logger.warning("Temp session {} not found", temp_id)
                return False

            session = self._pending_sessions.pop(temp_id)
            self._sessions[real_session_id] = session
            self._temp_to_real[temp_id] = real_session_id
            self._real_to_temp[real_session_id] = temp_id

            logger.info("Registered session: {} -> {}", temp_id, real_session_id)
            return True

    async def remove_session(self, session_id: str) -> bool:
        """Stop and remove a session by temp or real ID; returns False if not found."""
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
        """Stop all active and pending sessions; errors are logged, not raised."""
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
