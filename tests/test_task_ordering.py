"""Tests for ``get_all_tasks_logic`` ordering.

Order contract: unfinished tasks first, then priority ascending (1=critical
first), then deadline ascending. The tests run on an in-memory SQLite
database seeded with raw SQL, so the ORM embedding hooks never fire and no
Postgres or embedding model is required.
"""

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Importing models registers Task on Base.metadata; the real engine built in
# backend.database is created from .env but never connected during this test.
from backend.logic.tasks import get_all_tasks_logic
from backend.models import Task

_INSERT = text(
    "INSERT INTO tasks (name, description, priority, embedding, deadline, finished, project_id) "
    "VALUES (:name, NULL, :priority, NULL, :deadline, :finished, NULL)"
)


def _seed_tasks(rows):
    """Create an in-memory SQLite DB with the tasks table and given rows.

    Each row is (name, priority, deadline, finished). Raw SQL bypasses the
    ORM before-insert embedding listeners.
    """
    engine = create_engine("sqlite://")
    Task.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    with engine.begin() as conn:
        for name, priority, deadline, finished in rows:
            conn.execute(
                _INSERT,
                {
                    "name": name,
                    "priority": priority,
                    "deadline": deadline.isoformat(),
                    "finished": finished,
                },
            )
    return session


def test_unfinished_before_finished():
    session = _seed_tasks([
        ("done", 1, date(2026, 10, 1), True),
        ("open", 4, date(2026, 12, 1), False),
    ])
    rows = get_all_tasks_logic(first_n=None, db=session)
    assert [r.name for r in rows] == ["open", "done"]


def test_unfinished_ordered_by_priority_then_deadline():
    session = _seed_tasks([
        ("b-normal", 3, date(2026, 10, 2), False),
        ("a-critical", 1, date(2026, 12, 1), False),
        ("c-high-early", 2, date(2026, 10, 1), False),
        ("d-high-late", 2, date(2026, 11, 1), False),
    ])
    rows = get_all_tasks_logic(first_n=None, db=session)
    assert [r.name for r in rows] == [
        "a-critical", "c-high-early", "d-high-late", "b-normal",
    ]


def test_first_n_limit_keeps_ordering():
    session = _seed_tasks([
        ("a-critical", 1, date(2026, 10, 1), False),
        ("b-normal", 3, date(2026, 10, 2), False),
        ("c-low", 4, date(2026, 10, 3), False),
    ])
    rows = get_all_tasks_logic(first_n=2, db=session)
    assert [r.name for r in rows] == ["a-critical", "b-normal"]