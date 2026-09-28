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
    for key, model, effort in (
        ('orchestrator', config.orchestrator, config.orchestrator_effort),
        ('searcher', config.searcher, config.searcher_effort),
        ('stt', config.stt, None),
    ):
        if not model:
            continue
        _upsert(db, key, ' '.join(part for part in (model, effort) if part))
    db.commit()
    return config
