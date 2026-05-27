from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.routers.ai import ai
from backend.routers import tasks, routines, projects, conversations
from pathlib import Path
from dotenv import load_dotenv
basedir = Path(__file__).resolve().parent.parent 
load_dotenv(basedir / ".env")
import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("semantic_router").setLevel(logging.ERROR)
for logger_name in [ "uvicorn.error"]:
    logger = logging.getLogger(logger_name)
    logger.handlers = []
    logger.propagate = False

api = FastAPI()
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permite que cualquier origen (tu HTML local) llame a la API
    allow_methods=["*"],
    allow_headers=["*"],
)

api.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

@api.get("/TUKI.svg")
async def favicon():
    return FileResponse("frontend/TUKI.svg")

@api.get("/chat")
async def read_index():
    return FileResponse('frontend/chat.html')

@api.get("/todo")
async def read_index():
    return FileResponse('frontend/todo.html')

@api.get("/kale")
async def read_index():
    return FileResponse('frontend/kale.html')

api.include_router(tasks.router)
api.include_router(routines.router)
api.include_router(projects.router)
api.include_router(ai.router)
api.include_router(conversations.router)