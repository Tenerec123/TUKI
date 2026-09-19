# Design: Event Entity for Scheduled Events

## Technical Approach

Clone the Task vertical slice: `Event` model joins the shared `handle_project_embeddings` listener; schemas mirror Task classes (embedding excluded by omission); `logic/events.py` + `routers/events.py` replicate tasks CRUD/search; AI tools follow exec/read patterns; Kale merges events into LoadCalendar as timed FullCalendar entries. All artifacts in English per project convention.

## Architecture Decisions

### D1 — SearchEvents with NULL-embedding rows

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Filter `embedding.isnot(None)` | Explicit, but silently hides dangling rows | — |
| Raise error | Breaks AI search UX; spec forbids raises | — |
| Rely on Postgres default NULLS LAST | Zero code; NULL `<=>` rows sort last, still returned if within limit | **Adopt** — matches existing `search_*_logic` (no NULL guard anywhere); listener guarantees embeddings on normal writes |

### D2 — Zero-duration rendering (start == end)

FullCalendar 6 treats `end` as exclusive; a timed event with explicit `end == start` renders invisibly in timeGrid. Spec requires visibility.

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Dot display | timeGrid has no dot mode; all-day dots break "time matters" | — |
| 30-min minimum visual block at LoadCalendar mapping | Pure frontend hack; stored data stays faithful (start==end is valid domain data) | **Adopt** — if `start === end`, set FullCalendar `end` to `start + 30min`. Matches FullCalendar's own `defaultTimedEventDuration` semantics |

### D3 — Month view multi-day behavior

Confirmed per spec: dayGridMonth renders timed events on their **start day** (native FullCalendar). Tradeoff accepted: an all-day band would require `allDay: true`, conflicting with time-bound semantics — events stay timed; full spans are visible in timeGridWeek/timeGridDay. No code needed.

### D4 — `.cal-event` CSS

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Reuse `.cal-task-done` colors | Repeats green-on-green bug | — |
| `var(--green)` bg + `var(--neon-yellow)` text | Matches `.cal-task-pending` text pattern; readable on dark green | **Adopt** — `!important` on all rules to beat FullCalendar's JS-injected inline styles; `border: 1px solid var(--green)`; global `.fc-event:hover` rules already cover hover/expand — no overrides |

### D5 — end < start validation location

| Option | Tradeoff | Decision |
|--------|----------|----------|
| SQLAlchemy `@validates` (Project precedent) | Raises ValueError → 500, not 422 | — |
| Logic-layer check | Manual `HTTPException(422)`; skips AI-tool path unless duplicated | — |
| Pydantic `model_validator(mode='after')` | Native 422 before any DB work; fires for HTTP **and** AI-tool schema construction | **Adopt** — only mechanism satisfying the 422 spec; tasks have no cross-field validation, so no clash. Tools catch `ValidationError` → error string |

### D6 — Naive datetime storage

`sa.DateTime()` (no timezone) in model and migration → `TIMESTAMP WITHOUT TIME ZONE`; no tz column. A `field_validator` on `start_time`/`end_time` rejects `tzinfo is not None` → 422 (Pydantic otherwise accepts aware datetimes). Tool docstring contract: ISO-8601 **naive local** (`2026-09-15T09:00:00`); `GetCurrentTime` supplies the tz context.

### D7 — Migration

`c7d8e9f0a1b2_add_events.py`, `down_revision='b2c3d4e5f6a7'`, style of `a1b2c3d4e5f6_add_kb_notes.py` (`from pgvector.sqlalchemy import Vector`). `Base.metadata.create_all(engine)` also creates the table at models import — DDL must mirror the model exactly (embedding nullable, matching the mixin column, so no drift).

## Data Flow

```
POST /api/events/ ──> Pydantic (naive check, end>=start) ──> logic.create_event_logic
        └──> Event row ──> before_insert listener ──> embedding = encode(name [+ description])

LoadCalendar ── Promise.all([GET /api/events/, GET /api/tasks/]) ──> merge
        └──> events: {start, end, allDay:false, className:'cal-event'}
              (zero-duration → end += 30min)
        └──> tasks:  allDay bands (unchanged)
        └──> FullCalendar
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/models.py` | Modify | `Event` model (id, name, description, start_time, end_time, embedding); add `@event.listens_for(Event, 'before_insert'/'before_update')` to handler stack (time-only patch already skips recompute via history check) |
| `backend/schemas.py` | Modify | `EventCreate`/`EventSchema`/`EventUpdate` — explicit fields, **not** `BaseItem` (no priority); validators per D5/D6 |
| `backend/logic/events.py` | Create | `get/get_all/create/update/delete/search_events_logic`; list ordered `(start_time, id)`; search via `cosine_distance` (+`limit`) |
| `backend/routers/events.py` | Create | `prefix="/api/events"`, `tags=["events"]`, 5 CRUD endpoints |
| `backend/main.py` | Modify | import + `api.include_router(events.router)` |
| `backend/ai/tools/read.py` | Modify | `GetAllEvents`, `SearchEvents(text, limit=5)` — ISO-8601 naive docstrings |
| `backend/ai/tools/exec.py` | Modify | `CreateEvent`, `UpdateEvent`, `DeleteEvent` — `datetime.fromisoformat`; catch `HTTPException`/`ValidationError` → error string (spec: never raise) |
| `backend/alembic/versions/c7d8e9f0a1b2_add_events.py` | Create | DDL per D7 |
| `frontend/kale_script.js` | Modify | LoadCalendar: `Promise.all` fetch; events map to timed `cal-event` entries; 30-min zero-duration min |
| `frontend/style.css` | Modify | `.cal-event` + `.cal-event .fc-event-title/.fc-event-main` rules per D4 |

## Interfaces / Contracts

```python
class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512))
    start_time: Mapped[datetime] = mapped_column(nullable=False)   # naive
    end_time: Mapped[datetime] = mapped_column(nullable=False)     # naive
    embedding = mapped_column(VECTOR(384))
```

```python
@field_validator("start_time", "end_time")
def _reject_aware(cls, v: datetime) -> datetime:
    if v.tzinfo is not None:
        raise ValueError("Datetimes must be naive local time (no offset)")
    return v

@model_validator(mode="after")
def _end_not_before_start(self):
    if self.end_time and self.start_time and self.end_time < self.start_time:
        raise ValueError("end_time must not precede start_time")
    return self
```

Tools: `GetAllEvents(first_n=None)`, `SearchEvents(text, limit=5)`, `CreateEvent(name, start_time, end_time, description=None)`, `UpdateEvent(event_id, name=None, description=None, start_time=None, end_time=None)`, `DeleteEvent(event_id)` — all datetimes ISO-8601 naive (`2026-09-15T09:00:00`), end ≥ start.

Migration upgrade: `op.create_table('events', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('name', sa.String(512), nullable=False), sa.Column('description', sa.String(512), nullable=True), sa.Column('start_time', sa.DateTime(), nullable=False), sa.Column('end_time', sa.DateTime(), nullable=False), sa.Column('embedding', Vector(384), nullable=True))`; downgrade: `op.drop_table('events')`.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (manual) | Validators: aware → 422; end<start → 422; zero-duration ok; name/description max 512 | curl against running API |
| Integration (manual) | CRUD round-trip, 404s, list ordering `(start_time, id)`, `first_n` | curl checklist per proposal success criteria |
| Semantic | SearchEvents relevance ("meeting with client" → "Client demo") | curl + AI-tool call |
| Frontend | Timed block in timeGridWeek, chip on start day in dayGridMonth, zero-duration visible, task/event coexistence, no green-on-green | Visual check in Kale |
| Migration | `alembic upgrade head` / `downgrade c7d8e9f0a1b2` (drops only events) | CLI |

## Migration / Rollout

New table only — no existing data touched. Rollback: `alembic downgrade c7d8e9f0a1b2` + revert commits. No feature flags.

## Open Questions

None.