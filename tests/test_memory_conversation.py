"""Tests for the wake memory conversation singleton.

Covers ``get_or_create_memory_conversation`` (creation, idempotence, repair
of a stale config ID) and that ``get_conversation_names_logic`` hides the
memory conversation from the user-facing list. Each test seeds its own
in-memory SQLite database with just the two tables involved (config and
conversations), so no embedding model is required beyond the module-level
import constraint described in ``test_task_ordering.py``.
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.logic.conversations import get_conversation_names_logic
from backend.logic.memory import (
    MEMORY_CONVERSATION_KEY,
    get_memory_conversation_id,
    get_or_create_memory_conversation,
    reset_if_stale,
)
from backend.models import AppConfig, Conversation, Message


def _seed_db():
    """Create an in-memory SQLite DB with only the config, conversations and messages tables."""
    engine = create_engine("sqlite://")
    Conversation.__table__.create(engine)
    AppConfig.__table__.create(engine)
    Message.__table__.create(engine)
    return sessionmaker(bind=engine)()


def test_get_or_create_is_idempotent():
    db = _seed_db()
    first = get_or_create_memory_conversation(db)
    second = get_or_create_memory_conversation(db)
    assert first.id == second.id
    assert get_memory_conversation_id(db) == first.id
    assert db.query(Conversation).count() == 1


def test_stale_config_id_is_repaired():
    db = _seed_db()
    db.add(AppConfig(key=MEMORY_CONVERSATION_KEY, value="999"))
    db.commit()
    conversation = get_or_create_memory_conversation(db)
    assert conversation.id != 999
    assert get_memory_conversation_id(db) == conversation.id


def test_memory_conversation_hidden_from_list():
    db = _seed_db()
    memory = get_or_create_memory_conversation(db)
    normal = Conversation(
        title="chat", messages=[], creation_date=date.today(), last_used=datetime.now()
    )
    db.add(normal)
    db.commit()
    names = get_conversation_names_logic(db=db)
    assert [c.id for c in names] == [normal.id]
    assert memory.id not in [c.id for c in names]


def _add_message(db, conversation):
    db.add(Message(conversation_id=conversation.id, position=0, type="prompt", text="hola"))
    db.commit()


def test_reset_if_stale_wipes_after_idle():
    db = _seed_db()
    conversation = get_or_create_memory_conversation(db)
    _add_message(db, conversation)
    conversation.last_used = datetime.now() - timedelta(hours=2)
    db.commit()
    assert reset_if_stale(conversation, db) is True
    assert db.query(Message).count() == 0


def test_reset_if_stale_keeps_fresh():
    db = _seed_db()
    conversation = get_or_create_memory_conversation(db)
    _add_message(db, conversation)
    conversation.last_used = datetime.now() - timedelta(minutes=30)
    db.commit()
    assert reset_if_stale(conversation, db) is False
    assert db.query(Message).count() == 1


def test_reset_if_stale_ignores_never_used():
    db = _seed_db()
    conversation = get_or_create_memory_conversation(db)
    _add_message(db, conversation)
    conversation.last_used = None
    db.commit()
    assert reset_if_stale(conversation, db) is False
    assert db.query(Message).count() == 1
