"""Gateway-safe model id encoding for Claude Code model discovery."""

from dataclasses import dataclass

GATEWAY_MODEL_ID_PREFIX = "anthropic"

# Claude Code currently treats any model id containing ``claude-3-`` as not
# supporting thinking. This intentionally uses that client-side capability
# heuristic while keeping the real provider/model ref reversible for routing.
NO_THINKING_GATEWAY_MODEL_ID_PREFIX = "claude-3-freecc-no-thinking"


@dataclass(frozen=True, slots=True)
class DecodedGatewayModelId:
    """Result of decoding a gateway model ID back to its provider/model pair.

    ``force_thinking_enabled`` is ``None`` when the ID carries no thinking
    preference (the caller should fall back to settings), or ``False`` when the
    no-thinking prefix was used to force it off.
    """

    provider_id: str
    provider_model: str
    force_thinking_enabled: bool | None = None


def gateway_model_id(provider_model_ref: str) -> str:
    """Return the normal Claude Code-discoverable id for a provider/model ref."""
    return f"{GATEWAY_MODEL_ID_PREFIX}/{provider_model_ref}"


def no_thinking_gateway_model_id(provider_model_ref: str) -> str:
    """Return a Claude Code-discoverable id that disables client thinking."""
    return f"{NO_THINKING_GATEWAY_MODEL_ID_PREFIX}/{provider_model_ref}"


def decode_gateway_model_id(model_name: str) -> DecodedGatewayModelId | None:
    """Decode a gateway model ID back to its provider/model pair.

    Returns ``None`` for any name not produced by :func:`gateway_model_id` or
    :func:`no_thinking_gateway_model_id`.  Expected structure:
    ``<gateway-prefix>/<provider_id>/<model_name>``.
    """
    prefix, separator, remainder = model_name.partition("/")
    if not separator:
        return None

    force_thinking_enabled: bool | None
    if prefix == GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = None
    elif prefix == NO_THINKING_GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = False
    else:
        return None

    provider_id, provider_separator, provider_model = remainder.partition("/")
    if not provider_separator or not provider_model:
        return None

    return DecodedGatewayModelId(
        provider_id=provider_id,
        provider_model=provider_model,
        force_thinking_enabled=force_thinking_enabled,
    )
