import json
import os
from typing import AsyncIterator
from urllib.parse import urlencode

import websockets

from .base import STTProvider

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
CHUNK_SIZE = 4096


class DeepgramSTTProvider(STTProvider):
    """
    Supports both batch and  streaming transcription. For raw PCM16
    input, the encoding is declared via URL query parameters.
    """

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "es",
        content_type: str = "audio/wav",
        raw_pcm: bool = False,
    ) -> str:
        """
            content_type: MIME type of the audio. When ``audio/x-raw`` or
                ``raw_pcm=True``, the raw PCM16 16 kHz mono encoding is
                declared via URL query parameters; otherwise Deepgram
                auto-detects from the container header.
            raw_pcm: When True, declare encoding/sample_rate/channels in the
                URL query parameters (for raw PCM16 without a container
                header).
        """
        async def chunk_iter() -> AsyncIterator[bytes]:
            for i in range(0, len(audio_data), CHUNK_SIZE):
                yield audio_data[i:i + CHUNK_SIZE]

        return await self._run_websocket(
            chunk_iter(),
            language=language,
            raw_pcm=raw_pcm or content_type == "audio/x-raw",
        )

    async def transcribe_stream(
        self,
        chunks: AsyncIterator[bytes],
        language: str = "es",
    ) -> str:
        """
        Args:
            chunks: Async iterator yielding raw PCM16 bytes (16 kHz mono).
            language: ISO 639-1 language code.
        """
        return await self._run_websocket(
            chunks,
            language=language,
            raw_pcm=True,
        )

    async def _run_websocket(
        self,
        chunks: AsyncIterator[bytes],
        language: str,
        raw_pcm: bool,
    ) -> str:
        """Shared WebSocket session: connect, forward, close.

        Returns the final transcript (latest final/interim hypothesis)
        once the socket closes.
        """
        api_key = os.environ.get('DEEPGRAM_API_KEY')
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable not set")

        headers = {"Authorization": f"Token {api_key}"}

        # Deepgram v1 streaming config goes in URL query parameters, NOT in a
        # Configure message. Sending {"type":"Configure","config":{...}}
        # makes the API reply with a SchemaError and no transcript.
        params = {
            "model": "nova-2",
            "language": language,
            "interim_results": "true",
            "endpointing": "300",
            "smart_format": "true",
        }
        if raw_pcm:
            params.update({
                "encoding": "linear16",
                "sample_rate": "16000",
                "channels": "1",
            })
        url = f"{DEEPGRAM_WS_URL}?{urlencode(params)}"
        print(f"[deepgram] connecting to {url}")

        transcript = ""

        async with websockets.connect(url, additional_headers=headers) as ws:
            print("[deepgram] connected to Deepgram WS")

            # Forward each incoming chunk to the socket as it arrives.
            chunk_count = 0
            total_bytes = 0
            async for chunk in chunks:
                if chunk:
                    await ws.send(chunk)
                    chunk_count += 1
                    total_bytes += len(chunk)
            print(f"[deepgram] forwarded {chunk_count} chunks / {total_bytes} bytes to Deepgram")

            # Signal that no more audio will be sent. Deepgram's documented
            # close message is CloseStream (not "Close").
            await ws.send(json.dumps({"type": "CloseStream"}))
            print("[deepgram] CloseStream sent, collecting results")

            # Collect results until the connection closes. Deepgram may
            # finalize a segment mid-utterance (endpointing fires on a
            # natural pause) and then start a new segment if the user keeps
            # talking. The full transcription is the CONCATENATION of all
            # final segments, not the last one (that bug lost "Créame una
            # tarea..." from multi-segment utterances).
            final_segments: list[str] = []
            last_interim = ""
            async for message in ws:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "Error":
                    print(f"[deepgram] ERROR from Deepgram: variant={data.get('variant')} "
                          f"description={data.get('description')}")
                    continue
                print(f"[deepgram] recv type={msg_type} is_final={data.get('is_final')}")
                if msg_type not in ("Results", "Transcript"):
                    continue
                alternatives = data.get("channel", {}).get("alternatives", [])
                if not alternatives:
                    continue
                text = alternatives[0].get("transcript", "").strip()
                if not text:
                    continue
                if data.get("is_final"):
                    final_segments.append(text)
                    transcript = " ".join(final_segments)
                else:
                    last_interim = text
                print(f"[deepgram] transcript={transcript!r}")

        # Fall back to the last interim hypothesis when no final was received.
        if not final_segments and last_interim:
            transcript = last_interim
        print(f"[deepgram] session closed, returning transcript={transcript!r}")
        return transcript