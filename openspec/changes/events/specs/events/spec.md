# Events Specification

## Purpose

Time-bound commitments (meetings, appointments) distinct from date-only tasks: naive start/end datetimes, optional description, mandatory embedding; exposed via CRUD API, AI tools, and timed green Kale blocks.

## Requirements

### Requirement: Event Data Model

An event exists with `id`, `name` (512, required), `description` (512, nullable), `start_time`, `end_time` (naive datetimes), and `embedding` (VECTOR(384)). No project_id, priority, or finished fields.

#### Scenario: Embedding never serialized

- GIVEN any stored event
- WHEN returned by the API or AI tools
- THEN the payload excludes the embedding vector

### Requirement: Event Embedding Generation

`handle_project_embeddings` MUST fire for Event before_insert/update, embedding `name` plus `description` when present; MUST recompute when name or description change; MUST NOT recompute on time-only changes.

#### Scenario: Insert embeds

- GIVEN a new event "Client demo"
- WHEN the insert fires the listener
- THEN embedding equals encode("Client demo")

#### Scenario: Time-only patch

- GIVEN a stored event
- WHEN PATCH changes only start_time
- THEN the embedding is unchanged

### Requirement: Events CRUD API

The API MUST expose GET /{id}, GET /, POST /, PATCH /{id}, DELETE / under /api/events. Unknown ids MUST 404. List MUST order by start_time asc (id tie-break); `first_n` MUST limit. Responses MUST use EventSchema.

#### Scenario: List limited

- GIVEN 10 events, varied start_times
- WHEN GET /api/events/?first_n=3
- THEN 3 events, earliest start_time first

#### Scenario: Unknown id

- GIVEN no event with id 999
- WHEN GET /api/events/999
- THEN 404

### Requirement: Event Validation

Create/update MUST 422 when end_time precedes start_time. Zero-duration (start_time == end_time) MUST be accepted. Datetimes MUST be naive local; offset inputs MUST 422. name required (max 512); description optional (max 512).

#### Scenario: End before start

- GIVEN start 10:00, end 09:00
- WHEN POST /api/events/
- THEN 422, nothing persists

#### Scenario: Zero duration

- GIVEN start_time == end_time
- WHEN POST /api/events/
- THEN the event persists

#### Scenario: Offset rejected

- GIVEN start_time "+02:00" offset
- WHEN POST /api/events/
- THEN 422

### Requirement: AI Tools for Events

The agent MUST auto-discover GetAllEvents(first_n), SearchEvents(text, limit=5), CreateEvent, UpdateEvent(event_id, partial), DeleteEvent(event_id). Docstrings MUST document ISO-8601 naive datetimes and end after start. Unknown ids MUST return an error string, never raise.

#### Scenario: CreateEvent

- GIVEN CreateEvent with ISO-8601 datetimes
- WHEN the tool runs
- THEN event persists, confirmation with id returns

#### Scenario: DeleteEvent unknown id

- GIVEN an unknown event id
- WHEN DeleteEvent runs
- THEN an error string returns

### Requirement: Event Semantic Search

SearchEvents MUST embed the query and rank by ascending cosine distance, capped at `limit` (default 5).

#### Scenario: Relevance

- GIVEN events "Client demo", "Gym session"
- WHEN SearchEvents("meeting with client")
- THEN "Client demo" ranks first

### Requirement: Kale Event Rendering

LoadCalendar MUST fetch /api/events/ with /api/tasks/, rendering timed entries (start=start_time, end=end_time, allDay=false) with className `cal-event` — green with readable title, never the `.cal-task-done` green-on-green bug. timeGrid MUST span multi-day events; dayGridMonth MUST show them on their start day. Zero-duration events MUST stay visible; tasks MUST stay allDay bands.

#### Scenario: Timed block

- GIVEN event 10:00-11:00 Wednesday
- WHEN timeGridWeek renders
- THEN a green block spans 10:00-11:00

#### Scenario: Task coexistence

- GIVEN event and task on the same day
- WHEN LoadCalendar renders
- THEN event = timed block, task = allDay band

### Requirement: Event Migration

Migration `c7d8e9f0a1b2_add_events` MUST create a model-matching table with naive TIMESTAMP columns, down_revision `b2c3d4e5f6a7`. Upgrade from head MUST succeed; downgrade MUST drop only the events table.

#### Scenario: Upgrade

- GIVEN alembic at head b2c3d4e5f6a7
- WHEN alembic upgrade head
- THEN the events table exists

#### Scenario: Downgrade

- GIVEN migration applied
- WHEN alembic downgrade c7d8e9f0a1b2
- THEN only the events table drops