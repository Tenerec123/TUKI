from .base import STTProvider
from .openrouter import OpenRouterSTTProvider
from .deepgram import DeepgramSTTProvider
from ..config import get_model_config

_PROVIDERS = {
    "openrouter": OpenRouterSTTProvider,
    "deepgram": DeepgramSTTProvider,
}


def get_stt_provider(provider_name: str | None = None) -> STTProvider:
    """Return the STT provider instance.

    Args:
        provider_name: Explicit provider to use ("openrouter" or "deepgram").
            When None, reads the ``stt_provider`` key from the model config
            (database-overridable, defaults to "openrouter").

    Returns:
        The requested STT provider instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if provider_name is None:
        provider_name = get_model_config().get('stt_provider', 'openrouter')
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise ValueError(
            f"Unknown STT provider '{provider_name}'. "
            f"Available providers: {', '.join(sorted(_PROVIDERS))}"
        )
    return provider_cls()


__all__ = [
    "get_stt_provider",
]