from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from ..schemas import ConversationCreate, ConversationSchema, ConversationData, ConversationUpdate
from ..database import get_db
from ..logic.conversations import (
    get_conversation_logic, get_conversation_names_logic,
    create_new_conversation_logic, edit_conversation_logic,
    delete_conversation_logic
)

router = APIRouter(
    prefix="/api/conversations",
    tags=["conversation"]
)

@router.get("/{id}", response_model=ConversationSchema)
def get_conversation(id: int, db: Session = Depends(get_db)):
    return get_conversation_logic(id=id, db=db)

@router.get("/", response_model=List[ConversationData])
def get_conversation_names(db: Session = Depends(get_db)):
    return get_conversation_names_logic(db=db)

@router.post("/", response_model=ConversationSchema)
def create_new_conversation(conversation: ConversationCreate, db: Session = Depends(get_db)):
    return create_new_conversation_logic(conversation=conversation, db=db)

@router.patch("/{id}", response_model=ConversationSchema)
def edit_conversation(id: int, changes: ConversationUpdate, db: Session = Depends(get_db)):
    return edit_conversation_logic(id=id, changes=changes, db=db)

@router.delete("/{id}", response_model=ConversationSchema)
def delete_conversation(id: int, db: Session = Depends(get_db)):
    return delete_conversation_logic(id=id, db=db)
