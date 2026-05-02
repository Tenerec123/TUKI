from fastapi import HTTPException
from sqlalchemy.orm import Session
from schemas import RoutineCreate, RoutineUpdate
from models import Routine

def get_routine_logic(id:int, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Task not found")
    return db_routine

def get_all_routine_logic(first_n:int, db: Session):
    if first_n is None:
        db_routine = db.query(Routine).all()
        return db_routine
    db_routine = db.query(Routine).limit(first_n).all()
    return db_routine

def create_routine_logic(routine: RoutineCreate, db: Session):
    db_routine = Routine(
        **routine.model_dump())
    db.add(db_routine)
    db.commit()
    db.refresh(db_routine)
    return db_routine

def update_routine_logic(id:int, updated_routine:RoutineUpdate, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Routine not found")
    if updated_routine.name is not None:db_routine.name = updated_routine.name
    if updated_routine.description is not None:db_routine.description = updated_routine.description
    if updated_routine.priority is not None:db_routine.priority = updated_routine.priority
    if updated_routine.project_id is not None:db_routine.project_id = updated_routine.project_id
    if updated_routine.last_run is not None:db_routine.last_run = updated_routine.last_run
    if updated_routine.next_run is not None:db_routine.next_run = updated_routine.next_run
    db.commit()
    return db_routine

def delete_routine_logic(id:int, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Routine not found")
    db.delete(db_routine)
    db.commit()
    return db_routine
