import asyncio
import json
import os
import time
from typing import AsyncIterator
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from ..voice import log as voicelog
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
            # Silence required before Deepgram finalizes a segment. Set ABOVE
            # the typical pause between the wake word and the command
            # ("alexa ... que hora es", <600ms) so the wake word is not split
            # into its own segment. A separate finalize for the wake segment
            # wedged Deepgram ~1s behind the live audio and the command's
            # final result arrived after the session closed (truncation).
            # This does NOT need to stay under the client's VAD trailing
            # silence (VAD_STOP_FRAMES in wake_script.js): when the client's
            # end frame arrives the router sends CloseStream, which flushes
            # whatever Deepgram still holds whether or not endpointing fired,
            # so the final lands either way. The wake-pause bound above is the
            # only constraint that matters.
            "endpointing": "600",
            "smart_format": "true",
        }
        if raw_pcm:
            params.update({
                "encoding": "linear16",
                "sample_rate": "16000",
                "channels": "1",
            })
        url = f"{DEEPGRAM_WS_URL}?{urlencode(params)}"
        voicelog.debug(f"connecting {url}", source="stt")

        t_start = time.perf_counter()
        transcript = ""
        self.timing = {}

        async with websockets.connect(url, additional_headers=headers) as ws:
            t_conn = time.perf_counter()
            voicelog.debug(f"connected  connect={(t_conn - t_start) * 1000:.1f}ms", source="stt")

            # Forward each incoming chunk to the socket as it arrives.
            chunk_count = 0
            total_bytes = 0
            async for chunk in chunks:
                if chunk:
                    await ws.send(chunk)
                    chunk_count += 1
                    total_bytes += len(chunk)
            # Anchor for finalization: the instant the last audio frame left.
            t_fwd_end = time.perf_counter()
            voicelog.debug(
                f"forwarded {chunk_count} chunks / {total_bytes} bytes", source="stt"
            )

            # Signal that no more audio will be sent. Deepgram's documented
            # close message is CloseStream (not "Close").
            await ws.send(json.dumps({"type": "CloseStream"}))
            voicelog.debug("CloseStream sent", source="stt")

            # Collect results until the connection closes. Deepgram may
            # finalize a segment mid-utterance (endpointing fires on a
            # natural pause) and then start a new segment if the user keeps
            # talking. The full transcription is the CONCATENATION of all
            # final segments, not the last one (that bug lost "Créame una
            # tarea..." from multi-segment utterances).
            #
            # After CloseStream, Deepgram flushes the queued results (possibly
            # several: mid-pause segment finals + the final one). The LAST
            # final lands only after Deepgram's ASR pipeline has processed its
            # tail buffer. If the client bursts audio (a ~1s pre-roll dumped
            # at once), that tail grows to ~1s and the final arrives late; a
            # fixed settle below the backlog then returns "" — measured live.
            # The client now replays its pre-roll at realtime pace, so the
            # backlog stays small (~0.1-0.3s) and one quiet window is enough.
            # Earlier gate variants broke multi-segment utterances (speech_final
            # fires on EVERY segment boundary), so the settle is unconditional.
            # Any result after CloseStream is the last word regardless; waiting
            # on server-close used to cost the full backlog (~1s) every turn.
            SETTLE_S = 0.5
            final_segments: list[str] = []
            last_interim = ""
            t_first_final = None
            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=SETTLE_S)
                except asyncio.TimeoutError:
                    break  # results settled: no more text is coming
                except ConnectionClosed:
                    break  # server closed the connection
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "Error":
                    voicelog.log(
                        f"stt ! Deepgram error variant={data.get('variant')} "
                        f"description={data.get('description')}"
                    )
                    continue
                voicelog.debug(
                    f"recv type={msg_type} is_final={data.get('is_final')} "
                    f"speech_final={data.get('speech_final')}",
                    source="stt",
                )
                if msg_type not in ("Results", "Transcript"):
                    continue
                alternatives = data.get("channel", {}).get("alternatives", [])
                if not alternatives:
                    continue
                text = alternatives[0].get("transcript", "").strip()
                if not text:
                    continue
                if data.get("is_final"):
                    if t_first_final is None:
                        t_first_final = time.perf_counter()
                        voicelog.debug(
                            f"first_final +{(t_first_final - t_fwd_end) * 1000:.1f}ms after last frame",
                            source="stt",
                        )
                    final_segments.append(text)
                    transcript = " ".join(final_segments)
                else:
                    last_interim = text
                voicelog.debug(f"transcript={transcript!r}", source="stt")
            t_results_end = time.perf_counter()

        # Fall back to the last interim hypothesis when no final was received.
        if not final_segments and last_interim:
            transcript = last_interim
        # Phase costs, not a single latency: Deepgram transcribes while the user
        # speaks, so ``listen`` spans the utterance. What the user actually waits
        # on is ``connect`` plus ``finalize``; the caller reads these from here.
        self.timing = {
            "connect": (t_conn - t_start) * 1000,
            "listen": (t_fwd_end - t_conn) * 1000,
            "finalize": (t_results_end - t_fwd_end) * 1000,
            "wall": (time.perf_counter() - t_start) * 1000,
        }
        voicelog.debug(
            f"session closed  chunks={chunk_count} bytes={total_bytes} "
            f"finals={len(final_segments)} transcript={transcript!r}",
            source="stt",
        )
        return transcript