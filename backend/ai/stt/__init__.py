from collections.abc import AsyncIterable
import io
import wave

from .base import STTProvider
from .openrouter import OpenRouterSTTProvider
from .deepgram import DeepgramSTTProvider
from ..config import get_model_config_for
from ..credentials import get_provider_key
from ..voice import log as voicelog

_PROVIDERS = {
    "openrouter": OpenRouterSTTProvider,
    "deepgram": DeepgramSTTProvider,
}


def _is_configured(provider_name: str) -> bool:
    try:
        get_provider_key(provider_name)
    except Exception:
        return False
    return True


def get_available_stt_providers() -> list[str]:
    """Return selectable STT providers, in the default preference order."""
    return [name for name in ("deepgram", "openrouter") if _is_configured(name)]


def _transcript_ok(transcript: object) -> bool:
    """A transcript attempt only counts when it produced real text."""
    return isinstance(transcript, str) and bool(transcript.strip())


def _raise_no_transcript(names: list[str], last_error: Exception | None):
    """Every configured provider failed or returned no text.

    Give callers (chat HTTP answer, wake error event) an actionable message
    instead of an empty transcript that would be sent to the LLM as the
    user's prompt (S10).
    """
    message = (
        "No STT provider produced a transcript "
        f"(tried: {', '.join(names)})"
    )
    if last_error is not None:
        message = f"{message}; last provider error: {last_error}"
    voicelog.log(f"stt ! {message}")
    raise ValueError(message)


def _provider_order(provider_name: str | None) -> list[str]:
    available = get_available_stt_providers()
    if provider_name is None:
        configured = get_model_config_for("stt")[1]
        provider_name = configured if configured in available else None
    if provider_name is not None and provider_name not in _PROVIDERS:
        raise ValueError(
            f"Unknown STT provider '{provider_name}'. "
            f"Available providers: {', '.join(sorted(_PROVIDERS))}"
        )
    if provider_name is not None and provider_name not in available:
        raise ValueError(f'STT provider "{provider_name}" is not configured')
    return ([provider_name] if provider_name else []) + [
        name for name in available if name != provider_name
    ]


def get_stt_provider(provider_name: str | None = None) -> STTProvider:
    """Return a configured STT provider, honoring model selection and defaults."""
    order = _provider_order(provider_name)
    if not order:
        raise ValueError("No STT provider is configured")
    return _PROVIDERS[order[0]]()


async def transcribe_with_fallback(
    audio_data: bytes,
    provider_name: str | None = None,
    **kwargs,
) -> str:
    """Transcribe batch audio, retrying once with the other configured provider.

    An empty transcript is a failed attempt, not a success: the alternate
    provider is tried before giving up with a clear error (S10).
    """
    last_error: Exception | None = None
    returned_empty = False
    names = _provider_order(provider_name)
    for name in names:
        try:
            transcript = await get_stt_provider(name).transcribe(audio_data, **kwargs)
        except Exception as exc:
            last_error = exc
            continue
        if _transcript_ok(transcript):
            return transcript
        returned_empty = True
    if not returned_empty:
        # Preserve the original contract: surface the provider's own error.
        raise last_error or ValueError("No STT provider is configured")
    _raise_no_transcript(names, last_error)


async def transcribe_stream_with_fallback(
    chunks: AsyncIterable[bytes],
    provider_name: str | None = None,
    selected_provider: STTProvider | None = None,
    **kwargs,
) -> str:
    """Transcribe live audio while retaining a WAV-safe fallback copy."""
    buffered: list[bytes] = []

    async def recording_chunks() -> AsyncIterable[bytes]:
        async for chunk in chunks:
            if chunk:
                buffered.append(chunk)
                yield chunk

    last_error: Exception | None = None
    source_consumed = False
    try:
        names = _provider_order(provider_name)
    except Exception:
        if selected_provider is None:
            raise
        names = [provider_name or "deepgram"]
    if selected_provider is not None:
        names = [names[0], *[name for name in names[1:] if name != names[0]]]
    providers = zip(names, [selected_provider] + [None] * len(names)) if selected_provider else ((name, None) for name in names)
    returned_empty = False
    for name, supplied_provider in providers:
        provider = supplied_provider or get_stt_provider(name)
        try:
            if name == "openrouter":
                if not source_consumed:
                    async for chunk in chunks:
                        if chunk:
                            buffered.append(chunk)
                    source_consumed = True
                transcript = await provider.transcribe(
                    _pcm16_wav(b"".join(buffered)),
                    content_type="audio/wav",
                    **kwargs,
                )
            else:
                stream = (
                    _buffered_chunks(buffered)
                    if source_consumed
                    else recording_chunks()
                )
                transcript = await provider.transcribe_stream(stream, **kwargs)
        except Exception as exc:
            last_error = exc
            # A failed streaming provider may have stopped consuming the source.
            # Drain it so the fallback receives the complete utterance.
            if not source_consumed:
                async for chunk in chunks:
                    if chunk:
                        buffered.append(chunk)
                source_consumed = True
            continue
        if _transcript_ok(transcript):
            return transcript
        returned_empty = True
        # An empty success consumes no more source; make sure the fallback
        # receives the complete utterance.
        if not source_consumed:
            async for chunk in chunks:
                if chunk:
                    buffered.append(chunk)
            source_consumed = True
    if not returned_empty:
        # Preserve the original contract: surface the provider's own error.
        raise last_error or ValueError("No STT provider is configured")
    _raise_no_transcript(names, last_error)


async def _buffered_chunks(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


def _pcm16_wav(audio_data: bytes) -> bytes:
    """Wrap mono 16 kHz PCM16 audio in the container OpenRouter expects."""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(audio_data)
    return output.getvalue()


__all__ = [
    "get_available_stt_providers",
    "get_stt_provider",
    "transcribe_with_fallback",
    "transcribe_stream_with_fallback",
]
