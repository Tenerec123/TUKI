from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..schemas import TaskCreate, TaskUpdate
from ..models import Task

def get_task_logic(id:int, db: Session):
    db_task = db.query(Task).where(Task.id == id).first()
    if db_task is None: raise HTTPException(status_code=404, detail="Task not found")
    return db_task

def get_all_tasks_logic(first_n:int, db: Session):
    if first_n is None:
        db_task = db.query(Task).order_by(Task.deadline).all()
        return db_task
    db_task = db.query(Task).limit(first_n).order_by(Task.deadline).all()
    return db_task
    
def create_task_logic(task: TaskCreate, db: Session):
    db_task = Task(
        **task.model_dump()
        )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task

def update_task_logic(id:int, updated_task:TaskUpdate, db: Session):
    db_task = db.query(Task).where(Task.id == id).first()
    if db_task is None: raise HTTPException(status_code=404, detail="Task not found")
    if updated_task.name is not None:db_task.name = updated_task.name
    if updated_task.description is not None:db_task.description = updated_task.description
    if updated_task.priority is not None:db_task.priority = updated_task.priority
    if updated_task.deadline is not None:db_task.deadline = updated_task.deadline
    if updated_task.finished is not None:db_task.finished = updated_task.finished
    db.commit()
    return db_task

def delete_task_logic(id:int, db: Session):
    db_task = db.query(Task).where(Task.id == id).first()
    if db_task is None: raise HTTPException(status_code=404, detail="Task not found")
    db.delete(db_task)
    db.commit()
    return db_task
