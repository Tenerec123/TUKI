from datetime import datetime
from typing import List, Callable
from schemas import TaskCreate, TaskUpdate, TaskSchema, ProjectCreate, ProjectUpdate, ProjectSchema, RoutineCreate, RoutineUpdate, RoutineSchema
from database import SessionLocal
from .tasks_logic import get_all_tasks_logic, create_task_logic, delete_task_logic, update_task_logic
from .projects_logic import get_all_project_logic, create_project_logic, delete_project_logic, update_project_logic
from .routines_logic import get_all_routine_logic, create_routine_logic, delete_routine_logic, update_routine_logic
def GetAllTasks():
    '''
    Returns all the tasks in the db as a list of dictionaries.
    Args: [any args]
    '''
    with SessionLocal() as db:
        tasks = get_all_tasks_logic(first_n=None,db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]

def CreateTask(name:str, description:str, priority:int, deadline:str, project_id:int = None):
    '''
    Creates a task with using the input characteristics.
    Deadline value in dd/mm/yyyy format.
    Args: [name:str, description:str, priority:int, deadline:str, project_id:int = None]
    '''
    with SessionLocal() as db:
        new_task = create_task_logic(
            task=TaskCreate(
                name=name,
                description=description,
                priority=priority,
                deadline=datetime.strptime(deadline, '%d/%m/%Y').date(),
                project_id=project_id
                ),
                db=db)
        return f"Task {name} with id {new_task.id} successfully created"

def DeleteTask(task_id:int):
    '''
    Deletes the task with the selected id.
    Be careful with this function and don't delete any task unless you are sure you know which task you are deleting.
    Args: [task_id:int]
    '''

    with SessionLocal() as db:
        deleted_task = delete_task_logic(id=task_id, db=db)
        return f"Task {deleted_task.name} with id {deleted_task.id} successfully deleted"

def UpdateTask(task_id:int, name:str = None, description:str = None, priority:int = None, deadline:str = None, finished:bool=None):
    '''
    Updates the parameters you select of the object with the task_id you choose. Set only what you want to change.
    Deadline value in dd/mm/yyyy format.
    Args: [task_id:int, name:str = None, description:str = None, priority:int = None, deadline:str = None, finished:bool=None]
    '''
    with SessionLocal() as db:
        update_task_logic(
            id=task_id, 
            updated_task=TaskUpdate(
                name=name,
                description=description,
                priority=priority,
                deadline= None if deadline is None else datetime.strptime(deadline, '%d/%m/%Y').date(), finished=finished),
            db=db)
        return f"Task {name} with id:{task_id} successfully updated."

def GetAllRoutines():
    '''
    Returns all the routines in the db as a list of dictionaries.
    Args: [any args]
    '''
    with SessionLocal() as db:
        routines = get_all_routine_logic(first_n=None,db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]

def CreateRoutine(name:str, description:str, priority:int, frequency:str, project_id:int = None):
    '''
    Creates a routine with using the input characteristics.
    Frequency in valid RRULE code. (e.g 'FREQ=WEEKLY;BYDAY=MO,WE,FR')
    Args: [name:str, description:str, priority:int, frequency:str, project_id:int = None]
    '''
    with SessionLocal() as db:
        new_routine = create_routine_logic(
            routine=RoutineCreate(
                name=name,
                description=description,
                priority=priority,
                frequency=frequency,
                project_id=project_id
                ),
                db=db)
        return f"Routine {name} with id {new_routine.id} successfully created"

def DeleteRoutine(routine_id:int):
    '''
    Deletes the routine with the selected id. 
    Be careful with this function and don't delete any routine unless you are sure you know which routine you are deleting.
    Args: [routine_id:int]
    '''
    with SessionLocal() as db:
        deleted_routine = delete_routine_logic(id=routine_id, db=db)
        return f"Routine {deleted_routine.name} with id {deleted_routine.id} successfully deleted"

def UpdateRoutine(routine_id:int, name:str = None, description:str = None, priority:int = None, frequency:str = None):
    '''
    Updates the parameters you select of the object with the routine_id you choose. Set only what you want to change.
    Frequency in valid RRULE code. (e.g 'FREQ=WEEKLY;BYDAY=MO,WE,FR')
    Args: [routine_id:int, name:str = None, description:str = None, priority:int = None, frequency:str = None]
    '''
    with SessionLocal() as db:
        update_routine_logic(
            id=routine_id, 
            updated_task=RoutineCreate(
                name=name,
                description=description,
                priority=priority,
                frequency=frequency),
            db=db)
        return f"Routine {name} with id:{routine_id} successfully updated."

def GetAllProjects():
    '''
    Returns all the projects in the db as a list of dictionaries.
    Args: [any args]
    '''
    with SessionLocal() as db:
        projects = get_all_project_logic(first_n=None,db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]

def CreateProject(name: str, description: str = None, priority: int = None, parent_id: int = None):
    '''
    Creates a project with using the input characteristics.
    Be careful setting the parent_id. If it has not parent, don't set it.
    There will be many problems if you create a parent loop, for example if the project's parent is also its son
    Args: [name: str, description: str = None, priority: int = None, parent_id: int = None]
    '''
    if parent_id is not None and parent_id <= 0: 
        parent_id = None
    with SessionLocal() as db:
        project_data = ProjectCreate(
            name=name, 
            description=description, 
            priority=priority, 
            parent_id=parent_id
        )
        new_project = create_project_logic(project=project_data, db=db)
        return f"Project '{name}' created successfully with ID: {new_project.id}"

def DeleteProject(project_id: int):
    '''
    Deletes a project and all its sub-projects/tasks (cascade).
    Be really carefun with this and make sure to know what you ara deleting.
    Args: [project_id: int]
    '''
    with SessionLocal() as db:
        deleted = delete_project_logic(id=project_id, db=db)
        return f"Project {deleted.id} and its dependencies successfully deleted"

def UpdateProject(project_id: int, name: str = None, description: str = None, priority: int = None, parent_id: int = None):
    '''
    Updates project parameters. Only provided fields will be updated.
    Args: [project_id: int, name: str = None, description: str = None, priority: int = None, parent_id: int = None]
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
    
def CreateTree():
    pass

def ProcessBatch(commands:List[dict]):
    
    output_log = []
    for command in commands:
        func = ToolDict.get(command['tool'], None)
        if func is None:
            output_log.append(f"{command} -> That command does not exist")
            continue
        try:
            output = func(**command['args'])
            output_log.append(f"{command} -> {output}")
        except Exception as e:
            output_log.append(f"{command} -> Error: {e}")
    return output_log

ToolList:List[Callable] = [GetAllTasks, CreateTask, DeleteTask, UpdateTask, GetAllProjects, CreateProject, DeleteProject, UpdateProject, GetAllRoutines, CreateRoutine, DeleteRoutine, UpdateRoutine]
ToolDict = {t.__name__: t for t in ToolList}

def DocCreator():
    doc = '''
This is the only tool calling command, with this you can call all the functios many times.
After this, you will receive all the tool ouptuts, so, for example make sure you don't ask for information and execute something for which you need that info in the same Bach.
IDs are assigned during execution. If you need a new ID, you must call the creation in one turn and use the ID in the next turn.

Each command has this format:
    {tool:"tool_name", args:{dict with all args}}

If there's an error, the tool with the error will not be executed but the others will.

All tool:

'''
    for tool in ToolList:
        doc+=f"{tool.__name__}:{tool.__doc__}\n"

    return doc

ProcessBatch.__doc__ = DocCreator()

tool_schemas = [
    # --- TASKS ---
    {
        'name': 'GetAllTasks',
        'description': 'Returns all the tasks in the database as a list of dictionaries.',
        'parameters': {
            'type': 'object',
            'properties': {},
            'required': []
        }
    },
    {
        'name': 'CreateTask',
        'description': 'Creates a task using the input characteristics. Use dd/mm/yyyy for deadlines.',
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'The name of the task.'},
                'description': {'type': 'string', 'description': 'Detailed description of the task.'},
                'priority': {'type': 'integer', 'description': 'Priority level (integer).'},
                'deadline': {'type': 'string', 'description': 'Deadline in dd/mm/yyyy format.'},
                'project_id': {'type': 'integer', 'description': 'Optional project ID to associate the task with.'}
            },
            'required': ['name', 'description', 'priority', 'deadline']
        }
    },
    {
        'name': 'DeleteTask',
        'description': 'Deletes the task with the selected id. Use with caution.',
        'parameters': {
            'type': 'object',
            'properties': {
                'task_id': {'type': 'integer', 'description': 'The unique ID of the task to delete.'}
            },
            'required': ['task_id']
        }
    },
    {
        'name': 'UpdateTask',
        'description': 'Updates selected parameters of a specific task. Only provide fields that need changing.',
        'parameters': {
            'type': 'object',
            'properties': {
                'task_id': {'type': 'integer', 'description': 'The ID of the task to update.'},
                'name': {'type': 'string', 'description': 'New name for the task.'},
                'description': {'type': 'string', 'description': 'New description for the task.'},
                'priority': {'type': 'integer', 'description': 'New priority level.'},
                'deadline': {'type': 'string', 'description': 'New deadline in dd/mm/yyyy format.'},
                'finished': {'type': 'boolean', 'description': 'Status of task completion.'}
            },
            'required': ['task_id']
        }
    },

    # --- ROUTINES ---
    {
        'name': 'GetAllRoutines',
        'description': 'Returns all the routines in the database as a list of dictionaries.',
        'parameters': {
            'type': 'object',
            'properties': {},
            'required': []
        }
    },
    {
        'name': 'CreateRoutine',
        'description': 'Creates a routine. Frequency must be in valid RRULE code.',
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Name of the routine.'},
                'description': {'type': 'string', 'description': 'Description of the routine.'},
                'priority': {'type': 'integer', 'description': 'Priority level.'},
                'frequency': {'type': 'string', 'description': "RRULE string (e.g., 'FREQ=WEEKLY;BYDAY=MO,WE')."},
                'project_id': {'type': 'integer', 'description': 'Optional project ID association.'}
            },
            'required': ['name', 'description', 'priority', 'frequency']
        }
    },
    {
        'name': 'DeleteRoutine',
        'description': 'Deletes the routine with the selected id. Use with caution.',
        'parameters': {
            'type': 'object',
            'properties': {
                'routine_id': {'type': 'integer', 'description': 'The unique ID of the routine to delete.'}
            },
            'required': ['routine_id']
        }
    },
    {
        'name': 'UpdateRoutine',
        'description': 'Updates selected parameters of a specific routine.',
        'parameters': {
            'type': 'object',
            'properties': {
                'routine_id': {'type': 'integer', 'description': 'The ID of the routine to update.'},
                'name': {'type': 'string', 'description': 'New name.'},
                'description': {'type': 'string', 'description': 'New description.'},
                'priority': {'type': 'integer', 'description': 'New priority level.'},
                'frequency': {'type': 'string', 'description': 'New RRULE frequency string.'}
            },
            'required': ['routine_id']
        }
    },

    # --- PROJECTS ---
    {
        'name': 'GetAllProjects',
        'description': 'Returns all projects in the database as a list of dictionaries.',
        'parameters': {
            'type': 'object',
            'properties': {},
            'required': []
        }
    },
    {
        'name': 'CreateProject',
        'description': 'Creates a project. Avoid parent loops when setting parent_id.',
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Name of the project.'},
                'description': {'type': 'string', 'description': 'Description of the project.'},
                'priority': {'type': 'integer', 'description': 'Priority level.'},
                'parent_id': {'type': 'integer', 'description': 'Optional ID of the parent project.'}
            },
            'required': ['name']
        }
    },
    {
        'name': 'DeleteProject',
        'description': 'Deletes a project and all its sub-projects/tasks (cascade). High impact action.',
        'parameters': {
            'type': 'object',
            'properties': {
                'project_id': {'type': 'integer', 'description': 'The ID of the project to delete.'}
            },
            'required': ['project_id']
        }
    },
    {
        'name': 'UpdateProject',
        'description': 'Updates project parameters. Only provided fields will be updated.',
        'parameters': {
            'type': 'object',
            'properties': {
                'project_id': {'type': 'integer', 'description': 'The ID of the project to update.'},
                'name': {'type': 'string', 'description': 'New name.'},
                'description': {'type': 'string', 'description': 'New description.'},
                'priority': {'type': 'integer', 'description': 'New priority level.'},
                'parent_id': {'type': 'integer', 'description': 'New parent ID.'}
            },
            'required': ['project_id']
        }
    }
]
