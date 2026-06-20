from ...schemas import ConversationSchema, Prompt, ConversationUpdate, MessageBase
from fastapi import APIRouter, UploadFile, Form, Depends, File, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from ...database import get_db, SessionLocal
from ...models import Conversation
from sqlalchemy.orm import Session
from ..conversations import edit_conversation_logic
from .stt import stt_conversion_logic
from .openai_agent import openai_agent
from .stream_manager import stream_manager

router = APIRouter(
    prefix="/api/ai",
    tags=["ai"]
)


async def chat_persistence_wrapper(prompt: Prompt):
    db = SessionLocal()
    try:
        db_conversation = db.query(Conversation).where(
            Conversation.id == prompt.conversation_id
        ).first()
        edit_conversation_logic(
            prompt.conversation_id,
            ConversationUpdate(messages=[MessageBase(is_user=True, text=prompt.user_message)]),
            db=db,
        )
        full_text = ""
        async for token in openai_agent(
            ConversationSchema.model_validate(db_conversation), prompt.model
        ):
            if token == "ERROR_TOKEN":
                break
            full_text += token
            stream_manager.push(prompt.conversation_id, token)
        edit_conversation_logic(
            prompt.conversation_id,
            ConversationUpdate(messages=[MessageBase(is_user=False, text=full_text)]),
            db=db,
        )
    finally:
        stream_manager.finish(prompt.conversation_id)
        print("AI_FINISH")
        db.close()


@router.post("/execute")
def ai_response(prompt: Prompt, background_tasks: BackgroundTasks):
    if not stream_manager.start(prompt.conversation_id):
        return {}
    background_tasks.add_task(chat_persistence_wrapper, prompt)
    return {"no response for now"}


@router.get("/connect/{conv_id}")
async def connect_streaming(conv_id: int):
    if not stream_manager.is_active(conv_id):
        raise HTTPException(
            status_code=400, detail="AI not running for this conversation"
        )
    return StreamingResponse(stream_manager.stream(conv_id), media_type="text/plain")


@router.post('/stt')
async def stt_conversion(file: UploadFile = File(...), conv_id = Form(...), db:Session = Depends(get_db)):
    result_text = await stt_conversion_logic(file, conv_id, db)
    return result_text
