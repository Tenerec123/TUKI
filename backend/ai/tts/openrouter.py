"""OpenRouter (Kokoro) text-to-speech — the reversible fallback.

The batch provider the pipeline used before the Deepgram swap, kept behind the same ``TTSSession`` interface so switching back is a config change, not a rewrite.

DEGRADED, not broken: Kokoro has no streaming transport, so a whole buffer must be synthesized before ANY audio for it exists. ``push_text`` accumulates and ``flush`` makes one request -- exactly the latency shape the swap removed. Everything the pipeline needs is provided, so this is a drop-in.
"""

import asyncio
import io
import os
import re
import time
import wave
from typing import AsyncIterator

from openai import AsyncOpenAI

from .base import (
    TTSSession,
    TTSProvider,
    TTSTurnEnd,
    TTS_CHANNELS,
    TTS_SAMPLE_WIDTH,
)

TTS_MODEL = "hexgrad/kokoro-82m"
TTS_VOICE = "ef_dora"  # Spanish female. Alternatives: em_alex, em_santa (Spanish male)

# Kokoro's native output rate; no other rate is requested from this provider.
_KOKORO_SAMPLE_RATE = 24000

_client: AsyncOpenAI | None = None


def _get_client() -> tuple[AsyncOpenAI, float, bool]:
    """Return the client plus its (ctor_ms, cold_start) cost.

    Wrapper construction only: httpx connects lazily, so DNS/TCP/TLS land in the first request and are reported as ``http``, not here.
    """
    global _client
    if _client is None:
        t0 = time.perf_counter()
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        ctor_ms = (time.perf_counter() - t0) * 1000
        print(f"[PERF] tts: client_ctor ctor={ctor_ms:.1f}ms cold_start=yes")
        return _client, ctor_ms, True
    return _client, 0.0, False


def _wrap_wav(pcm: bytes, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(TTS_CHANNELS)
        wav_file.setsampwidth(TTS_SAMPLE_WIDTH)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


async def synthesize_pcm(text: str) -> tuple[bytes, int]:
    """Synthesize speech and return raw PCM plus its sample rate.

    Raw PCM (lowest latency); the caller decides on the container. There is no final-sibilant workaround any more: it existed only for a Kokoro bug Aura-2 does not have, and the markdown that fed it is now stripped in ``sanitize.py``.
    """
    t_call = time.perf_counter()
    client, ctor_ms, cold = _get_client()
    t_http = time.perf_counter()
    resp = await client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        response_format="pcm",
    )
    http_ms = (time.perf_counter() - t_http) * 1000
    pcm = resp.content

    rate = _KOKORO_SAMPLE_RATE
    try:
        content_type = resp.response.headers.get("content-type", "")
        match = re.search(r"rate=(\d+)", content_type)
        if match:
            rate = int(match.group(1))
    except Exception:
        pass

    frames = len(pcm) // (TTS_SAMPLE_WIDTH * TTS_CHANNELS)
    audio_ms = frames / rate * 1000
    print(
        f"[PERF] tts: synth cold={'yes' if cold else 'no'} "
        f"client_ctor={ctor_ms:.1f}ms http={http_ms:.1f}ms "
        f"synth_ms={(time.perf_counter() - t_call) * 1000:.1f}ms "
        f"rate={rate} frames={frames} pcm_bytes={len(pcm)} audio={audio_ms:.1f}ms"
    )
    return pcm, rate


async def text_to_speech_wav(text: str) -> bytes:
    """Synthesize speech with OpenRouter TTS and return WAV bytes."""
    pcm, rate = await synthesize_pcm(text)
    return _wrap_wav(pcm, rate)


class OpenRouterTTTSession(TTSSession):
    """Batch synthesis wrapped in the duplex session interface.

    No socket and no real duplex: text accumulates in a buffer and a flush turns the whole buffer into one WAV. Same interface, so the pipeline cannot tell providers apart.
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self.chars_sent = 0
        self.billable_chars = 0
        self.flushes = 0
        self.bytes_audio = 0
        self.turns = 0
        self.connect_ms = 0.0
        self._sample_rate = _KOKORO_SAMPLE_RATE
        self.first_audio_s: float | None = None
        self.t_first_push: float | None = None
        self._pending: asyncio.Queue | None = None
        self._closed = False

    @property
    def sample_rate(self) -> int:
        """The rate the provider actually returned: Kokoro's native rate, or whatever the response headers reported on the first flush."""
        return self._sample_rate

    async def start(self) -> None:
        t0 = time.perf_counter()
        self._pending = asyncio.Queue()
        self.connect_ms = (time.perf_counter() - t0) * 1000
        print("[openrouter-tts] session ready (batch: no connect cost to hide)")

    async def push_text(self, text: str) -> None:
        if not text.strip():
            return
        if self.t_first_push is None:
            self.t_first_push = time.perf_counter()
        self._buffer.append(text)
        self.chars_sent += len(text)

    async def flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        if self._pending is None:
            raise RuntimeError("flush() called before start()")
        self.flushes += 1
        pcm, rate = await synthesize_pcm(text)
        self._sample_rate = rate
        if self.first_audio_s is None:
            self.first_audio_s = time.perf_counter()
        self.bytes_audio += len(pcm)
        self.turns += 1
        self.billable_chars += len(text)
        await self._pending.put(pcm)
        await self._pending.put(TTSTurnEnd(
            index=self.turns,
            chars=len(text),
            audio_ms=len(pcm) / (TTS_SAMPLE_WIDTH * TTS_CHANNELS) / rate * 1000,
        ))

    async def clear(self) -> None:
        self._buffer.clear()

    async def audio(self) -> AsyncIterator[bytes | TTSTurnEnd]:
        # Tolerant of a missing start(): the queue is created lazily so a connect or ordering failure ends the consumer's iterator instead of raising inside the pipeline's async-for.
        if self._pending is None:
            self._pending = asyncio.Queue()
        while True:
            item = await self._pending.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._buffer.clear()
        if self._closed:
            return
        self._closed = True
        # Always signal end-of-stream, even if start() never ran: close() is called from a finally block, so it must terminate the consumer rather than assume a healthy session. The queue is created HERE and stored on self, so a later audio() call reads the same queue instead of a fresh one that never sees the sentinel.
        if self._pending is None:
            self._pending = asyncio.Queue()
        self._pending.put_nowait(None)

    def report(self) -> None:
        ttfb = -1.0
        if self.first_audio_s is not None and self.t_first_push is not None:
            ttfb = (self.first_audio_s - self.t_first_push) * 1000
        print(
            f"[PERF] tts_openrouter: ttfb={ttfb:.1f}ms turns={self.turns} "
            f"flushes={self.flushes} chars_sent={self.chars_sent} "
            f"bytes={self.bytes_audio} "
            f"audio_ms={self.bytes_audio / 2 / self.sample_rate * 1000:.1f}"
        )


class OpenRouterTTSProvider(TTSProvider):
    """Batch TTS provider using OpenRouter's hosted Kokoro."""

    def session(self) -> OpenRouterTTTSession:
        return OpenRouterTTTSession()
