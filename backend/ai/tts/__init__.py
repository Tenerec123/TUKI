from .base import (
    TTSSession,
    TTSProvider,
    TTSTurnEnd,
    iter_wav_turns,
)
from .deepgram import DeepgramTTSProvider
from .openrouter import OpenRouterTTSProvider
from ..config import get_model_config

_PROVIDERS = {
    "openrouter": OpenRouterTTSProvider,
    "deepgram": DeepgramTTSProvider,
}


def get_tts_provider(provider_name: str | None = None) -> TTSProvider:
    """Return a TTS provider instance.

    ``provider_name`` defaults to the one stored in the DB, then to ``openrouter``.
    Raises ``ValueError`` for an unknown name.
    """
    if provider_name is None:
        provider_name = get_model_config().get('tts_provider', 'openrouter')
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise ValueError(
            f"Unknown TTS provider '{provider_name}'. "
            f"Available providers: {', '.join(sorted(_PROVIDERS))}"
        )
    return provider_cls()


__all__ = [
    "get_tts_provider",
    "TTSSession",
    "TTSProvider",
    "TTSTurnEnd",
    "iter_wav_turns",
]
