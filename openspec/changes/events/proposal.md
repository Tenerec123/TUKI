# Proposal: Event entity for scheduled events

## Intent

TUKI lacks a time-bound entity: meetings and timed commitments get squeezed into date-only tasks, and the AI cannot schedule or recall them. This change adds an `Event` (name, optional description, start/end datetimes, mandatory embedding) with full CRUD API, AI tools, and green Kale rendering.

## Scope

### In Scope
- Event model + shared embedding listener + Alembic migration `c7d8e9f0a1b2_add_events.py`
- CRUD API: `backend/routers/events.py` + `backend/logic/events.py`, wired in `main.py`
- AI tools: Create/Update/DeleteEvent (exec.py), GetAll/SearchEvents (read.py) — auto-discovered
- Kale: fetch events in LoadCalendar; render timed green blocks (`.cal-event`) with readable text

### Out of Scope
- Recurrence (routines + RRULE cover it); `all_day` flag; project/priority/finished
- Editor UI beyond calendar rendering; tests (no test runner)

## Capabilities

### New Capabilities
- `events`: Event entity — embedding, semantic search, CRUD API, AI tools, green calendar rendering

### Modified Capabilities
None — `openspec/specs/` is empty; no existing capability changes.

## Approach

Clone the Task vertical slice: `Event` model + extend shared `handle_project_embeddings` listener; schemas mirror TaskCreate/TaskSchema/TaskUpdate (embedding never serialized); logic get/get_all/create/update/delete/cosine search; router GET/GET-all/POST/PATCH/DELETE; tools follow Task patterns with ISO-8601 datetimes; Kale uses timed FullCalendar events via `classNames: ['cal-event']`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/models.py` | Modified | Event model; listener +Event |
| `backend/schemas.py` | Modified | EventCreate / EventSchema / EventUpdate |
| `backend/logic/events.py` | New | CRUD + search logic |
| `backend/routers/events.py` | New | CRUD endpoints |
| `backend/main.py` | Modified | Import + `include_router(events)` |
| `backend/ai/tools/read.py` | Modified | GetAllEvents, SearchEvents |
| `backend/ai/tools/exec.py` | Modified | Create/Update/DeleteEvent |
| `backend/alembic/versions/c7d8e9f0a1b2_add_events.py` | New | Table matches model; `down_revision='b2c3d4e5f6a7'` |
| `frontend/kale_script.js` | Modified | LoadCalendar fetches events |
| `frontend/style.css` | Modified | `.cal-event` green + readable text |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Green-on-green text (repeats `.cal-task-done` bug) | Med | Explicit readable text color; visual check |
| Migration vs `create_all` drift | Low | Migration mirrors model |
| Embedding cost per write | Low | Shared listener pattern |

## Rollback Plan

Applied: `alembic downgrade c7d8e9f0a1b2` drops the table; revert commits. Unapplied: delete migration before `alembic upgrade`. No existing data touched.

## Dependencies

- SentenceTransformer model (already loaded via `get_embedding_model`)
- pgvector support (already in DB)

## Success Criteria

- [ ] CRUD round-trip via curl: create / read / update / delete
- [ ] SearchEvents returns semantically relevant events
- [ ] `alembic upgrade head` creates events table; downgrade removes it
- [ ] Kale shows green timed blocks, not all-day
- [ ] AI can create and list events through tools