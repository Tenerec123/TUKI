"""Text-to-speech provider abstraction."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

# Mono / 16-bit linear PCM, shared by both providers so turns concatenate.
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2


@dataclass(frozen=True)
class TTSTurnEnd:
    """Marker emitted once a turn's audio has been fully delivered."""

    index: int
    chars: int = 0
    audio_ms: float = 0.0


class TTSSession(ABC):
    """One duplex session: ``start()`` -> (``push_text()``/``flush()``/``clear()``)* -> ``close()``, with ``audio()`` consumed concurrently throughout."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Rate of the linear16 PCM this session emits, fixed at connect. Only the provider knows it, so anything framing audio must read it here."""

    @abstractmethod
    async def start(self) -> None:
        """Open the connection. Must be awaited before the first push."""

    @abstractmethod
    async def push_text(self, text: str) -> None:
        """Queue ``text`` for synthesis. Never blocks on playback."""

    @abstractmethod
    async def flush(self) -> None:
        """Ask the provider to emit audio for everything buffered so far."""

    @abstractmethod
    async def clear(self) -> None:
        """Discard buffered text without emitting audio (barge-in)."""

    @abstractmethod
    async def audio(self) -> AsyncIterator[bytes | TTSTurnEnd]:
        """Yield linear16 PCM chunks plus a ``TTSTurnEnd`` per turn, until close."""

    @abstractmethod
    async def close(self) -> None:
        """End the session and release the connection."""


class TTSProvider(ABC):
    """Abstract base class for text-to-speech providers."""

    @abstractmethod
    def session(self) -> TTSSession:
        """Return a NEW, unstarted session. One per conversation: both providers bind voice and media settings at connect and cannot change them after."""


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw linear16 PCM in a 44-byte WAV header.

    The rate is passed in, never assumed: a wrong rate plays at the wrong pitch. The ESP32 skips the header and feeds samples to I2S; the browser needs a container.
    """
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(TTS_CHANNELS)
        wav_file.setsampwidth(TTS_SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


async def iter_wav_turns(session: TTSSession) -> AsyncIterator[bytes]:
    """Re-frame a session's continuous PCM into one WAV per turn.

    The rate comes from ``session.sample_rate``, never from the caller. The client contract is one self-contained WAV per WebSocket frame (``frontend/wake_script.js`` turns every binary frame into an ``<audio>`` blob) while the providers stream bare PCM, and this is the only bridge, so the router and frontend stay untouched.

    Cost: a turn is held until its marker, so N+1 cannot play before N ends. Turns are one sentence each, so that buffer is a sentence of audio, not a whole reply.
    """
    buffer = bytearray()
    rate = session.sample_rate
    async for item in session.audio():
        if isinstance(item, TTSTurnEnd):
            if buffer:
                yield wav_bytes(bytes(buffer), rate)
                buffer.clear()
        else:
            buffer.extend(item)
    # An unmarked turn (socket dropped mid-stream) must still reach the client.
    if buffer:
        yield wav_bytes(bytes(buffer), rate)
