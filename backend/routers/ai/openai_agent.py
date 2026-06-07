from datetime import date
import os
from ...schemas import ConversationSchema
from .tools import tool_schemas, ToolDict
from openai import AsyncOpenAI
import json
from .prompt_router import get_routed_rules, llm_router

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ['OPENROUTER_API_KEY']
)

async def openai_agent(conversation:ConversationSchema, model, max_inferences = 5):
    rules = get_routed_rules(conversation)
    messages = [{'role':'developer','content':rules}]

    for i, msg in enumerate(conversation.messages):
        messages.append({'role':'user' if msg.is_user else 'assistant', 'content':msg.text})

    try:
        for i in range(max_inferences):
            is_last_attempt = (i == max_inferences - 1)
            tool_selection = None if is_last_attempt  else "auto"
            
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_schemas,
                tool_choice=tool_selection,
                reasoning_effort="none",
                parallel_tool_calls=True,
                stream=True,
            )

            full_tool_calls = {} # Para reconstruir los argumentos fragmentados

            async for chunk in response:
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    # print(f'[DEBUG] TOOL CALL CHUNK: {delta.tool_calls}')
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
        print(e)
        yield 'ERROR_TOKEN'
    return