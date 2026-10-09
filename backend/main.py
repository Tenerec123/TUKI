from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import config, tasks, routines, projects, conversations, notes, events, ai, mcp
from backend.wake_models import ensure_wake_models
from backend.models import get_embedding_model
from pathlib import Path
import anyio
import time
from dotenv import load_dotenv

# Boot warmup for the local embedding model: bounded so a cold-cache download
# that stalls cannot hold readiness hostage (see lifespan below).
EMBEDDING_WARMUP_TIMEOUT_S = 15.0
basedir = Path(__file__).resolve().parent.parent 
load_dotenv(basedir / ".env")
import logging
from contextlib import asynccontextmanager
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
for logger_name in [ "uvicorn.error"]:
    logger = logging.getLogger(logger_name)
    logger.handlers = []
    logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- CÓDIGO QUE SE EJECUTA AL ARRANCAR ----
    # Sube el límite de hilos síncronos para peticiones en paralelo
    anyio.to_thread.current_default_thread_limiter().total_threads = 100
    
    # Descarga (solo si faltan) los modelos ONNX del wake word. Idempotente:
    # si ya están, el chequeo son 4 stat() y no toca la red.
    ensure_wake_models()

    # Warm the local embedding model at boot. Otherwise the first encode pays
    # torch's CPU init inside a request (measured ~4s on CreateTask's
    # before_insert hook). Blocking CPU work, so it runs in a worker thread.
    # A failure here must not abort boot: embeddings fall back to lazy loading.
    try:
        def _warm_embeddings() -> None:
            get_embedding_model().encode("warmup")

        started = time.perf_counter()
        with anyio.fail_after(EMBEDDING_WARMUP_TIMEOUT_S):
            await anyio.to_thread.run_sync(_warm_embeddings)
        print(f"[warmup] embedding model ready in {time.perf_counter() - started:.1f}s")
    except Exception as exc:
        print(f"[warmup] embedding warmup failed, will load lazily: {exc}")

    yield  # Aquí es donde la aplicación se queda corriendo
    
    # ---- CÓDIGO QUE SE EJECUTA AL APAGAR (Opcional) ----
    pass

# Pasas el lifespan a la instancia de FastAPI
api = FastAPI(lifespan=lifespan)

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

@api.get("/notes")
async def read_notes():
    return FileResponse('frontend/notes.html')

@api.get("/wake")
async def read_wake():
    return FileResponse('frontend/wake.html')

api.include_router(config.router)
api.include_router(tasks.router)
api.include_router(routines.router)
api.include_router(projects.router)
api.include_router(conversations.router)
api.include_router(notes.router)
api.include_router(events.router)
api.include_router(ai.router)
api.include_router(mcp.router)