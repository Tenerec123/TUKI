from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from schemas import RoutineCreate, RoutineSchema, RoutineUpdate, RoutineToday, RoutineCheckSchema
from database import get_db
from .routines_logic import get_routine_logic, get_all_routine_logic, create_routine_logic, update_routine_logic, delete_routine_logic, get_today_routine_logic, check_routine_logic, uncheck_routine_logic, get_routine_stats_logic, get_accuracy_logic
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

@router.post("/check/{id:str}")
def check_routine(id:int, db:Session = Depends(get_db)):
    return check_routine_logic(id=id,db=db)

@router.delete("/uncheck/{id:str}")
def uncheck_routine(id:int, db:Session = Depends(get_db)):
    return uncheck_routine_logic(id=id,db=db)

@router.post("/", response_model=RoutineSchema)
def create_routine(routine: RoutineCreate, db: Session = Depends(get_db)):
    return create_routine_logic(routine=routine, db=db)

@router.patch("/{id}", response_model=RoutineSchema)
def update_routine(id:int, updated_routine:RoutineUpdate, db: Session = Depends(get_db)):
    return update_routine_logic(id=id, updated_routine=updated_routine, db=db)

@router.delete("/{id}", response_model=RoutineSchema)
def delete_routine(id:int, db: Session = Depends(get_db)):
    return delete_routine_logic(id=id, db=db)