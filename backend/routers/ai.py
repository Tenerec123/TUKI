from datetime import datetime
import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types
from schemas import ConversationSchema, MessageSchema, Prompt, ConversationUpdate, MessageBase
from fastapi import APIRouter, UploadFile, Form, Depends, File
from fastapi.responses import StreamingResponse
from .tools import ProcessBatch, tool_schemas, ToolDict
from openai import OpenAI
import json
from database import get_db
from models import Conversation
from sqlalchemy.orm import Session
from .conversations import edit_conversation_logic
import io
from faster_whisper import WhisperModel
basedir = Path(__file__).resolve().parent.parent 
load_dotenv(basedir / ".env")
gemini_client = genai.Client(api_key=os.getenv('GOOGLE_GENAI_API_KEY', ''))
router = APIRouter(
    prefix="/api/ai", # Todos los endpoints empezarán con esto
    tags=["ai"]        # Organiza la documentación automática (/docs)
)


def GetRules():
    return
rules= f"""
Identity: T.U.K.I. Technical Assistant. Style: Direct, technical, no filler. Your user will be the creator
Use always this date format: DD/MM/YYYY
All priorities are between 1 and 64 (0 is not asigned)
There is only a tool, ProcessBatch. Inside it you can use all the other tools multiple times
When generating mathematical content, use the $ delimiter for inline LaTeX and $$ for display. 
"""
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ['OPENROUTER_API_KEY']
) # OpenRouter para multicontratación
config = types.GenerateContentConfig(
    system_instruction=rules,
    tools=[ProcessBatch],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False),
    max_output_tokens=10000,
)

def gemini_agent(conversation:ConversationSchema):
        # Lista de modelos por orden de prioridad para T.U.K.I.
    MODEL_STACK = [
        'gemini-3.1-flash-lite-preview',  
        'gemini-2.0-flash',
        'gemini-flash-lite-latest',
        'gemini-2.0-flash',
        'models/gemini-3.1-pro-preview',
        'models/gemini-2.5-pro',
        'models/gemini-3-flash-preview',
        'models/gemini-3.1-flash-lite-preview',
        'models/gemini-2.5-flash',
        'models/gemini-flash-latest',
        'models/gemini-pro-latest',
    ]
    
    for model in MODEL_STACK:
        try:
            history_map = [
                types.Content(
                    role="model", 
                    parts=[types.Part(text=f"TIME: {datetime.today().strftime("%d/%m/%Y, %H:%M:%S")}")]
                    )
                ]
            prompt_text = ""
            for i, msg in enumerate(conversation.messages):
                if i == len(conversation.messages) -1:
                    prompt_text = msg.text
                    break
                history_map.append(types.Content(
                    role= 'user' if msg.is_user else 'model',
                    parts=[types.Part(text=msg.text)]
                ))

            # 2. Crear el chat con el historial cargado
            chat = gemini_client.chats.create(
                model=model,
                config=config,
                history=history_map,
            )

            response = chat.send_message_stream(prompt_text)    
            for chunk in response:
                if chunk.text:
                    print(chunk.text)
                    yield chunk.text
            return # Avoids returning again the AI response of the next model
            
        except Exception as e:
            if '503' in str(e) or "429" in str(e):
                print(f"Problems with {model}, system will try the next one")
                continue
            elif '404' in str(e):
                print(f"ERROR!!!!!! {model} NO EXISTE")
                continue
            else: raise e
    return {'response':"All models are UNAVAILABLE, impossible to respond"}

formatted_tools = [
    {
        "type": "function",
        "function": schema
    } 
    for schema in tool_schemas
]

def openai_agent(conversation:ConversationSchema, max_inferences = 3):

    MODEL_STACK = [
        "nvidia/nemotron-3-super-120b-a12b:free"
        ,   # Rey actual
        "google/gemma-4-31b-it:free",               # Nuevo en 2026, brutal para JSON
        "openai/gpt-oss-120b:free",                 # El nuevo MoE abierto de OpenAI
        "qwen/qwen3-coder:free",                    # El mejor para lógica de sistemas
        "nvidia/nemotron-3-super-120b-a12b:free",   # Enorme, ideal para 100 tareas
        "z-ai/glm-4.5-air:free",     
        "meta-llama/llama-3.3-70b-instruct:free"
        "minimax/minimax-m2.5:free"]
    function_outputs = []
    for model in MODEL_STACK:
        try:
            for i in range(max_inferences):
                is_last_attempt = (i == max_inferences - 1)
                tool_selection = None if is_last_attempt else "auto"
                messages = [{'role':'developer','content':rules}]
                for i, msg in enumerate(conversation.messages):
                    messages.append({'role':'user' if msg.is_user else 'assistant', 'content':msg.text})
                for f_output in function_outputs:
                    messages.append({'role':'tool','content':f_output})

                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=formatted_tools if not is_last_attempt else None,
                    tool_choice=tool_selection,
                    stream=True,
                )

                full_tool_calls = {} # Para reconstruir los argumentos fragmentados

                is_final_inference = False

                for chunk in response:
                    delta = chunk.choices[0].delta
                    
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            # Los chunks de herramientas vienen con índices, hay que ensamblarlos
                            if tc.index not in full_tool_calls:
                                full_tool_calls[tc.index] = tc
                            else:
                                # Acumulamos los fragmentos del JSON de argumentos
                                full_tool_calls[tc.index].function.arguments += tc.function.arguments
                        continue # No hacemos yield de nada al usuario aún

                    if delta.content:
                        if not is_final_inference: is_final_inference = True
                        yield delta.content

                if full_tool_calls:
                    results = []
                    for tool_call in full_tool_calls.values():
                        args = json.loads(tool_call.function.arguments)
                        func = ToolDict.get(tool_call.function.name, None)
                        if func is not None:
                            output = func(**args)
                        else: output = f"Tool {tool_call.function.name} does not exist"
                        results.append(output)
                    messages.append({"role": "assistant", "content":f"Tool calling outputs: {results}"})
                if is_final_inference: return
                
        except Exception as e:
            print(e)
    return

async def chat_persistence_wrapper(prompt:Prompt, db):
    db_conversation = db.query(Conversation).where(Conversation.id == prompt.conversation_id).first()
    edit_conversation_logic(prompt.conversation_id, ConversationUpdate(messages=[MessageBase(is_user=True, text=prompt.user_message)]), db=db)
    full_text = ""
    for token in openai_agent(ConversationSchema.model_validate(db_conversation)):
        full_text += token
        yield token # Re-enviamos al endpoint
    edit_conversation_logic(prompt.conversation_id, ConversationUpdate(messages=[MessageBase(is_user=False, text=full_text)]), db=db)


def ai_response_logic(prompt:Prompt, db:Session):
    return StreamingResponse(chat_persistence_wrapper(prompt, db), media_type="text/plain")


@router.post("/")
def ai_response(prompt:Prompt, db:Session = Depends(get_db)):
    return ai_response_logic(prompt=prompt, db=db)

model = WhisperModel("tiny", device="cuda", compute_type="int8")
@router.post('/stt')
async def stt_conversion(file: UploadFile = File(...), conv_id = Form(...), db:Session = Depends(get_db)):
    audio_data = await file.read()
    audio_file = io.BytesIO(audio_data)
    segments, info = model.transcribe(audio_file, beam_size=5, language="es", initial_prompt="Hablando con TUKI, mi asistente")
    full_text = ""
    for segment in segments:
        full_text += segment.text + " "
    return full_text