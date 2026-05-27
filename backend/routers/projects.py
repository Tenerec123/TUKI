from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from ..schemas import ProjectCreate, ProjectSchema, ProjectUpdate
from ..database import get_db
from .projects_logic import get_project_logic, get_all_project_logic, create_project_logic, update_project_logic, delete_project_logic
router = APIRouter(
    prefix="/api/projects", # Todos los endpoints empezarán con esto
    tags=["projects"]        # Organiza la documentación automática (/docs)
)

@router.get("/{id}", response_model=ProjectSchema)
def get_project(id:int, db: Session = Depends(get_db)):
    return get_project_logic(id=id, db=db)

@router.get("/", response_model=List[ProjectSchema])
def get_all_project(first_n:int = None, db: Session = Depends(get_db)):
    return get_all_project_logic(first_n=first_n, db=db)
    
@router.post("/", response_model=ProjectSchema)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    return create_project_logic(project=project, db=db)

@router.patch("/{id}", response_model=ProjectSchema)
def update_project(id:int, updated_project:ProjectUpdate, db: Session = Depends(get_db)):
    return update_project_logic(id=id, updated_project=updated_project, db=db)

@router.delete("/{id}", response_model=ProjectSchema)
def delete_project(id:int, db: Session = Depends(get_db)):
    return delete_project_logic(id=id, db=db)