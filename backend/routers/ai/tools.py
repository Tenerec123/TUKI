from datetime import datetime
from typing import List, Callable
import json
import os
from ...schemas import TaskCreate, TaskUpdate, TaskSchema, ProjectCreate, ProjectUpdate, ProjectSchema, RoutineCreate, RoutineUpdate, RoutineSchema
from ...database import SessionLocal
from ..tasks_logic import get_all_tasks_logic, create_task_logic, delete_task_logic, update_task_logic, search_tasks_logic
from ..projects_logic import get_all_project_logic, create_project_logic, delete_project_logic, update_project_logic, search_projects_logic
from ..routines_logic import get_all_routine_logic, create_routine_logic, delete_routine_logic, update_routine_logic, search_routines_logic

# ── Bootstrap icon fallback for routines ──────────────────────────────
_ICON_KEYWORDS = [
    # (keywords, icon)
    (['daily','diaria','dia','day','cada dia','todos los dias','diario'], 'calendar-day'),
    (['weekly','semanal','semana','week','cada semana'], 'calendar-week'),
    (['monthly','mensual','month','cada mes'], 'calendar-month'),
    (['reminder','recordatorio','remind','aviso','alert'], 'bell-fill'),
    (['alarm','alarma','despertar'], 'alarm-fill'),
    (['report','informe','reporte','analytics'], 'file-text'),
    (['email','mail','correo','newsletter'], 'envelope-fill'),
    (['meeting','reunion','reunión','standup','sync'], 'people-fill'),
    (['task','tarea','chore','pendiente'], 'check2-square'),
    (['clean','limpiar','cleaning','limpieza','order'], 'broom'),
    (['health','salud','exercise','ejercicio','workout','gym'], 'heart-pulse-fill'),
    (['water','agua','drink'], 'droplet-fill'),
    (['medication','medicina','pill','medicacion','medicación'], 'capsule'),
    (['study','estudio','learn','aprender','training','train'], 'book-fill'),
    (['read','leer','reading','lectura'], 'book'),
    (['call','llamar','llamada','phone','telefono','teléfono'], 'telephone-fill'),
    (['pay','pagar','pago','bill','factura','invoice'], 'credit-card-fill'),
    (['backup','copia','backup'], 'cloud-arrow-up-fill'),
    (['check','verificar','verify','review','revisar'], 'clipboard-check-fill'),
    (['birthday','cumpleaños','gift','regalo'], 'gift-fill'),
    (['meditate','meditacion','meditación','mindfulness','peace'], 'peace-fill'),
    (['write','escribir','journal','diario','blog'], 'pencil-fill'),
    (['code','codigo','código','programar','dev','developer'], 'code-slash'),
    (['music','musica','música','podcast'], 'music-note-beamed'),
    (['travel','viaje','viajar','commute'], 'airplane-engines-fill'),
    (['buy','comprar','purchase','shopping'], 'cart-fill'),
    (['cook','cocinar','food','comida','meal'], 'egg-fill'),
    (    ['walk','caminar','walking','dog','perro'], 'person-walking'),
    (['run','correr','running','sprint','trotar','jog'], 'person-walking'),
    (['garden','jardin','jardín','plant','planta'], 'flower1'),
    (['pray','rezar','oracion','oración'], 'church'),
]

def _icon_fallback(name: str, description: str = "") -> str:
    """Match a routine name/description to a Bootstrap icon."""
    text = f"{name} {description}".lower()
    for keywords, icon in _ICON_KEYWORDS:
        if any(kw in text for kw in keywords):
            return icon
    return 'check-circle-fill'

def _resolve_project(db, project_id: int = None, project_name: str = None):
    """Resolve a project ID from direct integer or name lookup.
    
    If project_id is given, validates it exists. If only project_name is given,
    searches by exact name. Returns (resolved_id, log_suffix) where log_suffix
    is a short note appended to the tool result so the AI understands.
    """
    from ...models import Project
    if project_id is not None:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj:
            return project_id, ""
        return None, f" (WARNING: project id {project_id} not found, no parent assigned)"
    if project_name is not None:
        proj = db.query(Project).filter(Project.name == project_name).first()
        if proj:
            return proj.id, f" (auto-assigned to project '{project_name}', id {proj.id})"
        return None, f" (WARNING: project '{project_name}' not found, no parent assigned)"
    return None, ""

def GetAllTasks():
    '''
    Returns all the tasks in the db as a list of dictionaries.
    Args: [any args]
    '''
    with SessionLocal() as db:
        tasks = get_all_tasks_logic(first_n=None,db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]

def CreateTask(name:str, description:str, priority:int, deadline:str, project_id:int = None, project_name:str = None):
    '''
    Creates a task with using the input characteristics.
    Deadline value in dd/mm/yyyy format.
    project_id: project id (preferred). project_name: alternative lookup by exact name.
    Args: [name:str, description:str, priority:int, deadline:str, project_id:int = None, project_name:str = None]
    '''
    with SessionLocal() as db:
        resolved, note = _resolve_project(db, project_id, project_name)
        new_task = create_task_logic(
            task=TaskCreate(
                name=name,
                description=description,
                priority=priority,
                deadline=datetime.strptime(deadline, '%d/%m/%Y').date(),
                project_id=resolved
                ),
                db=db)
        return f"Task {name} with id {new_task.id} successfully created{note}"

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
        routines = get_all_routine_logic(db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]

def SearchTasks(text:str, limit:int = 5):
    '''
    Searches tasks by semantic similarity to the given text.
    Returns the most relevant tasks ranked by relevance (cosine distance).
    Args: [text:str, limit:int = 5]
    '''
    with SessionLocal() as db:
        tasks = search_tasks_logic(text=text, limit=limit, db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]

def SearchProjects(text:str, limit:int = 5):
    '''
    Searches projects by semantic similarity to the given text.
    Returns the most relevant projects ranked by relevance (cosine distance).
    Args: [text:str, limit:int = 5]
    '''
    with SessionLocal() as db:
        projects = search_projects_logic(text=text, limit=limit, db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]

def SearchRoutines(text:str, limit:int = 5):
    '''
    Searches routines by semantic similarity to the given text.
    Returns the most relevant routines ranked by relevance (cosine distance).
    Args: [text:str, limit:int = 5]
    '''
    with SessionLocal() as db:
        routines = search_routines_logic(text=text, limit=limit, db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]

def CheckEmail(max_unreads:int = 5):
    '''
    Checks the configured email inbox for unread messages.
    Returns sender, subject, and a snippet for each unread.
    Supports any IMAP server (Gmail, Outlook, custom).
    Args: [max_unreads:int = 5]
    '''
    import imaplib
    import email as email_lib

    server = os.environ.get('IMAP_SERVER', 'imap.gmail.com')
    user = os.environ.get('EMAIL_USER', '')
    passwd = os.environ.get('EMAIL_PASS', '')

    if not user or not passwd:
        return {'error': 'Email not configured. Set EMAIL_USER and EMAIL_PASS in .env'}

    try:
        mail = imaplib.IMAP4_SSL(server)
        mail.login(user, passwd)
        mail.select('INBOX')

        _, data = mail.search(None, 'UNSEEN')
        ids = data[0].split() if data[0] else []
        results = []

        for i in ids[-max_unreads:]:
            _, msg_data = mail.fetch(i, '(RFC822)')
            msg = email_lib.message_from_bytes(msg_data[0][1])
            payload = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        payload = part.get_payload(decode=True).decode('utf-8', errors='replace')[:200]
                        break
            else:
                payload = msg.get_payload(decode=True).decode('utf-8', errors='replace')[:200]
            results.append({
                'from': msg.get('From', ''),
                'subject': msg.get('Subject', ''),
                'date': msg.get('Date', ''),
                'snippet': payload.strip().replace('\n', ' ')[:200]
            })

        mail.logout()
        return {'unread_count': len(ids), 'emails': results}
    except Exception as e:
        return {'error': f'Failed to check email: {str(e)}'}


def CreateRoutine(name:str, description:str, priority:int, frequency:str, init_date:str = None, project_id:int = None, project_name:str = None, icon:str = None):
    '''
    Creates a routine with using the input characteristics.
    Frequency in valid RRULE code. (e.g 'FREQ=WEEKLY;BYDAY=MO,WE,FR')
    init_date value in dd/mm/yyyy format.
    project_id: project id (preferred). project_name: alternative lookup by exact name.
    icon: Bootstrap icon CSS class name, NOT an emoji or unicode (optional, e.g. bell-fill, clock, calendar-check, person-walking)
    Args: [name:str, description:str, priority:int, frequency:str, init_date:str, project_id:int = None, project_name:str = None, icon:str = None]
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
                init_date= datetime.today().date() if init_date == None else datetime.strptime(init_date, '%d/%m/%Y').date() 
                ),
                db=db)
        return f"Routine {name} with id {new_routine.id} successfully created{note}"

def DeleteRoutine(routine_id:int):
    '''
    Deletes the routine with the selected id. 
    Be careful with this function and don't delete any routine unless you are sure you know which routine you are deleting.
    Args: [routine_id:int]
    '''
    with SessionLocal() as db:
        deleted_routine = delete_routine_logic(id=routine_id, db=db)
        return f"Routine {deleted_routine.name} with id {deleted_routine.id} successfully deleted"

def UpdateRoutine(routine_id:int, name:str = None, description:str = None, priority:int = None, frequency:str = None, init_date:str = None, project_id:int = None, icon:str = None):
    '''
    Updates the parameters you select of the object with the routine_id you choose. Set only what you want to change.
    Frequency in valid RRULE code. (e.g 'FREQ=WEEKLY;BYDAY=MO,WE,FR')
    icon: Bootstrap icon CSS class name, NOT an emoji or unicode (optional, e.g. bell-fill, clock, calendar-check)
    Args: [routine_id:int, name:str = None, description:str = None, priority:int = None, frequency:str = None, icon:str = None]
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
                init_date= None if init_date is None else datetime.strptime(init_date, '%d/%m/%Y').date()),
            db=db)
        return f"Routine {routine.name} with id:{routine_id} successfully updated."

def GetAllProjects():
    '''
    Returns all the projects in the db as a list of dictionaries.
    Args: [any args]
    '''
    with SessionLocal() as db:
        projects = get_all_project_logic(first_n=None,db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]

def CreateProject(name: str, description: str = None, priority: int = None, parent_id: int = None, parent_name: str = None):
    '''
    Creates a project with using the input characteristics.
    Be careful setting the parent_id. If it has not parent, don't set it.
    There will be many problems if you create a parent loop, for example if the project's parent is also its son
    parent_id: parent project id (preferred). parent_name: alternative lookup by exact name.
    Args: [name: str, description: str = None, priority: int = None, parent_id: int = None, parent_name: str = None]
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
    
ToolList:List[Callable] = [GetAllTasks, CreateTask, DeleteTask, UpdateTask, GetAllProjects, CreateProject, DeleteProject, GetAllRoutines, CreateRoutine, DeleteRoutine, UpdateRoutine, CheckEmail, SearchTasks, SearchProjects, SearchRoutines]
ToolDict = {t.__name__: t for t in ToolList}

tool_schemas = [
    # --- TASKS ---
    {
        'type': 'function',
        'function': {
            'name': 'GetAllTasks',
            'description': 'Returns all tasks in the database.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []}
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'CreateTask',
            'description': 'Creates a task. Format deadline as dd/mm/yyyy. Set project_id ONLY if explicitly provided; otherwise leave it empty.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'priority': {'type': 'integer'},
                    'deadline': {'type': 'string'},
                    'project_id': {'type': ['integer', 'null'], 'description': 'Project ID (preferred if known). Leave null if you do NOT have an explicit ID.'},
                    'project_name': {'type': 'string', 'description': 'Project name as alternative to project_id. Only use if you know the exact name and do NOT have the ID. Ignored if project_id is set.'}
                },
                'required': ['name', 'description', 'priority', 'deadline']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'DeleteTask',
            'description': 'Deletes a specific task.',
            'parameters': {
                'type': 'object',
                'properties': {'task_id': {'type': 'integer'}},
                'required': ['task_id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'UpdateTask',
            'description': 'Updates selected parameters of a specific task.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'integer'},
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'priority': {'type': 'integer'},
                    'deadline': {'type': 'string'},
                    'finished': {'type': 'boolean'}
                },
                'required': ['task_id']
            }
        }
    },

    # --- ROUTINES ---
    {
        'type': 'function',
        'function': {
            'name': 'GetAllRoutines',
            'description': 'Returns all routines in the database.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []}
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'CreateRoutine',
            'description': 'Creates a routine.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'priority': {'type': 'integer'},
                    'frequency': {'type': 'string', 'description': "RRULE syntax (e.g., 'FREQ=WEEKLY;BYDAY=MO,WE')."},
                    'init_date': {'type': 'string'},
                    'project_id': {'type': ['integer','null'], 'description': 'Project ID (preferred if known). Leave null if you do NOT have an explicit ID.'},
                    'project_name': {'type': 'string', 'description': 'Project name as alternative to project_id. Only use if you know the exact name and do NOT have the ID. Ignored if project_id is set.'},
                    'icon': {'type': 'string', 'description': 'Bootstrap icon CSS class name — NOT an emoji or unicode character. Examples: bell-fill, clock, calendar-check (optional).'}
                },
                'required': ['name', 'description', 'priority', 'frequency']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'DeleteRoutine',
            'description': 'Deletes a specific routine.',
            'parameters': {
                'type': 'object',
                'properties': {'routine_id': {'type': 'integer'}},
                'required': ['routine_id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'UpdateRoutine',
            'description': 'Updates selected parameters of a specific routine.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'routine_id': {'type': 'integer'},
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'priority': {'type': 'integer'},
                    'frequency': {'type': 'string', 'description': 'RRULE syntax.'},
                    'init_date': {'type': 'string'},
                    'project_id': {'type': 'integer'},
                    'icon': {'type': 'string', 'description': 'Bootstrap icon CSS class name — NOT an emoji or unicode character. Examples: bell-fill, clock, calendar-check (optional).'}
                },
                'required': ['routine_id']
            }
        }
    },

    # --- PROJECTS ---
    {
        'type': 'function',
        'function': {
            'name': 'GetAllProjects',
            'description': 'Returns all projects in the database.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []}
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'CreateProject',
            'description': 'Creates a project.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'priority': {'type': 'integer'},
                    'parent_id': {'type': ['integer','null'], 'description': 'Parent project ID (preferred if known). Leave null if you do NOT have an explicit ID.'},
                    'parent_name': {'type': 'string', 'description': 'Parent project name as alternative to parent_id. Only use if you know the exact name. Ignored if parent_id is set.'}
                },
                'required': ['name']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'DeleteProject',
            'description': 'Deletes a project and all its sub-projects/tasks (cascade).',
            'parameters': {
                'type': 'object',
                'properties': {'project_id': {'type': 'integer'}},
                'required': ['project_id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'UpdateProject',
            'description': 'Updates selected project parameters.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'project_id': {'type': 'integer'},
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'priority': {'type': 'integer'},
                    'parent_id': {'type': ['integer', 'null'], 'description': 'Optional parent (another project) ID. DO NOT guess or invent an ID if not explicitly known.'}

                },
                'required': ['project_id']
            }
        }
    },
    # --- EMAIL ---
    {
        'type': 'function',
        'function': {
            'name': 'CheckEmail',
            'description': 'Checks the configured IMAP inbox for unread messages. Returns sender, subject, and a text snippet per email.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'max_unreads': {
                        'type': 'integer',
                        'description': 'Maximum number of unread emails to fetch (default 5).'
                    }
                },
                'required': []
            }
        }
    },
    # --- SEMANTIC SEARCH ---
    {
        'type': 'function',
        'function': {
            'name': 'SearchTasks',
            'description': 'Finds tasks by meaning, not keywords. Searches by semantic similarity to natural language queries (e.g. "cosas de la facu", "bugs urgentes"). Returns ranked results.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'text': {'type': 'string', 'description': 'Natural language query to search for.'},
                    'limit': {'type': 'integer', 'description': 'Max results (default 5).'}
                },
                'required': ['text']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'SearchProjects',
            'description': 'Finds projects by meaning, not keywords. Searches by semantic similarity to natural language queries (e.g. "proyectos personales", "laburo"). Returns ranked results.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'text': {'type': 'string', 'description': 'Natural language query to search for.'},
                    'limit': {'type': 'integer', 'description': 'Max results (default 5).'}
                },
                'required': ['text']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'SearchRoutines',
            'description': 'Finds routines by meaning, not keywords. Searches by semantic similarity to natural language queries (e.g. "hábitos diarios", "ejercicio"). Returns ranked results.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'text': {'type': 'string', 'description': 'Natural language query to search for.'},
                    'limit': {'type': 'integer', 'description': 'Max results (default 5).'}
                },
                'required': ['text']
            }
        }
    }
]

# --- Tool groups for phase-based execution ---
TOOL_READ_NAMES = {'GetAllTasks', 'GetAllProjects', 'GetAllRoutines', 'CheckEmail', 'SearchTasks', 'SearchProjects', 'SearchRoutines'}
TOOL_WRITE_NAMES = {'CreateTask', 'DeleteTask', 'UpdateTask', 'CreateProject', 'DeleteProject', 'UpdateProject', 'CreateRoutine', 'DeleteRoutine', 'UpdateRoutine'}
TOOL_SKIP_NAMES = set()

READ_TOOLS_SCHEMAS = [s for s in tool_schemas if s['function']['name'] in TOOL_READ_NAMES]
WRITE_TOOLS_SCHEMAS = [s for s in tool_schemas if s['function']['name'] in TOOL_WRITE_NAMES]
ALL_TOOLS_SCHEMAS = [s for s in tool_schemas if s['function']['name'] not in TOOL_SKIP_NAMES]


def _sanitize_args(args: dict) -> dict:
    """Clean model-generated args before passing to tool functions.
    
    Models often send the string "null" instead of JSON null for optional fields.
    We convert those to None and drop any None-valued keys (the function already has defaults).
    """
    cleaned = {}
    for key, value in args.items():
        # String "null" or "None" → skip (let the default handle it)
        if isinstance(value, str) and value.lower() in ("null", "none"):
            continue
        # JSON null (Python None) → skip, default handles it
        if value is None:
            continue
        cleaned[key] = value
    return cleaned


def execute_tool_call(name: str, arguments: str) -> str:
    """Execute a tool by name with JSON arguments string. Returns result JSON string."""
    func = ToolDict.get(name)
    if not func:
        print(f"[TOOL] {name} NOT FOUND in ToolDict")
        return f'"Error: Tool {name} not found"'
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        args = _sanitize_args(args)
        print(f"[TOOL] {name}(args={args})")
        result = func(**args)
        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        print(f"[TOOL] {name} → OK ({len(result_str)} chars)")
        return result_str
    except Exception as e:
        print(f"[TOOL] {name} → ERROR: {e}")
        return json.dumps(f"Execution Error: {str(e)}", default=str)