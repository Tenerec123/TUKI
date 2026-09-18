import asyncio
import io
import wave
from array import array
from typing import AsyncIterator

from .stt import get_stt_provider
from .agent import openai_agent
from .config import get_model_config, AUDIO_SYSTEM_PROMPT
from .tools.discovery import ALL_TOOL_SCHEMAS
from .tts import text_to_speech_wav

# Sentence terminators recognized by the streaming splitter. A newline is a
# valid terminator so multi-line answers are spoken line by line.
_SENTENCE_ENDINGS = (".", "?", "!", "\n")

# Trailing silence appended to each sentence WAV so consecutive streamed
# sentences don't cut abruptly when played back-to-back.
_SENTENCE_GAP_SECONDS = 0.2

# Final-sentence softening: a short linear fade over the actual audio tail
# plus a longer closing silence. Word-final fricatives (like the /s/ in
# "dos") live in the last samples of the buffer; without a fade they slam
# off at the edge of playback and get lost or sound cut.
_FINAL_FADE_MS = 40
_FINAL_GAP_SECONDS = 0.4


def _pad_wav_with_silence(wav_bytes: bytes, seconds: float = _SENTENCE_GAP_SECONDS) -> bytes:
    """Append a short trailing silence to a WAV buffer.

    The TTS synthesizes each sentence with no closing silence, so playing
    sentence WAVs in sequence sounds like hard cuts. Padding the tail makes
    the pause between sentences sound natural.
    """
    src = io.BytesIO(wav_bytes)
    out = io.BytesIO()
    with wave.open(src, "rb") as reader, wave.open(out, "wb") as writer:
        writer.setnchannels(reader.getnchannels())
        writer.setsampwidth(reader.getsampwidth())
        writer.setframerate(reader.getframerate())
        writer.writeframes(reader.readframes(reader.getnframes()))
        silence_samples = int(reader.getframerate() * seconds)
        writer.writeframes(b"\x00" * (silence_samples * reader.getsampwidth() * reader.getnchannels()))
    return out.getvalue()


def _stretch_final_sibilant(text: str) -> str:
    """Text-only workaround for kokoro clipping final sibilants.

    Probe evidence: synthesizing "Dos." produces a WAV whose final 40 ms is
    pure vowel at full amplitude (0.2% HF energy; Deepgram hears NOTHING),
    while "Doss." produces a clean sibilant tail (97% HF energy; Deepgram
    hears "dos"). Doubling the final "s" in the TTS INPUT (never in display
    text) makes the model emit a real /s/ without mispronunciation.
    """
    stripped = text.strip()
    if not stripped:
        return text
    body = stripped
    trailing = ""
    while body and body[-1] in ".!?…":
        trailing = body[-1] + trailing
        body = body[:-1]
    if body and body[-1] == "s":
        return body + "s" + trailing
    if body and body[-1] == "z":
        # z-doubling risks G2P confusion; ellipsis is the verified fallback.
        return body + "..."
    return text


def _finalize_wav(wav_bytes: bytes) -> bytes:
    """Softly end the final sentence: fade the audio tail, add closing silence.

    Applies a linear fade to the last ``_FINAL_FADE_MS`` of the actual speech
    so low-amplitude consonants (final /s/, /r/, etc.) glide out instead of
    being chopped, then appends a longer silence so the response ends with a
    natural tail instead of an abrupt stop.
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
            fade_samples = int(rate * _FINAL_FADE_MS / 1000)
            start = max(0, len(samples) - fade_samples)
            span = max(1, len(samples) - start)
            for i in range(len(samples) - start):
                # Ramp from full amplitude (1.0) at the start of the fade
                # region down to silence (0.0) at the very last sample.
                samples[start + i] = int(samples[start + i] * (1 - i / span))
            writer.writeframes(samples.tobytes())
        else:
            writer.writeframes(frames)
        silence_samples = int(rate * _FINAL_GAP_SECONDS)
        writer.writeframes(b"\x00" * (silence_samples * sampwidth * channels))
    return out.getvalue()


async def voice_agent_logic(stream) -> AsyncIterator[bytes]:
    """Stream the agent's reply as one WAV per sentence, in order.

    Pipeline:
        STT (utterance) -> LLM streaming (openai_agent) -> sentence splitter
        -> text-to-speech (single consumer, FIFO) -> one WAV per sentence.

    The producer keeps pulling LLM tokens (and queueing sentences) while the
    single consumer synthesizes the previous one, so the browser hears the
    first sentence while the model is still working on the rest.
    """
    # Real-time streaming STT: the Deepgram WebSocket opens first, then each
    # raw PCM16 (16 kHz mono) chunk is forwarded as it arrives from the
    # client request stream. No WAV wrapper, no buffering.
    provider = get_stt_provider("deepgram")
    transcription = await provider.transcribe_stream(stream)
    print(f"[STT] Transcription: {transcription}")
    messages = [{'role': 'developer', 'content': AUDIO_SYSTEM_PROMPT}, {'role': 'user', 'content': transcription}]

    # Unbounded queue: the producer never blocks on put, so it can keep
    # consuming the LLM stream while the consumer is busy synthesizing.
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def producer() -> None:
        """Consume LLM tokens, queueing each complete sentence as it appears."""
        buffer = ""
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
                buffer += token.get("content", "")
                stripped = buffer.strip()
                # A sentence is complete when the accumulated buffer ends with a
                # terminator (after trimming trailing whitespace).
                if stripped and stripped[-1] in _SENTENCE_ENDINGS:
                    await queue.put(stripped)
                    buffer = ""
        except Exception as exc:
            print(f"[TTS] producer error: {exc}")
        finally:
            # Flush any trailing text as the final sentence, then signal EOF.
            leftover = buffer.strip()
            if leftover:
                await queue.put(leftover)
            await queue.put(None)

    # Single consumer guarantees FIFO playback order — TTS is never
    # parallelized (parallel TTS could complete out of order). The parallelism
    # that matters is between the producer task and this consumer loop.
    #
    # One-sentence lookahead: hold the current sentence TEXT until the next
    # one (or the end sentinel) arrives, so the FINAL sentence can be both
    # text-transformed (_stretch_final_sibilant) and audio-softened
    # (_finalize_wav), while intermediate ones only get the inter-sentence
    # silence pad.
    producer_task = asyncio.create_task(producer())
    try:
        pending_text = None
        while True:
            sentence = await queue.get()
            if sentence is None:
                break
            if pending_text is not None:
                print(f"[TTS] phrase: {pending_text[:60]}")
                yield _pad_wav_with_silence(await text_to_speech_wav(pending_text))
            pending_text = sentence
        if pending_text is not None:
            final_text = _stretch_final_sibilant(pending_text)
            print(f"[TTS] final sentence: {final_text[:60]}")
            yield _finalize_wav(await text_to_speech_wav(final_text))
    finally:
        # On client disconnect / generator close, stop pulling from the LLM.
        # No-op when the producer already finished normally.
        producer_task.cancel()