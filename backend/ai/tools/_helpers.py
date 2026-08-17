"""Helper functions used by exec_tools.py and tools.py."""

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
    (['walk','caminar','walking','dog','perro'], 'person-walking'),
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
