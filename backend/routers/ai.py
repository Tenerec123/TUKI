from ..schemas import Prompt
from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Response
from fastapi.responses import StreamingResponse
from ..ai.stt import stt_conversion_logic
from ..ai.stream_manager import stream_manager
from ..ai.chat import chat_persistence_wrapper
router = APIRouter(
    prefix="/api/ai",
    tags=["ai"]
)

@router.post("/execute")
def ai_response(prompt: Prompt, background_tasks: BackgroundTasks):
    if not stream_manager.start(prompt.conversation_id):
        return {}
    background_tasks.add_task(chat_persistence_wrapper, prompt)
    return {}


@router.get("/connect/{conv_id}")
async def connect_streaming(conv_id: int):
    if not stream_manager.is_active(conv_id):
        # No active stream for this conversation — return 204 (success, no body)
        # instead of an error. This is a normal state when reconnecting after a
        # generation finished, not a client fault.
        return Response(status_code=204)
    return StreamingResponse(stream_manager.stream(conv_id), media_type="application/x-ndjson")


@router.post('/stt')
async def stt_conversion(file: UploadFile = File(...)):
    result_text = await stt_conversion_logic(file)
    return result_text