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
        model="qwen2.5:1.5b",  # El nombre exacto que te sale en 'ollama list'
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
        OPTIMIZED_THRESHOLD = 0.56
        normal = Route(
            name="normal",
            score_threshold=OPTIMIZED_THRESHOLD,
            utterances=[
                # Greetings (TUKI-specific & General)
                "hola tuki", "tuki, ¿estás ahí?", "tuki, buenos días", 
                "hola, tuki", "tuki, ¿qué tal?", "buenas, tuki",
                "hey tuki", "tuki, reportándome", "hola de nuevo, tuki",
                "tuki, despierta", "tuki, inicio de sesión", "tuki, salúdame",
                "qué pasa tuki", "tuki estás activo", "buen día tuki",
                "hello tuki", "tuki are you there", "good morning tuki",
                "hi tuki", "hey there tuki", "tuki wakeup", "tuki online",
                
                # Preguntas generales y soporte (sin DB)
                "¿cuál es el sentido de la vida?", "explícame cómo funciona un transformer",
                "¿qué es la complejidad computacional O(n)?", "tuki, ayúdame con este razonamiento",
                "¿por qué el sistema está dando errores de logs?", "dame tu opinión sobre mi última tarea",
                "¿qué significa ser un par intelectual?", "tuki, dame una idea para un proyecto",
                "cómo optimizar este código", "tuki, ¿cuál es el mejor lenguaje para sistemas?",
                "tuki, reflexiona sobre el progreso de hoy", "tuki, dame feedback técnico",
                "¿es mejor usar una base de datos relacional o no relacional?",
                "tuki, ¿cómo mejoro mi flujo de trabajo?", "explícame el concepto de escalabilidad",
                "¿cómo se calcula el valor esperado en una decisión?", "¿qué es la inyección de dependencias?",
                "dame un algoritmo óptimo para ordenar vectores", "tuki, analiza este bloque de código",
                "¿cómo mitigo la latencia en una API?", "¿cuál es la diferencia entre concurrencia y paralelismo?",
                "tuki, necesito abstraer este patrón de diseño", "revisa esta expresión regular",
                "¿qué opinas del Bachillerato Internacional?", "¿cómo estructuro una monografía técnica?",
                "tuki, dame una analogía matemática para esto", "¿cómo evito colisiones semánticas en embeddings?",
                "dime un dato interesante de teoría de números", "tuki, ¿cómo funciona el cifrado RSA?",
                
                # EN - General Queries & Knowledge (No DB)
                "what is the meaning of life?", "explain how a transformer model works",
                "what is O(n) computational complexity?", "tuki, help me with this reasoning",
                "why is the system throwing log errors?", "give me your opinion on my last task",
                "what does it mean to be an intellectual peer?", "tuki, give me an idea for a project",
                "how to optimize this code block", "tuki, what is the best language for systems?",
                "tuki, reflect on today's progress", "tuki, give me technical feedback",
                "is it better to use relational or non-relational databases?",
                "tuki, how do I improve my workflow?", "explain the concept of scalability",
                "how do you calculate expected value in decision making?", "what is dependency injection?",
                "give me an optimal sorting algorithm", "tuki, analyze this snippet",
                "how do I mitigate API latency?", "what is the difference between concurrency and parallelism?",
                "tuki, I need to abstract this design pattern", "review this regex pattern",
                "what are your thoughts on MIT computer science?", "how do I structure a technical research paper?",
                "tuki, give me a mathematical analogy for this", "how to avoid semantic collisions in embeddings?",
                "tell me an interesting fact about number theory", "tuki, how does RSA encryption work?",
                
                # Charlas abstractas / Meta-conversación
                "estoy pensando en cambiar la arquitectura", "hoy ha sido un día productivo",
                "tengo un bloqueo con un problema matemático", "la disciplina es un vector de largo plazo",
                "necesito optimizar mi tiempo de entrenamiento", "estoy evaluando el riesgo de este despliegue",
                "analicemos la escalabilidad de este backend", "estoy cansado pero hay que seguir construyendo",
                "I'm thinking about changing the architecture", "today was a productive day",
                "I'm stuck on a combinatorics problem", "discipline is a long-term vector",
                "I need to optimize my workout timing", "I am evaluating the risk of this deployment",
                "let's analyze the scalability of this backend", "exhausted but we must keep building"
            ],
        )
        query_db = Route(
            name="query_db",
            score_threshold=OPTIMIZED_THRESHOLD,
            utterances=[
                "¿qué tareas tengo pendientes?", "muéstrame mis tareas pendientes",
                "¿cuáles son mis tareas de hoy?", "lista mis tareas activas",
                "¿qué tareas tengo esta semana?", "¿cuántas tareas tengo abiertas?",
                "dime qué tareas me quedan por hacer", "¿qué tengo pendiente?",
                "dame un reporte de mis tareas", "¿qué obligaciones hay en la lista?",
                "muéstrame el backlog de tareas", "¿qué tareas prioritarias tengo?",
                "¿qué se supone que tengo que hacer hoy?", "lista todo lo pendiente de la semana",
                "¿cuál es el estado de mis tareas?", "enséñame las tareas de prioridad alta",
                
                # ES — Leer/consultar proyectos
                "¿qué proyectos tengo activos?", "muéstrame mis proyectos",
                "lista mis proyectos en progreso", "¿cuántos proyectos tengo abiertos?",
                "dame un resumen de mis proyectos", "¿qué proyectos están en curso?",
                "¿cómo van mis proyectos?", "muéstrame el estado del proyecto zyklas",
                "¿qué hitos de proyectos tengo?", "dame la lista de desarrollo de software",
                "¿qué proyectos tienen entregas pronto?", "revisa mis proyectos de programación",
                "¿cuántos proyectos tengo finalizados?", "lista general de proyectos del sistema",
                
                # ES — Leer/consultar hábitos y rutinas
                "¿qué rutinas tengo programadas?", "muéstrame mis hábitos registrados",
                "¿qué hábitos tengo para hoy?", "lista mis rutinas activas",
                "¿cuántos hábitos tengo configurados?", "¿cómo va mi racha de hábitos?",
                "¿qué entrenamiento me toca hoy?", "muéstrame la rutina PPL de esta semana",
                "¿ya completé mis hábitos diarios?", "¿qué rutinas de mañana tengo?",
                "dame el estado de mis hábitos de estudio", "lista las rutinas de la base de datos",
                
                # EN — Read tasks
                "show me my pending tasks", "what tasks do I have today?",
                "list my active tasks", "what tasks are still open?",
                "how many tasks do I have this week?", "what do I have left to do?",
                "give me a summary of my pending tasks", "get my task logs",
                "what's on my to-do list?", "display today's objective list",
                "what tasks are currently active?", "show high priority tasks",
                "is there anything pending for tonight?", "list my backlog",
                "what are my open issues?", "show me the task queue",
                
                # EN — Read projects
                "show me my active projects", "list my projects",
                "what projects are in progress?", "how many open projects do I have?",
                "give me an overview of my projects", "what is the status of project zyklas?",
                "list ongoing development projects", "show my project board",
                "what projects are assigned for this month?", "get a list of all active repositories",
                "display my tracking projects", "how many projects are registered?",
                
                # EN — Read habits/routines
                "show me my habits", "list my scheduled routines",
                "what habits do I have for today?", "how many habits are configured?",
                "what is my habit streak?", "show today's workout routine",
                "check my daily habit tracking", "list active system routines",
                "what are my morning habits?", "give me an overview of my routines",
                "is my PPL routine scheduled for today?", "show registered habits"
            ],
        )
        execution = Route(
            name="execution",
            score_threshold=OPTIMIZED_THRESHOLD,
            utterances=[
                "añádeme esto para hoy",
                "añádeme esto para mañana",
                "añádeme para la semana que viene revisar el presupuesto",
                "añade para este mes completar el informe",
                "añade para hoy mismo comprar el regalo",
                "añade para pasado mañana terminar la refactorización",
                "añade para este viernes la reunión de equipo",
                "añade esto para el fin de semana sin falta",
                "add this for today",
                "add this for tomorrow",
                "add review the budget for next week",
                "add complete the report for this month",
                "add buy the gift for today",
                "add finish the refactor for the day after tomorrow",
                "add team meeting for this Friday",
                "add this for the weekend"
                "añade comprar el pan",
                "añade llamar al médico",
                "añade estudiar inglés mañana",
                "añade revisar el pull request urgente",
                "añade rediseñar la landing page",
                "añade migración de base de datos",
                "añade hacer ejercicio a las 8",
                "añade leer sobre arquitectura hexagonal",
                "add buy groceries",
                "add call the doctor",
                "add review the pull request",
                "add fix the login bug",
                "add mobile app redesign",
                "add data pipeline setup",
                "add write the report",
                "add deploy the service"
                "crea una tarea de comprar pan",
                "crea una tarea de llamar al médico",
                "crea una tarea de hacer ejercicio",
                "crea una tarea de revisar el correo",
                "crea una tarea de limpiar la cocina",
                "crea una tarea de estudiar inglés",
                "crea una tarea de hacer un commit",
                "crea una tarea de organizar el escritorio",
                "crea una tarea de pagar la factura",
                "añade una tarea de escribir el informe",
                "añade una tarea de preparar la presentación",
                "añade una tarea de revisar el pull request",
                "añade una tarea de actualizar dependencias",
                "añade una tarea de hacer la compra",
                "añade una tarea de leer el libro",
                "crea un proyecto de rediseño de la app",
                "crea un proyecto de machine learning",
                "crea un proyecto de gestión de inventario",
                "crea un proyecto de automatización de pruebas",
                "crea un proyecto de desarrollo web",
                "crea un proyecto de análisis de datos",
                "añade un proyecto de integración con la API",
                "añade un proyecto de migración de base de datos",
                "marca como completada la tarea de comprar pan",
                "marca como hecha la tarea de llamar al médico",
                "marca como completada la tarea de hacer ejercicio",
                "marca como hecha la tarea de revisar el correo",
                "marca la tarea de estudiar inglés como completada",
                "completa la tarea de limpiar la cocina",
                "completa la tarea de hacer el commit",
                "elimina la tarea de comprar pan",
                "borra la tarea de llamar al médico",
                "elimina la tarea de hacer ejercicio",
                "borra la tarea de revisar el correo",
                "elimina el proyecto de rediseño de la app",
                "borra el proyecto de machine learning",
                "cambia la prioridad de la tarea de comprar pan",
                "actualiza la fecha límite de la tarea de hacer ejercicio",
                "modifica la descripción de la tarea de revisar el correo",
                "cambia la prioridad del proyecto de desarrollo web",
                "actualiza el estado de la tarea de estudiar inglés",
                "create a task to buy groceries",
                "create a task to call the doctor",
                "create a task to go for a run",
                "create a task to review the pull request",
                "create a task to fix the login bug",
                "create a task to write the report",
                "create a task to update the readme",
                "create a task to clean the office",
                "create a task to read the book",
                "add a task to send the invoice",
                "add a task to deploy the service",
                "add a task to prepare the slides",
                "add a task to refactor the auth module",
                "add a task to schedule the meeting",
                "create a project for the new landing page",
                "create a project for the mobile app redesign",
                "create a project for the data pipeline",
                "create a project for the API integration",
                "add a project for the backend migration",
                "add a project for the testing framework",
                "mark the buy groceries task as done",
                "mark the call doctor task as completed",
                "mark the fix login bug task as done",
                "mark the write report task as complete",
                "complete the deploy service task",
                "complete the update readme task",
                "delete the buy groceries task",
                "remove the call doctor task",
                "delete the fix login bug task",
                "remove the mobile app redesign project",
                "delete the data pipeline project",
                "change the priority of the buy groceries task",
                "update the deadline of the fix login bug task",
                "modify the description of the write report task",
                "change the status of the deploy service task",
                "update the priority of the landing page project",
            ],
        )

        routes = [query_db, execution, normal]
        encoder = HuggingFaceEncoder(name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

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
        print("Not clear")
        result = llm_router(conversation)
    print(result)
    return get_base_rules() + "\n" + specific_rules[result]
    
def llm_router(conversation:ConversationSchema):
    role_namer={False:'AI', True:'USER'}
    prediction = get_llm_predictions([
        {'role':role_namer[message.is_user],'text':message.text} for message in conversation.messages[-3:]
    ])
    return 'normal' if not prediction['read'] and not prediction['write'] else ('execution' if prediction['write'] else 'query_db') 
    