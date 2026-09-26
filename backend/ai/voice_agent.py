import asyncio
import io
import time
import wave
from array import array
from typing import AsyncIterator

from .stt import get_stt_provider
from .agent import openai_agent
from .config import get_model_config, AUDIO_SYSTEM_PROMPT
from .tools.discovery import ALL_TOOL_SCHEMAS
from .tts import mark_phrase_complete, text_to_speech_wav

# Sentence terminators recognized by the streaming splitter.
_SENTENCE_ENDINGS = (".", "?", "!", "\n")
# Terminators, i.e. what a segment can be left with once whitespace is
# stripped. A segment left with nothing ("..", or the newline closing a
# "\r\n" pair) has no word content and must not be spoken.
_PHRASE_NOISE = "".join(_SENTENCE_ENDINGS)

_PHRASE_FADE_MS = 40
_PHRASE_GAP_SECONDS = 0.2


def _is_sentence_end(buffer: str, index: int) -> bool:
    """Report whether ``buffer[index]`` terminates a spoken phrase.

    A "." surrounded by digits is part of a number ("3.5", "2.0"), not a
    sentence end. A "." at the very end of the buffer that follows a digit is
    also not a sentence end yet: the next token could still be the fractional
    part ("2." + "5"), so the text is left in the buffer to be re-examined.
    """
    char = buffer[index]
    if char not in _SENTENCE_ENDINGS:
        return False
    if char != ".":
        return True
    prev_is_digit = index > 0 and buffer[index - 1].isdigit()
    if not prev_is_digit:
        return True
    if index + 1 == len(buffer):
        return False  # "2." so far — wait for the next token to decide
    return not buffer[index + 1].isdigit()


def _split_phrases(buffer: str, scan_from: int = 0) -> tuple[list[str], str, int]:
    """Split ``buffer`` at the FIRST sentence terminator, repeatedly.

    Returns the complete phrases found, the trailing remainder that is not
    terminated yet, and the offset the next call should resume scanning from. A
    single chunk carrying several sentences yields several phrases, so each one
    is synthesized (and played) as soon as it completes.

    ``scan_from`` avoids re-walking text already examined. Only the last
    character of the buffer can still change verdict (a trailing "." may turn
    out to be "2.5"), so a long terminator-free run — a code block, a table, a
    bare URL — stays linear instead of quadratic on the event loop.
    """
    phrases: list[str] = []
    start = 0
    index = min(max(scan_from, 0), len(buffer))
    resume = index
    while index < len(buffer):
        if not _is_sentence_end(buffer, index):
            # The next token can still turn this into a terminator ("2." + "5"),
            # so the next scan has to restart here.
            resume = index
            index += 1
            continue
        segment = buffer[start:index + 1].strip()
        start = index + 1
        index = start
        resume = start
        if segment.strip(_PHRASE_NOISE):
            phrases.append(mark_phrase_complete(segment))
    return phrases, buffer[start:], resume - start


def _soften_phrase_wav(wav_bytes: bytes) -> bytes:
    """Fade the audio tail of a phrase and append a short trailing silence.

    The fade is a linear ramp to zero over the last ``_PHRASE_FADE_MS`` of the
    actual speech so low-amplitude consonants (final /s/, /r/, etc.) glide out
    instead of being chopped. The pad makes the pause before the next phrase
    sound natural.

    Applied identically to every phrase: whether a phrase is the last one is a
    stream-position question that this layer has no business answering.
    """
    src = io.BytesIO(wav_bytes)
    out = io.BytesIO()
    with wave.open(src, "rb") as reader, wave.open(out, "wb") as writer:
        channels = reader.getnchannels()
        sampwidth = reader.getsampwidth()
        rate = reader.getframerate()
        writer.setnchannels(channels)
        writer.setsampwidth(sampwidth)
        writer.setframerate(rate)
        frames = reader.readframes(reader.getnframes())
        if sampwidth == 2 and channels == 1:
            samples = array("h", frames)
            fade_samples = int(rate * _PHRASE_FADE_MS / 1000)
            start = max(0, len(samples) - fade_samples)
            span = max(1, len(samples) - start)
            for i in range(len(samples) - start):
                # Ramp from full amplitude (1.0) at the start of the fade
                # region down to silence (0.0) at the very last sample.
                samples[start + i] = int(samples[start + i] * (1 - i / span))
            writer.writeframes(samples.tobytes())
        else:
            writer.writeframes(frames)
        silence_samples = int(rate * _PHRASE_GAP_SECONDS)
        writer.writeframes(b"\x00" * (silence_samples * sampwidth * channels))
    return out.getvalue()


def _wav_duration_ms(wav_bytes: bytes) -> float:
    """Return the playable duration of a WAV payload in ms.

    Reads the container header only (no sample data), so it is safe to call on
    the audio path: cost does not depend on the payload length.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            return reader.getnframes() / reader.getframerate() * 1000
    except Exception:
        return 0.0


def _print_voice_summary(perf: dict, stats: dict) -> None:
    """Emit the cumulative timing block for one request.

    ``eos`` is the end-of-speech anchor (transcription returned) and
    ``first_audio`` is the moment the first phrase is ready to send, so their
    delta is the latency the user actually perceives.
    """
    t_end = time.perf_counter()
    eos = perf["eos"]
    first = stats.get("first_audio")
    headline = (first - eos) * 1000 if first is not None else -1.0
    print(
        f"[PERF] voice: END total={(t_end - perf['t_req']) * 1000:.1f}ms "
        f"stt={perf['stt']:.1f}ms "
        f"eos_to_first_audio={headline:.1f}ms "
        f"(p1_synth={stats.get('p1_synth', 0.0):.1f}ms "
        f"p1_wait={stats.get('p1_wait', 0.0):.1f}ms) "
        f"agent_to_first_text={perf.get('first_text_ms', -1.0):.1f}ms "
        f"phrases={int(stats.get('phrases', 0))}"
    )
    print(
        f"[PERF] voice: SUM synth={stats.get('synth', 0.0):.1f}ms "
        f"queue_wait={stats.get('queue_wait', 0.0):.1f}ms "
        f"soften={stats.get('soften', 0.0):.1f}ms "
        f"audio={stats.get('audio_ms', 0.0):.1f}ms "
        f"bytes={int(stats.get('bytes', 0))} "
        f"p1_audio={stats.get('p1_audio', 0.0):.1f}ms"
    )


async def voice_agent_logic(stream) -> AsyncIterator[bytes]:
    """Stream the agent's reply as one WAV per phrase, in order.

    Pipeline:
        STT (utterance) -> LLM streaming (openai_agent) -> phrase splitter
        -> text-to-speech (single consumer, FIFO) -> one WAV per phrase.
    """
    # Real-time streaming STT: the Deepgram WebSocket opens first, then each
    # raw PCM16 (16 kHz mono) chunk is forwarded as it arrives from the
    # client request stream. No WAV wrapper, no buffering.
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

    # Unbounded queue: the producer never blocks on put, so it can keep
    # consuming the LLM stream while the consumer is busy synthesizing.
    # Each item carries its enqueue timestamp so the consumer can measure the
    # queue wait (enqueue -> dequeue) that the producer's lead time hides.
    queue: asyncio.Queue[tuple[str, float] | None] = asyncio.Queue()
    stats: dict = {"phrases": 0, "queue_wait": 0.0, "synth": 0.0, "soften": 0.0,
                   "audio_ms": 0.0, "bytes": 0}

    async def producer() -> None:
        """Consume LLM tokens, queueing each complete phrase as it appears."""
        buffer = ""
        scan_from = 0
        t_agent = time.perf_counter()
        print(f"[PERF] voice: agent_start=+{(t_agent - perf['eos']) * 1000:.1f}ms since eos")
        try:
            async for token in openai_agent(
                messages=messages,
                model=get_model_config()['orchestrator'],
                max_rounds=10,
                tool_schemas=ALL_TOOL_SCHEMAS,
            ):
                if isinstance(token, str):
                    # The agent yields the literal string 'ERROR_TOKEN' on failure.
                    print(f"[TTS] agent stream error token: {token}")
                    break
                if token.get("type") == "finish":
                    break
                if token.get("type") != "agent":
                    continue  # tool_call / tool_result carry no spoken content
                if "first_text" not in perf:
                    perf["first_text"] = time.perf_counter()
                    perf["first_text_ms"] = (perf["first_text"] - t_agent) * 1000
                    print(f"[PERF] voice: first_text_delta=+{perf['first_text_ms']:.1f}ms since agent_start")
                buffer += token.get("content", "")        
                phrases, buffer, scan_from = _split_phrases(buffer, scan_from)
                for phrase in phrases:
                    # Backstop for the invariant the splitter holds: an empty
                    # phrase reaches the provider as a 4xx and kills the reply.
                    if not phrase.strip():
                        continue
                    t_enq = time.perf_counter()
                    await queue.put((phrase, t_enq))
                    print(f"[PERF] voice: enqueue chars={len(phrase)} "
                          f"at=+{(t_enq - perf['eos']) * 1000:.1f}ms since eos")
        except Exception as exc:
            print(f"[TTS] producer error: {exc}")
        finally:
            # Flush any trailing text as the last phrase, then signal EOF. The
            # stream ending is what completes it, so it is marked like any other
            # phrase rather than reaching TTS unmarked.
            leftover = buffer.strip()
            if leftover:
                t_enq = time.perf_counter()
                await queue.put((mark_phrase_complete(leftover), t_enq))
                print(f"[PERF] voice: enqueue chars={len(leftover)} "
                      f"at=+{(t_enq - perf['eos']) * 1000:.1f}ms since eos (leftover)")
            await queue.put(None)
    
    producer_task = asyncio.create_task(producer())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            phrase, t_enq = item
            wait_ms = (time.perf_counter() - t_enq) * 1000
            print(f"[PERF] voice: dequeue wait={wait_ms:.1f}ms chars={len(phrase)}")
            print(f"[TTS] phrase: {phrase[:60]}")
            t_syn = time.perf_counter()
            wav_bytes = await text_to_speech_wav(phrase)
            synth_ms = (time.perf_counter() - t_syn) * 1000
            t_soft = time.perf_counter()
            payload = _soften_phrase_wav(wav_bytes)
            soften_ms = (time.perf_counter() - t_soft) * 1000
            audio_ms = _wav_duration_ms(payload)
            nbytes = len(payload)
            print(f"[PERF] voice: synth synth={synth_ms:.1f}ms queue_wait={wait_ms:.1f}ms "
                  f"soften={soften_ms:.1f}ms audio={audio_ms:.1f}ms bytes={nbytes}")
            stats["phrases"] += 1
            stats["queue_wait"] += wait_ms
            stats["synth"] += synth_ms
            stats["soften"] += soften_ms
            stats["audio_ms"] += audio_ms
            stats["bytes"] += nbytes
            if stats["phrases"] == 1:
                stats["p1_synth"] = synth_ms
                stats["p1_wait"] = wait_ms
                stats["p1_audio"] = audio_ms
                stats["first_audio"] = time.perf_counter()
            yield payload
    finally:
        producer_task.cancel()
        _print_voice_summary(perf, stats)
