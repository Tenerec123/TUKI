from schemas import ConversationSchema
from semantic_router import Route
from semantic_router.encoders import HuggingFaceEncoder
from semantic_router.routers import SemanticRouter
from datetime import date


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
- Math/Science: Use $ for inline LaTeX and $$ for display blocks. No markdown alternatives for math equations.
"""

specific_rules = {
    'query_db':"you MUST use a get-info function in order to respond the user's answer",
    'execution':
"""FOLLOW STRICTLY THESE STEPS:
1-use get-info functions to be sure you don't create an object that already exists or to find the ids of the objects you have to upadte/delete
2-use create/update/delete functions to make the changes the user demands.""",
    None:''
}

_sr = None

def get_sr():
    global _sr
    if _sr is None:
        OPTIMIZED_THRESHOLD = 0.51

        query_db = Route(
            name="query_db",
            score_threshold=OPTIMIZED_THRESHOLD,
            utterances=[
                # ES — Leer/consultar tareas
                "¿qué tareas tengo pendientes?",
                "muéstrame mis tareas pendientes",
                "¿cuáles son mis tareas de hoy?",
                "lista mis tareas activas",
                "¿qué tareas tengo esta semana?",
                "¿cuántas tareas tengo abiertas?",
                "dime qué tareas me quedan por hacer",
                "¿qué tengo pendiente?",
                # ES — Leer/consultar proyectos
                "¿qué proyectos tengo activos?",
                "muéstrame mis proyectos",
                "lista mis proyectos en progreso",
                "¿cuántos proyectos tengo abiertos?",
                "dame un resumen de mis proyectos",
                "¿qué proyectos están en curso?",
                # ES — Leer/consultar hábitos y rutinas
                "¿qué rutinas tengo programadas?",
                "muéstrame mis hábitos registrados",
                "¿qué hábitos tengo para hoy?",
                "lista mis rutinas activas",
                "¿cuántos hábitos tengo configurados?",
                # EN — Read tasks
                "show me my pending tasks",
                "what tasks do I have today?",
                "list my active tasks",
                "what tasks are still open?",
                "how many tasks do I have this week?",
                "what do I have left to do?",
                "give me a summary of my pending tasks",
                # EN — Read projects
                "show me my active projects",
                "list my projects",
                "what projects are in progress?",
                "how many open projects do I have?",
                "give me an overview of my projects",
                # EN — Read habits/routines
                "show me my habits",
                "list my scheduled routines",
                "what habits do I have for today?",
                "how many habits are configured?",
            ],
        )

        execution = Route(
            name="execution",
            score_threshold=OPTIMIZED_THRESHOLD,
            utterances=[
                # ES — Crear tareas (contenido variado para generalizar el patrón)
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
                # ES — Crear proyectos (contenido variado)
                "crea un proyecto de rediseño de la app",
                "crea un proyecto de machine learning",
                "crea un proyecto de gestión de inventario",
                "crea un proyecto de automatización de pruebas",
                "crea un proyecto de desarrollo web",
                "crea un proyecto de análisis de datos",
                "añade un proyecto de integración con la API",
                "añade un proyecto de migración de base de datos",
                # ES — Marcar como completado
                "marca como completada la tarea de comprar pan",
                "marca como hecha la tarea de llamar al médico",
                "marca como completada la tarea de hacer ejercicio",
                "marca como hecha la tarea de revisar el correo",
                "marca la tarea de estudiar inglés como completada",
                "completa la tarea de limpiar la cocina",
                "completa la tarea de hacer el commit",
                # ES — Eliminar
                "elimina la tarea de comprar pan",
                "borra la tarea de llamar al médico",
                "elimina la tarea de hacer ejercicio",
                "borra la tarea de revisar el correo",
                "elimina el proyecto de rediseño de la app",
                "borra el proyecto de machine learning",
                # ES — Actualizar
                "cambia la prioridad de la tarea de comprar pan",
                "actualiza la fecha límite de la tarea de hacer ejercicio",
                "modifica la descripción de la tarea de revisar el correo",
                "cambia la prioridad del proyecto de desarrollo web",
                "actualiza el estado de la tarea de estudiar inglés",
                # EN — Create tasks (varied content)
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
                # EN — Create projects (varied content)
                "create a project for the new landing page",
                "create a project for the mobile app redesign",
                "create a project for the data pipeline",
                "create a project for the API integration",
                "add a project for the backend migration",
                "add a project for the testing framework",
                # EN — Mark as complete
                "mark the buy groceries task as done",
                "mark the call doctor task as completed",
                "mark the fix login bug task as done",
                "mark the write report task as complete",
                "complete the deploy service task",
                "complete the update readme task",
                # EN — Delete
                "delete the buy groceries task",
                "remove the call doctor task",
                "delete the fix login bug task",
                "remove the mobile app redesign project",
                "delete the data pipeline project",
                # EN — Update
                "change the priority of the buy groceries task",
                "update the deadline of the fix login bug task",
                "modify the description of the write report task",
                "change the status of the deploy service task",
                "update the priority of the landing page project",
            ],
        )

        routes = [query_db, execution]
        encoder = HuggingFaceEncoder(name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

        _sr = SemanticRouter(
            encoder=encoder,
            routes=routes,
            auto_sync="local",
            top_k=5
        )
    return _sr

def get_semantic_rules(conversation:ConversationSchema):
    sr = get_sr()

    result = sr(conversation.messages[-1].text)
    print(result.name)

    return get_base_rules() + "\n" + specific_rules[result.name]