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

@router.post("/models")
def save_model_config(config:ModelConfig, db:Session = Depends(get_db)):
    for key, value in config.model_dump().items():
        c = db.query(Config).where(Config.key == key).first()
        if c is None:
            c = Config(key = key, value = value)
            db.add(c)
        else:
            c.value = value
    db.commit()
    return config