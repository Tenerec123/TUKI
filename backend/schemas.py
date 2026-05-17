from pydantic import BaseModel, Field, ConfigDict, field_serializer
from datetime import date, datetime
from typing import List, Optional


class BaseItem(BaseModel):
    name: str = Field(..., max_length=512, description='Name of the todo')
    description:str = Field(..., max_length=512, description='Description of the todo')
    priority: int = Field(default=0, ge=0, le=64, description="Priority of the todo")
    model_config = ConfigDict(from_attributes=True)

# Task Classes
class TaskCreate(BaseItem):
    project_id:Optional[int] = Field(None)
    deadline: date = Field(..., description="Latest day in which the task must/should be done (depending on the priority)")
    finished: bool = Field(default=False, description="True if is the task is done and false if not")
    
class TaskSchema(TaskCreate):
    project_id:Optional[int] = Field(None)
    id: int = Field(..., description="Unique identifier of the task")
    @field_serializer('deadline')
    def serialize_deadline(self, deadline: date) -> str:
        return deadline.strftime("%d/%m/%Y")
    
class TaskUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=512, description='Name of the task')
    description: Optional[str] = Field(None, max_length=512, description='Description of the task')
    priority: Optional[int] = Field(None, ge=0, le=64, description="Priority of the task")
    deadline: Optional[date] = Field(default=None, description="Latest day in which the task must/should be done (depending on the priority)")
    finished: Optional[bool] = Field(default=None, description="True if is the task is done and false if not")
    project_id:Optional[int] = Field(None)
# Routine Classes
class RoutineCreate(BaseItem):
    frequency: str = Field(..., description='Frequency in RRULE or custom string')
    project_id:Optional[int] = Field(None)
    init_date: date = Field(...)

class RoutineToday(BaseModel):
    name: str = Field(..., max_length=512, description='Name of the todo')
    checked:bool = Field(...)
    id:int = Field(..., description="Unique identifier")

class RoutineSchema(RoutineCreate):
    id:int = Field(..., description="Unique identifier")
    
    @field_serializer('init_date')
    def serialize_deadline(self, init_date: date) -> str:
        return init_date.strftime("%d/%m/%Y")

class RoutineUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = Field(None, max_length=512)
    priority: Optional[int] = Field(None, ge=0, le=64)
    frequency: Optional[str] = Field(None)
    project_id:Optional[int] = Field(None)
    init_date:Optional[date] = Field(None)

class RoutineCheckSchema(BaseModel):
    routine_id:int = Field(...)
    check_date:date = Field(...)
    @field_serializer('check_date')
    def serialize_deadline(self, check_date: date) -> str:
        return check_date.strftime("%d/%m/%Y")

# Project Classes

class ProjectCreate(BaseItem):
    parent_id:Optional[int] = Field(None)
# 1. Esquema base para lectura simple (sin hijos)

class ProjectRead(BaseItem):
    id: int = Field(..., description="Unique identifier")
    parent_id:Optional[int] = Field(None)
    model_config = ConfigDict(from_attributes=True)

# 2. Esquema principal: Muestra tareas, rutinas e hijos DIRECTOS
class ProjectSchema(ProjectRead):
    sub_tasks: List[TaskSchema] = []
    sub_routines: List[RoutineSchema] = []
    # Aquí está el truco: usamos el esquema que NO tiene sub_projects
    sub_projects: List[ProjectRead] = [] 

    model_config = ConfigDict(from_attributes=True)
ProjectSchema.model_rebuild()

class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = Field(None, max_length=512)
    priority: Optional[int] = Field(None, ge=0, le=64)
    parent_id: Optional[int] = Field(None)


class MessageBase(BaseModel):
    is_user:bool = Field(...)
    text:str = Field(...)
    model_config = ConfigDict(from_attributes=True)


class MessageSchema(MessageBase):
    position:int = Field(...)

class ConversationBase(BaseModel):
    title: str = Field(...)
    
class ConversationCreate(ConversationBase):
    pass

class ConversationSchema(ConversationCreate):
    id:int = Field(...)
    messages: Optional[List[MessageSchema]] = Field(default=[])
    creation_date:date = Field(...)
    last_used:datetime = Field(...)
    model_config = ConfigDict(from_attributes=True)
    @field_serializer('creation_date')
    def serialize_creation_date(self, creation_date: date) -> str:
        return creation_date.strftime("%d/%m/%Y")
    
class ConversationUpdate(BaseModel):
    messages:Optional[List[MessageBase]] = Field(default=[])
    title: Optional[str] = Field(None)
    last_used:Optional[datetime] = Field(None)

class ConversationData(BaseModel):
    title: str = Field(...)
    id:int = Field(...)
    last_used:datetime = Field(...)


class Prompt(BaseModel):
    conversation_id: int
    user_message: str
    model_config = ConfigDict(from_attributes=True)
    