from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db

# The application currently bootstraps metadata at import time. Keep these
# hermetic API tests independent of a live deployment database.
Base.metadata.create_all = lambda *args, **kwargs: None

from backend.main import api
from backend.models import APIKey, AppConfig, ModelConfig


def make_client() -> tuple[TestClient, Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppConfig.__table__.create(engine)
    ModelConfig.__table__.create(engine)
    APIKey.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    def override_db() -> Iterator[Session]:
        yield session

    api.dependency_overrides[get_db] = override_db
    return TestClient(api), session


def test_capabilities_follow_configured_provider_keys() -> None:
    client, session = make_client()
    try:
        assert client.get("/api/config/capabilities").json() == {
            "llm": False, "stt": False, "tts": False,
            "chat": False, "microphone": False, "wake": False,
        }
        session.add(APIKey(provider="opencode-go", key="secret"))
        session.commit()
        assert client.get("/api/config/capabilities").json() == {
            "llm": True, "stt": False, "tts": False,
            "chat": False, "microphone": False, "wake": False,
        }
        saved = client.post(
            "/api/config/models",
            json=[["orchestrator", "opencode-go", "glm-5.3-flash", None]],
        )
        assert saved.status_code == 200
        assert client.get("/api/config/capabilities").json() == {
            "llm": True, "stt": False, "tts": False,
            "chat": True, "microphone": False, "wake": False,
        }
        session.add(APIKey(provider="deepgram", key="secret"))
        session.commit()
        assert client.get("/api/config/capabilities").json() == {
            "llm": True, "stt": True, "tts": True,
            "chat": True, "microphone": True, "wake": True,
        }
    finally:
        api.dependency_overrides.pop(get_db, None)
        session.close()


def test_tts_default_prefers_deepgram_then_openrouter_then_empty() -> None:
    client, session = make_client()
    try:
        assert client.get("/api/config/tts-provider").json() == {"provider": ""}
        session.add(APIKey(provider="openrouter", key="secret"))
        session.commit()
        assert client.get("/api/config/tts-provider").json() == {"provider": "openrouter"}
        session.add(APIKey(provider="deepgram", key="secret"))
        session.commit()
        assert client.get("/api/config/tts-provider").json() == {"provider": "deepgram"}
    finally:
        api.dependency_overrides.pop(get_db, None)
        session.close()


def test_deepgram_without_llm_does_not_enable_services() -> None:
    client, session = make_client()
    try:
        session.add(APIKey(provider="deepgram", key="secret"))
        session.commit()
        assert client.get("/api/config/capabilities").json() == {
            "llm": False, "stt": True, "tts": True,
            "chat": False, "microphone": False, "wake": False,
        }
    finally:
        api.dependency_overrides.pop(get_db, None)
        session.close()
