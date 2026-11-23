from ..schemas import Prompt
from fastapi import APIRouter, UploadFile, File, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from ..ai.stt import transcribe_with_fallback, get_stt_provider
from ..ai.tts import get_tts_provider
from ..ai.stream_manager import stream_manager
from ..ai.chat import chat_persistence_wrapper
from ..ai.agent import openai_agent
from ..ai.voice.agent import voice_agent_logic
from ..ai.config import get_model_config, AUDIO_SYSTEM_PROMPT
from ..ai.tools.discovery import ALL_TOOL_SCHEMAS
import asyncio
import json
import time
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
    # Browsers upload MediaRecorder blobs as "audio/ogg; codecs=opus": keep
    # only the base MIME so providers infer the container correctly.
    content_type = (file.content_type or "audio/ogg").split(";", 1)[0].strip() or "audio/ogg"
    try:
        result_text = await transcribe_with_fallback(
            audio_data,
            content_type=content_type,
        )
    except Exception as exc:
        # A real provider failure (bad key, network, etc.) is still an error.
        if "No STT provider produced a transcript" not in str(exc):
            raise HTTPException(
                status_code=502,
                detail=f"Audio transcription failed: {exc}",
            ) from exc
        # No speech was detected: this is not a failure, just "nothing heard".
        # Return an empty transcript so the frontend can show a friendly
        # message instead of sending an empty prompt to the LLM.
        return ""
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

    # The end frame marks when the CLIENT stopped talking (after its VAD
    # trailing silence) - the anchor the user actually feels. Passed down so the
    # turn log measures the perceived timeline, not just the server-side one.
    client_meta: dict = {}

    # Aggressive preconnect: if the client sends a "start" frame before any
    # audio, open STT + TTS sockets NOW (while the user is still speaking)
    # so connect costs overlap speech instead of being pure latency.
    preopened_stt = None
    preopened_tts = None
    # Wait briefly for an optional "start" frame (aggressive preconnect).
    # If none arrives (legacy clients / tests), proceed without preconnect.
    first_msg = None
    try:
        first_msg = await asyncio.wait_for(websocket.receive(), timeout=0.2)
    except asyncio.TimeoutError:
        first_msg = None
    if first_msg is not None and first_msg.get("type") == "websocket.disconnect":
        return
    start_data = None
    if first_msg is not None and first_msg.get("text"):
        try:
            start_data = json.loads(first_msg["text"])
        except json.JSONDecodeError:
            start_data = None
    if start_data and start_data.get("type") == "start":
        try:
            from backend.ai.stt.deepgram import DeepgramSTTProvider

            stt_provider = get_stt_provider()
            if isinstance(stt_provider, DeepgramSTTProvider):
                try:
                    preopened_stt = await asyncio.wait_for(
                        stt_provider.preconnect(language="es", raw_pcm=True), timeout=2.0
                    )
                except Exception:
                    preopened_stt = None
        except Exception as exc:
            print(f"[voice-agent-ws] stt preconnect failed: {exc}")
            preopened_stt = None
        try:
            tts_session = get_tts_provider().session()
            try:
                await asyncio.wait_for(tts_session.start(), timeout=2.0)
                preopened_tts = tts_session
            except Exception:
                preopened_tts = None
        except Exception as exc:
            print(f"[voice-agent-ws] tts preconnect failed: {exc}")
            preopened_tts = None

    async def audio_chunks() -> AsyncIterator[bytes]:
        """Yield incoming binary audio frames until the utterance ends."""
        chunk_count = 0
        total_bytes = 0
        # First message may already be audio (no "start" frame) — yield it.
        if first_msg is not None and first_msg.get("bytes"):
            chunk_count += 1
            total_bytes += len(first_msg["bytes"])
            yield first_msg["bytes"]
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
                    client_meta["t_client_stop"] = time.perf_counter()
                    try:
                        lag = data.get("client_lag_ms")
                        if lag is not None:
                            client_meta["client_lag_ms"] = float(lag)
                    except (TypeError, ValueError):
                        pass
                    print(f"[voice-agent-ws] end frame -> received {chunk_count} chunks / {total_bytes} bytes")
                    return

    try:
        async for item in voice_agent_logic(
            audio_chunks(),
            streaming_path=use_proto2,
            client_meta=client_meta,
            preopened_stt=preopened_stt,
            preopened_tts_session=preopened_tts,
        ):
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
