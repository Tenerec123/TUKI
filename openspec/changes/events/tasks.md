# Tasks: Event Entity for Scheduled Events

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~290 (270–320), all additions |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR, 4 work-unit commits |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units (one commit per unit)

| Unit | Goal | Files | Notes |
|------|------|-------|-------|
| 1 | Migration + model + listener | alembic/versions/c7d8e9f0a1b2, models.py | create_all parity |
| 2 | Events API slice | schemas.py, logic/events.py, routers/events.py, main.py | curl-verifiable |
| 3 | AI tools | read.py, exec.py | auto-discovered |
| 4 | Kale rendering | kale_script.js, style.css | visual check |

## Ordering Constraints

- Migration + model land together: models.py runs `create_all` at import — DDL must mirror the model exactly (embedding nullable, sa.DateTime() = no timezone).
- Verify `alembic heads` = `b2c3d4e5f6a7` before writing migration; `down_revision` must match; downgrade drops only `events`.
- routers/events.py must exist before main.py import; `include_router(events.router)` after import.
- Tool catch behavior (HTTPException/ValidationError → error string) is NEW — existing Task tools raise 404; do not clone blindly.

## Phase 1: Foundation (Migration + Model)

- [x] 1.1 Create `backend/alembic/versions/c7d8e9f0a1b2_add_events.py`: table `events` (id PK, name String(512) NOT NULL, description String(512), start_time sa.DateTime() NOT NULL, end_time sa.DateTime() NOT NULL, embedding Vector(384)); down_revision='b2c3d4e5f6a7'; downgrade drops only events.
- [x] 1.2 Add `Event` to `backend/models.py` (id, name, description, start_time, end_time, embedding VECTOR(384); NOT TimestampMixin — no priority).
- [x] 1.3 Add `@event.listens_for(Event, 'before_insert'/'before_update')` to `handle_project_embeddings`; history check already skips time-only patches. Done: upgrade head; insert embeds name+description.

## Phase 2: API Slice (Schemas, Logic, Router)

- [x] 2.1 `backend/schemas.py`: EventCreate (name/description max 512, start_time, end_time), EventSchema (+id, from_attributes), EventUpdate (all optional); field_validator rejects tzinfo → 422; model_validator end<start → 422; zero-duration ok.
- [x] 2.2 Create `backend/logic/events.py`: get (404 "Event not found"), get_all ordered (start_time, id) + first_n, create, update (partial), delete, search via cosine_distance + limit.
- [x] 2.3 Create `backend/routers/events.py`: prefix `/api/events`, tags ["events"], GET /{id}, GET /, POST /, PATCH /{id}, DELETE /{id} → EventSchema.
- [x] 2.4 `backend/main.py`: import events router; `api.include_router(events.router)`. Done: CRUD round-trip; ?first_n=3 earliest-first; /999 → 404.

## Phase 3: AI Tools

- [x] 3.1 `backend/ai/tools/read.py`: GetAllEvents(first_n=None), SearchEvents(text, limit=5) — docstrings document ISO-8601 naive datetimes.
- [x] 3.2 `backend/ai/tools/exec.py`: CreateEvent, UpdateEvent(event_id, partial), DeleteEvent — `datetime.fromisoformat`; catch HTTPException/ValidationError → error string; unknown id → error string, never raise.

## Phase 4: Frontend (Kale)

- [x] 4.1 `frontend/kale_script.js` LoadCalendar: Promise.all(fetch /api/events/, /api/tasks/); events → {start, end, allDay:false, className:'cal-event'}; start===end → end += 30min; tasks stay allDay.
- [x] 4.2 `frontend/style.css`: `.cal-event` bg var(--green) + 1px border var(--green) `!important`; `.cal-event .fc-event-title/.fc-event-main` color var(--neon-yellow) `!important` (D4 — no green-on-green).

## Phase 5: Manual Verification (no test runner)

- [x] 5.1 curl: aware/end<start → 422; zero-duration persists; CRUD round-trip; 404s; list order + first_n; SearchEvents("meeting with client") ranks "Client demo" first.
- [x] 5.2 `alembic upgrade head` then `downgrade c7d8e9f0a1b2` (drops only events).
- [ ] 5.3 Kale visual: timed green block 10:00–11:00 in timeGridWeek; chip on start day in dayGridMonth; zero-duration visible; task allDay + event timed coexist; text readable.