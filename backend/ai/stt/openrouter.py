import io
from .base import STTProvider
from ..config import get_model_config_for
from ..credentials import create_chat_client


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

        stt_config = get_model_config_for('stt')
        # A Deepgram-selected row contains a Deepgram model id (for example
        # nova-2), which is not valid for OpenRouter's batch fallback.
        stt_model = (
            stt_config[2]
            if stt_config[1] == 'openrouter'
            else 'nvidia/parakeet-tdt-0.6b-v3'
        )
        client = create_chat_client('openrouter')
        try:
            result = await client.audio.transcriptions.create(
                model=stt_model,
                file=(filename, file, content_type),
                language=language,
            )
        finally:
            await client.close()
        return result.text
