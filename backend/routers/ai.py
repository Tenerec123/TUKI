from ..schemas import Prompt
from fastapi import APIRouter, UploadFile, File, Response, Request
from fastapi.responses import StreamingResponse
from ..ai.stt import stt_conversion_logic
from ..ai.stream_manager import stream_manager
from ..ai.chat import chat_persistence_wrapper
from ..ai.agent import openai_agent
from ..ai.voice_agent import voice_agent_logic
from ..ai.config import get_model_config, AUDIO_SYSTEM_PROMPT
from ..ai.tools.discovery import ALL_TOOL_SCHEMAS
from ..ai.tts import text_to_speech_wav
import asyncio
import wave
import io
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
    result_text = await stt_conversion_logic(file)
    return result_text

@router.post("/voice-agent")
async def voice_agent(request: Request):
    audio_bytes = await voice_agent_logic(request.stream())
    return Response(content=audio_bytes, media_type="audio/wav")