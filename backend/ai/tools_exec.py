"""Write/execution tool functions for the AI agent.

Each function has a standardized docstring:
    - First line(s): description
    - Args: (optional, only for non-obvious parameters)
"""

from datetime import date, datetime
from ..schemas import TaskCreate, TaskUpdate, RoutineCreate, RoutineUpdate, ProjectCreate, ProjectUpdate
from ..database import SessionLocal
from ..logic.tasks import create_task_logic, delete_task_logic, update_task_logic
from ..logic.projects import create_project_logic, delete_project_logic, update_project_logic
from ..logic.routines import create_routine_logic, delete_routine_logic, update_routine_logic
from ._helpers import _icon_fallback, _resolve_project


def CreateTask(name: str, description: str, priority: int, deadline: str, project_id: int = None, project_name: str = None):
    '''
    Args:
        project_id: Project ID (preferred). Leave empty if unsure.
        project_name: Alternative to project_id — exact name lookup.
    '''
    with SessionLocal() as db:
        resolved, note = _resolve_project(db, project_id, project_name)
        new_task = create_task_logic(
            task=TaskCreate(
                name=name,
                description=description,
                priority=priority,
                deadline=date.fromisoformat(deadline),
                project_id=resolved
            ),
            db=db)
        return f"Task {name} with id {new_task.id} successfully created{note}"


def DeleteTask(task_id: int):
    '''
    Irreversible.
'''
    with SessionLocal() as db:
        deleted_task = delete_task_logic(id=task_id, db=db)
        return f"Task {deleted_task.name} with id {deleted_task.id} successfully deleted"


def UpdateTask(task_id: int, name: str = None, description: str = None, priority: int = None, deadline: str = None, finished: bool = None):
    '''
    Only provided fields are modified.
'''
    with SessionLocal() as db:
        update_task_logic(
            id=task_id,
            updated_task=TaskUpdate(
                name=name,
                description=description,
                priority=priority,
                deadline=None if deadline is None else date.fromisoformat(deadline),
                finished=finished
            ),
            db=db)
        return f"Task {name} with id:{task_id} successfully updated."


def CreateRoutine(name: str, description: str, priority: int, frequency: str, init_date: str = None, project_id: int = None, project_name: str = None, icon: str = None):
    '''
    Frequency in RRULE syntax (e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR).
    Args:
        project_id: Project ID (preferred). Leave empty if unsure.
        project_name: Alternative to project_id — exact name lookup.
        icon: Bootstrap icon CSS class (e.g. bell-fill, clock).
    '''
    if icon is None:
        icon = _icon_fallback(name, description)
    with SessionLocal() as db:
        resolved, note = _resolve_project(db, project_id, project_name)
        new_routine = create_routine_logic(
            routine=RoutineCreate(
                name=name,
                description=description,
                priority=priority,
                frequency=frequency,
                project_id=resolved,
                icon=icon,
                init_date=datetime.today().date() if init_date is None else date.fromisoformat(init_date)
            ),
            db=db)
        return f"Routine {name} with id {new_routine.id} successfully created{note}"


def DeleteRoutine(routine_id: int):
    '''
    Irreversible.
'''
    with SessionLocal() as db:
        deleted_routine = delete_routine_logic(id=routine_id, db=db)
        return f"Routine {deleted_routine.name} with id {deleted_routine.id} successfully deleted"


def UpdateRoutine(routine_id: int, name: str = None, description: str = None, priority: int = None, frequency: str = None, init_date: str = None, project_id: int = None, icon: str = None):
    '''
    Only provided fields are modified. Frequency in RRULE syntax.
    Args:
        project_id: Project ID to reassign (optional).
        icon: Bootstrap icon CSS class (e.g. bell-fill, clock).
    '''
    with SessionLocal() as db:
        routine = update_routine_logic(
            id=routine_id,
            updated_routine=RoutineUpdate(
                name=name,
                description=description,
                priority=priority,
                frequency=frequency,
                project_id=project_id,
                icon=icon,
                init_date=None if init_date is None else date.fromisoformat(init_date)
            ),
            db=db)
        return f"Routine {routine.name} with id:{routine_id} successfully updated."


def CreateProject(name: str, description: str = None, priority: int = None, parent_id: int = None, parent_name: str = None):
    '''
    Avoid parent loops.
    Args:
        parent_id: Parent project ID (preferred). Leave empty if unsure.
        parent_name: Alternative to parent_id — exact name lookup.
    '''
    if parent_id is not None and parent_id <= 0:
        parent_id = None
    with SessionLocal() as db:
        resolved, note = _resolve_project(db, parent_id, parent_name)
        if resolved is not None and resolved <= 0:
            resolved = None
        project_data = ProjectCreate(
            name=name,
            description=description,
            priority=priority,
            parent_id=resolved
        )
        new_project = create_project_logic(project=project_data, db=db)
        return f"Project '{name}' created successfully with ID: {new_project.id}{note}"


def DeleteProject(project_id: int):
    '''
    Cascade: deletes all sub-projects and tasks. Irreversible.
'''
    with SessionLocal() as db:
        deleted = delete_project_logic(id=project_id, db=db)
        return f"Project {deleted.id} and its dependencies successfully deleted"


def UpdateProject(project_id: int, name: str = None, description: str = None, priority: int = None, parent_id: int = None):
    '''
    Only provided fields are modified.
    Args:
        parent_id: Parent project ID (optional).
    '''
    if project_id == parent_id:
        return "ERROR: The id of the project cannot be the same as the parent id."

    with SessionLocal() as db:
        update_data = ProjectUpdate(
            name=name,
            description=description,
            priority=priority,
            parent_id=parent_id
        )
        update_project_logic(id=project_id, updated_project=update_data, db=db)
        return f"Project {project_id} updated successfully."
