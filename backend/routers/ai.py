from ..schemas import Prompt
from fastapi import APIRouter, UploadFile, File, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from ..ai.stt import get_stt_provider
from ..ai.stream_manager import stream_manager
from ..ai.chat import chat_persistence_wrapper
from ..ai.agent import openai_agent
from ..ai.voice_agent import voice_agent_logic
from ..ai.config import get_model_config, AUDIO_SYSTEM_PROMPT
from ..ai.tools.discovery import ALL_TOOL_SCHEMAS
import asyncio
import json
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


@router.websocket("/voice-agent-ws")
async def voice_agent_ws(websocket: WebSocket):
    """Stream an utterance over WebSocket: binary PCM16 frames in, audio out.

    Protocol:
    - Legacy (no query param): returns WAV per turn (binary frames).
    - Streaming (?proto=2): returns JSON pcm_start frame, then binary PCM16 frames, ends on socket close.
    """
    await websocket.accept()
    print("[voice-agent-ws] WebSocket accepted")

    # Check protocol version from query params
    query_params = websocket.query_params
    use_proto2 = query_params.get("proto") == "2"

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
        async for item in voice_agent_logic(audio_chunks(), streaming_path=use_proto2):
            if isinstance(item, dict):
                await websocket.send_json(item)
            elif isinstance(item, bytes):
                await websocket.send_bytes(item)
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