from ..schemas import Prompt
from fastapi import APIRouter, UploadFile, File, Response, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from ..ai.stt import get_stt_provider
from ..ai.stream_manager import stream_manager
from ..ai.chat import chat_persistence_wrapper
from ..ai.agent import openai_agent
from ..ai.tts import TTS_CHANNELS, TTS_SAMPLE_WIDTH, TTS_DEFAULT_RATE
from ..ai.voice_agent import voice_agent_logic
from ..ai.config import get_model_config, AUDIO_SYSTEM_PROMPT
from ..ai.tools.discovery import ALL_TOOL_SCHEMAS
import asyncio
import io
import json
import wave
from typing import AsyncIterator

router = APIRouter(
    prefix="/api/ai",
    tags=["ai"]
)

@router.post("/execute")
async def ai_response(prompt: Prompt):
    if not stream_manager.start(prompt.conversation_id):
        return {}
    task = asyncio.create_task(chat_persistence_wrapper(prompt))
    stream_manager._streams[prompt.conversation_id].task = task
    return {}

@router.get("/connect/{conv_id}")
async def connect_streaming(conv_id: int):
    if not stream_manager.is_active(conv_id):
        return Response(status_code=204)
    return StreamingResponse(stream_manager.stream(conv_id), media_type="application/x-ndjson")

@router.post("/stop/{conv_id}")
async def stop_streaming(conv_id: int):
    if not stream_manager.is_active(conv_id):
        return Response(status_code=204)
    stream_manager._streams[conv_id].task.cancel()


@router.post('/stt')
async def stt_conversion(file: UploadFile = File(...)):
    audio_data = await file.read()
    # Chat mic always uses OpenRouter batch STT, regardless of the global
    # stt_provider config (that config only drives the voice agent).
    provider = get_stt_provider("openrouter")
    result_text = await provider.transcribe(
        audio_data,
        content_type=file.content_type or "audio/ogg",
    )
    return result_text

@router.post("/voice-agent")
async def voice_agent(request: Request):
    # voice_agent_logic streams one WAV per phrase with no framing, so legacy
    # clients (ESP32, curl) still get a single valid WAV. The header is written
    # from the TTS constants up front: a reply can legitimately have zero
    # phrases (input like "...", a failed agent turn), and a writer closed with
    # no params raises instead of returning anything.
    combined = io.BytesIO()
    phrase_count = 0
    with wave.open(combined, "wb") as out:
        out.setnchannels(TTS_CHANNELS)
        out.setsampwidth(TTS_SAMPLE_WIDTH)
        out.setframerate(TTS_DEFAULT_RATE)
        async for wav_bytes in voice_agent_logic(request.stream()):
            with wave.open(io.BytesIO(wav_bytes), "rb") as src:
                params = (src.getnchannels(), src.getsampwidth(), src.getframerate())
                if params != (out.getnchannels(), out.getsampwidth(), out.getframerate()):
                    # Concatenating mismatched PCM would emit corrupt audio.
                    raise ValueError(
                        f"Inconsistent PCM parameters across phrases: {params} != "
                        f"{(out.getnchannels(), out.getsampwidth(), out.getframerate())}"
                    )
                out.writeframes(src.readframes(src.getnframes()))
            phrase_count += 1
    if phrase_count == 0:
        # Nothing to speak is a valid outcome (the WS path just closes the
        # socket), so answer with a silent WAV instead of a 500.
        print("[voice-agent] no phrases to synthesize — returning an empty WAV")
    return Response(content=combined.getvalue(), media_type="audio/wav")


@router.websocket("/voice-agent-ws")
async def voice_agent_ws(websocket: WebSocket):
    """Stream an utterance over WebSocket: binary PCM16 frames in, WAVs out.

    The browser connects via WebSocket (works over plain HTTP/1.1, no TLS),
    sends raw PCM16 16 kHz mono mic frames as binary messages, and signals
    the end of the utterance with a JSON ``{"type": "end"}`` message. Each
    sentence of the reply is sent back as a separate binary WAV message as
    soon as it is synthesized, so playback can start while the LLM is still
    generating the rest of the answer.
    """
    await websocket.accept()
    print("[voice-agent-ws] WebSocket accepted")

    async def audio_chunks() -> AsyncIterator[bytes]:
        """Yield incoming binary audio frames until the utterance ends."""
        chunk_count = 0
        total_bytes = 0
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                print(f"[voice-agent-ws] client disconnected after {chunk_count} chunks / {total_bytes} bytes")
                return
            if message.get("bytes"):
                chunk_count += 1
                total_bytes += len(message["bytes"])
                yield message["bytes"]
            if message.get("text"):
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "end":
                    print(f"[voice-agent-ws] end frame -> received {chunk_count} chunks / {total_bytes} bytes")
                    return

    try:
        async for wav_bytes in voice_agent_logic(audio_chunks()):
            await websocket.send_bytes(wav_bytes)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        # Report the failure to the client when the socket is still open.
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass  # socket already gone
    finally:
        try:
            await websocket.close()
        except Exception:
            pass