from ...schemas import ConversationSchema, Prompt, ConversationUpdate, MessageBase
from fastapi import APIRouter, UploadFile, Form, Depends, File, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from ...database import get_db, SessionLocal
from ...models import Conversation
from sqlalchemy.orm import Session
from ..conversations import edit_conversation_logic
import asyncio
from .fake_ai import fake_ai
from .openai_agent import openai_agent
router = APIRouter(
    prefix="/api/ai", # Todos los endpoints empezarán con esto
    tags=["ai"]        # Organiza la documentación automática (/docs)
)
TOKENS_CREATED = []
LIVE_STREAM = asyncio.Queue()
ON_STREAM = False
CONV_ID = None

async def chat_persistence_wrapper(prompt:Prompt):
    
    db = SessionLocal()
    try:
        db_conversation = db.query(Conversation).where(Conversation.id == prompt.conversation_id).first()
        edit_conversation_logic(prompt.conversation_id, ConversationUpdate(messages=[MessageBase(is_user=True, text=prompt.user_message)]), db=db)
        full_text = ""
        async for token in openai_agent(ConversationSchema.model_validate(db_conversation), prompt.model):
            if token =='ERROR_TOKEN':break
            full_text += token
            LIVE_STREAM.put_nowait(token)
            TOKENS_CREATED.append(token)
        edit_conversation_logic(prompt.conversation_id, ConversationUpdate(messages=[MessageBase(is_user=False, text=full_text)]), db=db)
    finally:
        global ON_STREAM,CONV_ID
        CONV_ID = None
        ON_STREAM = False
        print("AI_FINISH")
        db.close()

@router.post("/execute")
def ai_response(prompt:Prompt, background_tasks:BackgroundTasks):
    global ON_STREAM,TOKENS_CREATED,CONV_ID
    if CONV_ID is not None: return {}
    while not LIVE_STREAM.empty():
        LIVE_STREAM.get_nowait()
    TOKENS_CREATED = []
    CONV_ID = prompt.conversation_id
    ON_STREAM = True
    background_tasks.add_task(chat_persistence_wrapper, prompt)
    return {"no response for now"}

async def ram_streaming_collector():
    yield "".join(TOKENS_CREATED) 
    while ON_STREAM or not LIVE_STREAM.empty():
        if not LIVE_STREAM.empty():
            token = await LIVE_STREAM.get()
            yield token
        else: await asyncio.sleep(0.05)

@router.get("/connect/{conv_id}")
def connect_streaming(conv_id:int):
    if conv_id != CONV_ID:
        raise HTTPException(status_code=400, detail="AI not running for this conversation")
    return StreamingResponse(ram_streaming_collector(), media_type="text/plain")