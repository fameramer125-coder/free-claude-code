"""Model routing for Claude-compatible requests."""

from dataclasses import dataclass

from loguru import logger

from config.provider_ids import SUPPORTED_PROVIDER_IDS
from config.settings import Settings

from .gateway_model_ids import decode_gateway_model_id
from .models.anthropic import MessagesRequest, TokenCountRequest


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """Routing result for a single model name lookup.

    ``provider_model`` is the bare model name sent in the upstream API request
    (e.g. ``"meta/llama-3.1-405b-instruct"``).  ``provider_model_ref`` is the
    full ``provider/model`` string (or original gateway model ID) used for
    display and model-list lookups.
    """

    original_model: str
    provider_id: str
    provider_model: str
    provider_model_ref: str
    thinking_enabled: bool


@dataclass(frozen=True, slots=True)
class RoutedMessagesRequest:
    request: MessagesRequest
    resolved: ResolvedModel


@dataclass(frozen=True, slots=True)
class RoutedTokenCountRequest:
    request: TokenCountRequest
    resolved: ResolvedModel


class ModelRouter:
    """Resolve incoming Claude model names to configured provider/model pairs."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def resolve(self, claude_model_name: str) -> ResolvedModel:
        """Resolve a Claude model name to a provider/model pair.

        Two paths are tried in order:

        1. **Direct** — the name is either a gateway model ID (base64-encoded
           provider+model+flags) or a raw ``provider_id/model`` prefix.  Thinking
           is taken from the embedded flag when present; otherwise from settings.
        2. **Mapped** — the name is a Claude tier alias (e.g. ``claude-opus-4-…``)
           looked up via :meth:`~config.settings.Settings.resolve_model`, which
           returns the configured ``provider/model`` ref for that tier.
        """
        (
            direct_provider_id,
            direct_provider_model,
            force_thinking_enabled,
        ) = self._direct_provider_model(claude_model_name)
        if direct_provider_id is not None and direct_provider_model is not None:
            thinking_enabled = (
                force_thinking_enabled
                if force_thinking_enabled is not None
                else self._settings.resolve_thinking(direct_provider_model)
            )
            logger.debug(
                "MODEL DIRECT: '{}' -> provider='{}' model='{}' thinking={}",
                claude_model_name,
                direct_provider_id,
                direct_provider_model,
                thinking_enabled,
            )
            return ResolvedModel(
                original_model=claude_model_name,
                provider_id=direct_provider_id,
                provider_model=direct_provider_model,
                provider_model_ref=claude_model_name,
                thinking_enabled=thinking_enabled,
            )

        provider_model_ref = self._settings.resolve_model(claude_model_name)
        thinking_enabled = self._settings.resolve_thinking(claude_model_name)
        provider_id = Settings.parse_provider_type(provider_model_ref)
        provider_model = Settings.parse_model_name(provider_model_ref)
        if provider_model != claude_model_name:
            logger.debug(
                "MODEL MAPPING: '{}' -> '{}'", claude_model_name, provider_model
            )
        return ResolvedModel(
            original_model=claude_model_name,
            provider_id=provider_id,
            provider_model=provider_model,
            provider_model_ref=provider_model_ref,
            thinking_enabled=thinking_enabled,
        )

    def _direct_provider_model(
        self, model_name: str
    ) -> tuple[str | None, str | None, bool | None]:
        """Try to extract a provider/model pair directly from ``model_name``.

        Returns ``(provider_id, provider_model, force_thinking_enabled)``.
        All three are ``None`` when the name is not a direct reference (caller
        should fall through to settings-based mapping).

        Two sub-paths:

        - **Gateway ID**: base64-encoded blob decoded by
          :func:`~api.gateway_model_ids.decode_gateway_model_id`; may carry an
          explicit thinking flag.
        - **Raw prefix**: ``"provider_id/model_name"`` — thinking flag is absent
          (``None``), so the caller falls back to settings.
        """
        decoded = decode_gateway_model_id(model_name)
        if decoded is not None:
            if decoded.provider_id not in SUPPORTED_PROVIDER_IDS:
                return None, None, None
            return (
                decoded.provider_id,
                decoded.provider_model,
                decoded.force_thinking_enabled,
            )

        provider_id, separator, provider_model = model_name.partition("/")
        if not separator:
            return None, None, None
        if provider_id not in SUPPORTED_PROVIDER_IDS:
            return None, None, None
        if not provider_model:
            return None, None, None
        return provider_id, provider_model, None

    def resolve_messages_request(
        self, request: MessagesRequest
    ) -> RoutedMessagesRequest:
        """Resolve routing and return a deep-copied request with ``model`` replaced.

        The returned ``RoutedMessagesRequest.request.model`` is the upstream model
        name (``resolved.provider_model``), not the original Claude tier alias.
        """
        resolved = self.resolve(request.model)
        routed = request.model_copy(deep=True)
        routed.model = resolved.provider_model
        return RoutedMessagesRequest(request=routed, resolved=resolved)

    def resolve_token_count_request(
        self, request: TokenCountRequest
    ) -> RoutedTokenCountRequest:
        """Resolve routing and return a deep-copied request with ``model`` replaced.

        Mirrors :meth:`resolve_messages_request` for the token-count path.
        """
        resolved = self.resolve(request.model)
        routed = request.model_copy(
            update={"model": resolved.provider_model}, deep=True
        )
        return RoutedTokenCountRequest(request=routed, resolved=resolved)
