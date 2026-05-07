from database import Base, engine
from sqlalchemy import Integer, String, ForeignKey
from typing import List, Optional
from datetime import date
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

# Mixin: Provee columnas comunes sin ser una tabla por sí misma
class TimestampMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512))
    priority: Mapped[Optional[int]] = mapped_column(Integer)

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

    # Relaciones con otras entidades (necesarias para back_populates)
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
    last_run: Mapped[Optional[date]] = mapped_column(nullable=True)
    next_run: Mapped[Optional[date]] = mapped_column(nullable=True)

class Conversation(Base):
    __tablename__="conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    messages: Mapped[List["Message"]] = relationship("Message",order_by="Message.position", back_populates="conversation", cascade="all, delete-orphan")
    creation_date:Mapped[Optional[date]] = mapped_column(nullable=True)

class Message(Base):
    __tablename__="messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(nullable=False)
    is_user: Mapped[bool] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(nullable=False)
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")