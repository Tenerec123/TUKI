from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..schemas import EventCreate, EventUpdate
from ..models import Event, get_embedding_model


def get_event_logic(id: int, db: Session):
    db_event = db.query(Event).where(Event.id == id).first()
    if db_event is None: raise HTTPException(status_code=404, detail="Event not found")
    return db_event


def get_all_events_logic(first_n: int, db: Session):
    if first_n is None:
        return db.query(Event).order_by(Event.start_time, Event.id).all()
    return db.query(Event).order_by(Event.start_time, Event.id).limit(first_n).all()


def create_event_logic(event: EventCreate, db: Session):
    db_event = Event(**event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


def update_event_logic(id: int, updated_event: EventUpdate, db: Session):
    db_event = db.query(Event).where(Event.id == id).first()
    if db_event is None: raise HTTPException(status_code=404, detail="Event not found")
    if updated_event.name is not None: db_event.name = updated_event.name
    if updated_event.description is not None: db_event.description = updated_event.description
    if updated_event.start_time is not None: db_event.start_time = updated_event.start_time
    if updated_event.end_time is not None: db_event.end_time = updated_event.end_time
    db.commit()
    return db_event


def delete_event_logic(id: int, db: Session):
    db_event = db.query(Event).where(Event.id == id).first()
    if db_event is None: raise HTTPException(status_code=404, detail="Event not found")
    db.delete(db_event)
    db.commit()
    return db_event


def search_events_logic(text: str, limit: int, db: Session):
    model = get_embedding_model()
    embedding = list(model.encode(text))
    return db.query(Event).order_by(Event.embedding.cosine_distance(embedding)).limit(limit).all()
