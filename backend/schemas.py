from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from datetime import date, datetime
from typing import List, Optional

class ModelConfig(BaseModel):
    orchestrator: str = Field(max_length=100)  # composite "MODEL_ID EFFORT" must fit config.value VARCHAR(128)
    # Reasoning effort for the orchestrator model ("none", "low", ...).
    # None means "not saved yet, use default 'none'".
    orchestrator_effort: Optional[str] = Field(default=None, max_length=16)
    searcher: Optional[str] = Field(default=None, max_length=128)
    stt: Optional[str] = Field(default=None, max_length=128)

class BaseItem(BaseModel):
    name: str = Field(..., max_length=512, description='Name of the todo')
    description: Optional[str] = Field(None, max_length=512, description='Description of the todo')
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
    icon: Optional[str] = Field(None, max_length=64, description='Bootstrap icon class (e.g. bell-fill, clock, calendar-check)')

class RoutineToday(BaseModel):
    name: str = Field(..., max_length=512, description='Name of the todo')
    checked:bool = Field(...)
    id:int = Field(..., description="Unique identifier")
    icon: Optional[str] = Field(None, max_length=64, description='Bootstrap icon class')

class RoutineSchema(RoutineCreate):
    id:int = Field(..., description="Unique identifier")

class RoutineUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = Field(None, max_length=512)
    priority: Optional[int] = Field(None, ge=0, le=64)
    frequency: Optional[str] = Field(None)
    project_id:Optional[int] = Field(None)
    init_date:Optional[date] = Field(None)
    icon: Optional[str] = Field(None, max_length=64, description='Bootstrap icon class')

class RoutineCheckSchema(BaseModel):
    routine_id:int = Field(...)
    check_date:date = Field(...)

# Event Classes
class EventCreate(BaseModel):
    name: str = Field(..., max_length=512, description='Name of the event')
    description: Optional[str] = Field(None, max_length=512, description='Description of the event')
    start_time: datetime = Field(..., description='Event start datetime, naive local time (no timezone offset)')
    end_time: datetime = Field(..., description='Event end datetime, naive local time (no timezone offset)')

    @field_validator("start_time", "end_time")
    @classmethod
    def _reject_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is not None:
            raise ValueError("Datetimes must be naive local time (no offset)")
        return v

    @model_validator(mode="after")
    def _end_not_before_start(self):
        if self.end_time and self.start_time and self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time")
        return self

class EventSchema(EventCreate):
    id: int = Field(..., description="Unique identifier of the event")
    model_config = ConfigDict(from_attributes=True)


class EventUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=512, description='Name of the event')
    description: Optional[str] = Field(None, max_length=512, description='Description of the event')
    start_time: Optional[datetime] = Field(None, description='Event start datetime, naive local time (no timezone offset)')
    end_time: Optional[datetime] = Field(None, description='Event end datetime, naive local time (no timezone offset)')

    @field_validator("start_time", "end_time")
    @classmethod
    def _reject_aware(cls, v: datetime) -> datetime:
        if v is not None and v.tzinfo is not None:
            raise ValueError("Datetimes must be naive local time (no offset)")
        return v

    @model_validator(mode="after")
    def _end_not_before_start(self):
        if self.end_time and self.start_time and self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time")
        return self

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
    type:str = Field(...)
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
    
class ConversationUpdate(BaseModel):
    messages:Optional[List[MessageBase]] = Field(default=[])
    title: Optional[str] = Field(None)
    last_used:Optional[datetime] = Field(None)

class ConversationData(BaseModel):
    title: str = Field(...)
    id:int = Field(...)
    last_used:datetime = Field(...)


class Prompt(BaseModel):
    conversation_id: int = Field(...)
    user_message: str = Field(...)
    model_config = ConfigDict(from_attributes=True)


class NoteMetaCreate(BaseModel):
    title: str = Field(...)
    path: str = Field(...)
    content: str = Field(...)

class NoteMetaUpdate(BaseModel):
    title: Optional[str] = Field(None)
    content: Optional[str] = Field(None)

class NoteMetaSchema(BaseModel):
    id: int = Field(...)
    title: str = Field(...)
    path: Optional[str] = Field(None)
    model_config = ConfigDict(from_attributes=True)

class FolderRequest(BaseModel):
    path: str