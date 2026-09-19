# Proposal: Per-Conversation Cost Tracking

## Intent

The agentic loop (up to 10 orchestrator rounds + nested WebSearch calls) spends OpenRouter credits with zero accounting — users cannot see what any conversation costs. Persist the TOTAL cost per conversation and surface it in the sidebar.

## Scope

### In Scope
- `total_cost NUMERIC(12,6) NOT NULL server_default '0'` on `conversations` + Alembic migration (head `c7d8e9f0a1b2`)
- Sum of loop-internal LLM inferences (orchestrator rounds + searcher round), accumulated per turn
- New `backend/ai/cost_tracker.py` (ContextVar accumulator); persist in `chat_persistence_wrapper` `finally` (chat.py:138-163)
- `{"type":"cost"}` NDJSON event pushed after the DB write, before `finish()`
- `total_cost: float` on `ConversationSchema` / `ConversationData`
- Sidebar badge; in-place update via `handleStreamLine`

### Out of Scope
- Per-message / per-inference breakdown — total only
- Title-generation calls (fire-and-forget, may outlive persist moment); STT (browser-side pre-loop, text-only, no conversation binding); TTS (voice agent only, no conversation row) — all three need generation-id auditing if wanted later
- Client-writable cost (no `ConversationUpdate`/PATCH exposure)

## Capabilities

### New Capabilities
- `conversation-costs`: per-conversation total OpenRouter spend — accumulation, persistence, API exposure, sidebar display

### Modified Capabilities
- None (`openspec/specs/` empty; no existing spec requirements change)

## Approach

1. **Capture** — `_agentic_round` adds `getattr(usage, "cost", None) or getattr(usage, "total_cost", None)` from the final stream chunk to the ContextVar accumulator (threads through nested WebSearch; zero signature changes).
2. **Persist** — `finally` (runs on success, ERROR_TOKEN-break, exceptions): read+reset accumulator, direct column UPDATE (never PATCH), push `cost` event, `finish()`.
3. **Serve** — ORM column auto-serializes via `from_attributes`; schema type `float`.
4. **Frontend** — badge span per sidebar `<li>`; new `cost` branch in `handleStreamLine` updates in place (no list rebuild, no selection loss).

**Alternatives considered (rejected):** per-message costing — richer, but schema/UI explosion; per-request DB writes — simpler, but N writes/turn and loses partial turns; event-yielded cost — explicit, but nested searcher consumes the generator and drops its cost.

## Affected Areas

| Area | Impact |
|------|--------|
| `backend/models.py` | Modified |
| `backend/ai/cost_tracker.py` | New |
| `backend/ai/agent.py` | Modified |
| `backend/ai/chat.py` | Modified |
| `backend/alembic/versions/*_add_conversation_total_cost.py` | New |
| `backend/schemas.py` | Modified |
| `frontend/chat_script.js` | Modified |
| `frontend/style.css` | Modified |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Mid-loop error / ERROR_TOKEN: failed round's usage unrecorded | Med | `finally` persists accumulated rounds; partial total documented, acceptable |
| Provider omits cost (`:free`, BYOK) | Med | `getattr` fallback → 0; no crash; sum stays flat |
| Legacy conversations | High | `server_default "0"` backfills semantics; history not recoverable |
| Double-count / concurrent writes | Low | One active stream per conversation; read+reset in `finally` |
| OpenRouter field drift (`cost` vs `total_cost`) | Low | `or` fallback covers both; SDK `extra=allow` verified |

## Rollback Plan

`alembic downgrade -1` drops the column; revert schema/frontend commits in the same revert. Column is additive — no data-loss risk.

## Dependencies

- OpenRouter final-chunk `usage.cost` (optional; absent → 0 per call)

## Success Criteria

- [ ] Migration applies; legacy conversations read `0`
- [ ] Completed conversation `total_cost` equals the sum of its inference chunks
- [ ] Sidebar badge shows the persisted value after each turn
- [ ] ERROR_TOKEN / exception turns persist partial cost