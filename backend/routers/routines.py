from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date
from typing import List
from ..schemas import RoutineCreate, RoutineSchema, RoutineUpdate, RoutineToday, RoutineCheckSchema
from ..database import get_db
from ..logic.routines import get_routine_logic, get_all_routine_logic, create_routine_logic, update_routine_logic, delete_routine_logic, get_today_routine_logic, set_routine_check_logic, get_routine_stats_logic, get_accuracy_logic, current_date
router = APIRouter(
    prefix="/api/routines",
    tags=["routines"]
)

@router.get("/accuracy/{id:int}")
def get_accuracy(id:int, db:Session = Depends(get_db)):
    return get_accuracy_logic(id, db)

@router.get("/today", response_model=List[RoutineToday])
def get_today_routine(db: Session = Depends(get_db)):
    return get_today_routine_logic(db=db)   

@router.get("/stats/{id:int}", response_model=List[RoutineCheckSchema])
def get_routine_stats(id:int,db: Session = Depends(get_db)):
    return get_routine_stats_logic(id=id, db=db)

@router.get("/{id:int}", response_model=RoutineSchema)
def get_routine(id:int, db: Session = Depends(get_db)):
    return get_routine_logic(id=id, db=db)

@router.get("/", response_model=List[RoutineSchema])
def get_all_routine(db: Session = Depends(get_db)):
    return get_all_routine_logic(db=db)

@router.post("/check/{id:int}")
def check_routine(id: int, day: date = None, db: Session = Depends(get_db)):
    # current_date(), not date.today(): the container runs UTC.
    if day is None: day = current_date()
    return set_routine_check_logic(id=id, day=day, checked=True, db=db)

@router.delete("/uncheck/{id:int}")
def uncheck_routine(id: int, day: date = None, db: Session = Depends(get_db)):
    if day is None: day = current_date()
    return set_routine_check_logic(id=id, day=day, checked=False, db=db)

@router.post("/", response_model=RoutineSchema)
def create_routine(routine: RoutineCreate, db: Session = Depends(get_db)):
    return create_routine_logic(routine=routine, db=db)

@router.patch("/{id}", response_model=RoutineSchema)
def update_routine(id:int, updated_routine:RoutineUpdate, db: Session = Depends(get_db)):
    return update_routine_logic(id=id, updated_routine=updated_routine, db=db)

@router.delete("/{id}", response_model=RoutineSchema)
def delete_routine(id:int, db: Session = Depends(get_db)):
    return delete_routine_logic(id=id, db=db)