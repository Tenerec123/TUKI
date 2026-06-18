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

router_prompt = """You are a routing classifier for a productivity assistant. Your ONLY output is one of four classes.

CLASSES:
- "normal": Greetings, general chat, conceptual questions, advice. NO database access needed.
- "query": The user wants to SEE, LIST, REVIEW, or CHECK data from the database (tasks, projects, routines, emails).
- "execution": The user wants to CREATE, UPDATE, DELETE, or MODIFY data in the database.
- "unsure": ANY doubt, vague request, or ambiguous intent. Better unsure than wrong.

CRITICAL RULE: False positives (classifying "execution" when unsure) are MUCH worse than returning "unsure".
If you have even a hint of doubt → "unsure".

Examples:
"Show me all my active tasks for this week." → {"route": "query"}
"Add a new routine called Gym Push Day." → {"route": "execution"}
"Change the deadline of my SolveSheep task to tomorrow." → {"route": "execution"}
"Do you think I should create any other project?" → {"route": "unsure"}
"How can I improve my daily focus?" → {"route": "normal"}
"hola" → {"route": "normal"}
"borrá la tercera tarea" → {"route": "execution"}
"qué hay en mi lista" → {"route": "query"}
"hacé lo que sea mejor" → {"route": "unsure"}
"organizame el día" → {"route": "unsure"}
"revisá si tengo emails nuevos" → {"route": "query"}
"chequeame el correo" → {"route": "query"}
"hay algo importante en mi bandeja de entrada" → {"route": "query"}

Output ONLY a raw JSON object: {"route": "normal" | "query" | "execution" | "unsure"}
No explanations, no markdown."""


def get_llm_predictions(query:str) -> dict:
    msgs = [
        {"role": "system", "content": router_prompt},
        {"role": "user", "content": f"[LAST MESSAGE] {query}"}
    ]
    response = client.chat.completions.create(
        model="granite4.1:3b",
        messages=msgs,
        temperature=0.0,
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
- Never output raw JSON blocks in the final response text. JSON is reserved for tool calls.
- Math/Science: Use $ for inline LaTeX and $$ for display blocks.
- Never write tool calls in the visible response.
"""

specific_rules = {
    'normal': "You will not need function calling. Respond as a normal text agent.",
    'query': "You MUST use read-only tools (GetAllTasks, GetAllProjects, GetAllRoutines, CheckEmail, SearchTasks, SearchProjects, SearchRoutines) to answer the user's request. Do NOT create, update, or delete anything.",
    'execution': """FOLLOW THESE STEPS:
1. FIRST: use read-only tools (GetAllTasks, GetAllProjects, GetAllRoutines, CheckEmail, SearchTasks, SearchProjects, SearchRoutines) to verify existing data and find the correct IDs.
2. THEN: use Create/Update/Delete tools to make the requested changes.
3. Never guess IDs — always read first.""",
    'unsure': "You have full freedom. Use tools if the user needs data or actions. Respond normally if it's general chat. Decide based on what makes sense.",
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
                "que opinas acerca de esto dame tu criterio",
                "necesito ideas creativas o ayuda para pensar",
                "gracias por la explicacion entiendo el punto",
                "puedes hablarme de la historia o teoria de algo",
                "que piensas sobre el examen o la prueba de",
                "dame una explicacion tecnica de como funciona"
            ]
        )
        query = Route(
            name="query",
            utterances=[
                "mostrar", "listar", "ver", "consultar", "buscar", "enseñame",
                "que tengo pendiente para hacer", "dime que hay registrado en el sistema",
                "revisar el historial", "cuales son mis elementos activos",
                "dame una lista de", "comprobar el estado de", "visualizar registros",
                "muestra las cosas que tengo", "enseñame lo que hay guardado",
                "que tareas tengo", "mostrame los proyectos"
            ]
        )
        execution = Route(
            name="execution",
            utterances=[
                "crear", "añadir", "eliminar", "borrar", "modificar", "actualizar",
                "quita esto inmediatamente", "pon una nueva entrada", "cambia el estado a",
                "inserta un elemento", "actualizame este registro",
                "cancela la ejecucion de", "registra un nuevo", "saca esto del sistema",
                "modificame el parametro de"
            ]
        )
        unsure = Route(
            name="unsure",
            utterances=[
                "no se que hacer", "tu decides", "haz lo que creas mejor",
                "ayudame con esto", "dame una mano", "organizame el dia",
                "revisa todo y decideme", "echale un ojo a todo",
                "que me recomiendas hacer", "pon orden en el sistema",
                "ocupate de lo que haya que hacer", "no estoy seguro",
                "hace lo que sea necesario", "como ves todo"
            ]
        )
        routes = [query, execution, normal, unsure]
        encoder = HuggingFaceEncoder(name="lightonai/modernbert-embed-large")

        _sr = SemanticRouter(
            encoder=encoder,
            routes=routes,
            auto_sync="local",
            top_k=5
        )
    return _sr


def classify(conversation: ConversationSchema) -> str:
    """Returns one of: normal, query, execution, unsure
    Uses LLM router only (semantic router disabled until false-positive rate improves)."""
    msg_preview = conversation.messages[-1].text[:120]
    result = llm_router(conversation)
    print(f"[ROUTER] '{msg_preview}' → {result}")
    return result


def get_routed_rules(conversation: ConversationSchema) -> str:
    route = classify(conversation)
    return get_base_rules() + "\n" + specific_rules[route]


def llm_router(conversation: ConversationSchema) -> str:
    prediction = get_llm_predictions(conversation.messages[-1].text)
    return prediction.get('route', 'unsure')
