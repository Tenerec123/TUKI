from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from schemas import RoutineCreate, RoutineSchema, RoutineUpdate
from database import get_db
from .routines_logic import get_routine_logic, get_all_routine_logic, create_routine_logic, update_routine_logic, delete_routine_logic, get_today_routine_logic
router = APIRouter(
    prefix="/api/routines",
    tags=["routines"]
)

@router.get("/today", response_model=List[RoutineSchema])
def get_today_routine(db: Session = Depends(get_db)):
    return get_today_routine_logic(db=db)   


@router.get("/{id:int}", response_model=RoutineSchema)
def get_routine(id:int, db: Session = Depends(get_db)):
    return get_routine_logic(id=id, db=db)

@router.get("/", response_model=List[RoutineSchema])
def get_all_routine(db: Session = Depends(get_db)):
    return get_all_routine_logic(db=db)

@router.post("/", response_model=RoutineSchema)
def create_routine(routine: RoutineCreate, db: Session = Depends(get_db)):
    return create_routine_logic(routine=routine, db=db)

@router.patch("/{id}", response_model=RoutineSchema)
def update_routine(id:int, updated_routine:RoutineUpdate, db: Session = Depends(get_db)):
    return update_routine_logic(id=id, updated_routine=updated_routine, db=db)

@router.delete("/{id}", response_model=RoutineSchema)
def delete_routine(id:int, db: Session = Depends(get_db)):
    return delete_routine_logic(id=id, db=db)