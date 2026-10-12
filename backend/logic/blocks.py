import os
import re
from datetime import datetime, time, timedelta, timezone
from typing import List, Match, Tuple
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rrulestr
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Block, BlockException
from ..schemas import BlockCreate, BlockUpdate, BlockExceptionCreate, BlockOccurrenceRead
from .routines import current_date

# Default expansion window when the caller does not provide explicit bounds.
DEFAULT_WINDOW_DAYS = 365
# Hard caps so a single request can neither hang nor exhaust memory. Measured
# with dateutil: FREQ=SECONDLY over 1 day = ~86k occurrences / 0.25s, and the
# 365-day default window would materialize ~31.5M. A plain FREQ=WEEKLY rule over
# the default 365-day window yields 53 occurrences, well inside the cap.
MAX_WINDOW_DAYS = 366
MAX_OCCURRENCES = 2000

# The project's "local" zone is the TIMEZONE env var defaulting to Europe/Madrid
# (same source of truth as logic/routines.py, ai/tools/read.py, ai/tools/dates.py).
_DEFAULT_TIMEZONE = "Europe/Madrid"

# Google Calendar (and other RFC 5545 producers) emit UNTIL in UTC, e.g.
# "UNTIL=20260131T235959Z". dateutil refuses an aware UNTIL against the naive
# DTSTART used everywhere in TUKI, so the trailing Z is converted to a naive
# timestamp in the project's timezone before parsing.
_UNTIL_UTC_RE = re.compile(r"UNTIL=(\d{8}T\d{6})Z")


def _local_timezone() -> ZoneInfo:
    """Project timezone from the ``TIMEZONE`` env var (mirrors ``logic/routines.py``)."""
    return ZoneInfo(os.environ.get("TIMEZONE") or "Europe/Madrid")


def _normalize_until(rrule_text: str) -> str:
    """Rewrite any UTC ``UNTIL=...Z`` into a naive project-local timestamp."""

    def _to_local(match: Match) -> str:
        utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        try:
            local = utc.astimezone(_local_timezone())
        except Exception as error:  # no tzdata installed, unknown TZ name, ...
            print(f"[blocks] Falling back to UTC for UNTIL normalization, TIMEZONE unusable: {error}")
            local = utc
        return "UNTIL=" + local.replace(tzinfo=None).strftime("%Y%m%dT%H%M%S")

    return _UNTIL_UTC_RE.sub(_to_local, rrule_text)


def _parse_rrule(rrule_text: str, dtstart: datetime) -> rrule:
    """Build a dateutil rule from an RRULE string anchored at ``dtstart``.

    ``ValueError`` is raised when the string cannot be parsed, so callers can
    turn a bad rule into an HTTP 400 instead of a 500.
    """
    try:
        return rrulestr(_normalize_until(rrule_text), dtstart=dtstart)
    except Exception as error:  # rrule is a nullable=False VARCHAR with no validation
        raise ValueError(f"Invalid rrule {rrule_text!r}: {error}")


def _build_rule(block: Block) -> rrule:
    """Build the rule for a persisted block; raises ``ValueError`` when invalid."""
    return _parse_rrule(block.rrule, block.start_time)


def _collect_starts(rule: rrule, window_start: datetime, window_end: datetime) -> List[datetime]:
    """Collect rule starts inside the window, aborting past ``MAX_OCCURRENCES``.

    Iterates the rule lazily from its dtstart, so the result is never
    materialized unbounded; ``ValueError`` is raised once more than
    ``MAX_OCCURRENCES`` starts have been examined (dateutil cannot seek, so a
    high-frequency rule anchored far in the past would otherwise walk millions
    of steps before reaching the window).
    """
    starts: List[datetime] = []
    examined = 0
    for occurrence in rule:
        if occurrence > window_end:
            break
        examined += 1
        if examined > MAX_OCCURRENCES:
            raise ValueError(
                f"Too many occurrences: more than {MAX_OCCURRENCES} fall inside the window"
            )
        if occurrence >= window_start:
            starts.append(occurrence)
    return starts


def _rule_contains(rule: rrule, target: datetime) -> bool:
    """True when ``target`` is one of the rule's occurrences.

    dateutil cannot seek, so this walks from dtstart; the ``MAX_OCCURRENCES``
    scan limit keeps a high-frequency rule with a distant target fail-fast
    (measured: a plain ``rule.between(target, target)`` takes 1.6 s at 7 days
    and 22.9 s at 31 days out on FREQ=SECONDLY) instead of walking millions of
    occurrences.
    """
    for examined, occurrence in enumerate(rule):
        if occurrence == target:
            return True
        if occurrence > target:
            return False
        if examined >= MAX_OCCURRENCES:
            raise ValueError(
                f"Cannot verify {target.isoformat()}: it lies beyond the "
                f"{MAX_OCCURRENCES}-occurrence scan limit"
            )
    return False


def _resolve_window(
    window_start: datetime,
    window_end: datetime,
) -> Tuple[datetime, datetime]:
    """Fill missing window bounds and reject spans wider than ``MAX_WINDOW_DAYS``."""
    if window_start is None:
        window_start = datetime.combine(current_date(), time.min)
    if window_end is None:
        window_end = window_start + timedelta(days=DEFAULT_WINDOW_DAYS)
    if window_end - window_start > timedelta(days=MAX_WINDOW_DAYS):
        raise ValueError(
            f"Occurrence window too wide: at most {MAX_WINDOW_DAYS} days are allowed"
        )
    return window_start, window_end


def expand_block_occurrences(
    block: Block,
    exceptions: List[BlockException],
    window_start: datetime,
    window_end: datetime,
) -> List[Tuple[datetime, datetime]]:
    """Expand a block's RRULE into concrete ``(start, end)`` occurrences.

    Rule-generated starts inside ``[window_start, window_end]`` keep the block's
    duration (``end_time - start_time``). Exceptions are then applied: an
    ``is_add`` exception inserts ``(start_time, end_time)``, and a removal
    (``is_add`` False) drops the generated occurrence with a matching start
    (ignored when absent). Results are sorted by start. ``ValueError`` is raised
    when the bounded scan of ``_collect_starts`` is exceeded.
    """
    duration: timedelta = block.end_time - block.start_time
    rule = _build_rule(block)
    generated_starts = _collect_starts(rule, window_start, window_end)

    # Keyed by occurrence start so an exception can add or remove deterministically.
    occurrences = {start: (start, start + duration) for start in generated_starts}

    for exception in exceptions:
        if exception.is_add:
            occurrences[exception.start_time] = (exception.start_time, exception.end_time)
        else:
            occurrences.pop(exception.start_time, None)

    return sorted(occurrences.values(), key=lambda occurrence: occurrence[0])


def validate_exception(block: Block, data: BlockExceptionCreate) -> None:
    """Validate an exception payload against a block's rule.

    An add (``is_add`` True) must carry an ``end_time`` after its ``start_time``.
    A removal (``is_add`` False) must have a NULL ``end_time`` and target an
    existing rule-generated start. Raises ``ValueError`` on violation.
    """
    if data.is_add:
        if data.end_time is None:
            raise ValueError("end_time is required when is_add is True")
        if data.end_time <= data.start_time:
            raise ValueError("end_time must be after start_time")
        return

    if data.end_time is not None:
        raise ValueError("end_time must be NULL when is_add is False")
    rule = _build_rule(block)
    if not _rule_contains(rule, data.start_time):
        raise ValueError("is_add=False requires start_time to match a rule-generated occurrence")


def get_block_logic(id: int, db: Session):
    db_block = db.query(Block).where(Block.id == id).first()
    if db_block is None: raise HTTPException(status_code=404, detail="Block not found")
    return db_block


def get_all_blocks_logic(first_n: int, db: Session):
    if first_n is not None and first_n < 0:
        raise HTTPException(status_code=400, detail="first_n must be >= 0")
    if first_n is None:
        return db.query(Block).order_by(Block.start_time, Block.id).all()
    return db.query(Block).order_by(Block.start_time, Block.id).limit(first_n).all()


def create_block_logic(block: BlockCreate, db: Session):
    try:
        _parse_rrule(block.rrule, block.start_time)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    db_block = Block(**block.model_dump())
    db.add(db_block)
    db.commit()
    db.refresh(db_block)
    return db_block


def update_block_logic(id: int, updated_block: BlockUpdate, db: Session):
    db_block = db.query(Block).where(Block.id == id).first()
    if db_block is None: raise HTTPException(status_code=404, detail="Block not found")

    # Validate the merged row, not the sparse payload: a partial PATCH that
    # only sends end_time can still invert the interval against the stored start.
    merged_start = updated_block.start_time if updated_block.start_time is not None else db_block.start_time
    merged_end = updated_block.end_time if updated_block.end_time is not None else db_block.end_time
    merged_rrule = updated_block.rrule if updated_block.rrule is not None else db_block.rrule
    if merged_end < merged_start:
        raise HTTPException(status_code=400, detail="end_time must not precede start_time")
    try:
        _parse_rrule(merged_rrule, merged_start)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if updated_block.name is not None: db_block.name = updated_block.name
    if updated_block.description is not None: db_block.description = updated_block.description
    if updated_block.start_time is not None: db_block.start_time = updated_block.start_time
    if updated_block.end_time is not None: db_block.end_time = updated_block.end_time
    if updated_block.rrule is not None: db_block.rrule = updated_block.rrule
    db.commit()
    return db_block


def delete_block_logic(id: int, db: Session):
    db_block = db.query(Block).where(Block.id == id).first()
    if db_block is None: raise HTTPException(status_code=404, detail="Block not found")
    db.delete(db_block)
    db.commit()
    return db_block


def get_block_exceptions_logic(id: int, db: Session) -> List[BlockException]:
    get_block_logic(id=id, db=db)
    return (
        db.query(BlockException)
        .where(BlockException.block_id == id)
        .order_by(BlockException.start_time, BlockException.id)
        .all()
    )


def create_block_exception_logic(id: int, data: BlockExceptionCreate, db: Session) -> BlockException:
    db_block = get_block_logic(id=id, db=db)
    try:
        validate_exception(db_block, data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    duplicate = (
        db.query(BlockException)
        .where(BlockException.block_id == id, BlockException.start_time == data.start_time)
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=400, detail="An exception already exists for that start_time")
    db_exception = BlockException(block_id=id, **data.model_dump())
    db.add(db_exception)
    db.commit()
    db.refresh(db_exception)
    return db_exception


def delete_block_exception_logic(id: int, exception_id: int, db: Session) -> BlockException:
    get_block_logic(id=id, db=db)
    db_exception = (
        db.query(BlockException)
        .where(BlockException.id == exception_id, BlockException.block_id == id)
        .first()
    )
    if db_exception is None: raise HTTPException(status_code=404, detail="Block exception not found")
    db.delete(db_exception)
    db.commit()
    return db_exception


def get_block_occurrences_logic(
    id: int,
    window_start: datetime,
    window_end: datetime,
    db: Session,
) -> List[BlockOccurrenceRead]:
    db_block = get_block_logic(id=id, db=db)
    exceptions = get_block_exceptions_logic(id=id, db=db)
    try:
        window_start, window_end = _resolve_window(window_start, window_end)
        expanded = expand_block_occurrences(db_block, exceptions, window_start, window_end)
    except ValueError as error:
        # Unparseable stored rules, oversized windows and pathological rules are
        # client-visible data errors, never a 500 (see logic/routines.py).
        raise HTTPException(status_code=400, detail=str(error))
    return [BlockOccurrenceRead(start_time=start, end_time=end) for start, end in expanded]
