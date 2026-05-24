from schemas import ConversationSchema, Prompt, ConversationUpdate, MessageBase
from fastapi import APIRouter, UploadFile, Form, Depends, File
from fastapi.responses import StreamingResponse
from database import get_db
from models import Conversation
from sqlalchemy.orm import Session
from ..conversations import edit_conversation_logic
import io
from .openai import openai_agent
from faster_whisper import WhisperModel
router = APIRouter(
    prefix="/api/ai", # Todos los endpoints empezarán con esto
    tags=["ai"]        # Organiza la documentación automática (/docs)
)

async def chat_persistence_wrapper(prompt:Prompt, db):
    db_conversation = db.query(Conversation).where(Conversation.id == prompt.conversation_id).first()
    edit_conversation_logic(prompt.conversation_id, ConversationUpdate(messages=[MessageBase(is_user=True, text=prompt.user_message)]), db=db)
    full_text = ""
    for token in openai_agent(ConversationSchema.model_validate(db_conversation), prompt.model):
        if token =='ERROR_TOKEN': 
            yield "\n THERE HAS BEEN AN ERROR, TRY ANOTHER MODEL"
            break
        full_text += token
        yield token # Re-enviamos al endpoint
    edit_conversation_logic(prompt.conversation_id, ConversationUpdate(messages=[MessageBase(is_user=False, text=full_text)]), db=db)

def ai_response_logic(prompt:Prompt, db:Session):
    return StreamingResponse(chat_persistence_wrapper(prompt, db), media_type="text/plain")

@router.post("/")
def ai_response(prompt:Prompt, db:Session = Depends(get_db)):
    return ai_response_logic(prompt=prompt, db=db)

_model = None

def get_whisper_model():
    global _model
    if _model is None:
        _model = WhisperModel("tiny", device="cuda", compute_type="int8")
    return _model

@router.post('/stt')
async def stt_conversion(file: UploadFile = File(...), conv_id = Form(...), db:Session = Depends(get_db)):
    model = get_whisper_model()
    audio_data = await file.read()
    audio_file = io.BytesIO(audio_data)
    segments, info = model.transcribe(audio_file, beam_size=5, language="es", initial_prompt="Hablando con TUKI, mi asistente")
    full_text = ""
    for segment in segments:
        full_text += segment.text + " "
    return full_text