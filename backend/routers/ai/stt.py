from fastapi import UploadFile, Form, Depends, File
from ...database import get_db
from sqlalchemy.orm import Session
import io
from faster_whisper import WhisperModel
import io
from .ai import router

_model = None

def get_whisper_model():
    global _model
    if _model is None:
        _model = WhisperModel("tiny", device="cpu", compute_type="int8")
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