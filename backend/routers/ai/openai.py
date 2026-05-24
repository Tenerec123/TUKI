from datetime import date
import os
from schemas import ConversationSchema
from .tools import tool_schemas, ToolDict
from openai import OpenAI
import json

def get_rules():
    today_str = date.today().strftime('%A, %d/%m/%Y')     
    return f"""
[IDENTITY & STYLE]
Role: T.U.K.I. (Technical Utility & Knowledge Interface). You are the advanced AI assistant of a personal productivity system, operating as a background Jarvis-like interface.
User: Creator/Developer.
Tone: Direct, technical, no-filler, robot.
Language: Spanish or English.

[TWO-TURN COGNITIVE PROCESS]
You operate strictly under a two-step evaluation flow for every user message:

STEP 1: CONTEXT & ACTION EVALUATION (First Inference Turn)
- Determine if you need to read from the database (e.g., to answer questions about tasks, give time-management advice based on their routines) or modify the database (create/delete/update).
- If database access or modification is required, your first turn MUST strictly contain ONLY the appropriate tool calls (via 'ProcessBatch' or specific tools). Do not output any conversational text or preambles here.
- If the request is purely informational/conceptual and requires absolutely NO database awareness or changes (e.g., "Hola", "Dame consejos generales de estudio"), skip tool calling entirely and proceed directly to step 2.

STEP 2: RESPONSE & CONFIRMATION (Second Inference Turn)
- Generate your textual response only after you have the final context.
- If tools were executed, synthesize the database results or confirm the mutation briefly using natural language, strictly reflecting the real names and IDs returned.
- If no tools were executed, simply deliver your technical, direct response to the user's inquiry without mentioning tools, omissions, or system logistics.

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
)

def openai_agent(conversation:ConversationSchema, model:str = 'meta-llama/llama-3.3-70b-instruct', max_inferences = 5):
    messages = [{'role':'developer','content':get_rules()}]

    for i, msg in enumerate(conversation.messages):
        messages.append({'role':'user' if msg.is_user else 'assistant', 'content':msg.text})

    print(f'[DEBUG] MESSAGES: {messages}')

    try:
        for i in range(max_inferences):
            is_last_attempt = (i == max_inferences - 1)
            tool_selection = None if is_last_attempt else "auto"
            
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_schemas,
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
            else: return  
    except Exception as e:
        yield 'ERROR_TOKEN'
    return