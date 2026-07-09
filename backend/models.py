from backend.database import Base, engine
from sqlalchemy import Integer, String, ForeignKey
from typing import List, Optional
from datetime import date,datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy import event
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.inspection import inspect
from sentence_transformers import SentenceTransformer

_embedding_model = None

def get_embedding_model():
    """Load and cache the embedding model on first use"""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer('sentence-transformers/distiluse-base-multilingual-cased-v2')
    return _embedding_model
# Create your models here.

class Config(Base):
    __tablename__ = "config"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)

# Mixin: Provee columnas comunes sin ser una tabla por sí misma
class TimestampMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512))
    priority: Mapped[Optional[int]] = mapped_column(Integer)
    embedding = mapped_column(VECTOR(384))

class Project(Base, TimestampMixin):
    __tablename__ = 'projects'

    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey('projects.id', ondelete="CASCADE"))

    # Relación Recursiva
    # remote_side indica que 'parent_id' es el lado que define la jerarquía hacia arriba
    parent_project: Mapped[Optional["Project"]] = relationship(
        "Project", 
        back_populates="sub_projects", 
        remote_side="Project.id" 
    )
    
    sub_projects: Mapped[List["Project"]] = relationship(
        "Project", 
        back_populates="parent_project",
        cascade="all, delete-orphan"
    )
    @validates('parent_id')
    def validate_parent_id(self, key, value):
        if value is not None and value == self.id:
            raise ValueError("Ciclo detectado: Un proyecto no puede ser padre de sí mismo.")
        return value
    sub_tasks: Mapped[List["Task"]] = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    sub_routines: Mapped[List["Routine"]] = relationship("Routine", back_populates="project", cascade="all, delete-orphan")

class Task(Base, TimestampMixin):
    __tablename__ = 'tasks'
    
    deadline: Mapped[date] = mapped_column(nullable=False)
    finished: Mapped[bool] = mapped_column(default=False)
    
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey('projects.id', ondelete="CASCADE"))
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="sub_tasks")

class Routine(Base, TimestampMixin):
    __tablename__ = 'routines'
    
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey('projects.id', ondelete="CASCADE"))
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="sub_routines")
    frequency: Mapped[str] = mapped_column(nullable=False)
    init_date:Mapped[Optional[date]]= mapped_column(nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

class Conversation(Base):
    __tablename__="conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    messages: Mapped[List["Message"]] = relationship("Message",order_by="Message.position", back_populates="conversation", cascade="all, delete-orphan")
    creation_date:Mapped[Optional[date]] = mapped_column(nullable=True)
    last_used:Mapped[Optional[datetime]] = mapped_column(nullable=True)

class Message(Base):
    __tablename__="messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(nullable=False)
    is_user: Mapped[bool] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(nullable=False)
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

class RoutineCheck(Base):
    __tablename__="routineChecks"
    id:Mapped[int] = mapped_column(primary_key=True)
    routine_id:Mapped[int] = mapped_column(ForeignKey('routines.id', ondelete='CASCADE'))
    check_date:Mapped[date] = mapped_column(nullable=False)


@event.listens_for(Project, 'before_insert')
@event.listens_for(Project, 'before_update')
@event.listens_for(Task, 'before_insert')
@event.listens_for(Task, 'before_update')
@event.listens_for(Routine, 'before_insert')
@event.listens_for(Routine, 'before_update')
def handle_project_embeddings(mapper, connection, target):
    state = inspect(target)
    
    # 1. Determinar si requiere cálculo de embedding
    is_insert = state.transient or state.pending
    
    if is_insert:
        should_update = target.embedding is None
    else:
        name_changed = state.get_history('name', passive=True).has_changes()
        desc_changed = state.get_history('description', passive=True).has_changes()
        should_update = target.embedding is None or name_changed or desc_changed

    # 2. Calcular si corresponde
    if should_update:
        model = get_embedding_model()
        text_to_embed = target.name
        if getattr(target, 'description', None):
            text_to_embed += " " + target.description
            
        target.embedding = list(model.encode(text_to_embed))

# Create all tables after all models are defined
Base.metadata.create_all(engine)
