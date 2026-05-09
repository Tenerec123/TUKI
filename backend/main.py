from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from routers import tasks, routines, projects, ai, conversations
# from schemas import BaseItem, TaskCreate, TaskSchema, TaskUpdate, RoutineCreate, RoutineSchema, RoutineUpdate, ProjectCreate, ProjectSchema, ProjectUpdate, PromptSchema
# from models import ItemBase, Task, Project, Routine
import logging
import os
# Esto apaga los logs de los motores de base de datos
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

# Get API URL from environment
API_URL = os.getenv('API_URL', 'http://localhost:8000')

def load_html_with_api_url(filepath: str) -> str:
    """Load HTML file and inject API_URL as global JavaScript variable"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Inject API_URL before closing </head> tag
    api_script = f'<script>window.API_URL = "{API_URL}";</script>'
    content = content.replace('</head>', f'{api_script}\n</head>')
    
    return content
api = FastAPI()
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permite que cualquier origen (tu HTML local) llame a la API
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Montar la carpeta para archivos (CSS, JS, Imágenes)
api.mount("/frontend", StaticFiles(directory="../frontend"), name="frontend")

# 2. Ruta para que al entrar a http://localhost:8000/ aparezca tu web
@api.get("/chat")
async def read_index():
    html_content = load_html_with_api_url('../frontend/chat.html')
    return HTMLResponse(content=html_content)

@api.get("/todo")
async def read_index():
    html_content = load_html_with_api_url('../frontend/todo.html')
    return HTMLResponse(content=html_content)

@api.get("/kale")
async def read_index():
    html_content = load_html_with_api_url('../frontend/kale.html')
    return HTMLResponse(content=html_content)


api.include_router(tasks.router)
api.include_router(routines.router)
api.include_router(projects.router)
api.include_router(ai.router)
api.include_router(conversations.router)