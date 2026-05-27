from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..models import Message, Conversation
from ..schemas import ConversationCreate, ConversationSchema, ConversationData, MessageBase, ConversationUpdate
from ..database import get_db
from datetime import date,datetime
router = APIRouter(
    prefix="/api/conversations", # Todos los endpoints empezarán con esto
    tags=["conversation"]        # Organiza la documentación automática (/docs)
)

@router.get("/{id}", response_model=ConversationSchema)
def get_conversation(id:int, db: Session = Depends(get_db)):
    db_conversations = db.query(Conversation).where(Conversation.id == id).first()
    return db_conversations
@router.get("/", response_model=List[ConversationData])
def get_conversation_names(db: Session = Depends(get_db)):
    db_conversations = db.query(Conversation).order_by(Conversation.last_used.desc()).all()
    return db_conversations
@router.post("/", response_model=ConversationSchema)
def create_new_conversation(convesation: ConversationCreate, db: Session = Depends(get_db)):
    db_conversation = Conversation(
        title=convesation.title,
        messages=[],
        creation_date=date.today(),
        last_used = datetime.now())
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)
    return db_conversation

def edit_conversation_logic(id:int, changes:ConversationUpdate, db:Session):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    if (changes.messages):
        first_new_pos = len(db_conversation.messages)
        db_conversation.last_used = datetime.now()
        for i, new_msg in enumerate(changes.messages):
            db_conversation.messages.append(Message(
                **new_msg.model_dump(),
                position = first_new_pos+i
            ))
    if changes.title: db_conversation.title = changes.title
    if changes.last_used: db_conversation.last_used = changes.last_used
    db.commit()
    db.refresh(db_conversation)
    return db_conversation

@router.patch("/{id}", response_model=ConversationSchema)
def edit_conversation(id:int, changes:ConversationUpdate, db: Session = Depends(get_db)):
    return edit_conversation_logic(id=id, changes=changes, db=db)

@router.delete("/{id}", response_model=ConversationSchema)
def delete_conversation(id:int, db: Session = Depends(get_db)):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    if db_conversation is None: raise HTTPException(status_code=404, detail="Task not found")
    db.delete(db_conversation)
    db.commit()
    return db_conversation