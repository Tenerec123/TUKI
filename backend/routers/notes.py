from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List
from ..models import NoteMeta
from ..schemas import NoteMetaCreate, NoteMetaSchema, NoteMetaUpdate, FolderRequest
from ..database import get_db
from ..logic.notes import (
    get_note_logic, create_note_logic, update_note_logic,
    delete_note_logic, get_file_tree_logic,
    create_folder_logic, delete_folder_logic,
    decide_ai_changes_logic
)

router = APIRouter(
    prefix="/api/notes",
    tags=["notes"]
)

@router.get("/tree")
def get_file_tree(permission: bool = Query(True), db: Session = Depends(get_db)):
    return get_file_tree_logic(permission=permission, db=db)

@router.get("/", response_model=List[NoteMetaSchema])
def list_notes(db: Session = Depends(get_db)):
    
    return db.query(NoteMeta).all()


@router.post("/folder")
def create_folder(req: FolderRequest):
    return create_folder_logic(path=req.path)

@router.delete("/folder")
def delete_folder(req: FolderRequest, db: Session = Depends(get_db)):
    return delete_folder_logic(path=req.path, db=db)

@router.get("/{id}")
def get_note(id: int, db: Session = Depends(get_db)):
    return {"content": get_note_logic(id=id, db=db)}

@router.post("/", response_model=NoteMetaSchema)
def create_note(note: NoteMetaCreate, permission: bool = Query(True), db: Session = Depends(get_db)):
    return create_note_logic(note=note, permission=permission, db=db)

@router.patch("/{id}", response_model=NoteMetaSchema)
def update_note(id: int, note_update: NoteMetaUpdate, db: Session = Depends(get_db)):
    return update_note_logic(id=id, note_update=note_update, db=db)

@router.delete("/{id}", response_model=NoteMetaSchema)
def delete_note(id: int, db: Session = Depends(get_db)):
    return delete_note_logic(id=id, db=db)

@router.post("/ai/sync")
def sync_notes(db:Session = Depends(get_db)):
    return decide_ai_changes_logic(True, db)

@router.post("/ai/discard")
def discard_notes(db:Session = Depends(get_db)):
    return decide_ai_changes_logic(False, db)