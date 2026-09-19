# Verification Report

**Change**: conversation-cost — per-conversation OpenRouter cost tracking
**Version**: N/A (openspec change; spec `openspec/specs/conversation-costs/spec.md` v1)
**Mode**: Standard (strict_tdd: false — no test runner; verification = static review + local smoke + checklist mapping)
**Date**: 2026-09-19

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 17 |
| Tasks complete | 14 |
| Tasks incomplete | 3 (4.3, 4.4, 4.6 — live-stack manual verification) |
| Core tasks (Phases 1–3) | 11/11 complete |

All implementation tasks (1.1–3.5) are checked and verified. Phase 4 (manual, no runner) has 4.1/4.2/4.5 checked with documented PARTIAL scope; 4.3/4.4/4.6 remain unchecked because they require the running stack (real PostgreSQL + real LLM rounds + browser). Per project config these are WARNING-level, not CRITICAL: each is structurally covered by code plus unit smoke evidence and is deployment-verifiable only.

## Build & Tests Execution

**Build**: N/A — no build step (FastAPI + vanilla JS).

```text
python -m py_compile backend/ai/cost_tracker.py backend/models.py backend/ai/agent.py
  backend/ai/chat.py backend/schemas.py backend/alembic/versions/d1c2b3a405f6_add_conversation_total_cost.py
  -> PY_COMPILE_OK
node --check frontend/chat_script.js
  -> NODE_CHECK_OK
```

**Tests**: no runner (strict_tdd: false). Independent smoke suite executed this session (Python 3.13.3 runtime evidence):

```text
COST_TRACKER_OK      TaskGroup child accumulate visible across contexts (0.04+0.02 parent + 0.01 child = 0.07);
                     consume() is read+reset (2nd call returns 0) -> exactly-once per turn;
                     accumulate(None)/accumulate(0) -> 0, no crash; set() path works
SCHEMAS_OK           ConversationSchema/ConversationData validate Decimal("0.57") -> float 0.57;
                     total_cost NOT in ConversationUpdate.model_fields; extra PATCH field ignored
DB_QUERIES3_SQL_OK   UPDATE conversations SET total_cost=(conversations.total_cost + 0.07)
                     WHERE conversations.id = 1 RETURNING conversations.total_cost compiles (no DB)
ALL_SMOKE_OK
```

**Migration** (offline dry-run: `alembic upgrade head --sql`):

```text
alembic heads -> d1c2b3a405f6 (head)                      [single head]
chain: -> c48094bf8a49 -> da54dc301614 -> 61d4fdf49e71 -> a1b2c3d4e5f6
      -> b2c3d4e5f6a7 -> c7d8e9f0a1b2 -> d1c2b3a405f6
ALTER TABLE conversations ADD COLUMN total_cost NUMERIC(12, 6) DEFAULT '0' NOT NULL;
```

Matches `models.py` (`Numeric(12, 6)`, `server_default="0"`, `default=Decimal("0")`). `down_revision='c7d8e9f0a1b2'` verified in chain (old head).

**Coverage**: not available (no test runner; N/A).

## Spec Compliance Matrix

| Requirement | Scenario | Test / Evidence | Result |
|-------------|----------|-----------------|--------|
| Agentic Loop Cost Accumulation | Normal loop (0.50+0.04+0.02+0.01=0.57) | smoke: accumulator exact 0.07; `_db_queries3` adds turn total to stored; **real LLM rounds need live stack** | PARTIAL |
| Agentic Loop Cost Accumulation | Nested searcher cost included | smoke (runtime): TaskGroup child accumulate visible to parent; 0.01 included | COMPLIANT (unit) |
| Partial Turn Persistence | Early break on error token (0.50+0.04=0.54, once) | trace: ERROR_TOKEN → `finally` single `consume()` + persist before `finish()`; read+reset verified exactly-once; **live ERROR_TOKEN pending** | PARTIAL |
| Partial Turn Persistence | Exception during stream (0.50+0.04=0.54) | trace: exception unwinds into same `finally` (chat.py:160/192-194); persist before `finish()`; **live exception turn pending** | PARTIAL |
| Missing Provider Cost | Free model call (no cost → 0, no crash) | smoke (runtime): `accumulate(None)` / `accumulate(0)` → 0, loop-safe | COMPLIANT (unit) |
| New Conversation Initialization | Fresh conversation (total_cost 0) | `create_new_conversation` never sets it → Python default `Decimal("0")` + `server_default '0'`; **live GET pending** | PARTIAL |
| Legacy Backfill | Pre-migration row reads 0 | offline dry-run renders `DEFAULT '0' NOT NULL` (additive backfill); **live upgrade head on DB pending** | PARTIAL |
| Cost API Exposure | Detail response (0.57 as number) | smoke (runtime): `ConversationSchema.from_attributes` → float 0.57; **HTTP round-trip pending** | COMPLIANT (unit) |
| Cost API Exposure | Sidebar list response | smoke (runtime): `ConversationData` → float 0.57; **HTTP round-trip pending** | COMPLIANT (unit) |
| Cost API Exposure | Client cannot write cost | smoke (runtime): `total_cost` absent from `ConversationUpdate`; extra field ignored; `edit_conversation_logic` (routers/conversations.py:33-47) only writes messages/title/last_used | COMPLIANT (unit+static) |
| Sidebar Cost Display | Badge on list load (0.57) | `getConversations` inserts `.conv-cost` via `formatCost(conversation.total_cost)`; `node --check` passed; **browser pending** | PARTIAL |
| Sidebar Cost Display | In-place update after stream | `handleStreamLine` cost branch sets badge = persisted value in place, no list rebuild, selection preserved; `node --check` passed; **browser pending** | PARTIAL |

**Compliance summary**: 5/12 runtime-verified (unit-level); 7/12 partial — structurally covered, live-stack verification pending (deployment-verifiable only).

## Correctness (Static Evidence)

| Requirement | Status | Notes |
|-------------|--------|-------|
| Mutable-holder ContextVar (not bare Decimal) | Implemented | `cost_tracker.py:27` `ContextVar[_TurnCost]`; `accumulate()` mutates shared holder (lines 41-49); TaskGroup child visibility unit-verified |
| Capture in `_agentic_round` | Implemented | `agent.py:70-72` inside `if chunk.usage:`, BEFORE `if not chunk.choices: continue` (line 73); `getattr(cost) or getattr(total_cost)` fallback; covers rounds AND nested searcher |
| reset before loop | Implemented | `chat.py:149` `cost_tracker.reset()` before `openai_agent` |
| Persist ordering in `finally` | Implemented | `chat.py:189-191`: `consume()` → `to_thread(_db_queries3)` → push `cost` event, all BEFORE `finish()` (line 193); after `_db_queries2`; inner `finally` guarantees `finish()` even if persist fails |
| Direct UPDATE, never PATCH | Implemented | `_db_queries3` (`chat.py:117-133`): `UPDATE ... RETURNING` via `asyncio.to_thread` |
| `total_cost` float on read schemas, absent on update | Implemented | `schemas.py:157` (ConversationSchema), `:169` (ConversationData); `ConversationUpdate` (160-163) untouched |
| Frontend badge + in-place event | Implemented | `chat_script.js`: `li.dataset.convId` (505), badge between selectBtn/optionsBtn (514-527), `formatCost` with null/NaN guard (493-496), cost branch (380-388), `state.convId` (416); no `children[1]` assumptions remain (line 150 uses `.querySelector('.conv-options')`; line 138/791 use `children[0]` = select) |
| CSS badge | Implemented | `style.css:143-149` `.conv-cost` 0.7em muted; hidden on desktop collapsed (161-163), kept on mobile collapsed (1109-1111) |
| No leftover debug / contexts | Clean | changed files carry no leftover debug output; pre-existing `_log`/`console.warn` patterns untouched |
| Type hints | Mostly followed | all new functions annotated; exception: `accumulate(cost)` param (matches design literal signature) — SUGGESTION below |

## Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| Accumulator storage: mutable holder ContextVar | Yes | exactly the design's `_TurnCost` pattern |
| Capture point: single line in `_agentic_round` | Yes | before no-choices guard, covers both inference points |
| Persistence: `_db_queries3` direct UPDATE via `to_thread` | Yes | plus `RETURNING` for the event value (documented deviation, assessed below) |
| Event payload: `{"type":"cost","content":<float>}` | Yes | shape preserved; content semantics = new persisted total (deviation, assessed below) |
| Column: `NUMERIC(12,6)` NOT NULL `server_default '0'` | Yes | model + migration identical |
| Migration `down_revision='c7d8e9f0a1b2'` | Yes | single head verified by dry-run |
| Frontend lookup switch away from `children[1]` | Yes | all children-index lookups audited |

## Deviation Assessment

**Deviation**: tasks.md 2.2 and design.md:89 specify pushing `float(total)` where `total = cost_tracker.consume()` — the TURN INCREMENT. Implementation pushes `float(new_total)` — the NEW CUMULATIVE persisted value returned by `UPDATE ... RETURNING` (`_db_queries3`). Documented in apply-progress and the `_db_queries3` docstring.

**Verdict: ACCEPT.** Reasoning:
- Spec Requirement "Sidebar Cost Display": "the displayed value MUST equal the persisted one". Pushing the cumulative persisted value makes the badge literally equal to the persisted total — satisfying the requirement MORE directly than a turn increment would (an increment would require client-side addition, risking float drift and divergence from the DB value).
- Spec scenario "In-place update after stream": "badge updates to the new value" — the "new value" is the new persisted `total_cost`; satisfied.
- Spec scenario "Normal loop completion" (DB side) is unaffected: the UPDATE still adds the turn increment; only the event payload semantic changed (cumulative vs delta). The event-shape contract `{"type":"cost","content":<float>}` is preserved — the frontend consumes it unchanged.
- Zero-cost turns: with cumulative payload, a 0-cost turn pushes the unchanged persisted total (e.g. 0.57) and the badge stays correct; the design's own failure-handling note says zero-cost turns must "refresh to current persisted value" — the cumulative payload delivers exactly that without ambiguity.
- No downstream consumer reads the event as a delta. Single-stream-per-conversation guard (routers/ai.py stream_manager.start) removes concurrent-writer skew between UPDATE and event.
- Residual risk if semantics change later: consumers wanting per-turn cost would need to diff consecutive events — none exists today.

## Issues Found

**CRITICAL**: None.

**WARNING**:
1. Live-stack scenarios not executed — tasks 4.3, 4.4, 4.6 unchecked; live halves of 4.1, 4.2, 4.5 pending. Specifically: real `alembic upgrade head` on PostgreSQL, real LLM turns (normal / searcher / ERROR_TOKEN / exception / `:free`), HTTP GET/PATCH round-trips, browser badge in-place behavior. Each is structurally covered (code trace + unit smoke) but only a running stack can close them.
2. Documented deviation (assessed above — flagged for reviewer awareness, not a defect): cost event carries the new cumulative persisted total instead of the turn increment.

**SUGGESTION**:
1. `cost_tracker.accumulate(cost)` param lacks a type annotation (AGENTS.md "type hints on all functions"): `cost: float | Decimal | None`. Matches the design literal; one-line fix.
2. Design open question (sub-cent badge format, e.g. `$0.0001`) is resolved by `formatCost`'s 4-decimal branch — confirm in review.
3. If `_db_queries3` raises (e.g., conversation deleted mid-turn), the `cost` event is skipped but `finish()` still runs (`chat.py:192-194`) — correct defensive behavior, worth knowing.

## Verdict

**PASS WITH WARNINGS**

Implementation matches spec and design; all core tasks verified; 5/12 spec scenarios runtime-verified at unit level, remaining 7 structurally covered and pending live-stack verification (WARNING, not CRITICAL, per project config). Deviation ACCEPTED.