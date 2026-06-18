from datetime import datetime, date
import os
from google import genai
from google.genai import types
from ...schemas import ConversationSchema
gemini_client = genai.Client(api_key=os.getenv('GOOGLE_GENAI_API_KEY', ''))

def get_rules():
    today_str = date.today().strftime('%A, %d/%m/%Y')     
    return f"""
[IDENTITY & STYLE]
Role: T.U.K.I. (Technical Utility & Knowledge Interface). You are the advanced AI assistant of a personal productivity system, operating as a background Jarvis-like interface.
User: Creator/Developer.
Tone: Direct, technical, no-filler, robot.
Language: Spanish or English.

[TIME CONTEXT]
Format: DD/MM/YYYY
Today: {today_str}

[PRIORITY ALGORITHM]
Priority = Urgency (0-32, risk if not done before deadline) + Importance (0-32, structural impact).
Range: [1, 64]

[TOOL USE RULES]
1. ZERO GUESSING PROTOCOL: You are STRICTLY FORBIDDEN from guessing, predicting, or hallucinating object IDs (e.g., executing DeleteTask with ID 1, 2, or 123 without reading first).
2. PRE-CONDITION: If the user request targets objects by semantic text or names (e.g., "todo lo que tenga que ver con X"), you DO NOT KNOW the IDs. Therefore, your first turn MUST use read tools ('GetAllTasks', 'GetAllProjects', 'GetAllRoutines') to inspect first.
3. EXECUTION PHASE: Only after the read tools return data, execute the mutations.
4. ARGS INTEGRITY: Arguments must exactly match the tool parameter schemas. Never invent or omit parameters.

[FORMATTING]
- Math/Science: Use $ for inline LaTeX and $$ for display blocks. No markdown alternatives for math equations.
"""

config = types.GenerateContentConfig(
    system_instruction=get_rules(),
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