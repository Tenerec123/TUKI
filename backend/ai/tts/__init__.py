from typing import AsyncIterator

from .base import (
    TTSSession,
    TTSProvider,
    TTSTurnEnd,
    iter_wav_turns,
)
from .deepgram import DeepgramTTSProvider
from .openrouter import OpenRouterTTSProvider
from ..config import get_app_setting
from ..credentials import ProviderCredentialError, get_provider_key

_PROVIDERS = {
    "openrouter": OpenRouterTTSProvider,
    "deepgram": DeepgramTTSProvider,
}


class FailoverTTSProvider(TTSProvider):
    """Try the preferred configured provider, then the other configured one."""

    def __init__(
        self,
        primary_name: str,
        primary: TTSProvider,
        fallback_name: str,
        fallback: TTSProvider,
    ) -> None:
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self._primary = primary
        self._fallback = fallback

    def session(self) -> TTSSession:
        return FailoverTTSSession(
            self.primary_name,
            self._primary.session(),
            self.fallback_name,
            self._fallback.session(),
        )


class FailoverTTSSession(TTSSession):
    """Keep a turn retryable until its selected session emits audio."""

    def __init__(
        self,
        primary_name: str,
        primary: TTSSession,
        fallback_name: str,
        fallback: TTSSession,
    ) -> None:
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self._active = primary
        self._fallback = fallback
        self._pending_turns: list[list[str]] = []
        self._current_turn: list[str] = []
        self._turn_has_audio = False
        self._fallback_used = False
        self._closed = False

    @property
    def sample_rate(self) -> int:
        return self._active.sample_rate

    async def _switch(self, error: Exception, flush_turns: bool = False) -> None:
        if self._fallback_used or self._turn_has_audio:
            raise error
        self._fallback_used = True
        try:
            await self._active.close()
        except Exception:
            pass
        self._active = self._fallback
        await self._active.start()
        turns = [*self._pending_turns]
        if self._current_turn:
            turns.append(self._current_turn)
        for turn in turns:
            for text in turn:
                await self._active.push_text(text)
            if flush_turns:
                await self._active.flush()

    async def start(self) -> None:
        try:
            await self._active.start()
        except Exception as error:
            await self._switch(error)

    async def push_text(self, text: str) -> None:
        if not text.strip():
            return
        self._current_turn.append(text)
        try:
            await self._active.push_text(text)
        except Exception as error:
            await self._switch(error)

    async def flush(self) -> None:
        if self._current_turn:
            self._pending_turns.append(self._current_turn)
            self._current_turn = []
        try:
            await self._active.flush()
        except Exception as error:
            await self._switch(error, flush_turns=True)
        # Keep the turn until audio() observes TTSTurnEnd. A successful flush
        # only requests synthesis; it does not prove that the turn was delivered.
        self._turn_has_audio = False

    def _retire_marked_turns(self, marker: TTSTurnEnd) -> None:
        """Remove every queued turn represented by one provider marker."""
        remaining = marker.chars
        retired = False
        while self._pending_turns and remaining:
            turn_chars = sum(len(text) for text in self._pending_turns[0])
            if remaining < turn_chars:
                break
            self._pending_turns.pop(0)
            retired = True
            remaining -= turn_chars
        # Some providers omit character counts. Retire one turn in that case,
        # and tolerate test/provider normalization differences below one turn.
        if self._pending_turns and not retired:
            self._pending_turns.pop(0)

    async def clear(self) -> None:
        self._pending_turns.clear()
        self._current_turn.clear()
        self._turn_has_audio = False
        try:
            await self._active.clear()
        except Exception as error:
            await self._switch(error)
            await self._active.clear()

    async def audio(self) -> AsyncIterator[bytes | TTSTurnEnd]:
        """Emit only complete turns, retrying an interrupted provider read.

        Provider bytes remain private until their ``TTSTurnEnd`` arrives. This
        makes a reader error or an early EOF transactional: partial primary
        audio is discarded and the buffered sentence is replayed once through
        the fallback session.
        """
        buffered = bytearray()
        while True:
            active = self._active
            try:
                async for item in active.audio():
                    if isinstance(item, bytes):
                        buffered.extend(item)
                        continue
                    if isinstance(item, TTSTurnEnd):
                        if buffered:
                            yield bytes(buffered)
                            buffered.clear()
                        self._retire_marked_turns(item)
                        self._turn_has_audio = True
                        yield item
                        self._turn_has_audio = False
                if self._closed:
                    if buffered:
                        yield bytes(buffered)
                    return
                if not buffered and not self._pending_turns and not self._current_turn:
                    return
                error = RuntimeError(
                    f"{getattr(active, '__class__', type(active)).__name__} "
                    "ended before the TTS turn marker"
                )
                if not self._fallback_used:
                    await self._switch(error, flush_turns=True)
                else:
                    raise error
                buffered.clear()
                continue
            except Exception as error:
                if self._closed:
                    raise
                if not buffered and not self._pending_turns and not self._current_turn:
                    raise
                if not self._fallback_used:
                    await self._switch(error, flush_turns=True)
                else:
                    raise
                buffered.clear()

    async def close(self) -> None:
        self._closed = True
        await self._active.close()


def _has_tts_key(provider_name: str) -> bool:
    try:
        get_provider_key(provider_name)
    except ProviderCredentialError:
        return False
    return True


def get_tts_provider(provider_name: str | None = None) -> TTSProvider:
    """Return the configured TTS provider with a key-backed failover.

    Deepgram is the default when available; OpenRouter is the fallback. The
    ``tts_provider`` application setting may prefer either configured provider.
    """
    explicitly_requested = provider_name is not None
    if provider_name is None:
        provider_name = get_app_setting('tts_provider')
    if provider_name is not None and not explicitly_requested and not _has_tts_key(provider_name):
        provider_name = None
    if provider_name is None:
        provider_name = "deepgram" if _has_tts_key("deepgram") else "openrouter"

    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise ValueError(
            f"Unknown TTS provider '{provider_name}'. "
            f"Available providers: {', '.join(sorted(_PROVIDERS))}"
        )
    if not _has_tts_key(provider_name):
        raise ProviderCredentialError(
            f'API key for provider "{provider_name}" is not configured'
        )

    other_name = "openrouter" if provider_name == "deepgram" else "deepgram"
    if not _has_tts_key(other_name):
        return provider_cls()
    return FailoverTTSProvider(
        provider_name,
        provider_cls(),
        other_name,
        _PROVIDERS[other_name](),
    )


__all__ = [
    "get_tts_provider",
    "FailoverTTSProvider",
    "FailoverTTSSession",
    "TTSSession",
    "TTSProvider",
    "TTSTurnEnd",
    "iter_wav_turns",
]
