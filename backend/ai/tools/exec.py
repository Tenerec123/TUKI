from fastapi import HTTPException
from pydantic import ValidationError
from ...schemas import TaskCreate, TaskUpdate, RoutineCreate, RoutineUpdate, ProjectCreate, ProjectUpdate, NoteMetaCreate, NoteMetaUpdate, EventCreate, EventUpdate, BlockCreate, BlockUpdate, BlockExceptionCreate
from ...database import SessionLocal
from ...logic.tasks import create_task_logic, delete_task_logic, update_task_logic
from ...logic.projects import create_project_logic, delete_project_logic, update_project_logic
from ...logic.routines import create_routine_logic, delete_routine_logic, update_routine_logic, set_routine_check_logic, current_date
from ...logic.events import create_event_logic, delete_event_logic, update_event_logic
from ...logic.blocks import create_block_logic, delete_block_logic, update_block_logic, create_block_exception_logic, delete_block_exception_logic
from ...logic.notes import create_note_logic, create_folder_logic, delete_note_logic, delete_folder_logic, update_note_logic
from ._helpers import _icon_fallback, _resolve_project
from .dates import parse_relative_date, parse_relative_datetime


def CreateTask(name: str, priority: int, deadline: str, description: str = None, project_id: int = None, project_name: str = None):
    '''
    Args:
        name: Short title of the task.
        deadline: When the task is due.
        description: Longer details (optional). null/empty = task without description.
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
                deadline=parse_relative_date(deadline),
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


def UpdateTask(task_id: int, name: str = None, description: str = None, priority: int = None, deadline: str = None, finished: bool = None, project_id: int = None):
    '''
    Only provided fields are modified. null/absent fields are left unchanged; there is no way to unassign a project.
    Args:
        name: New name (optional).
        description: New description (optional).
        deadline: New deadline (optional).
        finished: True/False (optional).
        project_id: Project ID to reassign (optional). null/absent = keep current project.
    '''
    with SessionLocal() as db:
        resolved, note = _resolve_project(db, project_id, None)
        update_task_logic(
            id=task_id,
            updated_task=TaskUpdate(
                name=name,
                description=description,
                priority=priority,
                deadline=None if deadline is None else parse_relative_date(deadline),
                finished=finished,
                project_id=resolved
            ),
            db=db)
        return f"Task {task_id} successfully updated{note}"


def CreateRoutine(name: str, priority: int, frequency: str, description: str = None, init_date: str = None, project_id: int = None, project_name: str = None, icon: str = None):
    '''
    Frequency in RRULE syntax (e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR).
    Args:
        name: Short title of the routine.
        frequency: RRULE frequency syntax.
        description: Longer details (optional). null/empty = routine without description.
        init_date: Start date (optional). null = today.
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
                init_date=current_date() if init_date is None else parse_relative_date(init_date)
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
    Only provided fields are modified. null/absent fields are left unchanged; there is no way to unassign a project.
    Frequency in RRULE syntax.
    Args:
        project_id: Project ID to reassign (optional). null/absent = keep current project.
        icon: Bootstrap icon CSS class (e.g. bell-fill, clock).
    '''
    with SessionLocal() as db:
        resolved, note = _resolve_project(db, project_id, None)
        routine = update_routine_logic(
            id=routine_id,
            updated_routine=RoutineUpdate(
                name=name,
                description=description,
                priority=priority,
                frequency=frequency,
                project_id=resolved,
                icon=icon,
                init_date=None if init_date is None else parse_relative_date(init_date)
            ),
            db=db)
        return f"Routine {routine.name} with id:{routine_id} successfully updated{note}"

def SetRoutineCheck(routine_id: int, day: str, checked: bool):
    '''
    Marks or unmarks a routine as done on a specific day.
    Args:
        checked: true to mark the routine as done, false to unmark it.
    '''
    info_to_the_model = {
        "applied":"change applied",
        "unchanged":"nothing changed, it was already as desired",
        "unavailable_future": "you cannot change future dates",
        "unavailable_out": "date out of routine scope"
    }
    try:
        target = parse_relative_date(day)
        with SessionLocal() as db:
            return info_to_the_model[set_routine_check_logic(
                id=routine_id,
                day=target,
                checked=checked,
                db=db)]
    except (HTTPException, ValidationError, ValueError, TypeError) as e:
        return f"Error updating routine check: {e}"


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
    Only provided fields are modified. null/absent fields are left unchanged; there is no way to unassign.
    Args:
        parent_id: Parent project ID (optional). null/absent = keep current parent.
    '''
    if project_id == parent_id:
        return "ERROR: The id of the project cannot be the same as the parent id."

    with SessionLocal() as db:
        resolved, note = _resolve_project(db, parent_id, None)
        update_data = ProjectUpdate(
            name=name,
            description=description,
            priority=priority,
            parent_id=resolved
        )
        update_project_logic(id=project_id, updated_project=update_data, db=db)
        return f"Project {project_id} updated successfully{note}"

def DraftCreateNote(title: str, path: str = None, content: str = ""):
    '''
    Creates a note in the draft folder.
    Args:
        title: omit .md
        path: folder1/folder2/folder3 format (optional). null/empty = root of drafts.
        content: markdown
    '''
    with SessionLocal() as db:
        create_note_logic(NoteMetaCreate(
            title=title,
            path=path or "",
            content=content
        ),
        permission=False,
        db=db)

    return f"Note {title} created succesfully"

def DraftDeleteNote(note_id):
    '''
    '''
    with SessionLocal() as db:
        noteM = delete_note_logic(
            id=note_id,
            db=db
        )
    return f"Note {noteM.title} (id:{note_id}) deleted successfully"

def DraftUpdateNote(note_id:int, title:str = None, content:str = None):
    '''
    Only provided fields are modified. null/absent fields are left unchanged.
    Args:
        note_id: Note id.
        title: New title (optional).
        content: New markdown content (optional).
    '''
    with SessionLocal() as db:
        update_note_logic(
            id= note_id,
            note_update=NoteMetaUpdate(
                title=title,
                content=content
            ),
            db=db
        )


def CreateEvent(name: str, start_time: str, end_time: str, description: str = None):
    '''
    Creates a scheduled event (meeting, appointment or other timed commitment).
    Datetimes are naive LOCAL time, e.g. 2026-09-15T09:00:00 (no timezone offset).
    start_time and end_time accept relative forms such as "tomorrow 09:00" or
    "next monday 14:30", or an absolute ISO-8601 local datetime.
    end_time must not precede start_time; start_time == end_time (zero duration) is allowed.
    Args:
        name: Short title of the event.
        start_time: Start datetime, relative (e.g. "tomorrow 09:00") or ISO-8601 naive local (e.g. 2026-09-15T09:00:00).
        end_time: End datetime, relative (e.g. "tomorrow 10:00") or ISO-8601 naive local (e.g. 2026-09-15T10:00:00).
        description: Longer details (optional). null/empty = event without description.
    '''
    try:
        with SessionLocal() as db:
            new_event = create_event_logic(
                event=EventCreate(
                    name=name,
                    description=description,
                    start_time=parse_relative_datetime(start_time),
                    end_time=parse_relative_datetime(end_time),
                ),
                db=db)
            return f"Event {new_event.name} with id {new_event.id} successfully created"
    except (HTTPException, ValidationError, ValueError) as e:
        return f"Error creating event: {e}"


def UpdateEvent(event_id: int, name: str = None, description: str = None, start_time: str = None, end_time: str = None):
    '''
    Only provided fields are modified. null/absent fields are left unchanged.
    Datetimes are naive LOCAL time, e.g. 2026-09-15T09:00:00 (no timezone offset).
    start_time and end_time accept relative forms such as "tomorrow 09:00" or
    "next monday 14:30", or an absolute ISO-8601 local datetime.
    end_time must not precede start_time; start_time == end_time (zero duration) is allowed.
    Args:
        event_id: ID of the event to update.
        name: New name (optional).
        description: New description (optional).
        start_time: New start datetime, relative (e.g. "tomorrow 09:00") or ISO-8601 naive local (optional).
        end_time: New end datetime, relative (e.g. "tomorrow 10:00") or ISO-8601 naive local (optional).
    '''
    try:
        with SessionLocal() as db:
            update_event_logic(
                id=event_id,
                updated_event=EventUpdate(
                    name=name,
                    description=description,
                    start_time=None if start_time is None else parse_relative_datetime(start_time),
                    end_time=None if end_time is None else parse_relative_datetime(end_time),
                ),
                db=db)
            return f"Event {event_id} successfully updated"
    except (HTTPException, ValidationError, ValueError) as e:
        return f"Error updating event: {e}"


def DeleteEvent(event_id: int):
    '''
    Deletes an event. Irreversible.
    Args:
        event_id: ID of the event to delete.
    '''
    try:
        with SessionLocal() as db:
            deleted_event = delete_event_logic(id=event_id, db=db)
            return f"Event {deleted_event.name} with id {deleted_event.id} successfully deleted"
    except HTTPException as e:
        return f"Error deleting event: {e}"


def CreateBlock(name: str, start_time: str, end_time: str, rrule: str, description: str = None) -> str:
    '''
    Creates a recurring time block using an iCalendar RRULE.
    Datetimes are naive local time and accept relative forms such as "tomorrow 09:00"
    or ISO-8601 local datetimes. The end time may equal the start time.
    Args:
        name: Short title of the recurring block.
        start_time: First occurrence start, relative or ISO-8601 naive local datetime.
        end_time: First occurrence end, relative or ISO-8601 naive local datetime.
        rrule: Recurrence rule in RFC 5545 syntax (for example FREQ=WEEKLY;BYDAY=MO).
        description: Longer details (optional).
    '''
    try:
        with SessionLocal() as db:
            block = create_block_logic(
                block=BlockCreate(
                    name=name,
                    description=description,
                    start_time=parse_relative_datetime(start_time),
                    end_time=parse_relative_datetime(end_time),
                    rrule=rrule,
                ),
                db=db,
            )
            return f"Block {block.name} with id {block.id} successfully created"
    except (HTTPException, ValidationError, ValueError) as error:
        detail = error.detail if isinstance(error, HTTPException) else error
        return f"Error creating block: {detail}"


def UpdateBlock(block_id: int, name: str = None, description: str = None, start_time: str = None, end_time: str = None, rrule: str = None) -> str:
    '''
    Updates only the provided fields of a recurring block.
    Datetimes are naive local time and accept relative forms such as "tomorrow 09:00"
    or ISO-8601 local datetimes.
    Args:
        block_id: ID of the block to update.
        name: New title (optional).
        description: New details (optional).
        start_time: New first occurrence start (optional).
        end_time: New first occurrence end (optional).
        rrule: New RFC 5545 recurrence rule (optional).
    '''
    try:
        with SessionLocal() as db:
            update_block_logic(
                id=block_id,
                updated_block=BlockUpdate(
                    name=name,
                    description=description,
                    start_time=None if start_time is None else parse_relative_datetime(start_time),
                    end_time=None if end_time is None else parse_relative_datetime(end_time),
                    rrule=rrule,
                ),
                db=db,
            )
            return f"Block {block_id} successfully updated"
    except (HTTPException, ValidationError, ValueError) as error:
        detail = error.detail if isinstance(error, HTTPException) else error
        return f"Error updating block: {detail}"


def DeleteBlock(block_id: int) -> str:
    '''
    Deletes a recurring block and its exceptions. Irreversible.
    Args:
        block_id: ID of the block to delete.
    '''
    try:
        with SessionLocal() as db:
            block = delete_block_logic(id=block_id, db=db)
            return f"Block {block.name} with id {block.id} successfully deleted"
    except HTTPException as error:
        return f"Error deleting block: {error.detail}"


def CreateBlockException(block_id: int, is_add: bool, start_time: str, end_time: str = None) -> str:
    '''
    Adds an extra occurrence at a start not generated by the rule, or removes a
    rule-generated occurrence. To move an occurrence, create a removal for its
    generated start and a separate addition at the new time.
    Datetimes are naive local time and accept relative forms such as "tomorrow 09:00"
    or ISO-8601 local datetimes. An addition requires end_time; a removal must omit it.
    Args:
        block_id: ID of the block.
        is_add: True to add an extra occurrence; false to remove a rule-generated occurrence.
        start_time: Start of the added occurrence or generated occurrence to remove.
        end_time: Required for an addition; omit for a removal.
    '''
    try:
        with SessionLocal() as db:
            exception = create_block_exception_logic(
                id=block_id,
                data=BlockExceptionCreate(
                    is_add=is_add,
                    start_time=parse_relative_datetime(start_time),
                    end_time=None if end_time is None else parse_relative_datetime(end_time),
                ),
                db=db,
            )
            action = "added" if exception.is_add else "removed"
            return f"Block exception {exception.id} successfully created; occurrence {action}"
    except (HTTPException, ValidationError, ValueError) as error:
        detail = error.detail if isinstance(error, HTTPException) else error
        return f"Error creating block exception: {detail}"


def DeleteBlockException(block_id: int, exception_id: int) -> str:
    '''
    Deletes a block exception, restoring the rule-generated occurrence for removals.
    Args:
        block_id: ID of the block.
        exception_id: ID of the exception to delete.
    '''
    try:
        with SessionLocal() as db:
            exception = delete_block_exception_logic(id=block_id, exception_id=exception_id, db=db)
            return f"Block exception {exception.id} successfully deleted"
    except HTTPException as error:
        return f"Error deleting block exception: {error.detail}"
