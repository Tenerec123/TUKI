from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from ..schemas import TaskCreate, TaskSchema, TaskUpdate
from ..database import get_db
from .tasks_logic import get_task_logic, get_all_tasks_logic, create_task_logic, update_task_logic, delete_task_logic

router = APIRouter(
    prefix="/api/tasks", # Todos los endpoints empezarán con esto
    tags=["tasks"]        # Organiza la documentación automática (/docs)
)

@router.get("/{id}", response_model=TaskSchema)
def get_task(id:int, db: Session = Depends(get_db)):
    return get_task_logic(id=id, db=db)

@router.get("/", response_model=List[TaskSchema])
def get_all_tasks(first_n:int = None, db: Session = Depends(get_db)):
    return get_all_tasks_logic(first_n=first_n, db=db)
    
@router.post("/", response_model=TaskSchema)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    return create_task_logic(task=task, db=db)

@router.patch("/{id}", response_model=TaskSchema)
def update_task(id:int, updated_task:TaskUpdate, db: Session = Depends(get_db)):
    return update_task_logic(id=id, updated_task=updated_task, db=db)

@router.delete("/{id}", response_model=TaskSchema)
def delete_task(id:int, db: Session = Depends(get_db)):
    return delete_task_logic(id=id, db=db)
