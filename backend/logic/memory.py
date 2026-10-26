"""Singleton memory conversation for the wake assistant.

The memory system reuses the conversations/messages tables instead of new
ones: a single Conversation row holds the assistant's memories as messages.
Its identity lives in the generic config key/value store rather than a
schema column, because system conversations are an infrastructure concern,
not a domain concept.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from ..models import Config, Conversation

MEMORY_CONVERSATION_KEY = "memory_conversation_id"
MEMORY_CONVERSATION_TITLE = "TUKI Memory"


def _store_memory_id(db: Session, conversation_id: int) -> None:
    row = db.query(Config).where(Config.key == MEMORY_CONVERSATION_KEY).first()
    if row is None:
        db.add(Config(key=MEMORY_CONVERSATION_KEY, value=str(conversation_id)))
    else:
        row.value = str(conversation_id)


def get_memory_conversation_id(db: Session) -> int | None:
    """Return the configured memory conversation ID, or None when unset."""
    row = db.query(Config).where(Config.key == MEMORY_CONVERSATION_KEY).first()
    return int(row.value) if row else None


def get_or_create_memory_conversation(db: Session) -> Conversation:
    """Return the memory conversation, creating (or repairing) it on demand.

    A stored ID that no longer resolves (row deleted externally) is treated
    as unset: a fresh conversation is created and the config key updated.
    """
    conversation_id = get_memory_conversation_id(db)
    if conversation_id is not None:
        conversation = db.query(Conversation).where(Conversation.id == conversation_id).first()
        if conversation is not None:
            return conversation

    conversation = Conversation(
        title=MEMORY_CONVERSATION_TITLE,
        messages=[],
        creation_date=date.today(),
        last_used=datetime.now(),
    )
    db.add(conversation)
    db.flush()  # assign the PK before storing the reference
    _store_memory_id(db, conversation.id)
    db.commit()
    db.refresh(conversation)
    return conversation


def reset_if_stale(conversation: Conversation, db: Session, max_idle_hours: float = 1.0) -> bool:
    """Wipe the conversation's messages when it has been idle too long.

    The wake session rolls: the history persists while the conversation is in
    use, but after max_idle_hours without activity the next read starts fresh.
    last_used doubles as the idle clock (edit_conversation_logic refreshes it
    on every append), so the wipe can only happen between sessions — never
    mid-conversation, which is what keeps the LLM prompt cache stable.
    Returns True when the conversation was reset.
    """
    if conversation.last_used is None:
        return False
    if datetime.now() - conversation.last_used <= timedelta(hours=max_idle_hours):
        return False
    conversation.messages = []  # delete-orphan cascade removes the Message rows
    db.commit()
    return True
