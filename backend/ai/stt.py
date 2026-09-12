import io
import os
from openai import AsyncOpenAI
from .config import get_model_config

_client = None

def get_stt_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ['OPENROUTER_API_KEY'],
        )
    return _client

async def stt_conversion_logic(file):
    if isinstance(file, io.BytesIO):
        audio_data = file.read()
        filename = "audio.wav"
        content_type = "audio/wav"
    else:
        audio_data = await file.read()
        filename = file.filename or "recording.ogg"
        content_type = file.content_type or "audio/ogg"

    stt_model = get_model_config().get('stt', 'nvidia/parakeet-tdt-0.6b-v3')
    client = get_stt_client()
    result = await client.audio.transcriptions.create(
        model=stt_model,
        file=(filename, audio_data, content_type),
        language="es",
    )
    return result.text