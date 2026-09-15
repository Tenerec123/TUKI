from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from ..schemas import EventCreate, EventSchema, EventUpdate
from ..database import get_db
from ..logic.events import (
    get_event_logic,
    get_all_events_logic,
    create_event_logic,
    update_event_logic,
    delete_event_logic,
)

router = APIRouter(
    prefix="/api/events",
    tags=["events"]
)

@router.get("/{id}", response_model=EventSchema)
def get_event(id: int, db: Session = Depends(get_db)):
    return get_event_logic(id=id, db=db)

@router.get("/", response_model=List[EventSchema])
def get_all_events(first_n: int = None, db: Session = Depends(get_db)):
    return get_all_events_logic(first_n=first_n, db=db)

@router.post("/", response_model=EventSchema)
def create_event(event: EventCreate, db: Session = Depends(get_db)):
    return create_event_logic(event=event, db=db)

@router.patch("/{id}", response_model=EventSchema)
def update_event(id: int, updated_event: EventUpdate, db: Session = Depends(get_db)):
    return update_event_logic(id=id, updated_event=updated_event, db=db)

@router.delete("/{id}", response_model=EventSchema)
def delete_event(id: int, db: Session = Depends(get_db)):
    return delete_event_logic(id=id, db=db)
