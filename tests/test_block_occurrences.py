"""Tests for the block occurrence engine and the /api/blocks HTTP surface.

Pure-function tests build detached ORM objects and call the expansion/validation
helpers directly (no database session, no embedding model). HTTP tests drive the
real app through ``TestClient`` with ``get_db`` overridden to a session bound to a
transaction that is rolled back afterwards, so the dev database is never mutated.

Postgres must still be reachable for collection to succeed: importing
``backend.models`` runs ``Base.metadata.create_all(engine)`` against the
configured ``DATABASE_URL`` (see ``tests/test_task_ordering.py``).
"""

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.database import engine, get_db
from backend.logic.blocks import (
    DEFAULT_WINDOW_DAYS,
    MAX_OCCURRENCES,
    _normalize_until,
    expand_block_occurrences,
    validate_exception,
)
from backend.logic.routines import current_date
from backend.main import api
from backend.models import Block, BlockException
from backend.schemas import BlockExceptionCreate

WINDOW_START = datetime(2026, 1, 1, 0, 0)
WINDOW_END = datetime(2026, 1, 31, 23, 59)

API = "/api/blocks"
JANUARY = {"from": "2026-01-01T00:00:00", "to": "2026-01-31T23:59:00"}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db_session():
    """A session bound to a transaction that is rolled back after the test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    """TestClient whose ``get_db`` resolves to the rolled-back ``db_session``."""

    def _override_get_db():
        yield db_session

    api.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(api)
    finally:
        api.dependency_overrides.pop(get_db, None)


# --------------------------------------------------------------------------- #
# Pure expansion / validation helpers
# --------------------------------------------------------------------------- #
def _weekly_block() -> Block:
    """Weekly Monday 09:00-10:00 block starting on Monday 2026-01-05."""
    return Block(
        name="Standup",
        start_time=datetime(2026, 1, 5, 9, 0),
        end_time=datetime(2026, 1, 5, 10, 0),
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )


def _remove(start: datetime) -> BlockException:
    return BlockException(is_add=False, start_time=start, end_time=None)


def _add(start: datetime, end: datetime) -> BlockException:
    return BlockException(is_add=True, start_time=start, end_time=end)


def test_expands_rule_generated_occurrences_in_window():
    occurrences = expand_block_occurrences(_weekly_block(), [], WINDOW_START, WINDOW_END)

    assert occurrences == [
        (datetime(2026, 1, 5, 9, 0), datetime(2026, 1, 5, 10, 0)),
        (datetime(2026, 1, 12, 9, 0), datetime(2026, 1, 12, 10, 0)),
        (datetime(2026, 1, 19, 9, 0), datetime(2026, 1, 19, 10, 0)),
        (datetime(2026, 1, 26, 9, 0), datetime(2026, 1, 26, 10, 0)),
    ]


def test_removal_drops_matching_occurrence():
    exceptions = [_remove(datetime(2026, 1, 12, 9, 0))]

    occurrences = expand_block_occurrences(_weekly_block(), exceptions, WINDOW_START, WINDOW_END)

    assert [start for start, _ in occurrences] == [
        datetime(2026, 1, 5, 9, 0),
        datetime(2026, 1, 19, 9, 0),
        datetime(2026, 1, 26, 9, 0),
    ]


def test_removal_with_absent_target_is_ignored():
    exceptions = [_remove(datetime(2026, 1, 7, 9, 0))]

    occurrences = expand_block_occurrences(_weekly_block(), exceptions, WINDOW_START, WINDOW_END)

    assert len(occurrences) == 4


def test_addition_inserts_extra_occurrence_sorted_by_start():
    exceptions = [_add(datetime(2026, 1, 7, 15, 0), datetime(2026, 1, 7, 16, 0))]

    occurrences = expand_block_occurrences(_weekly_block(), exceptions, WINDOW_START, WINDOW_END)

    assert occurrences == [
        (datetime(2026, 1, 5, 9, 0), datetime(2026, 1, 5, 10, 0)),
        (datetime(2026, 1, 7, 15, 0), datetime(2026, 1, 7, 16, 0)),
        (datetime(2026, 1, 12, 9, 0), datetime(2026, 1, 12, 10, 0)),
        (datetime(2026, 1, 19, 9, 0), datetime(2026, 1, 19, 10, 0)),
        (datetime(2026, 1, 26, 9, 0), datetime(2026, 1, 26, 10, 0)),
    ]


def test_removal_plus_addition_moves_an_occurrence():
    exceptions = [
        _remove(datetime(2026, 1, 12, 9, 0)),
        _add(datetime(2026, 1, 12, 15, 0), datetime(2026, 1, 12, 16, 0)),
    ]

    occurrences = expand_block_occurrences(_weekly_block(), exceptions, WINDOW_START, WINDOW_END)

    assert occurrences == [
        (datetime(2026, 1, 5, 9, 0), datetime(2026, 1, 5, 10, 0)),
        (datetime(2026, 1, 12, 15, 0), datetime(2026, 1, 12, 16, 0)),
        (datetime(2026, 1, 19, 9, 0), datetime(2026, 1, 19, 10, 0)),
        (datetime(2026, 1, 26, 9, 0), datetime(2026, 1, 26, 10, 0)),
    ]


def test_validate_exception_accepts_valid_removal_and_addition():
    block = _weekly_block()

    validate_exception(block, BlockExceptionCreate(is_add=False, start_time=datetime(2026, 1, 12, 9, 0)))
    validate_exception(
        block,
        BlockExceptionCreate(
            is_add=True,
            start_time=datetime(2026, 1, 7, 15, 0),
            end_time=datetime(2026, 1, 7, 16, 0),
        ),
    )


def test_validate_exception_rejects_removal_without_rule_target():
    data = BlockExceptionCreate(is_add=False, start_time=datetime(2026, 1, 7, 9, 0))

    with pytest.raises(ValueError):
        validate_exception(_weekly_block(), data)


def test_validate_exception_rejects_removal_with_end_time():
    data = BlockExceptionCreate(
        is_add=False,
        start_time=datetime(2026, 1, 12, 9, 0),
        end_time=datetime(2026, 1, 12, 10, 0),
    )

    with pytest.raises(ValueError):
        validate_exception(_weekly_block(), data)


def test_validate_exception_rejects_addition_without_end_time():
    data = BlockExceptionCreate(is_add=True, start_time=datetime(2026, 1, 7, 15, 0))

    with pytest.raises(ValueError):
        validate_exception(_weekly_block(), data)


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
def _create_block(client: TestClient, **overrides) -> dict:
    payload = {
        "name": "Standup",
        "description": "daily sync",
        "start_time": "2026-01-05T09:00:00",
        "end_time": "2026-01-05T10:00:00",
        "rrule": "FREQ=WEEKLY;BYDAY=MO",
    }
    payload.update(overrides)
    response = client.post(f"{API}/", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_block_crud_lifecycle(client):
    created = _create_block(client)
    block_id = created["id"]
    assert created["rrule"] == "FREQ=WEEKLY;BYDAY=MO"

    listed = client.get(f"{API}/")
    assert listed.status_code == 200
    assert block_id in [b["id"] for b in listed.json()]

    fetched = client.get(f"{API}/{block_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Standup"

    patched = client.patch(f"{API}/{block_id}", json={"name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"

    deleted = client.delete(f"{API}/{block_id}")
    assert deleted.status_code == 200
    assert client.get(f"{API}/{block_id}").status_code == 404


def test_get_missing_block_returns_404(client):
    assert client.get(f"{API}/999999").status_code == 404


def test_occurrences_without_window_uses_default_and_returns_content(client):
    today = current_date()
    start = datetime.combine(today, time(9, 0))
    end = datetime.combine(today, time(10, 0))
    block = _create_block(
        client,
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        rrule="FREQ=DAILY",
    )

    response = client.get(f"{API}/{block['id']}/occurrences")

    assert response.status_code == 200
    starts = [o["start_time"] for o in response.json()]
    # Real content, not an empty list: the default window must span the next year.
    assert len(starts) == DEFAULT_WINDOW_DAYS
    assert starts[0] == start.isoformat()
    assert starts == sorted(starts)
    assert datetime.fromisoformat(starts[-1]).date() <= today + timedelta(days=DEFAULT_WINDOW_DAYS)


def test_weekly_rule_over_default_window_returns_200(client):
    """The normal case: FREQ=WEEKLY expands across the default 365-day window."""
    today = current_date()
    block = _create_block(client)  # FREQ=WEEKLY;BYDAY=MO, dtstart 2026-01-05 09:00

    response = client.get(f"{API}/{block['id']}/occurrences")

    assert response.status_code == 200
    starts = [datetime.fromisoformat(o["start_time"]) for o in response.json()]
    assert starts, "the default window must contain weekly occurrences"
    assert starts == sorted(starts)
    # Real content: every occurrence is a Monday 09:00 inside the default window.
    assert all(start.weekday() == 0 and start.hour == 9 for start in starts)
    assert all(today <= start.date() <= today + timedelta(days=DEFAULT_WINDOW_DAYS) for start in starts)


def test_occurrences_with_explicit_window(client):
    block = _create_block(client)

    response = client.get(f"{API}/{block['id']}/occurrences", params=JANUARY)

    assert response.status_code == 200
    assert [o["start_time"] for o in response.json()] == [
        "2026-01-05T09:00:00",
        "2026-01-12T09:00:00",
        "2026-01-19T09:00:00",
        "2026-01-26T09:00:00",
    ]


def test_occurrences_rejects_timezone_aware_bounds(client):
    block = _create_block(client)

    response = client.get(
        f"{API}/{block['id']}/occurrences",
        params={"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-31T23:59:00"},
    )

    assert response.status_code == 400


def test_removal_exception_matching_generated_start_returns_200(client):
    block = _create_block(client)

    response = client.post(
        f"{API}/{block['id']}/exceptions",
        json={"is_add": False, "start_time": "2026-01-12T09:00:00"},
    )

    assert response.status_code == 200
    occurrences = client.get(f"{API}/{block['id']}/occurrences", params=JANUARY).json()
    assert "2026-01-12T09:00:00" not in [o["start_time"] for o in occurrences]


def test_removal_exception_with_end_time_returns_400(client):
    block = _create_block(client)

    response = client.post(
        f"{API}/{block['id']}/exceptions",
        json={
            "is_add": False,
            "start_time": "2026-01-12T09:00:00",
            "end_time": "2026-01-12T10:00:00",
        },
    )

    assert response.status_code == 400


def test_addition_exception_returns_200(client):
    block = _create_block(client)

    response = client.post(
        f"{API}/{block['id']}/exceptions",
        json={
            "is_add": True,
            "start_time": "2026-01-07T15:00:00",
            "end_time": "2026-01-07T16:00:00",
        },
    )

    assert response.status_code == 200


def test_addition_exception_with_end_before_start_returns_400(client):
    block = _create_block(client)

    response = client.post(
        f"{API}/{block['id']}/exceptions",
        json={
            "is_add": True,
            "start_time": "2026-01-07T15:00:00",
            "end_time": "2026-01-07T14:00:00",
        },
    )

    assert response.status_code == 400


def test_duplicate_exception_returns_400(client):
    block = _create_block(client)
    payload = {"is_add": False, "start_time": "2026-01-19T09:00:00"}

    assert client.post(f"{API}/{block['id']}/exceptions", json=payload).status_code == 200
    assert client.post(f"{API}/{block['id']}/exceptions", json=payload).status_code == 400


def test_unparseable_rrule_on_create_returns_400(client):
    response = client.post(
        f"{API}/",
        json={
            "name": "Broken",
            "start_time": "2026-01-05T09:00:00",
            "end_time": "2026-01-05T10:00:00",
            "rrule": "FREQ=BADVALUE",
        },
    )

    assert response.status_code == 400


def test_normalize_until_uses_the_project_timezone(monkeypatch):
    """UNTIL is reinterpreted in the TIMEZONE zone, never the host zone."""
    monkeypatch.setenv("TIMEZONE", "Europe/Madrid")
    # Winter (CET, UTC+1): 08:00Z -> 09:00 Madrid.
    assert _normalize_until("UNTIL=20260126T080000Z") == "UNTIL=20260126T090000"
    # Summer (CEST, UTC+2): 08:00Z -> 10:00 Madrid.
    assert _normalize_until("UNTIL=20260726T080000Z") == "UNTIL=20260726T100000"


def test_normalize_until_uses_env_timezone_not_the_host_zone(monkeypatch):
    """Pick zones whose offset differs from this machine's zone.

    The regression this guards against is a no-arg ``astimezone()`` (the HOST
    zone). Because UTC and Pacific/Auckland have different offsets from each
    other, at least one of the two blocks below can never match the host zone, so
    the buggy implementation fails this test on every machine.
    """
    # Pacific/Auckland: January = NZDT (UTC+13), July = NZST (UTC+12).
    monkeypatch.setenv("TIMEZONE", "Pacific/Auckland")
    assert _normalize_until("UNTIL=20260126T080000Z") == "UNTIL=20260126T210000"
    assert _normalize_until("UNTIL=20260726T080000Z") == "UNTIL=20260726T200000"

    # UTC (offset 0) can never equal Auckland's UTC+12/+13.
    monkeypatch.setenv("TIMEZONE", "UTC")
    assert _normalize_until("UNTIL=20260126T080000Z") == "UNTIL=20260126T080000"


def test_utc_until_rrule_is_normalized_to_project_timezone(client, monkeypatch):
    """Google Calendar emits UNTIL in UTC (``...Z``); it must be read in TIMEZONE.

    The zone is forced to Pacific/Auckland, whose offset (UTC+13 in January)
    differs from this machine's zone, so the assertion fails if the boundary is
    read with the host zone instead. 2026-01-26T00:00Z is 13:00 in Auckland, so
    the 09:00 Monday survives; a host-zone read (Madrid, UTC+1 in January) would
    place the boundary at 01:00 and drop that occurrence.
    """
    monkeypatch.setenv("TIMEZONE", "Pacific/Auckland")
    block = _create_block(client, rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20260126T000000Z")

    response = client.get(f"{API}/{block['id']}/occurrences", params=JANUARY)

    assert response.status_code == 200
    assert [o["start_time"] for o in response.json()] == [
        "2026-01-05T09:00:00",
        "2026-01-12T09:00:00",
        "2026-01-19T09:00:00",
        "2026-01-26T09:00:00",
    ]


def test_partial_patch_inverting_interval_returns_400(client):
    block = _create_block(client)

    response = client.patch(f"{API}/{block['id']}", json={"end_time": "2026-01-05T08:00:00"})

    assert response.status_code == 400


def test_stored_unparseable_rrule_never_500s(client, db_session):
    """Read-time expansion of a bad stored rule must answer 400, never 500."""
    block = Block(
        name="Broken",
        start_time=datetime(2026, 1, 5, 9, 0),
        end_time=datetime(2026, 1, 5, 10, 0),
        rrule="FREQ=BADVALUE",
    )
    db_session.add(block)
    db_session.commit()

    response = client.get(f"{API}/{block.id}/occurrences", params=JANUARY)

    assert response.status_code == 400


def test_list_blocks_negative_first_n_returns_400(client):
    assert client.get(f"{API}/", params={"first_n": -1}).status_code == 400


def test_list_blocks_zero_first_n_returns_empty_list(client):
    _create_block(client)  # ensure at least one row exists

    response = client.get(f"{API}/", params={"first_n": 0})

    assert response.status_code == 200
    assert response.json() == []


def test_occurrence_cap_allows_a_rule_at_the_limit(client):
    """Exactly MAX_OCCURRENCES rule starts in the window is accepted (200)."""
    start = datetime(2026, 1, 5, 9, 0)
    window_end = start + timedelta(hours=MAX_OCCURRENCES - 1)
    block = _create_block(client, rrule="FREQ=HOURLY")

    response = client.get(
        f"{API}/{block['id']}/occurrences",
        params={"from": start.isoformat(), "to": window_end.isoformat()},
    )

    assert response.status_code == 200
    assert len(response.json()) == MAX_OCCURRENCES


def test_occurrence_cap_rejects_a_rule_past_the_limit(client):
    """One start beyond MAX_OCCURRENCES is rejected with 400."""
    start = datetime(2026, 1, 5, 9, 0)
    window_end = start + timedelta(hours=MAX_OCCURRENCES)
    block = _create_block(client, rrule="FREQ=HOURLY")

    response = client.get(
        f"{API}/{block['id']}/occurrences",
        params={"from": start.isoformat(), "to": window_end.isoformat()},
    )

    assert response.status_code == 400


def test_unparseable_rrule_on_patch_returns_400(client):
    block = _create_block(client)

    response = client.patch(f"{API}/{block['id']}", json={"rrule": "FREQ=BADVALUE"})

    assert response.status_code == 400


def test_pathological_rrule_returns_400_not_a_hang(client):
    """FREQ=SECONDLY over a day is ~86k occurrences; the guard must reject fast."""
    block = _create_block(client, rrule="FREQ=SECONDLY")

    response = client.get(
        f"{API}/{block['id']}/occurrences",
        params={"from": "2026-01-05T09:00:00", "to": "2026-01-06T09:00:00"},
    )

    assert response.status_code == 400


def test_oversized_window_returns_400(client):
    block = _create_block(client)

    response = client.get(
        f"{API}/{block['id']}/occurrences",
        params={"from": "2020-01-01T00:00:00", "to": "2030-01-01T00:00:00"},
    )

    assert response.status_code == 400


def test_removal_with_distant_start_on_pathological_rule_returns_400(client):
    """The bounded membership scan must fail fast, not walk millions of steps."""
    block = _create_block(client, rrule="FREQ=SECONDLY")

    response = client.post(
        f"{API}/{block['id']}/exceptions",
        json={"is_add": False, "start_time": "2026-02-05T09:00:00"},
    )

    assert response.status_code == 400
