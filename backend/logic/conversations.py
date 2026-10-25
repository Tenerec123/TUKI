from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..models import Message, Conversation
from ..schemas import ConversationCreate, ConversationUpdate
from datetime import date, datetime


def get_conversation_logic(id: int, db: Session):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    return db_conversation


def get_conversation_names_logic(db: Session):
    db_conversations = db.query(Conversation).order_by(Conversation.last_used.desc()).all()
    return db_conversations


def create_new_conversation_logic(conversation: ConversationCreate, db: Session):
    db_conversation = Conversation(
        title=conversation.title,
        messages=[],
        creation_date=date.today(),
        last_used=datetime.now())
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)
    return db_conversation


def edit_conversation_logic(id: int, changes: ConversationUpdate, db: Session):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    if (changes.messages):
        first_new_pos = len(db_conversation.messages)
        db_conversation.last_used = datetime.now()
        for i, new_msg in enumerate(changes.messages):
            db_conversation.messages.append(Message(
                **new_msg.model_dump(),
                position=first_new_pos + i
            ))
    if changes.title: db_conversation.title = changes.title
    if changes.last_used: db_conversation.last_used = changes.last_used
    db.commit()
    db.refresh(db_conversation)
    return db_conversation


def delete_conversation_logic(id: int, db: Session):
    db_conversation = db.query(Conversation).where(Conversation.id == id).first()
    if db_conversation is None: raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(db_conversation)
    db.commit()
    return db_conversation
