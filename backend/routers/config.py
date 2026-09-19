from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from ..schemas import ModelConfig
from ..models import Config
from ..database import get_db
router = APIRouter(
    prefix="/api/config",
    tags=["config"]
)

@router.get("/models")
def get_model_config():
    """Read current model config (orchestrator + searcher), falling back to defaults."""
    from ..ai.config import get_model_config
    return get_model_config()


def _upsert(db: Session, key: str, value: str):
    """Insert or update a single Config row (config table is a generic key/value store)."""
    c = db.query(Config).where(Config.key == key).first()
    if c is None:
        c = Config(key = key, value = value)
        db.add(c)
    else:
        c.value = value


@router.post("/models")
def save_model_config(config:ModelConfig, db:Session = Depends(get_db)):
    data = config.model_dump()
    # The reasoning effort is persisted INSIDE the 'orchestrator' row as a
    # composite "MODEL_ID EFFORT" value (single space), split back on read in
    # backend/ai/config.py. A separate 'orchestrator_effort' row is NEVER
    # written (a stale row from another DB is left untouched).
    orchestrator = data.get('orchestrator')
    effort = data.get('orchestrator_effort') or ''
    if orchestrator:
        value = orchestrator if not effort else f"{orchestrator} {effort}"
        _upsert(db, 'orchestrator', value)
    for key, value in data.items():
        if key in ('orchestrator', 'orchestrator_effort'):
            continue
        if value is not None:
            _upsert(db, key, value)
    db.commit()
    return config