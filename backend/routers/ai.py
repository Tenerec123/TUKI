from datetime import datetime, date
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



rules_gemini= f"""
Identity: T.U.K.I. Technical Assistant. Style: Direct, technical, no filler. Your user will be the creator.
Responses as short as possible, if you have to execute tools you don't have to explain all you have done, only make a little summary or just say what you have done if it's too long.
Priority = Urgency (0-32, based on the consequences if the task is not done before the deadline) + Importance (0-32, based on task type/impact), capped at 1-64. Higher = more urgent/important.
Use always this date format: DD/MM/YYYY
All priorities are between 1 and 64 (0 is not asigned)
There is only a tool, ProcessBatch. Inside it you can use all the other tools multiple times
When generating mathematical content, use the $ delimiter for inline LaTeX and $$ for display."""
def get_openai_rules():
    today_str = date.today().strftime('%A, %d/%m/%Y')     
    return f"""
[IDENTITY & STYLE]
Role: T.U.K.I. Technical Assistant.
User: Creator/Developer.
Tone: Direct, technical, no-filler, robot.
Execution: After using tools, your textual response must strictly reflect the real names and IDs returned in the execution messages using natural language. 

[TIME CONTEXT]
Format: DD/MM/YYYY
Today: {today_str}

[PRIORITY ALGORITHM]
Priority = Urgency (0-32, risk if not done before deadline) + Importance (0-32, structural impact).
Range: [1, 64]

[TOOL USE RULES]
1. ZERO GUESSING PROTOCOL: You are STRICTLY FORBIDDEN from guessing, predicting, or hallucinating object IDs (e.g., executing DeleteTask with ID 1, 2, or 123 without reading first).
2. PRE-CONDITION: If the user request targets objects by semantic text or names (e.g., "todo lo que tenga que ver con X"), you DO NOT KNOW the IDs. Therefore, your very first inference turn MUST be a 'ProcessBatch' containing ONLY the database read functions needed to inspect the context ('GetAllTasks', 'GetAllProjects', 'GetAllRoutines').
3. EXECUTION PHASE: Only after the tool execution returns the database arrays, you will parse them, filter the items matching the user's intent, and execute the mutations ('DeleteTask', 'CreateProject', etc.) via a final 'ProcessBatch' call in the second turn.
4. SINGLE MUTATION BATCH: For any request requiring multiple modifications, you must group them into a single 'ProcessBatch' call. Sequential individual mutation calls are prohibited.
5. ARGS INTEGRITY: The 'args' object inside 'ProcessBatch' must exactly match the schema parameters of the target function. Never invent or omit parameters.

[FORMATTING]
- Math/Science: Use $ for inline LaTeX and $$ for display blocks. No markdown alternatives for math equations.
"""
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ['OPENROUTER_API_KEY']
) # OpenRouter para multicontratación
config = types.GenerateContentConfig(
    system_instruction=rules_gemini,
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

formatted_tools = [{
        'type': 'function',
        'function': {
            'name': 'ProcessBatch',
            'description': 'Executes multiple task, project, or routine mutations sequentially in a single API roundtrip. Use ONLY when the user requests multiple creations or mutations.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'commands': {
                        'type': 'array',
                        'description': 'Ordered list of tools to execute.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'tool': {
                                    'type': 'string',
                                    'description': 'The exact name of the tool.',
                                    'enum': [
                                        'GetAllTasks', 'GetAllProjects', 'GetAllRoutines',
                                        'CreateTask', 'DeleteTask', 'UpdateTask',
                                        'CreateProject', 'DeleteProject', 'UpdateProject',
                                        'CreateRoutine', 'DeleteRoutine', 'UpdateRoutine'
                                    ]
                                },
                                'args': {
                                    'type': 'object',
                                    'description': 'Arguments dict mapping exactly to the chosen tool parameters.'
                                }
                            },
                            'required': ['tool', 'args']
                        }
                    }
                },
                'required': ['commands']
            }
        }
    }]

formatted_tools += [
    {
        "type": "function",
        "function": schema
    } 
    for schema in tool_schemas
]

def openai_agent(conversation:ConversationSchema, max_inferences = 5):

    MODEL_STACK = [
        'meta-llama/llama-3.3-70b-instruct',
        "nvidia/nemotron-3-super-120b-a12b:free",   # Rey actual
        'liquid/lfm-2.5-1.2b-instruct:free',
        'nousresearch/hermes-3-llama-3.1-405b:free',
        'meta-llama/llama-3.2-3b-instruct:free',
        "google/gemma-4-31b-it:free",               # Nuevo en 2026, brutal para JSON
        "openai/gpt-oss-120b:free",                 # El nuevo MoE abierto de OpenAI
        "qwen/qwen3-coder:free",                    # El mejor para lógica de sistemas
        "nvidia/nemotron-3-super-120b-a12b:free",   # Enorme, ideal para 100 tareas
        "z-ai/glm-4.5-air:free",     
        "meta-llama/llama-3.3-70b-instruct:free",
        "minimax/minimax-m2.5:free"]
    function_outputs = []
    messages = [{'role':'developer','content':get_openai_rules()}]

    for i, msg in enumerate(conversation.messages):
        messages.append({'role':'user' if msg.is_user else 'assistant', 'content':msg.text})

    print(f'[DEBUG] MESSAGES: {messages}')

    for model in MODEL_STACK:
        try:
            for i in range(max_inferences):
                is_last_attempt = (i == max_inferences - 1)
                tool_selection = None if is_last_attempt else "auto"
                
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=formatted_tools,
                    tool_choice=tool_selection,
                    reasoning_effort="none",
                    parallel_tool_calls=True,
                    stream=True,
                )

                full_tool_calls = {} # Para reconstruir los argumentos fragmentados

                for chunk in response:
                    delta = chunk.choices[0].delta
                    if delta.tool_calls:
                        print(f'[DEBUG] TOOL CALL CHUNK: {delta.tool_calls}')
                        for call_data in delta.tool_calls:
                            idx = call_data.index
                            
                            if idx not in full_tool_calls:
                                # Inicializamos el diccionario para este índice con un clon/estructura limpia
                                full_tool_calls[idx] = {
                                    "id": call_data.id,
                                    "name": call_data.function.name,
                                    "arguments": call_data.function.arguments or ""
                                }
                            else:
                                # Si el chunk actual trae id o name (raro pero posible), lo preservamos
                                if call_data.id: 
                                    full_tool_calls[idx]["id"] = call_data.id
                                if call_data.function and call_data.function.name: 
                                    full_tool_calls[idx]["name"] = call_data.function.name
                                
                                # Acumulamos los fragmentos de texto del JSON de argumentos
                                if call_data.function and call_data.function.arguments:
                                    full_tool_calls[idx]["arguments"] += call_data.function.arguments
                        continue

                    if delta.content:
                        print(f'[DEBUG] CONTENT: {delta.content}')
                        yield delta.content

                if full_tool_calls:
                    # 1. EL MODELO DEBE VER SU PROPIA LLAMADA EN EL HISTORIAL
                    print(f'[DEBUG] TOOL CALLS')
                    assistant_tool_call_msg = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": call_data['id'],
                                "type": "function",
                                "function": {
                                    "name": call_data['name'],
                                    "arguments": call_data['arguments']
                                }
                            } for call_data in full_tool_calls.values()
                        ]
                    }
                    print(f'[DEBUG] CALLS: {assistant_tool_call_msg}')
                    messages.append(assistant_tool_call_msg)

                    # 2. EJECUTAR Y AÑADIR CADA RESULTADO CON ROL 'tool'
                    for call_data in full_tool_calls.values():
                        try:
                            args = json.loads(call_data['arguments'])
                            # Buscamos la función en tu ToolDict (línea 164 de tu tools.py)
                            func = ToolDict.get(call_data['name'])
                            
                            if func:
                                result = func(**args)
                            else:
                                result = f"Error: Tool {call_data['name']} not found in ToolDict"
                        except Exception as e:
                            result = f"Execution Error: {str(e)}"
                        print(f'[DEBUG] MESSAGES: {result}')
                        # 3. Respuesta de rol 'tool' vinculada al ID
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_data['id'],
                            "name": call_data['name'],
                            "content": json.dumps(result, default=str)
                        })
                    continue
                else:
                    return
                
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