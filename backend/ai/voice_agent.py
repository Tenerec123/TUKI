import asyncio
import contextlib
import io
import time
import wave
from typing import AsyncIterable, AsyncIterator

from .stt import get_stt_provider
from .agent import openai_agent
from .config import get_model_config, AUDIO_SYSTEM_PROMPT
from .tools.discovery import ORCHESTRATOR_TOOL_SCHEMAS
from .tts import TTSSession, get_tts_provider, iter_wav_turns, TTSTurnEnd
from .tts.sanitize import MarkdownSanitizer, sanitize_chunk_report, sanitize_stream_report


def _wav_duration_ms(wav_bytes: bytes) -> float:
    """Return the playable duration of a WAV payload in ms.

    Reads the container header only, so the cost does not depend on payload length.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            return reader.getnframes() / reader.getframerate() * 1000
    except Exception:
        return 0.0


def _print_voice_summary(perf: dict, stats: dict, session: TTSSession) -> None:
    """Emit the cumulative timing block for one request.

    ``eos`` is the end-of-speech anchor and ``first_audio`` the moment the first turn is
    ready to send, so their delta is the latency the user actually perceives.
    """
    t_end = time.perf_counter()
    eos = perf["eos"]
    first = stats.get("first_audio")
    headline = (first - eos) * 1000 if first is not None else -1.0
    print(
        f"[PERF] voice: END total={(t_end - perf['t_req']) * 1000:.1f}ms "
        f"stt={perf['stt']:.1f}ms "
        f"eos_to_first_audio={headline:.1f}ms "
        f"(tts_connect={perf.get('tts_connect_ms', 0.0):.1f}ms "
        f"overlapped={'yes' if perf.get('connect_overlapped') else 'no'} "
        f"ttfb={stats.get('ttfb', -1.0):.1f}ms) "
        f"agent_to_first_text={perf.get('first_text_ms', -1.0):.1f}ms "
        f"turns={int(stats.get('turns', 0))}"
    )
    print(
        f"[PERF] voice: SUM audio={stats.get('audio_ms', 0.0):.1f}ms "
        f"bytes={int(stats.get('bytes', 0))} "
        f"p1_audio={stats.get('p1_audio', 0.0):.1f}ms"
    )
    with contextlib.suppress(Exception):
        session.report()


async def voice_agent_logic(stream: AsyncIterable[bytes], streaming_path: bool = False) -> AsyncIterator[dict | bytes]:
    """Stream the agent's reply. Supports both legacy WAV-per-turn and PCM streaming.

    Pipeline: STT (utterance) -> LLM streaming (openai_agent) -> markdown sanitizer -> sentence splitter -> duplex TTS session.

    Paths:
    - Legacy (streaming_path=False): yields bytes as WAV per turn via iter_wav_turns.
    - Streaming (streaming_path=True): yields dict {"type":"pcm_start","sample_rate":<int>} before first PCM chunk,
      then yields bytes chunks of raw PCM16. TTSTurnEnd markers are used for accounting/logging.
    """
    # Real-time streaming STT: the Deepgram WebSocket opens first, then each raw PCM16 (16 kHz mono) chunk is forwarded as it arrives. No WAV wrapper, no buffering.
    perf: dict = {"t_req": time.perf_counter()}
    provider = get_stt_provider("deepgram")
    perf["t_stt"] = time.perf_counter()
    print(f"[PERF] voice: stt_provider={(perf['t_stt'] - perf['t_req']) * 1000:.1f}ms")
    transcription = await provider.transcribe_stream(stream)
    perf["eos"] = time.perf_counter()
    perf["stt"] = (perf["eos"] - perf["t_stt"]) * 1000
    print(f"[STT] Transcription: {transcription}")
    print(f"[PERF] voice: stt_total={perf['stt']:.1f}ms (eof anchor)")
    messages = [{'role': 'developer', 'content': AUDIO_SYSTEM_PROMPT}, {'role': 'user', 'content': transcription}]

    session = get_tts_provider().session()
    stats: dict = {"turns": 0, "audio_ms": 0.0, "bytes": 0}
    sanitizer = MarkdownSanitizer()

    # Connect now, in parallel with the LLM: awaiting it here would add the handshake straight onto the user's wait.
    connect_task = asyncio.create_task(session.start())
    perf["t_connect"] = time.perf_counter()

    async def push_turn(text: str) -> None:
        """Send one turn's text and ask for its audio.

        The connect is awaited lazily on the FIRST push only, so the handshake overlaps the LLM's time-to-first-token.
        """
        if not text.strip():
            return
        if not perf.get("t_connected"):
            await connect_task
            perf["t_connected"] = time.perf_counter()
            perf["tts_connect_ms"] = (
                perf["t_connected"] - perf["t_connect"]
            ) * 1000
            # ``connect_overlapped`` is sampled where first text arrives, not here: reaching this point at all means the agent already produced text.
            print(
                f"[PERF] voice: tts_connect={perf['tts_connect_ms']:.1f}ms "
                f"(awaited at first push, overlapped="
                f"{'yes' if perf.get('connect_overlapped') else 'no'})"
            )
        await session.push_text(text)
        await session.flush()

    async def producer() -> None:
        """Consume LLM tokens, sanitizing and pushing each complete sentence."""
        pending: list[str] = []
        t_agent = time.perf_counter()
        print(f"[PERF] voice: agent_start=+{(t_agent - perf['eos']) * 1000:.1f}ms since eos")
        try:
            async for token in openai_agent(
                messages=messages,
                model=get_model_config()['orchestrator'],
                max_rounds=10,
                tool_schemas=ORCHESTRATOR_TOOL_SCHEMAS,
            ):
                if isinstance(token, str):
                    # The agent yields the literal string 'ERROR_TOKEN' on failure.
                    print(f"[TTS] agent stream error token: {token}")
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
                    # Whether the handshake FINISHED while the LLM was still producing its first token. Sampled HERE on purpose: by the first push "first_text" is already set, so testing it later always reports "no".
                    perf["connect_overlapped"] = connect_task.done()
                    print(
                        f"[PERF] voice: first_text_delta=+{perf['first_text_ms']:.1f}ms "
                        f"since agent_start connect_done={connect_task.done()}"
                    )
                pending = sanitizer.push(token.get("content", ""))
                sanitize_chunk_report(sanitizer)
                for unit in pending:
                    t_push = time.perf_counter()
                    await push_turn(unit)
                    print(f"[PERF] voice: push chars={len(unit)} "
                          f"at=+{(t_push - perf['eos']) * 1000:.1f}ms since eos")
        except Exception as exc:
            print(f"[TTS] producer error: {exc}")
        finally:
            try:
                pending = sanitizer.flush()
                sanitize_chunk_report(sanitizer, force=True)
                sanitize_stream_report(sanitizer)
                for unit in pending:
                    await push_turn(unit)
            except Exception as exc:
                print(f"[TTS] tail flush error: {exc}")
            finally:
                with contextlib.suppress(Exception):
                    await session.close()

    producer_task = asyncio.create_task(producer())
    try:
        if streaming_path:
            sent_pcm_start = False
            async for item in session.audio():
                if isinstance(item, TTSTurnEnd):
                    stats["turns"] += 1
                    # Log per-turn from markers (avoid per-chunk spam)
                    print(f"[PERF] voice: turn={stats['turns']} audio={item.audio_ms:.1f}ms chars={item.chars}")
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
                print(f"[PERF] voice: turn={stats['turns'] + 1} audio={audio_ms:.1f}ms bytes={nbytes}")
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
