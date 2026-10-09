"""Deterministic parser for relative date expressions in tool arguments.

Language (case-insensitive; internal whitespace collapsed; spaces around
``+``/``-`` stripped, so ``"today + 7"`` and ``"today+7d"`` are equivalent)::

    expr    := anchor offset*
    anchor  := "today" | "now" | "tomorrow" | "yesterday"
             | ("next"|"last"|"this") " " weekday
             | "end_of_week" | "end_of_month" | "end_of_year"
    offset  := ("+"|"-") INT unit?
    unit    := "d" | "w" | "m" | "y"
    weekday := monday..sunday, also 3-letter mon..sun

Spanish aliases: hoy -> today, mañana -> tomorrow, ayer -> yesterday.
ISO passthrough: a bare ``YYYY-MM-DD`` is parsed as-is.

Semantics:
- today / now -> today; tomorrow -> +1 day; yesterday -> -1 day.
- next <wd> -> the weekday STRICTLY after today (today IS <wd> -> +7 days).
- this <wd> -> that weekday of the current ISO week (Monday start); may be past.
- last <wd> -> the weekday STRICTLY before today (today IS <wd> -> -7 days).
- end_of_week -> upcoming Sunday (today when today is Sunday).
- end_of_month -> last calendar day of the current month.
- end_of_year -> Dec 31 of the current year.
- Offsets apply in written order; a bare INT means days. Unit ``m`` uses
  relativedelta(months=n) with month-end clamping (Oct 31 + 1m = Nov 30);
  ``y`` uses relativedelta(years=n).

When ``today`` is omitted the default is the USER'S timezone date
(``current_date()``, i.e. the ``TIMEZONE`` env var, default Europe/Madrid),
never the container clock.

Anything unparseable raises ``ValueError`` with a model-readable message.
"""

import calendar
import re
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from ...logic.routines import current_date

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OFFSET_RE = re.compile(r"([+-])(\d+)([dwmy]?)")

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_ANCHOR_ALIASES = {
    "hoy": "today",
    "mañana": "tomorrow",
    "ayer": "yesterday",
}

_WEEKDAY_MODIFIERS = ("next", "last", "this")


def parse_relative_date(value: str, today: date | None = None) -> date:
    """Resolve a relative date expression to a concrete date.

    ``today`` defaults to the user's timezone date (``current_date()``);
    injecting it makes callers (and tests) deterministic.
    """
    if today is None:
        today = current_date()

    normalized = _normalize(value)
    if not normalized:
        raise _invalid(value)

    if _ISO_RE.match(normalized):
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            raise _invalid(value)

    anchor_text, offsets_text = _split_offsets(normalized)
    base = _resolve_anchor(anchor_text, today, value)
    return _apply_offsets(base, offsets_text, value)


def _normalize(value: str) -> str:
    """Lowercase, collapse whitespace, and glue sign offsets to their number."""
    text = re.sub(r"\s+", " ", str(value).strip().lower())
    return re.sub(r"\s*([+-])\s*", r"\1", text)


def _split_offsets(text: str) -> tuple[str, str]:
    """Split ``text`` at the first sign into (anchor, offset sequence)."""
    match = re.search(r"[+-]", text)
    if match is None:
        return text, ""
    return text[: match.start()], text[match.start():]


def _resolve_anchor(anchor_text: str, today: date, original: str) -> date:
    """Resolve the anchor clause to a base date."""
    anchor = _ANCHOR_ALIASES.get(anchor_text, anchor_text)

    if anchor in ("today", "now"):
        return today
    if anchor == "tomorrow":
        return today + timedelta(days=1)
    if anchor == "yesterday":
        return today - timedelta(days=1)
    if anchor == "end_of_week":
        return today + timedelta(days=(6 - today.weekday()) % 7)
    if anchor == "end_of_month":
        last_day = calendar.monthrange(today.year, today.month)[1]
        return date(today.year, today.month, last_day)
    if anchor == "end_of_year":
        return date(today.year, 12, 31)

    parts = anchor.split(" ")
    if (
        len(parts) == 2
        and parts[0] in _WEEKDAY_MODIFIERS
        and parts[1] in _WEEKDAYS
    ):
        return _weekday_anchor(parts[0], _WEEKDAYS[parts[1]], today)

    raise _invalid(original)


def _weekday_anchor(modifier: str, target: int, today: date) -> date:
    """Resolve ``next/this/last <weekday>`` against ``today``."""
    if modifier == "this":
        monday = today - timedelta(days=today.weekday())
        return monday + timedelta(days=target)
    if modifier == "next":
        ahead = (target - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)
    # modifier == "last"
    back = (today.weekday() - target) % 7
    return today - timedelta(days=back or 7)


def _apply_offsets(base: date, offsets_text: str, original: str) -> date:
    """Apply ``+/-`` offset clauses left to right; bare INT means days."""
    if not offsets_text:
        return base

    result = base
    position = 0
    for match in _OFFSET_RE.finditer(offsets_text):
        if match.start() != position:
            raise _invalid(original)
        position = match.end()

        sign = 1 if match.group(1) == "+" else -1
        amount = int(match.group(2)) * sign
        unit = match.group(3) or "d"

        if unit == "d":
            result += timedelta(days=amount)
        elif unit == "w":
            result += timedelta(weeks=amount)
        elif unit == "m":
            result += relativedelta(months=amount)
        else:  # unit == "y"
            result += relativedelta(years=amount)

    if position != len(offsets_text):
        raise _invalid(original)
    return result


def _invalid(value: str) -> ValueError:
    """Build the model-readable error for an unparseable expression."""
    return ValueError(
        f"invalid date expression '{value}'; use YYYY-MM-DD or relative forms "
        f"like today, tomorrow, next monday, today+7d"
    )
