"""Tests for the relative date expression parser.

Every case injects ``today`` so the result is deterministic; the main fixture
is 2026-10-09, a Friday.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai.tools.dates import parse_relative_date
import backend.ai.tools.dates as dates

TODAY = date(2026, 10, 9)  # Friday


@pytest.mark.parametrize("expr,expected", [
    ("today", date(2026, 10, 9)),
    ("now", date(2026, 10, 9)),
    ("tomorrow", date(2026, 10, 10)),
    ("yesterday", date(2026, 10, 8)),
    ("next monday", date(2026, 10, 12)),
    ("next mon", date(2026, 10, 12)),
    ("this monday", date(2026, 10, 5)),
    ("last friday", date(2026, 10, 2)),
    ("end_of_week", date(2026, 10, 11)),
    ("end_of_month", date(2026, 10, 31)),
    ("end_of_year", date(2026, 12, 31)),
    ("today+7d", date(2026, 10, 16)),
    ("today+1w", date(2026, 10, 16)),
    ("today+1m", date(2026, 11, 9)),
    ("today-1d", date(2026, 10, 8)),
    ("next monday+2d", date(2026, 10, 14)),
    ("today+1m-3d", date(2026, 11, 6)),
    ("today + 7", date(2026, 10, 16)),
    ("Today", date(2026, 10, 9)),
    ("2026-12-25", date(2026, 12, 25)),
    ("hoy", date(2026, 10, 9)),
    ("mañana", date(2026, 10, 10)),
])
def test_relative_forms(expr, expected):
    assert parse_relative_date(expr, today=TODAY) == expected


def test_next_weekday_when_today_is_that_weekday():
    monday = date(2026, 10, 12)
    assert parse_relative_date("next monday", today=monday) == date(2026, 10, 19)


def test_month_end_clamp():
    assert parse_relative_date("today+1m", today=date(2026, 10, 31)) == date(2026, 11, 30)


def test_leap_february_end_of_month():
    assert parse_relative_date("end_of_month", today=date(2028, 2, 10)) == date(2028, 2, 29)


def test_default_uses_current_date(monkeypatch):
    monkeypatch.setattr(dates, "current_date", lambda: date(2030, 1, 1))
    assert parse_relative_date("today") == date(2030, 1, 1)
    assert parse_relative_date("tomorrow") == date(2030, 1, 2)


def test_offset_order_under_month_end_clamp():
    end_of_october = date(2026, 10, 31)
    assert parse_relative_date("today+1m-3d", today=end_of_october) == date(2026, 11, 27)
    assert parse_relative_date("today-3d+1m", today=end_of_october) == date(2026, 11, 28)


def test_year_offset_unit():
    assert parse_relative_date("today+1y", today=TODAY) == date(2027, 10, 9)


def test_end_of_week_on_sunday():
    sunday = date(2026, 10, 11)
    assert parse_relative_date("end_of_week", today=sunday) == sunday


def test_this_weekday_from_sunday():
    sunday = date(2026, 10, 11)
    assert parse_relative_date("this monday", today=sunday) == date(2026, 10, 5)


@pytest.mark.parametrize("expr", [
    "next tuesday-ish",
    "",
    "   ",
    "someday",
    "next",
    "today+",
    "today+1x",
])
def test_invalid_input_raises_value_error(expr):
    with pytest.raises(ValueError):
        parse_relative_date(expr, today=TODAY)


def test_error_message_is_model_readable():
    with pytest.raises(ValueError) as excinfo:
        parse_relative_date("next tuesday-ish", today=TODAY)
    assert "next tuesday-ish" in str(excinfo.value)
    assert "YYYY-MM-DD" in str(excinfo.value)
