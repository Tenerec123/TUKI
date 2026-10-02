"""Deepgram Aura text-to-speech over a streaming WebSocket.

Replaces the batch Kokoro synthesis that used to sit at the end of the voice pipeline: TTS was the ONLY non-streaming link, so playback could not start until a whole phrase was synthesized (measured: p1_synth up to 4257 ms). Aura takes text in and streams audio out, so audio arrives while the LLM is still writing the reply.

Protocol (verified against the Deepgram streaming TTS docs): ``wss://api.deepgram.com/v1/speak?model=...&encoding=linear16&sample_rate=...`` with ``Authorization: Token <DEEPGRAM_API_KEY>`` (the STT provider's env var). Client -> server: ``Speak``/``Flush``/``Clear``/``Close``. BINARY frames back are raw linear16 PCM -- the endpoint emits RAW audio, so no ``container`` parameter is accepted on this transport. JSON events: ``Flushed``, ``Metadata``, ``Cleared``, ``Close``, ``Warning``, ``Error``.

Turn ordering was MEASURED against Aura v1 (/v1/speak); the newer Flux docs do not match.

  * A ``Speak`` on its own produces NO audio. Synthesis does not begin until a ``Flush`` arrives, which is why the pipeline flushes per sentence.
  * ``Flushed`` arrives AFTER that turn's binary frames and carries the sequence number, so it is the turn boundary and is emitted as ``TTSTurnEnd``.
  * ``Metadata`` is connection-level (one event near connect: ``request_id``, model name/version/UUID) with no per-turn audio or character counts, so it cannot mark a boundary. ``SpeechMetadata`` is Flux behavior, never seen on this endpoint.
  * Rapid successive Flushes can coalesce into one turn, and audio can arrive after ``Close`` with no trailing ``Flushed``. The trailing buffer is framed as a final turn.

Getting this backwards is not subtle: awaiting ``Metadata`` as the boundary stalls forever, and treating a pre-audio ``Flushed`` as the boundary emits a 44-byte empty turn followed by audio that never gets framed.

Hard limits: 2000 characters per ``Speak`` (more is a 4xx that loses the rest of the reply); 2400 characters per minute at the socket (normal TUKI speech is ~900 chars/min, so a long LLM burst can exceed it and the audio is throttled); 20 ``Flush`` per 60 s, after which Deepgram warns and IGNORES flushes. Measured, not assumed.
"""

import asyncio
import json
import os
import time
from typing import AsyncIterator
from urllib.parse import urlencode

import websockets

from .base import TTSSession, TTSProvider, TTSTurnEnd

DEEPGRAM_TTS_WS_URL = "wss://api.deepgram.com/v1/speak"

# Argentine Spanish: the user is Rioplatense. Overridable without a code change.
DEFAULT_VOICE = os.environ.get("TTS_DEEPGRAM_VOICE", "aura-2-antonia-es")

AUDIO_SAMPLE_RATE = 24000

# Deepgram: 2000 characters per Speak message (Aura-1 and Aura-2 alike).
SPEAK_CHUNK_LIMIT = 2000

# Deepgram: 2400 characters per minute at the WebSocket.
THROUGHPUT_LIMIT_CHARS_PER_MIN = 2400

# Deepgram: 20 Flush per 60s, then flushes are ignored until the window resets.
FLUSH_LIMIT_PER_WINDOW = 20
_FLUSH_LIMIT_SECONDS = 60.0

# The queue between socket reader and consumer is unbounded ON PURPOSE: the producer (LLM tokens) must never block on a slow playback client.
_QUEUE_MAXSIZE = 0


def split_for_speak(text: str, limit: int = SPEAK_CHUNK_LIMIT) -> list[str]:
    """Split ``text`` into ``Speak``-sized pieces, preferring a word boundary.

    A hard cut mid-word would be spoken as two words, so the cut moves back to the last space when one is close enough to the limit. That space STAYS at the end of the left piece: Deepgram concatenates consecutive ``Speak`` messages before synthesizing, so dropping it welds the halves into one word ("larga" + "Esta" heard as "largaEsta"). The concatenation of the result always equals the input.
    """
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        space = window.rfind(" ")
        # +1 keeps the space on the left piece; a hard cut has none, so it stays at limit.
        cut = space + 1 if space >= limit // 2 else limit
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


class DeepgramTTTSession(TTSSession):
    """One duplex Aura session. Voice and media settings are fixed at connect."""

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        sample_rate: int = AUDIO_SAMPLE_RATE,
    ) -> None:
        self._voice = voice
        self._sample_rate = sample_rate
        self._ws = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._pump: asyncio.Task | None = None
        self._closed = False
        # Instrumentation.
        self.connect_ms = 0.0
        self.chars_sent = 0
        self.flushes = 0
        self.bytes_audio = 0
        self.warnings: list[str] = []
        self.turns = 0
        self.first_audio_s: float | None = None
        self.t_first_push: float | None = None
        self.request_id: str | None = None
        self.model_version: str | None = None
        self._send_windows: list[tuple[float, int]] = []
        self._flush_windows: list[float] = []
        self._flush_pending = False
        self._flush_defer_logged = False
        self._flush_task: asyncio.Task | None = None
        # Aura v1 reports no per-turn audio or character counts, so both are measured locally from what was sent and received.
        self._turn_bytes = 0
        self._turn_chars = 0

    # --- lifecycle ----------------------------------------------------------

    @property
    def sample_rate(self) -> int:
        """The rate sent in the connect query, so the rate the socket emits."""
        return self._sample_rate

    async def start(self) -> None:
        api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable not set")
        params = {
            "model": self._voice,
            "encoding": "linear16",
            "sample_rate": str(self._sample_rate),
        }
        url = f"{DEEPGRAM_TTS_WS_URL}?{urlencode(params)}"
        headers = {"Authorization": f"Token {api_key}"}
        print(f"[deepgram-tts] connecting to {url}")

        t0 = time.perf_counter()
        self._ws = await websockets.connect(url, additional_headers=headers)
        self.connect_ms = (time.perf_counter() - t0) * 1000
        print(f"[deepgram-tts] connected voice={self._voice} "
              f"rate={self._sample_rate} connect={self.connect_ms:.1f}ms")
        self._pump = asyncio.create_task(self._read())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._ws is not None:
                await self._ws.send(json.dumps({"type": "Close"}))
                # Deepgram drains what it has buffered before the socket goes away, and this is the only chance to get the tail of the last sentence. Ten seconds covers a normal reply; a larger backlog is reported, not silently cut.
                try:
                    await asyncio.wait_for(asyncio.shield(self._pump), timeout=10.0)
                except asyncio.TimeoutError:
                    print(
                        "[deepgram-tts] close timed out with audio still buffered; "
                        "the tail of the reply may be truncated"
                    )
        except asyncio.CancelledError:
            # A client disconnect cancels mid-close; the teardown below still has to run or the socket leaks.
            raise
        except Exception as exc:
            print(f"[deepgram-tts] close error: {type(exc).__name__}: {exc}")
        finally:
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            if self._pump is not None and not self._pump.done():
                self._pump.cancel()
            if self._flush_task is not None and not self._flush_task.done():
                self._flush_task.cancel()
            # The end-of-stream sentinel MUST be emitted even when start() failed and there is no reader task to put it: without it a connect failure leaves iter_wav_turns() awaiting an empty queue forever, so the request HANGS instead of returning an error. put_nowait cannot block here.
            self._queue.put_nowait(None)

    # --- duplex surface -----------------------------------------------------

    async def push_text(self, text: str) -> None:
        if not text.strip():
            return
        # A HELD flush is drained first, so a deferred Flush can never drag the following sentence into the previous turn.
        await self._drain_flush()
        if self.t_first_push is None:
            self.t_first_push = time.perf_counter()
        for piece in split_for_speak(text):
            await self._ws.send(json.dumps({"type": "Speak", "text": piece}))
            self._track_sent(len(piece))
            self._turn_chars += len(piece)

    async def flush(self) -> None:
        """Ask for a Flush.

        Deepgram allows 20 per 60 s and then ignores them, so a request made inside a spent window is held in ``_flush_pending`` and sent by ``_schedule_flush_window`` the moment the window rolls.
        """
        self._flush_pending = True
        await self._drain_flush()

    async def clear(self) -> None:
        """Discard buffered text (barge-in). Deepgram emits nothing for it."""
        self._flush_pending = False
        await self._ws.send(json.dumps({"type": "Clear"}))

    async def _drain_flush(self) -> None:
        if not self._flush_pending:
            return
        self._prune_flush_window()
        if len(self._flush_windows) >= FLUSH_LIMIT_PER_WINDOW:
            if not self._flush_defer_logged:
                self._flush_defer_logged = True
                print(
                    f"[deepgram-tts] flush budget spent "
                    f"({FLUSH_LIMIT_PER_WINDOW} per {_FLUSH_LIMIT_SECONDS:.0f}s); "
                    f"holding the flush until the window resets"
                )
            self._schedule_flush_window()
            return
        self._flush_pending = False
        self._flush_defer_logged = False
        await self._ws.send(json.dumps({"type": "Flush"}))
        self.flushes += 1
        self._flush_windows.append(time.perf_counter())

    def _prune_flush_window(self) -> None:
        cutoff = time.perf_counter() - _FLUSH_LIMIT_SECONDS
        while self._flush_windows and self._flush_windows[0] < cutoff:
            self._flush_windows.pop(0)

    def _schedule_flush_window(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        wait = self._flush_windows[0] + _FLUSH_LIMIT_SECONDS - time.perf_counter()
        self._flush_task = asyncio.create_task(self._flush_after_window(max(0.0, wait)))

    async def _flush_after_window(self, wait: float) -> None:
        try:
            await asyncio.sleep(wait)
            self._flush_task = None
            await self._drain_flush()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[deepgram-tts] held flush failed: {type(exc).__name__}: {exc}")

    async def audio(self) -> AsyncIterator[bytes | TTSTurnEnd]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    # --- instrumentation ----------------------------------------------------

    def _track_sent(self, chars: int) -> None:
        now = time.perf_counter()
        self.chars_sent += chars
        self._send_windows.append((now, chars))
        cutoff = now - 60.0
        while self._send_windows and self._send_windows[0][0] < cutoff:
            self._send_windows.pop(0)

    def chars_per_minute(self) -> int:
        return sum(chars for _, chars in self._send_windows)

    def report(self) -> None:
        rate = self.chars_per_minute()
        cap = THROUGHPUT_LIMIT_CHARS_PER_MIN
        pct = 100.0 * rate / cap if cap else 0.0
        over = " OVER-CAP" if rate > cap else ""
        ttfb = -1.0
        if self.first_audio_s is not None and self.t_first_push is not None:
            ttfb = (self.first_audio_s - self.t_first_push) * 1000
        print(
            f"[PERF] tts_deepgram: connect={self.connect_ms:.1f}ms "
            f"ttfb={ttfb:.1f}ms turns={self.turns} flushes={self.flushes} "
            f"chars_sent={self.chars_sent} model={self.model_version} "
            f"chars_min={rate}/{cap} ({pct:.0f}%){over} "
            f"bytes={self.bytes_audio} "
            f"audio_ms={self.bytes_audio / 2 / self._sample_rate * 1000:.1f}"
        )
        for warning in self.warnings:
            print(f"[deepgram-tts] Warning event: {warning}")

    # --- socket reader ------------------------------------------------------

    async def _read(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, (bytes, bytearray)):
                    if self.first_audio_s is None:
                        self.first_audio_s = time.perf_counter()
                    self.bytes_audio += len(message)
                    self._turn_bytes += len(message)
                    await self._queue.put(bytes(message))
                    continue
                await self._handle_event(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[deepgram-tts] read error: {exc}")
        finally:
            await self._queue.put(None)

    async def _handle_event(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[deepgram-tts] non-JSON text frame: {raw[:120]!r}")
            return
        msg_type = data.get("type")

        if msg_type == "Metadata":
            # CONNECTION-LEVEL, not per turn: MEASURED on Aura v1, it fires once after the socket opens with only request_id and model name/version/UUID -- no audio_duration_ms, no character counts. Emitting a turn here would collapse the whole reply into one turn, the exact batching this swap removes.
            self.request_id = data.get("request_id")
            self.model_version = data.get("model_version")
            print(f"[deepgram-tts] Metadata request_id={self.request_id} "
                  f"model={data.get('model_name')}@{self.model_version}")
            return

        if msg_type == "Flushed":
            # THE turn boundary. MEASURED: the last binary frame of a turn and this event share a timestamp, so the turn is fully delivered. Also the only thing that STARTS synthesis on Aura v1 -- a Speak with no Flush produces no audio at all.
            self.turns += 1
            audio_ms = self._turn_bytes / 2 / self._sample_rate * 1000
            self._turn_bytes = 0
            print(
                f"[deepgram-tts] turn={self.turns} seq={data.get('sequence_id')} "
                f"audio_ms={audio_ms:.0f}"
            )
            await self._queue.put(TTSTurnEnd(
                index=self.turns,
                chars=self._turn_chars,
                audio_ms=audio_ms,
            ))
            self._turn_chars = 0
            return

        if msg_type == "Cleared":
            # Barge-in ack. The discarded text produced no audio, so no turn boundary is emitted and the buffer resets.
            self._turn_bytes = 0
            self._turn_chars = 0
            print("[deepgram-tts] Cleared (buffered text discarded)")
            return

        if msg_type == "Close":
            print("[deepgram-tts] server Close")
            return

        if msg_type == "Warning":
            # Rate-limit and throughput warnings land here. Not fatal, but they explain stalled or throttled audio, so they are surfaced.
            detail = (
                f"code={data.get('warn_code')} variant={data.get('variant')} "
                f"msg={data.get('warn_msg')} desc={data.get('description')}"
            )
            self.warnings.append(detail)
            print(f"[deepgram-tts] WARNING {detail}")
            return

        if msg_type == "Error":
            detail = f"{data.get('description')} ({data.get('variant')})"
            self.warnings.append(f"Error {detail}")
            print(f"[deepgram-tts] ERROR {detail}")
            return

        print(f"[deepgram-tts] unhandled event type={msg_type}")


class DeepgramTTSProvider(TTSProvider):
    """Streaming TTS provider backed by Deepgram Aura over a WebSocket."""

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        sample_rate: int = AUDIO_SAMPLE_RATE,
    ) -> None:
        self._voice = voice
        self._sample_rate = sample_rate

    def session(self) -> DeepgramTTTSession:
        return DeepgramTTTSession(voice=self._voice, sample_rate=self._sample_rate)
