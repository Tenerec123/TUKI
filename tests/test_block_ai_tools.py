"""Focused tests for AI tools that read and mutate recurring blocks."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.ai.tools import exec as exec_tools
from backend.ai.tools import read as read_tools
from backend.ai.tools.local_tools import TOOL_SCHEMAS, ToolDict
from backend.logic import blocks as block_logic
from backend.models import Block
from backend.schemas import BlockOccurrenceRead


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _block(block_id: int = 1):
    return SimpleNamespace(
        id=block_id,
        name="Standup",
        description="Daily sync",
        start_time=datetime(2026, 1, 5, 9),
        end_time=datetime(2026, 1, 5, 10),
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )


def test_block_read_tools_return_schema_data_and_forward_occurrence_window(monkeypatch):
    monkeypatch.setattr(read_tools, "SessionLocal", _Session)
    monkeypatch.setattr(read_tools, "get_all_blocks_logic", lambda first_n, db: [_block()])
    monkeypatch.setattr(read_tools, "search_blocks_logic", lambda text, limit, db: [_block(2)])
    assert read_tools.GetAllBlocks(first_n=3)[0]["id"] == 1
    assert read_tools.SearchBlocks("team sync", limit=2)[0]["id"] == 2

    seen = {}

    def get_occurrences(id, window_start, window_end, db):
        seen.update(id=id, window_start=window_start, window_end=window_end)
        return [BlockOccurrenceRead(start_time=window_start, end_time=window_end)]

    monkeypatch.setattr(read_tools, "get_block_occurrences_logic", get_occurrences)
    occurrences = read_tools.GetBlockOccurrences(
        block_id=1,
        from_datetime="2026-01-05T09:00:00",
        to_datetime="2026-01-05T10:00:00",
    )
    assert seen == {
        "id": 1,
        "window_start": datetime(2026, 1, 5, 9),
        "window_end": datetime(2026, 1, 5, 10),
    }
    assert occurrences[0]["start_time"] == datetime(2026, 1, 5, 9)


@pytest.mark.parametrize(
    ("argument", "api_bound"),
    [("from_datetime", "from"), ("to_datetime", "to")],
)
def test_block_occurrence_tool_rejects_timezone_aware_bounds(monkeypatch, argument, api_bound):
    monkeypatch.setattr(read_tools, "SessionLocal", lambda: pytest.fail("database should not be queried"))

    result = read_tools.GetBlockOccurrences(
        block_id=1,
        **{argument: "2026-01-05T09:00:00+02:00"},
    )

    assert result == f"Error getting block occurrences: {api_bound} must be naive local time (no offset)"


def test_block_exception_read_tool_returns_serialized_exceptions(monkeypatch):
    monkeypatch.setattr(read_tools, "SessionLocal", _Session)
    monkeypatch.setattr(
        read_tools,
        "get_block_exceptions_logic",
        lambda id, db: [SimpleNamespace(
            id=7,
            block_id=id,
            is_add=False,
            start_time=datetime(2026, 1, 12, 9),
            end_time=None,
        )],
    )

    exceptions = read_tools.GetBlockExceptions(block_id=3)

    assert exceptions == [{
        "id": 7,
        "block_id": 3,
        "is_add": False,
        "start_time": datetime(2026, 1, 12, 9),
        "end_time": None,
    }]


def test_search_blocks_embeds_query_and_applies_similarity_order_and_limit(monkeypatch):
    class _Query:
        def order_by(self, expression):
            self.expression = expression
            return self

        def limit(self, limit):
            self.limit_value = limit
            return self

        def all(self):
            return ["matched block"]

    class _EmbeddingModel:
        def encode(self, text):
            assert text == "team sync"
            return [0.1, 0.2]

    query = _Query()

    class _Database:
        def query(self, model):
            assert model is Block
            return query

    monkeypatch.setattr(block_logic, "get_embedding_model", _EmbeddingModel)

    results = block_logic.search_blocks_logic(text="team sync", limit=4, db=_Database())

    assert results == ["matched block"]
    assert query.limit_value == 4
    assert query.expression is not None


def test_create_and_update_block_tools_parse_datetimes_and_forward_payloads(monkeypatch):
    monkeypatch.setattr(exec_tools, "SessionLocal", _Session)
    created = {}

    def create_block_logic(block, db):
        created["payload"] = block
        return _block(9)

    monkeypatch.setattr(exec_tools, "create_block_logic", create_block_logic)
    result = exec_tools.CreateBlock(
        name="Standup",
        start_time="2026-01-05T09:00:00",
        end_time="2026-01-05T10:00:00",
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert "id 9" in result
    assert created["payload"].start_time == datetime(2026, 1, 5, 9)
    assert created["payload"].rrule == "FREQ=WEEKLY;BYDAY=MO"

    updated = {}
    monkeypatch.setattr(
        exec_tools,
        "update_block_logic",
        lambda id, updated_block, db: updated.update(id=id, payload=updated_block),
    )
    assert "successfully updated" in exec_tools.UpdateBlock(
        block_id=9,
        end_time="2026-01-05T11:00:00",
    )
    assert updated["id"] == 9
    assert updated["payload"].end_time == datetime(2026, 1, 5, 11)
    assert updated["payload"].name is None

    monkeypatch.setattr(exec_tools, "delete_block_logic", lambda id, db: _block(id))
    assert "successfully deleted" in exec_tools.DeleteBlock(block_id=9)


def test_block_exception_tools_forward_typed_payloads_and_delete_scope(monkeypatch):
    monkeypatch.setattr(exec_tools, "SessionLocal", _Session)
    created = {}

    def create_exception_logic(id, data, db):
        created.update(block_id=id, payload=data)
        return SimpleNamespace(id=8, is_add=True)

    monkeypatch.setattr(exec_tools, "create_block_exception_logic", create_exception_logic)
    result = exec_tools.CreateBlockException(
        block_id=4,
        is_add=True,
        start_time="2026-01-07T15:00:00",
        end_time="2026-01-07T16:00:00",
    )
    assert "created" in result
    assert created["block_id"] == 4
    assert created["payload"].start_time == datetime(2026, 1, 7, 15)
    assert created["payload"].end_time == datetime(2026, 1, 7, 16)

    deleted = {}
    monkeypatch.setattr(
        exec_tools,
        "delete_block_exception_logic",
        lambda id, exception_id, db: deleted.update(block_id=id, exception_id=exception_id)
        or SimpleNamespace(id=8),
    )
    assert "deleted" in exec_tools.DeleteBlockException(block_id=4, exception_id=8)
    assert deleted == {"block_id": 4, "exception_id": 8}


def test_block_tools_are_registered_with_expected_required_arguments():
    expected_required = {
        "GetAllBlocks": [],
        "SearchBlocks": ["text"],
        "GetBlockOccurrences": ["block_id"],
        "GetBlockExceptions": ["block_id"],
        "CreateBlock": ["name", "start_time", "end_time", "rrule"],
        "UpdateBlock": ["block_id"],
        "DeleteBlock": ["block_id"],
        "CreateBlockException": ["block_id", "is_add", "start_time"],
        "DeleteBlockException": ["block_id", "exception_id"],
    }
    schemas = {schema["function"]["name"]: schema for schema in TOOL_SCHEMAS}

    for name, required in expected_required.items():
        assert name in ToolDict
        assert name in schemas
        assert schemas[name]["function"]["parameters"].get("required", []) == required
