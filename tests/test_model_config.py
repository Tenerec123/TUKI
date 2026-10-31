from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db

# Avoid import-time PostgreSQL bootstrap in hermetic SQLite API tests.
Base.metadata.create_all = lambda *args, **kwargs: None

from backend.ai.config import (
    MODEL_DEFAULTS,
    get_model_config,
    get_model_config_for,
    parse_model_config_row,
)
from backend.ai import credentials
from backend.models import APIKey, AppConfig, ModelConfig


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppConfig.__table__.create(engine)
    ModelConfig.__table__.create(engine)
    APIKey.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_defaults_are_four_field_provider_qualified_rows(db_session: Session) -> None:
    rows = get_model_config(db_session)

    assert rows == MODEL_DEFAULTS
    assert {row[0] for row in rows} == {"orchestrator", "stt", "searcher", "namer"}
    assert all(len(row) == 4 and row[1] for row in rows)
    assert rows[0][3] == "minimal"


def test_legacy_model_settings_are_ignored_and_preserved(db_session: Session) -> None:
    legacy_values = {
        "orchestrator": "openai/gpt-6-luna none",
        "searcher": "google/gemini-2.5-flash-lite",
        "stt": "nvidia/parakeet-tdt-0.6b-v3",
        "stt_provider": "deepgram",
        "tts_provider": "deepgram",
        "memory_conversation_id": "42",
    }
    db_session.add_all([
        AppConfig(key=key, value=value) for key, value in legacy_values.items()
    ])
    db_session.commit()

    rows = get_model_config(db_session)

    assert rows == MODEL_DEFAULTS
    remaining = {row.key: row.value for row in db_session.query(AppConfig).all()}
    assert remaining == legacy_values
    assert db_session.query(ModelConfig).count() == 0


def test_orchestrator_usage_is_named_full_word(db_session: Session) -> None:
    """The orchestrator role is persisted and exposed as 'orchestrator', not 'orch'."""
    assert MODEL_DEFAULTS[0][0] == "orchestrator"
    assert get_model_config_for("orchestrator", db_session)[0] == "orchestrator"
    with pytest.raises(ValueError, match="Unknown model usage"):
        get_model_config_for("orch", db_session)


def test_single_role_lookup_is_one_direct_query_with_default_fallback(
    db_session: Session,
) -> None:
    """One single-role lookup issues one usage-scoped query and falls back to
    the MODEL_DEFAULTS row when the database has no row for that usage."""
    db_session.add(
        ModelConfig(
            usage="searcher",
            provider="openrouter",
            model_name="from-db",
            thinking_process=None,
        )
    )
    db_session.commit()
    statements = []

    def record(*args) -> None:
        statements.append(args[2])

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        searcher = get_model_config_for("searcher", db_session)
        assert searcher == ["searcher", "openrouter", "from-db", None]
        assert len(statements) == 1

        namer = get_model_config_for("namer", db_session)
        assert namer == next(row for row in MODEL_DEFAULTS if row[0] == "namer")
        assert len(statements) == 2
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_model_config_row_rejects_invalid_usage_or_provider() -> None:
    with pytest.raises(ValueError, match="exactly four"):
        parse_model_config_row(["orchestrator", "openrouter", "model"])
    with pytest.raises(ValueError, match="Unknown model usage"):
        parse_model_config_row(["unknown", "openrouter", "model", None])
    with pytest.raises(ValueError, match="only supported for speech"):
        parse_model_config_row(["orchestrator", "deepgram", "nova-2", None])


def test_openai_clients_use_provider_url_and_database_key(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(credentials, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(credentials, "get_provider_key", lambda provider: f"{provider}-secret")

    client = credentials.create_chat_client("opencode-go")

    assert isinstance(client, FakeClient)
    assert captured["api_key"] == "opencode-go-secret"
    assert captured["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    assert captured["default_headers"] == {"User-Agent": "TUKI/1.0"}


def test_tts_provider_setting_is_write_only_compatible(db_session: Session) -> None:
    from backend.main import api

    def override_db() -> Iterator[Session]:
        yield db_session

    api.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(api).get("/api/config/tts-provider")
        assert response.status_code == 200
        assert response.json() == {"provider": ""}
        db_session.add(APIKey(provider="deepgram", key="test-deepgram-secret"))
        db_session.commit()

        preferred = TestClient(api).get("/api/config/tts-provider")
        assert preferred.json() == {"provider": "deepgram"}

        deepgram = TestClient(api).put(
            "/api/config/tts-provider", json={"provider": "deepgram"}
        )
        assert deepgram.status_code == 200
        assert deepgram.json() == {"provider": "deepgram"}
    finally:
        api.dependency_overrides.pop(get_db, None)


def test_config_api_deletes_provider_key_and_refreshes_status(db_session: Session) -> None:
    from backend.main import api

    def override_db() -> Iterator[Session]:
        yield db_session

    api.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(api)
        llms_page = client.get("/llms")
        assert llms_page.status_code == 200
        assert llms_page.text.count("data-provider-key-delete=") == 3
        for provider in ("openrouter", "deepgram"):
            response = client.put(
                f"/api/config/keys/{provider}",
                json={"key": f"{provider}-secret"},
            )
            assert response.status_code == 200

        assert client.get("/api/config/capabilities").json()["chat"] is True
        assert client.get("/api/config/capabilities").json()["wake"] is True

        deleted = client.delete("/api/config/keys/openrouter")
        assert deleted.status_code == 200
        assert deleted.json() == {"provider": "openrouter", "configured": False}
        assert "openrouter-secret" not in deleted.text

        statuses = {row["provider"]: row["configured"] for row in client.get(
            "/api/config/keys"
        ).json()}
        assert statuses == {
            "openrouter": False,
            "opencode-go": False,
            "deepgram": True,
        }
        capabilities = client.get("/api/config/capabilities").json()
        assert capabilities == {
            "llm": False,
            "stt": True,
            "tts": True,
            "chat": False,
            "microphone": False,
            "wake": False,
        }

        # Deleting an already-absent key is idempotent.
        repeated = client.delete("/api/config/keys/openrouter")
        assert repeated.status_code == 200
        assert repeated.json() == {"provider": "openrouter", "configured": False}

        unknown = client.delete("/api/config/keys/not-a-provider")
        assert unknown.status_code == 404
        assert unknown.json() == {"detail": "Unknown provider"}
    finally:
        api.dependency_overrides.pop(get_db, None)


def test_config_api_saves_write_only_keys_and_four_field_rows(db_session: Session) -> None:
    from backend.main import api

    def override_db() -> Iterator[Session]:
        yield db_session

    api.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(api)
        save_key = client.put(
            "/api/config/keys/openrouter",
            json={"key": "test-openrouter-secret"},
        )
        assert save_key.status_code == 200
        assert save_key.json() == {"provider": "openrouter", "configured": True}
        assert "test-openrouter-secret" not in save_key.text

        oversized_key = "x" * 5000
        rejected_key = client.put(
            "/api/config/keys/openrouter",
            json={"key": oversized_key},
        )
        assert rejected_key.status_code == 422
        assert oversized_key not in rejected_key.text

        statuses = client.get("/api/config/keys")
        assert statuses.status_code == 200
        assert {row["provider"]: row["configured"] for row in statuses.json()}[
            "openrouter"
        ] is True
        assert "test-openrouter-secret" not in statuses.text

        model_rows = [
            ["orchestrator", "opencode-go", "glm-5.3-flash", "low"],
            ["searcher", "openrouter", "google/gemini-2.5-flash-lite", None],
            ["stt", "deepgram", "nova-2", None],
            ["namer", "openrouter", "mistralai/mistral-nemo", None],
        ]
        save_models = client.post("/api/config/models", json=model_rows)
        assert save_models.status_code == 200
        saved = {row[0]: row for row in save_models.json()}
        assert saved["orchestrator"] == model_rows[0]
        assert saved["stt"] == model_rows[2]

        stored_key = db_session.query(APIKey).where(
            APIKey.provider == "openrouter"
        ).one()
        assert stored_key.key == "test-openrouter-secret"
    finally:
        api.dependency_overrides.pop(get_db, None)
