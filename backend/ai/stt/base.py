from abc import ABC, abstractmethod
from typing import AsyncIterator


class STTProvider(ABC):
    """Abstract base class for speech-to-text providers."""

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "es",
        content_type: str = "audio/wav",
    ) -> str:
        pass

    async def transcribe_stream(
        self,
        chunks: AsyncIterator[bytes],
        language: str = "es",
    ) -> str:
        """
        Uses WebSocket
        NotImplementedError: If the provider does not support streaming.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support streaming transcription"
        )