from fastapi import HTTPException
from dateutil.rrule import rrulestr
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from ..schemas import RoutineCreate, RoutineUpdate, RoutineToday
from ..models import Routine, RoutineCheck, get_embedding_model
from datetime import datetime,date,time,timedelta
import os
from zoneinfo import ZoneInfo

def current_date() -> date:
    """Today in the user's timezone, falling back to the server date."""
    try:
        return datetime.now(ZoneInfo(os.environ.get("TIMEZONE") or "Europe/Madrid")).date()
    except Exception as e:  # no tzdata installed, unknown TZ name, ...
        print(f"[routines] Falling back to the server date, TIMEZONE unusable: {e}")
        return date.today()

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

def is_routine_available_on(routine: Routine, day: date) -> bool:
    """True when `routine` is scheduled on `day` and the day is not in the future."""
    if routine.init_date is None: return False
    if day > current_date(): return False
    try:
        rule = rrulestr(str(routine.frequency), dtstart=datetime.combine(routine.init_date, datetime.min.time()))
    except Exception as e:  # frequency is a nullable=False VARCHAR with no validation
        print(f"[routines] Unparseable frequency {routine.frequency!r} on routine {routine.id}: {e}")
        return False
    return datetime.combine(day, time.min) in rule

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
    restriction = date.today() - timedelta(days=365)
    db_check = db.query(RoutineCheck).where(RoutineCheck.routine_id == id, restriction < RoutineCheck.check_date).all()
    return db_check

def get_all_routine_logic(db: Session):
    return db.query(Routine).all()

def get_today_routine_logic(db:Session):
    today = current_date()
    db_routine = db.query(Routine).all()
    checked_today_ids = {
        row.routine_id for row in db.query(RoutineCheck.routine_id)
        .filter(RoutineCheck.check_date == today)
        .all()
    }
    return [RoutineToday(
        id=routine.id,
        name=routine.name,
        checked=routine.id in checked_today_ids,
        icon=routine.icon,
    )
    for routine in db_routine if is_routine_available_on(routine, today)]

def create_routine_logic(routine: RoutineCreate, db: Session):
    db_routine = Routine(
        **routine.model_dump())
    db.add(db_routine)
    db.commit()
    db.refresh(db_routine)
    return db_routine

def set_routine_check_logic(id: int, day: date, checked: bool, db: Session) -> str:
    """Mark or unmark a routine on `day`, returning one of four outcomes.

    Returns:
        "applied": 
        "unchanged":
        "unavailable_future"
        "unavailable_out"

    Both unavailable outcomes write nothing.
    """
    routine = get_routine_logic(id, db)
    if not is_routine_available_on(routine, day):
        return "unavailable_future" if day > current_date() else "unavailable_out"
    db_checks = db.query(RoutineCheck).where(RoutineCheck.routine_id == id, RoutineCheck.check_date == day).all()
    if checked and not db_checks:
        new_db_check = RoutineCheck(routine_id=id, check_date=day)
        db.add(new_db_check)
        db.commit()
        db.refresh(new_db_check)
        return "applied"
    if not checked and db_checks:
        for db_check in db_checks:
            db.delete(db_check)
        db.commit()
        return "applied"
    return "unchanged"


def update_routine_logic(id:int, updated_routine:RoutineUpdate, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Routine not found")
    if updated_routine.name is not None:db_routine.name = updated_routine.name
    if updated_routine.description is not None:db_routine.description = updated_routine.description
    if updated_routine.priority is not None:db_routine.priority = updated_routine.priority
    if updated_routine.frequency is not None:db_routine.frequency = updated_routine.frequency
    if updated_routine.project_id is not None:db_routine.project_id = updated_routine.project_id
    if updated_routine.init_date is not None:db_routine.init_date = updated_routine.init_date
    if updated_routine.icon is not None:db_routine.icon = updated_routine.icon
    db.commit()
    return db_routine

def delete_routine_logic(id:int, db: Session):
    db_routine = db.query(Routine).where(Routine.id == id).first()
    if db_routine is None: raise HTTPException(status_code=404, detail="Routine not found")
    db.delete(db_routine)
    db.commit()
    return db_routine

def search_routines_logic(text: str, limit: int, db: Session):
    model = get_embedding_model()
    embedding = list(model.encode(text))
    return db.query(Routine).order_by(Routine.embedding.cosine_distance(embedding)).limit(limit).all()
