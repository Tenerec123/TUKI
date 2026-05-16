from fastapi import HTTPException
from dateutil.rrule import rrulestr

from sqlalchemy.orm import Session
from schemas import RoutineCreate, RoutineUpdate
from models import Routine, RoutineCheck
from datetime import datetime,date

def get_routine_logic(id:int, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Task not found")
    return db_routine

def get_all_routine_logic(db: Session):
    return db.query(Routine).all()

def get_today_routine_logic(db:Session):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    db_routine = db.query(Routine).all()
    print(str(db_routine[0].frequency))
    return [routine for routine in db_routine if today in rrulestr(str(routine.frequency), dtstart=today)]

def create_routine_logic(routine: RoutineCreate, db: Session):
    db_routine = Routine(
        **routine.model_dump())
    db.add(db_routine)
    db.commit()
    db.refresh(db_routine)
    return db_routine

def check_routine_logic(id:int, db:Session):
    db_check = db.query(RoutineCheck).where(RoutineCheck.routine_id == id, RoutineCheck.check_date == date.today()).first()
    print(db_check)
    if db_check: return db_check
    new_db_check = RoutineCheck(
        routine_id=id,
        check_date=date.today()
    )
    db.add(new_db_check)
    db.commit()
    db.refresh(new_db_check)
    return new_db_check

def uncheck_routine_logic(id:int, db: Session):
    db_check = db.query(RoutineCheck).where(RoutineCheck.routine_id == id, RoutineCheck.check_date == date.today()).first()
    if db_check is None: raise HTTPException(status_code=404, detail="Routine not found")
    db.delete(db_check)
    db.commit()
    return db_check


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
