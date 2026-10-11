import asyncio
import contextlib
import io
import time
import wave
from typing import AsyncIterable, AsyncIterator

from .stt import get_stt_provider
from .agent import openai_agent
from .voice_router import route_fast_path
from .config import get_model_config, AUDIO_SYSTEM_PROMPT
from .tools.discovery import ORCHESTRATOR_TOOL_SCHEMAS
from .tts import TTSSession, get_tts_provider, iter_wav_turns, TTSTurnEnd
from .tts.sanitize import MarkdownSanitizer, sanitize_chunk_report, sanitize_stream_report
from . import voicelog


def _wav_duration_ms(wav_bytes: bytes) -> float:
    """Return the playable duration of a WAV payload in ms.

    Reads the container header only, so the cost does not depend on payload length.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            return reader.getnframes() / reader.getframerate() * 1000
    except Exception:
        return 0.0


_WAKE_TOKENS = ("alexa", "rexa")


def _strip_wake_word(transcript: str) -> str:
    """Drop a leading wake-word token from the transcript.

    Defense in depth: the client no longer ships its pre-roll ring (the wake
    word lives there), but a user speaking over the wake word, or a frame
    captured while the socket was connecting, can still put "alexa" first.
    A wake word alone is not a command, so stripping it here also routes
    wake-word-only detections to the empty-turn path.
    """
    parts = transcript.strip().split()
    if not parts:
        return transcript
    first = parts[0].strip(".,;:!?¿¡").lower()
    if first in _WAKE_TOKENS:
        return " ".join(parts[1:]).strip()
    return transcript


def _perceived_anchor(perf: dict) -> float:
    t_client_stop = perf.get("t_client_stop")
    if t_client_stop is not None:
        lag_s = perf.get("client_lag_ms", 0) or 0
        try:
            lag_s = float(lag_s) / 1000.0
        except (TypeError, ValueError):
            lag_s = 0.0
        return t_client_stop - lag_s
    return perf["t_req"]


def _print_empty_turn(perf: dict) -> None:
    """One-line block for a turn that produced no speech."""
    anchor = _perceived_anchor(perf)
    lag_ms = perf.get("client_lag_ms", 0) or 0
    try:
        lag_s = float(lag_ms) / 1000.0
    except (TypeError, ValueError):
        lag_s = 0.0
    voicelog.log("---- turn ----")
    voicelog.log('heard    ""')
    if lag_s:
        voicelog.log(f"vad      client wait {lag_s:.2f}s  [silence + queue drain]")
    voicelog.log(f"total    {time.perf_counter() - anchor:.2f}s  (empty transcript)")


def _print_voice_summary(perf: dict, stats: dict, session: TTSSession) -> None:
    """Emit the per-turn block: one line per step, top-down, all durations in seconds.

    ``perf`` durations are milliseconds except the raw perf_counter anchors
    (``t_req``/``t_client_stop``/``first_audio``), which are seconds. The
    perceived timeline anchors at ``t_client_stop`` (when the user stopped
    talking) when the router provides it, else at ``t_req``.
    """
    t_end = time.perf_counter()
    first = stats.get("first_audio")
    anchor = _perceived_anchor(perf)
    speech_to_audio = (first - anchor) if first is not None else None
    timing = perf.get("stt_timing", {})
    hidden = "yes" if perf.get("connect_overlapped") else "no"

    lag_ms = perf.get("client_lag_ms", 0) or 0
    try:
        lag_s = float(lag_ms) / 1000.0
    except (TypeError, ValueError):
        lag_s = 0.0

    voicelog.log("---- turn ----")
    voicelog.log(f'heard    "{perf.get("transcript", "")}"')
    if lag_s:
        voicelog.log(f"vad      client wait {lag_s:.2f}s  [silence + queue drain]")
    voicelog.log(
        f"stt      {perf['stt'] / 1000:.2f}s  "
        f"[connect {timing.get('connect', 0.0) / 1000:.2f} | "
        f"listen {timing.get('listen', 0.0) / 1000:.2f} | "
        f"finalize {timing.get('finalize', 0.0) / 1000:.2f}]"
    )

    if perf.get("fast_path"):
        confidence = perf.get("confidence")
        conf_s = "?" if confidence is None else f"{confidence:.2f}"
        voicelog.log(
            f"intent   {perf.get('intent', '?')}  conf {conf_s}  "
            f"(+{perf.get('intent_ms', -1.0) / 1000:.2f}s)"
        )
    else:
        voicelog.log(
            f"agent    first text +{perf.get('first_text_ms', -1.0) / 1000:.2f}s  "
            f"[connect ready: {hidden}]"
        )

    voicelog.log(
        f"tts      open {getattr(session, 'connect_ms', 0.0) / 1000:.2f}s  "
        f"[hidden under stt: {hidden}]  ttfb {stats.get('ttfb', -1.0) / 1000:.2f}s"
    )

    if perf.get("fast_path"):
        voicelog.log(
            f'said     "{perf.get("say_text", "")}"  '
            f"audio {stats.get('audio_ms', 0.0) / 1000:.2f}s"
        )
    else:
        voicelog.log(
            f"said     (llm) {int(stats.get('turns', 0))} turn(s)  "
            f"audio {stats.get('audio_ms', 0.0) / 1000:.2f}s"
        )

    s2a = "n/a" if speech_to_audio is None else f"{speech_to_audio:.2f}s"
    voicelog.log(f"total    {t_end - anchor:.2f}s  speech->audio {s2a}")
    with contextlib.suppress(Exception):
        session.report()


async def voice_agent_logic(
    stream: AsyncIterable[bytes],
    streaming_path: bool = False,
    client_meta: dict | None = None,
) -> AsyncIterator[dict | bytes]:
    """Stream the agent's reply. Supports both legacy WAV-per-turn and PCM streaming.

    Pipeline: STT (utterance) -> LLM streaming (openai_agent) -> markdown sanitizer -> sentence splitter -> duplex TTS session.

    ``client_meta`` optionally carries the timestamp (perf_counter) at which the
    client stopped talking (the router records it when the end frame arrives,
    after the client's VAD trailing silence) and the client-reported lag. When
    present, the turn log's ``total``/``speech->audio`` measure the perceived
    timeline as ``t_client_stop - client_lag_ms/1000`` (true end of speech).

    Paths:
    - Legacy (streaming_path=False): yields bytes as WAV per turn via iter_wav_turns.
    - Streaming (streaming_path=True): yields dict {"type":"pcm_start","sample_rate":<int>} before first PCM chunk,
      then yields bytes chunks of raw PCM16. TTSTurnEnd markers are used for accounting/logging.
    """
    # Real-time streaming STT: the Deepgram WebSocket opens first, then each raw PCM16 (16 kHz mono) chunk is forwarded as it arrives. No WAV wrapper, no buffering.
    perf: dict = {"t_req": time.perf_counter()}

    # Open the TTS socket NOW, in parallel with STT: the handshake costs ~0.5s and
    # does not depend on the transcript, so overlapping it with STT hides it from
    # the user's wait instead of adding it at the end.
    session = get_tts_provider().session()
    stats: dict = {"turns": 0, "audio_ms": 0.0, "bytes": 0}
    sanitizer = MarkdownSanitizer()
    connect_task = asyncio.create_task(session.start())
    perf["t_connect"] = time.perf_counter()

    provider = get_stt_provider("deepgram")
    perf["t_stt"] = time.perf_counter()
    transcription = await provider.transcribe_stream(stream)
    # Defense in depth: never let the wake word reach the router or the LLM.
    transcription = _strip_wake_word(transcription)
    perf["eos"] = time.perf_counter()
    perf["stt"] = (perf["eos"] - perf["t_stt"]) * 1000
    perf["transcript"] = transcription
    perf["stt_timing"] = dict(getattr(provider, "timing", {}) or {})
    # The handshake overlapped STT if it finished before the user stopped talking.
    perf["connect_overlapped"] = connect_task.done()
    # Perceived timeline: anchor at t_client_stop minus client-reported lag
    # (true end of speech). The router fills it when the end frame arrives.
    meta = client_meta or {}
    perf["t_client_stop"] = meta.get("t_client_stop")
    perf["client_lag_ms"] = meta.get("client_lag_ms", 0)

    if not transcription.strip():
        # The socket was opened before STT; an empty turn must tear it down or it leaks.
        connect_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await connect_task
        with contextlib.suppress(Exception):
            await session.close()
        _print_empty_turn(perf)
        if streaming_path:
            yield {"type": "end"}
        return
    messages = [{'role': 'developer', 'content': AUDIO_SYSTEM_PROMPT}, {'role': 'user', 'content': transcription}]

    perf["t_route_start"] = time.perf_counter()
    route_task = asyncio.create_task(route_fast_path(transcription, perf))

    async def push_turn(text: str) -> None:
        """Send one turn's text and ask for its audio.

        The connect is awaited lazily on the FIRST push only, so the handshake overlaps STT and the LLM's time-to-first-token.
        """
        if not text.strip():
            return
        if not perf.get("t_connected"):
            await connect_task
            perf["t_connected"] = time.perf_counter()
        await session.push_text(text)
        await session.flush()

    async def producer() -> None:
        """Consume LLM tokens, sanitizing and pushing each complete sentence."""
        pending: list[str] = []
        t_agent = time.perf_counter()
        try:
            # Fast path checked INSIDE try: returning before it would skip the
            # finally below and leave session.audio() waiting forever.
            quick = await route_task
            perf["intent_ms"] = (time.perf_counter() - perf["t_route_start"]) * 1000
            if quick is not None:
                perf["fast_path"] = True
                perf["say_text"] = quick
                await push_turn(quick)
                return
            async for token in openai_agent(
                messages=messages,
                model=get_model_config()['orchestrator'],
                max_rounds=10,
                tool_schemas=ORCHESTRATOR_TOOL_SCHEMAS,
            ):
                if isinstance(token, str):
                    # The agent yields the literal string 'ERROR_TOKEN' on failure.
                    voicelog.log(f"agent ! stream error token: {token}")
                    break
                if token.get("type") == "finish":
                    break
                if token.get("type") == "inference_end":
                    pending = sanitizer.release()
                    sanitize_chunk_report(sanitizer, force=True)
                    for unit in pending:
                        await push_turn(unit)
                    continue
                if token.get("type") != "agent":
                    continue  # tool_call / tool_result carry no spoken content
                if "first_text" not in perf:
                    perf["first_text"] = time.perf_counter()
                    perf["first_text_ms"] = (perf["first_text"] - t_agent) * 1000
                pending = sanitizer.push(token.get("content", ""))
                sanitize_chunk_report(sanitizer)
                for unit in pending:
                    await push_turn(unit)
        except Exception as exc:
            voicelog.log(f"agent ! error: {exc}")
        finally:
            try:
                pending = sanitizer.flush()
                sanitize_chunk_report(sanitizer, force=True)
                sanitize_stream_report(sanitizer)
                for unit in pending:
                    await push_turn(unit)
            except Exception as exc:
                voicelog.log(f"agent ! tail flush error: {exc}")
            finally:
                # The LLM may yield nothing and leave the handshake pending;
                # close() can't kill it (it sees no _ws yet), so cancel it first
                # or the started Deepgram socket/pump leaks.
                connect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await connect_task
                with contextlib.suppress(Exception):
                    await session.close()

    producer_task = asyncio.create_task(producer())
    try:
        if streaming_path:
            sent_pcm_start = False
            async for item in session.audio():
                if isinstance(item, TTSTurnEnd):
                    stats["turns"] += 1
                    # Per-turn detail is folded into the end-of-turn block.
                    t_first_push = getattr(session, "t_first_push", None)
                    if stats["turns"] == 1:
                        stats["p1_audio"] = item.audio_ms
                        if t_first_push is not None and getattr(session, "first_audio_s", None):
                            try:
                                stats["ttfb"] = (session.first_audio_s - t_first_push) * 1000
                            except Exception:
                                stats["ttfb"] = -1.0
                    continue
                if isinstance(item, bytes):
                    if not sent_pcm_start:
                        rate = session.sample_rate
                        yield {"type": "pcm_start", "sample_rate": rate}
                        sent_pcm_start = True
                    nbytes = len(item)
                    rate = session.sample_rate
                    if rate > 0:
                        audio_ms = nbytes / 2 / rate * 1000
                    else:
                        audio_ms = 0.0
                    stats["bytes"] += nbytes
                    stats["audio_ms"] += audio_ms
                    if stats.get("first_audio") is None:
                        stats["first_audio"] = time.perf_counter()
                    yield item
                    continue
        else:
            async for wav_bytes in iter_wav_turns(session):
                audio_ms = _wav_duration_ms(wav_bytes)
                nbytes = len(wav_bytes)
                stats["turns"] += 1
                stats["audio_ms"] += audio_ms
                stats["bytes"] += nbytes
                if stats["turns"] == 1:
                    stats["first_audio"] = time.perf_counter()
                    stats["p1_audio"] = audio_ms
                    t_first_push = getattr(session, "t_first_push", None)
                    if t_first_push is not None and getattr(session, "first_audio_s", None):
                        try:
                            stats["ttfb"] = (session.first_audio_s - t_first_push) * 1000
                        except Exception:
                            stats["ttfb"] = -1.0
                yield wav_bytes
    finally:
        producer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await producer_task
        with contextlib.suppress(Exception):
            await session.close()
        _print_voice_summary(perf, stats, session)
