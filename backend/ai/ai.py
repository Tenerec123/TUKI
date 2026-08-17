from ..schemas import ConversationSchema, Prompt, ConversationUpdate, MessageBase
from fastapi import APIRouter, UploadFile, Form, Depends, File, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from ..database import get_db, SessionLocal
from ..models import Conversation, Message
from sqlalchemy.orm import Session
from ..routers.conversations import edit_conversation_logic
from .stt import stt_conversion_logic
from .agent import openai_agent
from .stream_manager import stream_manager
from openai import OpenAI
import os
import asyncio
import json
from .config import MAX_AGENTIC_ROUNDS, SYSTEM_PROMPT, get_model_config
from .tools.discovery import ORCHESTRATOR_TOOL_SCHEMAS
router = APIRouter(
    prefix="/api/ai",
    tags=["ai"]
)

def _build_messages(conversation: ConversationSchema, base_prompt) -> list:
    msgs = [{'role': 'developer', 'content': base_prompt}]
    for msg in conversation.messages:
        if not (msg.text or '').strip():
            continue
        if msg.type == 'prompt':
            msgs.append({'role': 'user', 'content': msg.text})
        elif msg.type == 'tool':
            try:
                tc = json.loads(msg.text)
                msgs.append({
                    'role': 'assistant',
                    'tool_calls': [{
                        'id': tc['id'],
                        'type': 'function',
                        'function': {'name': tc['name'], 'arguments': tc['args']},
                    }],
                    'content': None,
                })
                msgs.append({
                    'role': 'tool',
                    'tool_call_id': tc['id'],
                    'name': tc['name'],
                    'content': tc['result'],
                })
            except (json.JSONDecodeError, KeyError):
                msgs.append({'role': 'assistant', 'content': msg.text})
        else:
            msgs.append({'role': 'assistant', 'content': msg.text})
    return msgs


async def _generate_title(conv_id: int, user_message: str):
    system_prompt = """Generate a short, descriptive title (max 6 words) for a conversation based on this first message. 
Reply ONLY with the title, no quotes, no punctuation.
Use the language of the query. If the query is in Spanish use Spanish, if it's in English, use English."""

    models = [
        "mistralai/mistral-nemo",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-nano-9b-v2:free",
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

    for model in models:
        title = await asyncio.to_thread(_call, model)
        if title:
            db = SessionLocal()
            try:
                edit_conversation_logic(conv_id, ConversationUpdate(title=title), db=db)
                print(f"[TITLE GEN] Set title '{title}' via {model}")
                return
            finally:
                db.close()

    print("[TITLE GEN] All models failed — conversation remains untitled")


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
            ConversationUpdate(messages=[MessageBase(type='prompt', text=prompt.user_message)]),
            db=db,
        )

        title_task = None
        if is_first_message:
            title_task = asyncio.create_task(
                _generate_title(prompt.conversation_id, prompt.user_message)
            )

        model_config = get_model_config()
        messages = _build_messages(ConversationSchema.model_validate(db_conversation), SYSTEM_PROMPT)
        base_len = len(messages)
        async for token in openai_agent(
            messages=messages,
            model_config = model_config['orchestrator'],
            max_rounds=MAX_AGENTIC_ROUNDS,
            tool_schemas=ORCHESTRATOR_TOOL_SCHEMAS,
            conv_id=db_conversation.id):
            if token == "ERROR_TOKEN":
                break
            stream_manager.push(prompt.conversation_id, token)

        db_format_messages = []
        call_list = []
        for new_msg in messages[base_len:]:
            if new_msg['role'] == "assistant":
                calls = new_msg.get("tool_calls","")
                content = new_msg.get('content', '')
                if calls == "" and content.strip():
                    db_format_messages.append(MessageBase(type="agent", text=content))
                else:
                    call_list.extend([{'id':tc['id'], 'args':tc['function']['arguments']} for tc in calls])
            elif new_msg['role'] == "tool":
                call = next(filter(lambda obj: obj['id'] == new_msg['tool_call_id'], call_list), None)
                if call is not None:
                    tc_for_db = {
                        'id':call['id'],
                        'name':new_msg['name'],
                        'args':call['args'],
                        'result':new_msg['content']
                    }
                    db_format_messages.append(MessageBase(type="tool", text=json.dumps(tc_for_db)))
        if db_format_messages:
            edit_conversation_logic(
                prompt.conversation_id,
                ConversationUpdate(messages=db_format_messages),
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
    return StreamingResponse(stream_manager.stream(conv_id), media_type="application/x-ndjson")


@router.post('/stt')
async def stt_conversion(file: UploadFile = File(...), conv_id = Form(...), db:Session = Depends(get_db)):
    result_text = await stt_conversion_logic(file, conv_id, db)
    return result_text
