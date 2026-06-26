from ...schemas import ConversationSchema, Prompt, ConversationUpdate, MessageBase
from fastapi import APIRouter, UploadFile, Form, Depends, File, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from ...database import get_db, SessionLocal
from ...models import Conversation, Message
from sqlalchemy.orm import Session
from ..conversations import edit_conversation_logic
from .stt import stt_conversion_logic
from .openai_agent import openai_agent, get_model_config
from .stream_manager import stream_manager
from openai import OpenAI
import os
import asyncio

router = APIRouter(
    prefix="/api/ai",
    tags=["ai"]
)


async def _generate_title(conv_id: int, user_message: str):
    """Generate a short conversation title from the first user message.
    Uses OpenRouter free models with fallback chain.
    """
    system_prompt = """Generate a short, descriptive title (max 6 words) for a conversation based on this first message. 
Reply ONLY with the title, no quotes, no punctuation.
Use the language of the query. If the query is in Spanish use Spanish, if it's in English, use English."""

    free_models = [
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "meta-llama/llama-3.2-3b-instruct:free",
    ]

    client = OpenAI(
        api_key=os.environ['OPENROUTER_API_KEY'],
        base_url="https://openrouter.ai/api/v1",
    )

    def _call(model: str) -> str | None:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=30,
                timeout=30,
            )
            title = response.choices[0].message.content.strip().strip('"\'')
            return title if title else None
        except Exception as e:
            print(f"[TITLE GEN] {model} failed: {e}")
            return None

    for model in free_models:
        title = await asyncio.to_thread(_call, model)
        if title:
            db = SessionLocal()
            try:
                edit_conversation_logic(conv_id, ConversationUpdate(title=title), db=db)
                print(f"[TITLE GEN] Set title '{title}' via {model}")
                return
            finally:
                db.close()

    print("[TITLE GEN] All free models failed — conversation remains untitled")


async def chat_persistence_wrapper(prompt: Prompt):
    db = SessionLocal()
    try:
        db_conversation = db.query(Conversation).where(
            Conversation.id == prompt.conversation_id
        ).first()

        # Check if this is the first user message
        msg_count = db.query(Message).filter(
            Message.conversation_id == prompt.conversation_id
        ).count()
        is_first_message = msg_count == 0

        edit_conversation_logic(
            prompt.conversation_id,
            ConversationUpdate(messages=[MessageBase(is_user=True, text=prompt.user_message)]),
            db=db,
        )

        # Launch title generation in parallel if first message
        title_task = None
        if is_first_message:
            title_task = asyncio.create_task(
                _generate_title(prompt.conversation_id, prompt.user_message)
            )

        model_config = get_model_config()
        full_text = ""
        async for token in openai_agent(
            ConversationSchema.model_validate(db_conversation), model_config
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

        if title_task:
            await title_task
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
