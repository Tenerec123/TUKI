import io
import os
import re
import wave
from openai import AsyncOpenAI

TTS_MODEL = "hexgrad/kokoro-82m"
TTS_VOICE = "ef_dora"  # Spanish female. Alternatives: em_alex, em_santa (Spanish male)
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2
TTS_DEFAULT_RATE = 24000

_client = None

def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ['OPENROUTER_API_KEY']
        )
    return _client

async def text_to_speech_wav(text: str) -> bytes:
    """Synthesize speech with OpenRouter TTS and return WAV bytes.

    Requests raw PCM (lowest latency for streaming pipelines) and wraps it
    in a WAV container so the ESP32 can skip the 44-byte header and feed
    the samples straight to I2S.
    """
    client = _get_client()
    resp = await client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        response_format="pcm",
    )
    pcm = resp.content

    rate = TTS_DEFAULT_RATE
    try:
        content_type = resp.response.headers.get("content-type", "")
        match = re.search(r"rate=(\d+)", content_type)
        if match:
            rate = int(match.group(1))
    except Exception:
        pass

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(TTS_CHANNELS)
        wav_file.setsampwidth(TTS_SAMPLE_WIDTH)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()