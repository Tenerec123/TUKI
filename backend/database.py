from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import os
from pathlib import Path
from dotenv import load_dotenv
basedir = Path(__file__).resolve().parent.parent 
load_dotenv(basedir / ".env")

# Engine con tipado implícito
engine = create_engine(
    os.getenv('DATABASE_URL'), 
    echo=False, 
)

# Definición de Base moderna
class Base(DeclarativeBase):
    pass
# from .models import Project, Task, Routine, Conversation, Message

# Create tables will be called after all models are defined

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False, 
    autoflush=False, 
    expire_on_commit=False
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()