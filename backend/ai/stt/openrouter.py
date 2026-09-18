import io
import os
from openai import AsyncOpenAI

from .base import STTProvider
from ..config import get_model_config

_client: AsyncOpenAI | None = None


class OpenRouterSTTProvider(STTProvider):
    """STT provider that uses OpenRouter's batch audio transcription API.

    This is the fallback provider and wraps the legacy stt_conversion_logic
    behavior. It sends the full audio in a single request and returns the
    final transcript.
    """

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "es",
        content_type: str = "audio/wav",
    ) -> str:
        # OpenRouter expects a file-like object plus a filename.
        file = io.BytesIO(audio_data)
        filename = "audio.wav"
        if content_type == "audio/ogg":
            filename = "recording.ogg"
        elif content_type == "audio/mpeg":
            filename = "recording.mp3"

        stt_model = get_model_config().get('stt', 'nvidia/parakeet-tdt-0.6b-v3')
        client = get_stt_client()
        result = await client.audio.transcriptions.create(
            model=stt_model,
            file=(filename, file, content_type),
            language=language,
        )
        return result.text


def get_stt_client() -> AsyncOpenAI:
    """Get a lazily-initialized OpenRouter async client."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ['OPENROUTER_API_KEY'],
        )
    return _client