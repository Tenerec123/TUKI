# Tasks: Per-Conversation Cost Tracking

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~150 (120–170): ≈95 backend (incl. 37-line migration), ≈40 frontend |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single deliverable |
| Delivery strategy | single-pr (user-requested single commit at apply end) |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

> Delivery constraint: ship as ONE commit at apply end. Do NOT plan commits, branches, or PRs inside tasks; apply phase must not commit.

## Ordering Constraints

- Migration + model land together: `models.py` runs `create_all` at import — DDL must mirror the model (`Numeric(12,6)`, `server_default '0'`).
- `cost_tracker.py` must exist before `agent.py`/`chat.py` import it; `accumulate()` goes BEFORE the `if not chunk.choices: continue` guard so the final usage-only chunk (no choices) still registers.
- Consume + persist + push the `cost` event BEFORE `stream_manager.finish()` (chat.py:163) — finish ends the stream.
- Badge insertion shifts `children[1]`; any children-index lookup must switch to `.querySelector('.conv-options')`.
- AMENDED 2026-09-19 (frontend placement): sidebar badges are REMOVED; a single cost display (`#conv-cost-display`) sits LEFT of the model selector in the form area. Removing the badge does NOT shift `children[0]` (title button) nor the `<li>` index — no child-index lookups change. Keep `.querySelector('.conv-options')` for rename blur detection. Backend unchanged.
- No runtime ID guessing: badge lookup uses `li[data-conv-id]` set from the GET response.

## Phase 1: Foundation (Model + Migration + Tracker)

- [x] 1.1 `backend/models.py`: add `from decimal import Decimal`; add `Numeric` to the sqlalchemy import (line 2); add `total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0", default=Decimal("0"))` to `Conversation` (line ~95).
- [x] 1.2 Create `backend/alembic/versions/d1c2b3a405f6_add_conversation_total_cost.py` (hex verified unique; `down_revision='c7d8e9f0a1b2'`): upgrade `op.add_column('conversations', sa.Column('total_cost', sa.Numeric(12,6), nullable=False, server_default='0'))`; downgrade `op.drop_column`. Verify `alembic heads` = d1c2b3a405f6 and `upgrade head` applies.
- [x] 1.3 Create `backend/ai/cost_tracker.py`: `_TurnCost` holder (`total: Decimal`), `_cost: ContextVar[_TurnCost]`, `reset()`, `set(cost)`, `accumulate(cost)` (mutates shared holder — visible across `asyncio.TaskGroup` children), `consume()` (read + reset).

## Phase 2: Core Implementation (Capture + Persist)

- [x] 2.1 `backend/ai/agent.py` `_agentic_round`: inside `if chunk.usage:` (line 63, before the line 65 `continue` guard) add `cost_tracker.accumulate(getattr(chunk.usage, "cost", None) or getattr(chunk.usage, "total_cost", None))`; import cost_tracker.
- [x] 2.2 `backend/ai/chat.py` `chat_persistence_wrapper`: call `cost_tracker.reset()` before the `openai_agent` loop; add `_db_queries3(conv_id, total)` helper (direct `UPDATE conversations SET total_cost = total_cost + :t`, never PATCH); in `finally` after `_db_queries2` (line 161): `total = cost_tracker.consume()` → `await asyncio.to_thread(_db_queries3, ...)` → `stream_manager.push(conv_id, {"type":"cost","content":float(total)})` — all BEFORE `finish()` (line 163).
- [x] 2.3 `backend/schemas.py`: add `total_cost: float` to `ConversationSchema` (152–157) and `ConversationData` (164–167); leave `ConversationUpdate` (159–162) untouched — PATCH must not write cost.

## Phase 3: Integration (Frontend)

> AMENDED 2026-09-19 (user decision): placement changed — NO per-conversation sidebar badge. A single muted cost display (`#conv-cost-display`, label `Cost: $...`) sits LEFT of the model selector in the form area and shows the ACTIVE conversation's `total_cost`. `formatCost`, `state.convId`, `li[data-conv-id]`, and the NDJSON `{"type":"cost"}` event are retained; only the placement and update target changed. Backend untouched.

- [x] 3.1 `frontend/chat_script.js` `getConversations()` (line 484): keep `li.dataset.convId = conversation.id`; NO badge span in the `<li>` (costSpan removed from render + append).
- [x] 3.2 `frontend/chat_script.js`: keep `formatCost(v)` (`null/NaN` guard, `v < 0.01 ? toFixed(4) : toFixed(2)`); add `updateCostDisplay(v)` that sets `#conv-cost-display` textContent to `'Cost: $' + formatCost(v)`.
- [x] 3.3 `frontend/chat_script.js`: carry `convId` into stream `state` (streamConversation line 407, loadConversation); `handleStreamLine` (line 342) `type == "cost"` branch → `if (state.convId == idOfSelectedConv) updateCostDisplay(chunkObj.content)` (stale-stream guard; updates in place, list never rebuilt, selection preserved).
- [x] 3.4 `frontend/chat_script.js`: no `.children[1]`/badge-index assumptions remain after removal (`children[0]` title and `children[conv_position]` li lookups unaffected). `loadConversation` calls `updateCostDisplay(data.total_cost)` after fetch; `deleteConversation` of the active conv resets display to 0.
- [x] 3.5 `frontend/chat.html` + `frontend/style.css`: static `<span id="conv-cost-display" class="conv-cost-display">` inserted in `.form-controls` immediately LEFT of `.model-selector-details`; `.conv-cost` styles + both sidebar-collapse hide rules REMOVED; `.conv-cost-display` monospace/muted (0.85rem, `--off-yellow`); mobile (<=768px) tightens to 0.75rem + 30vw max-width with ellipsis so mic/send never get squeezed. Never hidden by sidebar collapse (lives in the form area).

## Phase 4: Manual Verification (no test runner)

- [x] 4.1 Migration: `alembic upgrade head`; fresh AND pre-migration conversations read `total_cost: 0` (spec: Fresh, Legacy). — PARTIAL: offline dry-run verified (`ADD COLUMN total_cost NUMERIC(12, 6) DEFAULT '0' NOT NULL`, head = d1c2b3a405f6, chain renders). LIVE pending: actual `upgrade head` on the DB + row reads.
- [x] 4.2 API: `GET /api/conversations/` and `GET /{id}` return `total_cost` as a number; `PATCH` body with `total_cost` leaves stored value unchanged (spec: Detail, List, Client cannot write). — PARTIAL: schema-level verified (float via model_validate; `total_cost` absent from `ConversationUpdate` → PATCH ignores it). LIVE pending: HTTP round-trip.
- [ ] 4.3 Normal loop: conversation 0.50 + rounds 0.04/0.02 + WebSearch searcher 0.01 → DB `total_cost` = 0.57, searcher cost included (spec: Normal, Nested searcher). — requires running stack + real LLM rounds; TaskGroup child visibility unit-tested locally instead.
- [ ] 4.4 Partial: ERROR_TOKEN turn and exception turn each persist completed-round cost exactly once, no double write (spec: Early break, Exception). — requires running stack; code path traced (single `consume()` in `finally`, before `finish()`).
- [x] 4.5 Free model: `:free`/BYOK round without `usage.cost` contributes 0; loop proceeds, no crash (spec: Free model). — PARTIAL: unit-verified (`accumulate(None)`/`accumulate(0)` → 0, no crash). LIVE pending: real `:free` round.
- [ ] 4.6 Cost display: header readout next to the model selector updates in place after each turn to the persisted value (via `{"type":"cost"}` event), list not rebuilt, selection preserved; zero-cost turn still emits `cost` event (spec: In-place update after stream, new placement). — requires browser + running stack; JS syntax verified locally.

## Review Fix Pass (2026-09-19) — fresh-context reviewers found CRITICAL + warnings

Fresh-context review of the uncommitted code surfaced one CRITICAL (shared-default mutation) and two warnings. All fixed in this pass; task checkboxes stay `[x]` (already implemented, now hardened):

- **1.3 (armed-window contract)** — CRITICAL FIX: `_TurnCost` gained `armed: bool`; `reset()` installs a zeroed ARMED holder; `accumulate()` is a NO-OP unless the current context's holder is armed; `consume()` reads the total, installs a zeroed DISARMED holder, and returns it. The import-time `default=_TurnCost()` is now DISARMED, so non-participating callers that never `reset()` (voice-agent pipeline via `openai_agent`, any future pipeline) call `accumulate()` freely and are silently ignored — the shared default can never accumulate leaked spend, and a wrapper `finally` that runs after a DB failure (which skipped `reset()`) reads 0 instead of another pipeline's garbage. Mutable-holder + TaskGroup-child visibility semantics UNCHANGED. `set()` DELETED (dead code — grep confirms zero `cost_tracker.set(` callers; it shadowed the builtin and its holder-replacement semantics would break child visibility). `accumulate(cost: Decimal | float | None)` type hint added.
- **2.2 (DELETE mid-turn)** — WARNING FIX: `_db_queries3` switched `.scalar_one()` → `.scalar_one_or_none()` and returns `Decimal | None`; when the conversation row is gone (deleted mid-stream), the UPDATE matches 0 rows → `None` → the `cost` event is skipped and `finish()` still runs. No more `NoResultFound` inside the wrapper `finally`, no "exception never retrieved" noise.
- **3.1 (dead code)** — WARNING FIX: `li.dataset.convId = conversation.id;` REMOVED (grep confirms zero reads anywhere — supersedes the earlier "keep" note); no `li[data-conv-id]` CSS/JS consumers exist.
- **3.2 (zero format)** — WARNING FIX: `formatCost(0)` now returns `"0.00"` (matches chat.html `Cost: $0.00` init); the sub-cent 4-decimal branch is kept for nonzero tiny values (`0.0001`).

Verification: py_compile on cost_tracker/chat/voice_agent/agent OK; `node --check` OK; smoke `SMOKE_OK` (unarmed accumulate no-op incl. TaskGroup context, armed mutate visible across TaskGroup children, consume reads-and-resets-and-disarms, post-disarm accumulate ignored, chained windows no double count); formatCost(0)=`0.00`, (0.0001)=`0.0001`, (0.57)=`0.57`.