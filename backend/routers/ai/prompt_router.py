from ...schemas import ConversationSchema
from semantic_router import Route
from semantic_router.encoders import HuggingFaceEncoder
from semantic_router.routers import SemanticRouter
from datetime import date
import json
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama" 
)

router_prompt = """SYSTEM:
You are a high-precision semantic routing microservice for an AI assistant backend.
Your sole task is to analyze the user's intent and determine the necessary infrastructure flags based EXCLUSIVELY on the final message of the provided conversation snippet.

ROUTING PARAMETERS:
1. "read" (boolean): 
   - Set to true if the user's request requires retrieving historical data, projects routines or tasks.
   - Set to false if it is a generic query, asking for advice, greeting, or philosophical/conceptual explanation that the model can answer using its parametric knowledge.

2. "write" (boolean):
   - Set to true if the user explicitly asks to perform an action, trigger an external script, run code, execute a specific function, or hardware-related automation.

Input: "Show me all my active tasks for this week."
Output: {"read": true, "write": false}

Input: "Add a new routine called Gym Push Day."
Output: {"read": false, "write": true}

Input: "Change the deadline of my SolveSheep task to tomorrow."
Output: {"read": false, "write": true}

Input: "Do you think I should create any other project?"
Output: {"read": false, "write": false}

Input: "How can I improve my daily focus?"
Output: {"read": false, "write": false}

OPERATIONAL RULES:
1. You will be provided with a brief conversation history.
2. Analyze the context to understand the evolution of the topic, but evaluate the intent based ONLY on the LAST message in the transcript (the current user request).
3. Output strictly a raw JSON object with the keys "read" and "write". Both values must be booleans (true/false).
4. Do not include any explanations, markdown code blocks (```json), or extra tokens.
"""

def get_llm_predictions(history_context: list[dict]) -> dict:
    print(history_context)
    msgs = [
            {
                "role": "system", 
                "content": router_prompt
            },
            {
                "role": "user", 
                "content": f"[HISTORY]{''.join([f"\n{msg['role']}: {msg['text']}" for msg in history_context])}"
            }
        ]
    response = client.chat.completions.create(
        model="cieloforge/qwen2.5-coder-3b-instruct-spec:latest",  # El nombre exacto que te sale en 'ollama list'
        messages=msgs,
        temperature=0.0, # Evita variaciones estadísticas
        # Forzar formato JSON compatible con el motor local
        response_format={"type": "json_object"} 
    )
    
    raw_content = response.choices[0].message.content
    return json.loads(raw_content)


def get_base_rules():
    today_str = date.today().strftime('%A, %d/%m/%Y')     
    return f"""
[IDENTITY & STYLE]
Role: T.U.K.I. (Technical Utility & Knowledge Interface). You are the advanced AI assistant of a personal productivity system, operating as a background Jarvis-like interface.
User: Creator/Developer.
Tone: Direct, technical, no-filler, robot.
Language: Spanish or English.

[TIME]
Format: DD/MM/YYYY
Today: {today_str}

[PRIORITY]
Priority = Urgency (0-32, risk if not done before deadline) + Importance (0-32, structural impact).
Range: [1, 64]

[FORMATTING]
- JSON RESTRICTION: Never output raw JSON blocks in the final response text. JSON formatting is strictly reserved for tool/function calling parameters, unless explicitly requested by the user.- Math/Science: Use $ for inline LaTeX and $$ for display blocks. No markdown alternatives for math equations.
- You MUSTN'T WRITE TOOL CALLS IN THE RESPONSE (what user reads)
"""

normal_rules = "You will not need (almost sure, but maybe yes) function calling, respond as a normal text agent"

specific_rules = {
    'query_db':"you MUST use a get-info function in order to respond the user's answer",
    'execution':
"""FOLLOW STRICTLY THESE STEPS:
1-use get-info functions to be sure you don't create an object that already exists or to find the ids of the objects you have to upadte/delete
2-use create/update/delete functions to make the changes the user demands.""",
    None:normal_rules,
    'normal':normal_rules
}

_sr = None

def get_sr():
    global _sr
    if _sr is None:
        OPTIMIZED_THRESHOLD = 0.70
        normal = Route(
            name="normal",
            utterances=[
                "hola buenas como estas tuki",
                "explicame un concepto teorico o filosofico",
                "dame consejos y recomendaciones generales sobre un tema",
                "que opinas acerca de esto, dame tu criterio",
                "necesito ideas creativas o ayuda para pensar",
                "gracias por la explicacion, entiendo el punto",
                "puedes hablarme de la historia o teoria de algo",
                "que piensas sobre el examen o la prueba de",
                "dame una explicacion tecnica de como funciona"
            ]
        )
        query_db = Route(
            name="query_db",
            utterances=[
                "mostrar", "listar", "ver", "consultar", "buscar", "enseñame",
                "que tengo pendiente para hacer", "dime que hay registrado en el sistema",
                "revisar el historial o los logs", "cuales son mis elementos activos",
                "dame una lista de", "comprobar el estado de", "visualizar registros",
                "muestra las cosas que tengo", "enseñame lo que hay guardado"
            ]
        )
        execution = Route(
            name="execution",
            utterances=[
                "crear", "añadir", "eliminar", "borrar", "modificar", "actualizar",
                "quita esto inmediatamente", "pon una nueva entrada", "cambia el estado a",
                "ejecutar script o comando", "inserta un elemento", "actualizame este registro",
                "cancela la ejecucion de", "registra un nuevo", "saca esto del sistema",
                "modificame el parametro de"
            ]
        )
        routes = [query_db, execution, normal]
        encoder = HuggingFaceEncoder(name="lightonai/modernbert-embed-large")

        _sr = SemanticRouter(
            encoder=encoder,
            routes=routes,
            auto_sync="local",
            top_k=5
        )
    return _sr

def get_routed_rules(conversation:ConversationSchema):
    sr = get_sr()

    result = sr(conversation.messages[-1].text).name
    if result is None: 
        result = llm_router(conversation)
    print(result)
    return get_base_rules() + "\n" + specific_rules[result]
    
def llm_router(conversation:ConversationSchema):
    role_namer={False:'AI', True:'USER'}
    prediction = get_llm_predictions([
        {'role':role_namer[message.is_user],'text':message.text} for message in conversation.messages[-3:]
    ])
    return 'normal' if not prediction['read'] and not prediction['write'] else ('execution' if prediction['write'] else 'query_db') 
    