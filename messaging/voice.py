"""Platform-neutral voice note helpers."""

import asyncio
from pathlib import Path


class PendingVoiceRegistry:
    """Track voice notes that are still waiting on transcription.

    Each entry is indexed under two keys — ``(chat_id, voice_msg_id)`` and
    ``(chat_id, status_msg_id)`` — so that a ``/clear`` reply to *either* the
    original voice message or the "Transcribing…" status message can locate
    and cancel the pending transcription.
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, chat_id: str, voice_msg_id: str, status_msg_id: str
    ) -> None:
        """Index a pending transcription under both the voice and status message IDs."""
        async with self._lock:
            entry = (voice_msg_id, status_msg_id)
            self._pending[(chat_id, voice_msg_id)] = entry
            self._pending[(chat_id, status_msg_id)] = entry

    async def cancel(self, chat_id: str, reply_id: str) -> tuple[str, str] | None:
        """Remove and return the pending entry for ``reply_id`` (voice or status ID).

        Returns ``None`` if no pending entry is found, indicating the voice note
        has already been transcribed or was never registered.
        """
        async with self._lock:
            entry = self._pending.pop((chat_id, reply_id), None)
            if entry is None:
                return None
            voice_msg_id, status_msg_id = entry
            self._pending.pop((chat_id, voice_msg_id), None)
            self._pending.pop((chat_id, status_msg_id), None)
            return entry

    async def is_pending(self, chat_id: str, voice_msg_id: str) -> bool:
        """Return whether a voice message is still awaiting transcription."""
        async with self._lock:
            return (chat_id, voice_msg_id) in self._pending

    async def complete(
        self, chat_id: str, voice_msg_id: str, status_msg_id: str
    ) -> None:
        """Remove both index keys after transcription succeeds."""
        async with self._lock:
            self._pending.pop((chat_id, voice_msg_id), None)
            self._pending.pop((chat_id, status_msg_id), None)


class VoiceTranscriptionService:
    """Run configured transcription backends off the asyncio event loop.

    Transcription is CPU/network-bound, so calls are dispatched via
    :func:`asyncio.to_thread` to avoid blocking the event loop.
    """

    def __init__(
        self,
        *,
        hf_token: str = "",
        nvidia_nim_api_key: str = "",
    ) -> None:
        self._hf_token = hf_token
        self._nvidia_nim_api_key = nvidia_nim_api_key

    async def transcribe(
        self,
        file_path: Path,
        mime_type: str,
        *,
        whisper_model: str,
        whisper_device: str,
    ) -> str:
        """Transcribe ``file_path`` using the configured backend (non-blocking)."""
        from .transcription import transcribe_audio

        return await asyncio.to_thread(
            transcribe_audio,
            file_path,
            mime_type,
            whisper_model=whisper_model,
            whisper_device=whisper_device,
            hf_token=self._hf_token,
            nvidia_nim_api_key=self._nvidia_nim_api_key,
        )
