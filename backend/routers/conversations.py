from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from models import Message, Conversation
from schemas import ConversationCreate, ConversationSchema, ConversationData, MessageBase, ConversationUpdate
from database import get_db

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
    db_conversations = db.query(Conversation).all()
    return db_conversations
@router.post("/", response_model=ConversationSchema)
def create_conversation(convesation: ConversationCreate, db: Session = Depends(get_db)):
    db_messages = [Message(**m.model_dump()) for m in convesation.messages]
    db_conversation = Conversation(title=convesation.title, messages=db_messages, creation_date=convesation.creation_date)
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)
    return db_conversation
@router.patch("/{id}", response_model=ConversationSchema)
def add_msgs_to_conversation(id:int, changes:ConversationUpdate, db: Session = Depends(get_db)):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    if (changes.msgs):
        first_new_pos = len(db_conversation.messages)
        for i, new_msg in enumerate(changes.msgs):
            db_conversation.messages.append(Message(
                **new_msg.model_dump(),
                position = first_new_pos+i
            ))
    print(changes.title)
    print(db_conversation.title)
    if changes.title: db_conversation.title = changes.title
    db.commit()
    db.refresh(db_conversation)
    return db_conversation
@router.delete("/{id}", response_model=ConversationSchema)
def delete_conversation(id:int, db: Session = Depends(get_db)):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    if db_conversation is None: raise HTTPException(status_code=404, detail="Task not found")
    db.delete(db_conversation)
    db.commit()
    return db_conversation