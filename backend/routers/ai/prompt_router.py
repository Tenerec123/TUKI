from ...schemas import ConversationSchema
from semantic_router import Route
from semantic_router.encoders import HuggingFaceEncoder
from semantic_router.routers import SemanticRouter
from datetime import date
import json
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ['OPENROUTER_API_KEY']
)

router_prompt = """Route user message to one class:
- "normal": chat, advice, concepts (no DB/internet needed)
- "query": view/list/check data (DB or internet)
- "execution": create/update/delete data
- "unsure": vague, ambiguous, conditional

Rules:
- Concept question → normal
- Needs current info → query
- Conditional CREATE/UPDATE/DELETE → execution
- Doubt → unsure
- Unsure better than normal/query/execution false positive

Examples:
"add task X" → execution
"show tasks" → query
"what is X" → normal
"do whatever's best" → unsure

Output ONLY: {"route": "normal|query|execution|unsure"}"""


def get_llm_predictions(query:str) -> dict:
    msgs = [
        {"role": "system", "content": router_prompt},
        {"role": "user", "content": f"[LAST MESSAGE] {query}"}
    ]
    response = client.chat.completions.create(
        model="google/gemini-2.5-flash-lite",
        messages=msgs,
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    raw_content = response.choices[0].message.content
    return json.loads(raw_content)


def get_base_rules():
    today_str = date.today().isoformat()
    return f"""T.U.K.I. — productivity assistant. Tone: direct, technical.
User: developer. Lang: Spanish/English. Date: {today_str}
Priority: Urgency(0-32) + Importance(0-32) = [1,64]
Rules: No raw JSON in responses. No tool calls in visible text. Use $ for LaTeX."""

specific_rules = {
    'normal': "You will not need function calling. Respond as a normal text agent.",
    'query': "You MUST use read-only tools (GetAllTasks, GetAllProjects, GetAllRoutines, CheckEmail, SearchTasks, SearchProjects, SearchRoutines, Weather, WebSearch) to answer the user's request. Do NOT create, update, or delete anything. Use Weather for current conditions and forecasts. Use WebSearch when the user asks about current events, news, recent tech info, or factual questions that need up-to-date external information.",
    'execution': """FOLLOW THESE STEPS:
1. FIRST: use read-only tools (GetAllTasks, GetAllProjects, GetAllRoutines, CheckEmail, SearchTasks, SearchProjects, SearchRoutines, Weather, WebSearch) to verify existing data and find the correct IDs.
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
