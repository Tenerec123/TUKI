import io
import os
import re
import time
import wave
from openai import AsyncOpenAI

TTS_MODEL = "hexgrad/kokoro-82m"
TTS_VOICE = "ef_dora"  # Spanish female. Alternatives: em_alex, em_santa (Spanish male)
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2
TTS_DEFAULT_RATE = 24000

_client = None

# Characters that end a spoken phrase. Used by the sibilant workaround below
# to tell a COMPLETE phrase (safe to transform) from an open-ended fragment.
_PHRASE_TERMINATORS = ".!?\u2026"

def mark_phrase_complete(text: str) -> str:
    """Return ``text`` ending with a terminator the sibilant gate recognizes.

    Whether a phrase is complete is known where it is produced, not by the text
    itself: a line break or the end of the agent stream ends a phrase without
    leaving a visible terminator behind. The splitter calls this instead of
    making ``_stretch_final_sibilant`` guess, and passing no marker on is what
    lets a phrase through untouched.
    """
    stripped = text.rstrip()
    if not stripped or stripped[-1] in _PHRASE_TERMINATORS:
        return text
    return stripped + "."

def _get_client() -> tuple[AsyncOpenAI, float, bool]:
    """Return the client plus its (ctor_ms, cold_start) cost.

    Measured here is wrapper construction only: the ``openai`` module is
    already imported and httpx opens its connection lazily, so DNS/TCP/TLS land
    in the first request and are reported as ``http``, not here.
    """
    global _client
    if _client is None:
        t0 = time.perf_counter()
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ['OPENROUTER_API_KEY']
        )
        ctor_ms = (time.perf_counter() - t0) * 1000
        print(f"[PERF] tts: client_ctor ctor={ctor_ms:.1f}ms cold_start=yes")
        return _client, ctor_ms, True
    return _client, 0.0, False

def _stretch_final_sibilant(text: str) -> str:
    """Lengthen a word-final sibilant so kokoro does not clip it.

    Probe evidence: synthesizing "Dos." produces a WAV whose final 40 ms is
    pure vowel at full amplitude (0.2% HF energy; Deepgram hears NOTHING),
    while "Doss." produces a clean sibilant tail (97% HF energy; Deepgram
    hears "dos"). Doubling the final "s" in the TTS INPUT (never in display
    text) makes the model emit a real /s/ without mispronunciation.

    This lives in the TTS layer and runs on every synthesis because the
    splitter marks every phrase it emits as complete via ``mark_phrase_complete``,
    so every input ends at a word boundary. As a safety net, text that does not
    end in a terminator is returned untouched: an open-ended fragment ending in
    "s" ("las") must not become "lass".
    """
    stripped = text.strip()
    if not stripped or stripped[-1] not in _PHRASE_TERMINATORS:
        return text
    body = stripped
    trailing = ""
    while body and body[-1] in _PHRASE_TERMINATORS:
        trailing = body[-1] + trailing
        body = body[:-1]
    if body and body[-1] == "s":
        return body + "s" + trailing
    if body and body[-1] == "z":
        # z-doubling risks G2P confusion; ellipsis is the verified fallback.
        return body + "..."
    return text

async def text_to_speech_wav(text: str) -> bytes:
    """Synthesize speech with OpenRouter TTS and return WAV bytes.

    Requests raw PCM (lowest latency for streaming pipelines) and wraps it
    in a WAV container so the ESP32 can skip the 44-byte header and feed
    the samples straight to I2S.
    """
    t_call = time.perf_counter()
    client, ctor_ms, cold = _get_client()
    t_http = time.perf_counter()
    resp = await client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=_stretch_final_sibilant(text),
        response_format="pcm",
    )
    http_ms = (time.perf_counter() - t_http) * 1000
    pcm = resp.content

    rate = TTS_DEFAULT_RATE
    try:
        content_type = resp.response.headers.get("content-type", "")
        match = re.search(r"rate=(\d+)", content_type)
        if match:
            rate = int(match.group(1))
    except Exception:
        pass

    frames = len(pcm) // (TTS_SAMPLE_WIDTH * TTS_CHANNELS)
    audio_ms = frames / rate * 1000

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(TTS_CHANNELS)
        wav_file.setsampwidth(TTS_SAMPLE_WIDTH)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm)

    # Printed after the WAV wrap so synth_ms spans this whole call, the same
    # span the caller times as synth.
    print(
        f"[PERF] tts: synth cold={'yes' if cold else 'no'} "
        f"client_ctor={ctor_ms:.1f}ms http={http_ms:.1f}ms "
        f"synth_ms={(time.perf_counter() - t_call) * 1000:.1f}ms "
        f"rate={rate} frames={frames} pcm_bytes={len(pcm)} audio={audio_ms:.1f}ms"
    )
    return buffer.getvalue()