# Design: Per-Conversation Cost Tracking

## Technical Approach

Persist the TOTAL OpenRouter spend per conversation: a `ContextVar` accumulator in a new `backend/ai/cost_tracker.py` sums inference costs as they arrive; `chat_persistence_wrapper`'s `finally` (chat.py:138-163) persists the turn's total with a direct column update and pushes an NDJSON `{"type":"cost"}` event that the frontend uses to update the sidebar badge in place. Satisfies requirements 1-7 of `openspec/specs/conversation-costs/spec.md`.

## Architecture Decisions

| Decision | Options | Tradeoff | Choice |
|---|---|---|---|
| **Accumulator storage** | (a) bare `ContextVar[Decimal]`; (b) `ContextVar` holding a **mutable holder object**; (c) threaded param | (a) LOST COST: `_agentic_round` runs `execute_tool_call` in `asyncio.TaskGroup` children (agent.py:109-111); child tasks copy context, so a bare Decimal set inside the searcher never reaches the parent — violates spec "Nested searcher cost included" (c) cascades through 4+ signatures | **(b)** `accumulate()` mutates the shared object in place; all context copies see it. `set/reset/consume` only run in the wrapper's own context, so reference replacement there is safe |
| **Capture point** | capture in `_agentic_round` vs `openai_agent` vs per event | `_agentic_round` (agent.py:61-64) already reads the final usage chunk (`_log_cache_usage`) | **Single line in `_agentic_round`** next to `_log_cache_usage` covers BOTH inference points: orchestrator rounds and the nested searcher (subagents.py:49 → `openai_agent` → `_agentic_round`) |
| **Persistence** | direct column UPDATE vs PATCH schema | PATCH would let clients forge cost; per-request writes = N writes/turn | **`_db_queries3`**: `UPDATE conversations SET total_cost = total_cost + :t` inside the wrapper `finally`, invoked via `asyncio.to_thread` like `_db_queries2` |
| **Event payload** | `{"type":"cost","content":<float>}` | float is what schemas expose; consumed as number by JS | **float(total)** |
| **Column type** | `FLOAT` vs `NUMERIC(12,6)` | float drift when summing many turns; `Numeric` → `Decimal` | **`NUMERIC(12,6)`** + `NOT NULL server_default '0'` (legacy backfill + fresh = 0) |

## Frontend Placement Amendment (2026-09-19)

**Decision (user):** the per-conversation sidebar badge is REMOVED. A single muted cost display (`#conv-cost-display`, label `Cost: $...`) sits LEFT of the model selector inside `.form-controls` and shows the ACTIVE conversation's `total_cost`. It supersedes the sidebar-badge placement in the Data Flow, File Changes, Interfaces, Failure Handling, and Success Criteria sections below.

- **Load / switch:** `loadConversation` renders `updateCostDisplay(data.total_cost)` from the `GET /{id}` response (`ConversationSchema.total_cost`).
- **Stream:** the existing `{"type":"cost"}` branch now calls `updateCostDisplay(chunkObj.content)` guarded by `state.convId == idOfSelectedConv` (stale streams cannot clobber the header).
- **Delete active conv:** display resets to 0.
- `formatCost`, `state.convId`, and the NDJSON event are unchanged; backend is untouched (`li[data-conv-id]` write since REMOVED — see Review Hardening Amendment 2026-09-19).
- Display never hides on sidebar collapse (it lives in the form area); mobile CSS tightens it (0.75rem, 30vw max-width ellipsis) so mic/send are never squeezed.

## Data Flow

```
Browser ─POST /api/ai/execute─▶ routers/ai.py: stream_manager.start() || create_task(wrapper)

wrapper (chat.py):
  try:
    cost_tracker.reset()                     # turn total = 0
    async for token in openai_agent(...):    # rounds 1..10
      _agentic_round ──┐
        stream chunks  │ final chunk usage → cost_tracker.accumulate(cost or total_cost)
        tools via TaskGroup child ──▶ WebSearch ─▶ nested openai_agent ─▶ _agentic_round
                                  (accumulate mutates SHARED holder — parent sees it)
  finally:
    total = cost_tracker.consume()           # read + reset (once per turn)
    await to_thread(_db_queries3, conv_id, total)   # UPDATE total_cost = total_cost + :t
    stream_manager.push(conv_id, {"type":"cost","content":float(total)})   # NDJSON
    stream_manager.finish(conv_id)

Browser ─GET /api/ai/connect─▶ stream() yields cost line ─▶ handleStreamLine "cost" branch
                                                       ─▶ #conv-cost-display text updated in place (header, left of model selector; amended 2026-09-19)
```

## File Changes

| File | Action | Description |
|---|---|---|
| `backend/ai/cost_tracker.py` | Create | ContextVar accumulator (mutable holder) |
| `backend/ai/agent.py` | Modify | `accumulate()` in `_agentic_round` usage branch (line ~64) |
| `backend/ai/chat.py` | Modify | `reset()` before loop; in `finally`: `consume()` + `_db_queries3` + push event before `finish()` |
| `backend/models.py` | Modify | `total_cost` column on `Conversation` (line ~101) |
| `backend/alembic/versions/d1c2b3a405f6_add_conversation_total_cost.py` | Create | Migration, `down_revision='c7d8e9f0a1b2'` |
| `backend/schemas.py` | Modify | `total_cost: float` on `ConversationSchema` + `ConversationData`; `ConversationUpdate` untouched |
| `frontend/chat.html` | Modify | `<span id="conv-cost-display">` static element inside `.form-controls`, immediately LEFT of `.model-selector-details` (amended 2026-09-19) |
| `frontend/chat_script.js` | Modify | `li[data-conv-id]` kept; sidebar badge span REMOVED from `getConversations`; `updateCostDisplay(v)` helper; `cost` branch in `handleStreamLine` targets `#conv-cost-display` (guarded by `state.convId == idOfSelectedConv`); `loadConversation` + `deleteConversation` call it (amended 2026-09-19) |
| `frontend/style.css` | Modify | `.conv-cost` + sidebar-collapse hide rules REMOVED; `.conv-cost-display` (monospace, muted, 0.85rem); mobile 0.75rem + 30vw ellipsis (amended 2026-09-19) |

## Review Hardening Amendment (2026-09-19)

Fresh-context review found the import-time `default=_TurnCost()` holder is SHARED by every context that never calls `reset()` — voice-agent `openai_agent` calls (voice_agent.py:135, no reset) mutated it, and a wrapper `finally` running after a DB failure that skipped `reset()` could consume that leaked spend into a real row. Hardening (supersedes the `set/reset/consume` lines in the Interfaces contract below):

- **ARMED-WINDOW CONTRACT**: `_TurnCost` gains `armed: bool`. `reset()` installs a zeroed ARMED holder; `accumulate()` is a NO-OP unless the current context's holder is armed; `consume()` reads the total, installs a zeroed DISARMED holder, and returns it. `accumulate()` only has effect inside a `reset()`…`consume()` window in the owning context; callers outside a window are ignored (voice pipeline, future pipelines). Mutable-holder + TaskGroup-child visibility semantics unchanged (children copy the armed holder reference at task creation).
- **`set()` DELETED** (dead code — zero callers; shadowed builtin; holder-replacement semantics would break child visibility). Not exposed for hidden tests (no test suite in repo).
- **Type hint**: `def accumulate(cost: Decimal | float | None) -> None`.
- **Persistence hardening** (chat.py `_db_queries3`): `.scalar_one()` → `.scalar_one_or_none()`, returns `Decimal | None`. Conversation deleted mid-stream → UPDATE matches 0 rows → `None` → cost event skipped, `finish()` still runs.
- **Frontend dead code**: `li.dataset.convId = conversation.id;` write REMOVED (no reads exist anywhere — supersedes "kept" in the Frontend Placement Amendment). `formatCost(0)` returns `"0.00"` (matches chat.html `Cost: $0.00` init); sub-cent 4-decimal branch retained for nonzero tiny values.

```python
# backend/ai/cost_tracker.py (post-hardening)
from contextvars import ContextVar
from decimal import Decimal

class _TurnCost:
    __slots__ = ("total", "armed")
    def __init__(self, armed: bool = False) -> None:
        self.total = Decimal("0"); self.armed = armed

_cost: ContextVar[_TurnCost] = ContextVar("conversation_cost", default=_TurnCost())  # shared default is DISARMED

def reset() -> None:                    # ARMS: zeroed armed holder (wrapper start)
    _cost.set(_TurnCost(armed=True))
def accumulate(cost: Decimal | float | None) -> None:  # NO-OP unless armed; MUTATES shared holder — safe across TaskGroup children
    if cost and _cost.get().armed:
        _cost.get().total += Decimal(str(cost))
def consume() -> Decimal:               # read total, DISARM (zeroed holder), return — exactly once per turn
    t = _cost.get().total; _cost.set(_TurnCost()); return t
```

## Interfaces / Contracts

```python
# backend/ai/cost_tracker.py
from contextvars import ContextVar
from decimal import Decimal

class _TurnCost:
    __slots__ = ("total",)
    def __init__(self) -> None: self.total = Decimal("0")

_cost: ContextVar[_TurnCost] = ContextVar("conversation_cost", default=_TurnCost())

def reset() -> None:                      # zero the turn accumulator (wrapper start)
    _cost.set(_TurnCost())
def set(cost: Decimal | float) -> None:   # replace total (tests / explicit override)
    _cost.set(_TurnCost()); _cost.get().total = Decimal(str(cost))
def accumulate(cost) -> None:             # MUTATES shared holder — safe across TaskGroup children
    if cost: _cost.get().total += Decimal(str(cost))
def consume() -> Decimal:                 # read + reset — called exactly once per turn
    t = _cost.get().total; reset(); return t
```

**Model** (`backend/models.py`): `total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0", default=Decimal("0"))` — needs `from sqlalchemy import Numeric` and `from decimal import Decimal`.

**Migration** (repo hand-written style): `op.add_column('conversations', sa.Column('total_cost', sa.Numeric(12, 6), nullable=False, server_default='0'))`; downgrade `op.drop_column(...)`.

**Capture** (`agent.py`, inside the existing `if chunk.usage:` block, before the `if not chunk.choices: continue` guard):
```python
cost_tracker.accumulate(getattr(chunk.usage, "cost", None) or getattr(chunk.usage, "total_cost", None))
```

**Persistence** (`chat.py` finally, after `_db_queries2`):
```python
total = cost_tracker.consume()
await asyncio.to_thread(_db_queries3, prompt.conversation_id, total)
stream_manager.push(prompt.conversation_id, {"type": "cost", "content": float(total)})
```

**Frontend** (amended 2026-09-19): static `<span id="conv-cost-display">` left of the model-selector `<details>` in `.form-controls`; `li.dataset.convId = conversation.id` (kept, badge removed); stream state gains `convId`; new branch: `state` carries `convId` → `updateCostDisplay(chunkObj.content)` guarded by `state.convId == idOfSelectedConv`; `updateCostDisplay(v)` = `#conv-cost-display` textContent `'Cost: $' + formatCost(v)`; `formatCost` = `null/NaN → 0`, `v < 0.01 ? v.toFixed(4) : v.toFixed(2)`.

## Failure Handling

- **Mid-loop error / ERROR_TOKEN**: `finally` persists whatever `consume()` reads — completed rounds count, the interrupted round's unrecorded usage is excluded (under-count documented, acceptable).
- **Provider omits cost**: `getattr(...) or ...` → `None` → `accumulate` skips → contributes 0; no crash path.
- **Double-count**: single stream per conversation (`stream_manager.start` guard routers/ai.py:25) + read/reset `consume()` once per turn; title task never touches `total_cost`.
- **Zero-cost turns**: event still pushed (`0`) → header display refreshes to current persisted value.

## Rollout / Rollback

Migration is additive. Rollback: `alembic downgrade -1` drops the column; revert schema/frontend commits in the same revert. Manual deploy per repo (docker compose on homelab), no data backfill beyond `server_default '0'`.

## Success Criteria

- [ ] `alembic upgrade head` applies; legacy conversations read `total_cost: 0`
- [ ] Turn with orchestrator rounds + WebSearch: DB total = sum of chunks (searcher cost included)
- [ ] `GET /api/conversations/` and `GET /{id}` return `total_cost` as a number
- [ ] `PATCH` with `total_cost` in body leaves stored value unchanged
- [ ] Header cost display (left of model selector) shows the active conversation's persisted value after each turn, in place, selection preserved (amended 2026-09-19)
- [ ] ERROR_TOKEN / exception turn persists partial cost; `:free` model round adds 0

## Open Questions

- None blocking. Minor UX: badge shows 4 decimals for sub-cent values (e.g. `$0.0001`) — confirm format in review.