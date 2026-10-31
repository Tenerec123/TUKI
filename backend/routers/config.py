from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai.config import (
    KNOWN_PROVIDERS,
    get_model_config,
    get_model_config_for,
    parse_model_config_row,
)
from ..database import get_db
from ..models import APIKey, AppConfig, ModelConfig as ModelConfigRow
from ..schemas import ProviderKeyInput

router = APIRouter(
    prefix="/api/config",
    tags=["config"],
)


@router.get("/models")
def read_model_config(db: Session = Depends(get_db)) -> list[list[str | None]]:
    """Return model assignments as [usage, provider, model, thinking] rows."""
    return get_model_config(db)


@router.post("/models")
def save_model_config(
    rows: list[list[str | None]],
    db: Session = Depends(get_db),
) -> list[list[str | None]]:
    """Validate and upsert four-field model assignment rows."""
    if len(rows) > 16:
        raise HTTPException(status_code=422, detail="Too many model config rows")

    try:
        normalized = [parse_model_config_row(row) for row in rows]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    usages = [row[0] for row in normalized]
    if len(usages) != len(set(usages)):
        raise HTTPException(status_code=422, detail="Each model usage may appear once")

    for usage, provider, model_name, thinking_process in normalized:
        row = db.query(ModelConfigRow).where(ModelConfigRow.usage == usage).first()
        if row is None:
            row = ModelConfigRow(
                usage=usage,
                provider=provider,
                model_name=model_name,
                thinking_process=thinking_process,
            )
            db.add(row)
        else:
            row.provider = provider
            row.model_name = model_name
            row.thinking_process = thinking_process

    db.commit()
    return get_model_config(db)


@router.get("/keys")
def list_key_status(db: Session = Depends(get_db)) -> list[dict[str, bool | str]]:
    """List configured providers without ever returning stored key values."""
    configured = {row.provider for row in db.query(APIKey.provider).all()}
    return [
        {"provider": provider, "configured": provider in configured}
        for provider in KNOWN_PROVIDERS
    ]


@router.get("/capabilities")
def read_service_capabilities(db: Session = Depends(get_db)) -> dict[str, bool]:
    """Report service availability from configured provider keys."""
    configured = {row.provider for row in db.query(APIKey.provider).all()}
    llm_providers = {"openrouter", "opencode-go"}
    voice_providers = {"openrouter", "deepgram"}
    llm = bool(configured & llm_providers)
    stt = bool(configured & voice_providers)
    tts = bool(configured & voice_providers)
    chat = get_model_config_for('orchestrator', db)[1] in configured & llm_providers
    return {
        "llm": llm,
        "stt": stt,
        "tts": tts,
        "chat": chat,
        "microphone": chat and stt,
        "wake": chat and stt and tts,
    }


@router.get("/tts-provider")
def read_tts_provider(db: Session = Depends(get_db)) -> dict[str, str]:
    """Return the selected or preferred configured TTS provider."""
    configured = {row.provider for row in db.query(APIKey.provider).all()}
    row = db.query(AppConfig).where(AppConfig.key == "tts_provider").first()
    if row is not None and row.value in configured and row.value in {"openrouter", "deepgram"}:
        return {"provider": row.value}
    if "deepgram" in configured:
        return {"provider": "deepgram"}
    if "openrouter" in configured:
        return {"provider": "openrouter"}
    return {"provider": ""}


@router.put("/tts-provider")
def save_tts_provider(
    payload: dict[str, str],
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Select a configured TTS provider."""
    provider = payload.get("provider", "")
    if provider not in {"openrouter", "deepgram"}:
        raise HTTPException(status_code=422, detail="Unknown TTS provider")
    configured = db.query(APIKey).where(APIKey.provider == provider).first()
    if configured is None:
        raise HTTPException(status_code=422, detail="TTS provider API key is not configured")
    row = db.query(AppConfig).where(AppConfig.key == "tts_provider").first()
    if row is None:
        db.add(AppConfig(key="tts_provider", value=provider))
    else:
        row.value = provider
    db.commit()
    return {"provider": provider}


@router.put("/keys/{provider}")
def save_provider_key(
    provider: str,
    payload: ProviderKeyInput,
    db: Session = Depends(get_db),
) -> dict[str, bool | str]:
    """Store or replace one provider key; the response contains status only."""
    if provider not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    key = payload.key.strip()
    if not key:
        raise HTTPException(status_code=422, detail="API key cannot be empty")
    if len(key) > 4096:
        raise HTTPException(status_code=422, detail="API key is too long")

    row = db.query(APIKey).where(APIKey.provider == provider).first()
    if row is None:
        db.add(APIKey(provider=provider, key=key))
    else:
        row.key = key
    db.commit()
    return {"provider": provider, "configured": True}


@router.delete("/keys/{provider}")
def delete_provider_key(
    provider: str,
    db: Session = Depends(get_db),
) -> dict[str, bool | str]:
    """Delete one saved provider key without returning its value.

    Deleting an already-absent key is idempotent so the LLMS UI can safely
    refresh status after a repeated request.
    """
    if provider not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    row = db.query(APIKey).where(APIKey.provider == provider).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return {"provider": provider, "configured": False}
