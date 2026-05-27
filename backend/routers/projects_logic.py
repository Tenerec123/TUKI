from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..schemas import ProjectCreate, ProjectUpdate
from ..models import Project

def get_project_logic(id:int, db: Session):
    db_project = db.query(Project).where(Project.id == id).first()
    if db_project is None: raise HTTPException(status_code=404, detail="Project not found")
    return db_project

def get_all_project_logic(first_n:int, db: Session):
    if first_n is None:
        db_project = db.query(Project).all()
        return db_project
    db_project = db.query(Project).limit(first_n).all()
    return db_project
    
def create_project_logic(project: ProjectCreate, db: Session):
    db_project = Project(**project.model_dump())
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project

def update_project_logic(id:int, updated_project:ProjectUpdate, db: Session):
    db_project = db.query(Project).where(Project.id == id).first()
    if db_project is None: raise HTTPException(status_code=404, detail="Project not found")
    if updated_project.name is not None:db_project.name = updated_project.name
    if updated_project.description is not None:db_project.description = updated_project.description
    if updated_project.priority is not None:db_project.priority = updated_project.priority
    if updated_project.parent_id is not None:db_project.parent_id = updated_project.parent_id
    db.commit()
    return db_project

def delete_project_logic(id:int, db: Session):
    project_db = db.query(Project).where(Project.id == id).first()
    if project_db is None: raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project_db)
    db.commit()
    return project_db