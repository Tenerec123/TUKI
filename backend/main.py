from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from routers import tasks, routines, projects, ai, conversations
# from schemas import BaseItem, TaskCreate, TaskSchema, TaskUpdate, RoutineCreate, RoutineSchema, RoutineUpdate, ProjectCreate, ProjectSchema, ProjectUpdate, PromptSchema
# from models import ItemBase, Task, Project, Routine
import logging
# Esto apaga los logs de los motores de base de datos
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
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
    return FileResponse('../frontend/chat.html')

@api.get("/todo")
async def read_index():
    return FileResponse('../frontend/todo.html')

@api.get("/kale")
async def read_index():
    return FileResponse('../frontend/kale.html')


api.include_router(tasks.router)
api.include_router(routines.router)
api.include_router(projects.router)
api.include_router(ai.router)
api.include_router(conversations.router)