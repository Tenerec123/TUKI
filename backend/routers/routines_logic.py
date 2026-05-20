from fastapi import HTTPException
from dateutil.rrule import rrulestr
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from schemas import RoutineCreate, RoutineUpdate, RoutineToday
from models import Routine, RoutineCheck
from datetime import datetime,date,time


# non-endpoint function
def get_days_until_today(frequency_str: str, init_date) -> int:
    if not frequency_str.startswith("RRULE:"):
        frequency_str = f"RRULE:{frequency_str}"
    if isinstance(init_date, date) and not isinstance(init_date, datetime):
        start_dt = datetime.combine(init_date, time.min)
    else:
        start_dt = init_date.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    end_dt = datetime.combine(datetime.now().date(), time.max)
    rule = rrulestr(frequency_str, dtstart=start_dt)
    theoretical_dates = rule.between(start_dt, end_dt, inc=True)
    return len(theoretical_dates)

def get_accuracy_logic(id:int, db:Session):
    db_routine = get_routine_logic(id, db)
    done_days = len(get_routine_stats_logic(id, db))
    total_days = get_days_until_today(db_routine.frequency, db_routine.init_date)
    if total_days <= 0: return "Unstarted"
    return done_days/total_days

def get_routine_logic(id:int, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Routine not found")
    return db_routine

def get_routine_stats_logic(id:int, db: Session):
    if db.query(Routine).where(Routine.id == id).first() is None: return []
    restriction = func.date('now', '-1 year')
    db_check = db.query(RoutineCheck).where(RoutineCheck.routine_id == id, restriction < RoutineCheck.check_date).all()
    return db_check

def get_all_routine_logic(db: Session):
    return db.query(Routine).all()

def get_today_routine_logic(db:Session):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    db_routine = db.query(Routine).all()
    checked_today_ids = {
        row.routine_id for row in db.query(RoutineCheck.routine_id)
        .filter(RoutineCheck.check_date == date.today())
        .all()
    }
    return [RoutineToday(
        id=routine.id,
        name=routine.name,
        checked=routine.id in checked_today_ids,
    )
    for routine in db_routine if today in rrulestr(str(routine.frequency), dtstart=datetime.combine(routine.init_date, datetime.min.time()))]

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
