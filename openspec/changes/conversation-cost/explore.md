# Exploration: conversation-cost

> Scope: store the TOTAL cost spent per conversation. Inference costs are summed at the end of each agentic loop. DB migration + frontend surface.
> Explicit user constraint: ONLY a per-conversation total is persisted. No per-message/per-inference breakdown in the DB. Per-call cost is needed only transiently to compute the total at loop end.

## 1. Current State

### 1.1 Conversations DB model
- `backend/models.py:95-101` — `Conversation`: `id`, `title` (String(100)), `messages` relationship (order_by `Message.position`, cascade delete), `creation_date` (date), `last_used` (datetime). **No cost or token columns exist.**
- `backend/models.py:103-110` — `Message`: `id`, `conversation_id` (FK, CASCADE), `position`, `type` (`prompt|agent|tool`), `text`.
- `backend/models.py:180` — `Base.metadata.create_all(engine)` runs at import; it creates missing TABLES only, never alters existing ones, so the new column MUST come from an Alembic migration.
- Schemas: `backend/schemas.py:146-157` (`ConversationSchema`, includes `messages`) and `backend/schemas.py:164-167` (`ConversationData`, title/id/last_used — feeds the sidebar). Both use `from_attributes=True`, so a new ORM column auto-serializes without router changes.
- Router: `backend/routers/conversations.py` — `GET /{id}` → ConversationSchema, `GET /` → List[ConversationData], `POST /` (create), `PATCH /{id}` (via `edit_conversation_logic`, also imported by chat.py), `DELETE`.

### 1.2 Agentic loop and inference points
- Entry: `POST /api/ai/execute` (`backend/routers/ai.py:23-29`) → `chat_persistence_wrapper` (`backend/ai/chat.py:114-164`) → `openai_agent` (`backend/ai/agent.py:127-143`), a `for` loop over `MAX_AGENTIC_ROUNDS = 10` (`backend/ai/config.py:4`). One round = one streaming `chat.completions.create` via `_agentic_round` (`agent.py:36-125`). Loop breaks on the `finish` token (finish_reason `stop` or no tool calls); exceptions yield the literal string `ERROR_TOKEN` (`agent.py:141-143`).
- **Inference points inside the loop:**
  1. Orchestrator call — `_agentic_round`, once per round (up to 10).
  2. Nested searcher call — `WebSearch` tool (`backend/ai/tools/subagents.py:49-53`) runs a SECOND `openai_agent` with the configured `searcher` model (`max_rounds=1`, no tools). Invoked via `execute_tool_call` (`backend/ai/tools/discovery.py:198-221`) inside `_agentic_round`. → exactly one extra LLM call per `WebSearch` execution.
- **Inference points OUTSIDE the loop (excluded by scope):**
  - Title generation `_generate_title` (`backend/ai/chat.py:47-86`): up to 3 attempts (mistral-nemo, gemma `:free`, nemotron `:free`), fire-and-forget task on the first message of a conversation. Separate task, may outlive the loop; excluded unless the user opts in.
  - STT (`backend/routers/ai.py:44-54` + `backend/ai/stt/openrouter.py`): called by the browser before the prompt (`chat_script.js:58-64`); the endpoint returns only the transcript text, no usage. Not bound to a conversation row.
  - TTS (`backend/ai/tts.py`): `audio.speech.create` (kokoro-82m) returns raw bytes, no usage; used ONLY by the voice agent (`backend/ai/voice_agent.py:108-192`), which runs `openai_agent` with NO `conv_id` and persists nothing.
- **No existing cost tracking.** The only usage handling is `_log_cache_usage` (`backend/ai/agent.py:11-29`), which logs `chunk.usage` cache metrics and already reads OpenRouter extra fields via `getattr`.
- **Usage availability (verified against OpenRouter docs + installed SDK):** OpenRouter ALWAYS includes a `usage` object on the final stream chunk (no `stream_options` needed; the `include_usage` params are deprecated/no-op). Current field name is `usage.cost` (credits, e.g. `0.00014`); legacy name `usage.total_cost`. Installed `openai==2.32.0` (requirements allow `>=1.10.0,<3.0.0`): `CompletionUsage.model_config = {extra: allow}` (verified empirically), so both `cost` and `total_cost` survive parsing as attributes/`model_extra`. Robust read: `getattr(usage, "cost", None) or getattr(usage, "total_cost", None)`.
- `fake_ai` (`backend/ai/fake_ai.py`) yields no usage → contributes 0. It is imported in chat.py but not wired into the live flow.

### 1.3 Alembic migrations
- Location: `backend/alembic/` (`env.py`, `alembic.ini`, `versions/`). `env.py` inserts the backend dir on `sys.path`, imports `database.Base` + `import models`, reads `DATABASE_URL` from env, `render_as_batch=True`.
- **Linear chain, HEAD = `c7d8e9f0a1b2` (add_events):**
  `c48094bf8a49` (init_clean_structure, root) → `da54dc301614` (init_db) → `61d4fdf49e71` (add_routine_icon) → `a1b2c3d4e5f6` (add_kb_notes) → `b2c3d4e5f6a7` (message_type) → `c7d8e9f0a1b2` (add_events) = **HEAD**
- Convention: hand-written files named `<hexrev>_<snake_slug>.py` (e.g. `61d4fdf49e71_add_routine_icon.py`), minimal `upgrade()`/`downgrade()` with `op.add_column` / `op.drop_column`.

### 1.4 Frontend conversation display
- `frontend/chat.html:31-39` — sidebar `<nav id="sidebar">` with `<ul id="conversation-list">`; each item = `.conv-select` title button + `.conv-options` menu button.
- `frontend/chat_script.js:484-511` `getConversations()` — fetches `GET /api/conversations/` and rebuilds the list. **Natural spot for a per-conversation cost badge:** inside each `<li>`, next to the title.
- `frontend/chat_script.js:342-380` `handleStreamLine` — parses NDJSON stream events (`agent`, `tool_call`, `tool_result`; `finish` is currently ignored). A new `cost` event can update the badge in place.
- `frontend/chat_script.js:432` `loadConversation` and :398 `streamConversation` — selection highlight is position-based; a wholesale `getConversations()` re-render after each turn drops selection state, so an in-place badge update is preferred.

### 1.5 API surface to extend
- Schemas (model-backed, `from_attributes`): `ConversationData` (sidebar list, `GET /api/conversations/`) and `ConversationSchema` (detail, `GET /api/conversations/{id}`). Add `total_cost` to both → no router edits.
- Write side: DO NOT expose `total_cost` through `ConversationUpdate`/`PATCH /{id}` (would let clients forge cost). Persist via a dedicated local write at loop end.

## 2. Affected Files
- `backend/models.py` — add `total_cost` column to `Conversation`.
- `backend/ai/agent.py` — capture per-call cost from the final usage chunk in `_agentic_round`.
- `backend/ai/cost_tracker.py` (NEW) — accumulation helper (ContextVar-based; threads automatically through nested `WebSearch` calls).
- `backend/ai/chat.py` — in the `finally` block: read+reset accumulator, persist total_cost, push `{"type":"cost"}` event via stream_manager before `finish()`.
- `backend/alembic/versions/<rev>_add_conversation_total_cost.py` (NEW) — migration, `down_revision="c7d8e9f0a1b2"`.
- `backend/schemas.py` — `ConversationSchema` + `ConversationData`: add `total_cost: float`.
- `frontend/chat_script.js` — sidebar cost badge; handle `cost` stream event (in-place update).
- `frontend/style.css` — badge styling (small, muted).

## 3. Approaches

### Cost capture
1. **ContextVar accumulator (recommended)** — `backend/ai/cost_tracker.py` with a `ContextVar[float]`; `_agentic_round` adds `usage.cost` of the final chunk; `chat_persistence_wrapper` finally reads + resets.
   - Pros: zero signature changes; nested `WebSearch` calls accumulate automatically (same asyncio task → same context); no key cleanup.
   - Cons: implicit state (must reset in `finally`).
   - Effort: Low
2. **Per-round `cost` event yielded through the stream** — `_agentic_round`/`openai_agent` yield `{"type":"cost"}`; wrapper sums.
   - Pros: explicit, testable.
   - Cons: `WebSearch` CONSUMES the nested generator and does not re-yield, so searcher cost is lost unless subagents.py also threads/resumes the event (ripple); event ordering pollutes the message flow.
   - Effort: Medium
3. **Accumulator object threaded via parameters** — pass through `openai_agent` → `_agentic_round` → `execute_tool_call` → `WebSearch` → nested `openai_agent`.
   - Pros: fully explicit dependency.
   - Cons: touches 4+ signatures cascading through the tool dispatch layer.
   - Effort: Medium

### Frontend display
1. **Sidebar badge + `cost` stream event (recommended)** — small span in each `<li>`; `handleStreamLine` adds a `cost` branch that updates the badge in place when the event arrives (pushed by the backend after the DB write, before `finish()`).
   - Pros: minimal code; no list re-render; no selection-state loss; value shown == value persisted.
   - Cons: one new stream event type.
   - Effort: Low
2. **Sidebar badge + refresh list after stream ends** — call `getConversations()` on stream completion.
   - Pros: no new event type.
   - Cons: full list rebuild per turn (flicker, selection highlight lost — current code never refreshes after turns).
   - Effort: Low
3. **Header/active-conversation display only** — show cost in the chat header.
   - Pros: tiny change.
   - Cons: cost is per-conversation; the sidebar list is where it belongs; header is shared across conversations.
   - Effort: Low

## 4. Recommendation

**DB (total only):**
```python
# backend/models.py — Conversation
from sqlalchemy import Numeric
from decimal import Decimal
total_cost: Mapped[Decimal] = mapped_column(
    Numeric(12, 6), nullable=False, default=0.0, server_default="0"
)
```
- `NUMERIC(12,6)` (exact decimal, no float drift when summing many turns; OpenRouter costs are sub-cent values like `0.00014` credits). Schemas type the field as `float` (pydantic coerces Decimal → float for JSON).
- `NOT NULL` + `server_default "0"`: legacy conversations read `0` with no special-casing; new conversations start at `0`.
- Migration `backend/alembic/versions/<rev>_add_conversation_total_cost.py`, `down_revision="c7d8e9f0a1b2"`, following the existing hand-written style (`op.add_column('conversations', ...)` / `op.drop_column`).

**Hook point:** capture cost inside `_agentic_round` (per LLM call) into the ContextVar accumulator, and persist in `chat_persistence_wrapper`'s `finally` (chat.py:138-163) — it runs on success, on `ERROR_TOKEN` early-break, and on exceptions, and already owns message persistence + `stream_manager.finish()`. Add one small `_db_queries3`-style DB write (direct column update, NOT via public PATCH schema) and `stream_manager.push(conv_id, {"type": "cost", "content": total})` right after persisting, before `finish()`.

**Cost source:** read `getattr(usage, "cost", None) or getattr(usage, "total_cost", None)` from the final stream chunk (same place `_log_cache_usage` already reads usage).

**Frontend:** badge in the sidebar `<li>` (chat_script.js getConversations), updated in place by a new `cost` event branch in `handleStreamLine`; small muted style in style.css.

## 5. Risks / Edge Cases
- **Mid-loop errors:** the `finally` persists whatever was accumulated — rounds already completed count; the round that raised before its usage chunk under-counts. Documented, acceptable.
- **Provider returns no cost:** `:free` models → 0 credits is correct; BYOK/failed provider may omit usage entirely → `getattr(...) or None` → treat as 0. No crash path; the sum just stays flat for that call.
- **Field-name drift:** current OpenRouter field is `cost`; legacy is `total_cost`. The `or` fallback covers both; verified `extra="allow"` on the installed SDK so neither is dropped.
- **Double-count:** usage arrives exactly once per call (final chunk). A single per-turn accumulator (read+reset in `finally`) prevents cross-turn double counting. `stream_manager.start` (routers/ai.py:25) allows only ONE active stream per conversation → no two loops write the same row concurrently; the title task never touches `total_cost`.
- **Legacy conversations:** `server_default "0"` backfills semantics (cost shows 0 = "no cost recorded yet"); exact historical cost is NOT recoverable retroactively.
- **Excluded cost sources (scope decisions):** title-generation (3 cheap attempts, fire-and-forget, can outlive the persist moment); chat STT (browser-side pre-loop call, response returns only text); TTS (voice agent only, no conversation row). If the user later wants them: OpenRouter's `/api/v1/generation?id=...` endpoint can audit them, but the response bodies currently return no usage — would need generation-id capture.
- **Decimal→JSON serialization:** schema field must be `float` (or `Decimal` with explicit serialization) so the frontend receives a number, not a string.
- **Mix of models over time:** the total is a plain running sum across any orchestrator/searcher models configured over the conversation's life; that is expected and inherent to "total cost".

## 6. Ready for Proposal
**Yes.** The orchestrator should tell the user:
- Scope decision baked in: total = LLM inferences INSIDE the agentic loop only (orchestrator rounds + nested `WebSearch` searcher calls). Title-gen, STT, and TTS are NOT included and would need a follow-up change if desired.
- Cost unit: OpenRouter credits (`usage.cost`), which are already dollar-priced by OpenRouter per model.
- DB: single `total_cost NUMERIC(12,6) NOT NULL DEFAULT 0` column + Alembic migration; schemas expose it as float; sidebar badge updated via a new `cost` stream event; no per-inference persistence.