from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from ..schemas import (
    BlockCreate,
    BlockRead,
    BlockUpdate,
    BlockExceptionCreate,
    BlockExceptionRead,
    BlockCalendarOccurrenceRead,
    BlockOccurrenceRead,
)
from ..database import get_db
from ..logic.blocks import (
    get_block_logic,
    get_all_blocks_logic,
    create_block_logic,
    update_block_logic,
    delete_block_logic,
    get_block_exceptions_logic,
    create_block_exception_logic,
    delete_block_exception_logic,
    get_block_occurrences_logic,
    get_all_block_occurrences_logic,
)

router = APIRouter(
    prefix="/api/blocks",
    tags=["blocks"]
)

@router.get("/occurrences", response_model=List[BlockCalendarOccurrenceRead])
def get_all_block_occurrences(
    from_: datetime = Query(None, alias="from"),
    to: datetime = Query(None),
    db: Session = Depends(get_db),
):
    for name, value in (("from", from_), ("to", to)):
        if value is not None and value.tzinfo is not None:
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be naive local time (no offset)",
            )
    return get_all_block_occurrences_logic(window_start=from_, window_end=to, db=db)

@router.get("/{id}", response_model=BlockRead)
def get_block(id: int, db: Session = Depends(get_db)):
    return get_block_logic(id=id, db=db)

@router.get("/", response_model=List[BlockRead])
def get_all_blocks(first_n: int = None, db: Session = Depends(get_db)):
    return get_all_blocks_logic(first_n=first_n, db=db)

@router.post("/", response_model=BlockRead)
def create_block(block: BlockCreate, db: Session = Depends(get_db)):
    return create_block_logic(block=block, db=db)

@router.patch("/{id}", response_model=BlockRead)
def update_block(id: int, updated_block: BlockUpdate, db: Session = Depends(get_db)):
    return update_block_logic(id=id, updated_block=updated_block, db=db)

@router.delete("/{id}", response_model=BlockRead)
def delete_block(id: int, db: Session = Depends(get_db)):
    return delete_block_logic(id=id, db=db)

@router.get("/{id}/occurrences", response_model=List[BlockOccurrenceRead])
def get_block_occurrences(
    id: int,
    from_: datetime = Query(None, alias="from"),
    to: datetime = Query(None),
    db: Session = Depends(get_db),
):
    for name, value in (("from", from_), ("to", to)):
        if value is not None and value.tzinfo is not None:
            raise HTTPException(status_code=400, detail=f"{name} must be naive local time (no offset)")
    return get_block_occurrences_logic(id=id, window_start=from_, window_end=to, db=db)

@router.get("/{id}/exceptions", response_model=List[BlockExceptionRead])
def get_block_exceptions(id: int, db: Session = Depends(get_db)):
    return get_block_exceptions_logic(id=id, db=db)

@router.post("/{id}/exceptions", response_model=BlockExceptionRead)
def create_block_exception(id: int, data: BlockExceptionCreate, db: Session = Depends(get_db)):
    return create_block_exception_logic(id=id, data=data, db=db)

@router.delete("/{id}/exceptions/{exception_id}", response_model=BlockExceptionRead)
def delete_block_exception(id: int, exception_id: int, db: Session = Depends(get_db)):
    return delete_block_exception_logic(id=id, exception_id=exception_id, db=db)
