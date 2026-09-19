# Conversation Costs Specification

## Purpose

Track the TOTAL OpenRouter spend of each conversation: the sum of LLM inference costs inside the agentic loop (orchestrator rounds plus the nested searcher call of every WebSearch execution), persisted per conversation and surfaced next to the model selector for the active conversation. Total-only scope; per-message/per-inference breakdowns, title-generation, STT, and TTS are excluded. (Placement amended 2026-09-19: the per-conversation sidebar badge was replaced by a single header display left of the model selector.)

## Requirements

### Requirement: Agentic Loop Cost Accumulation

The system MUST accumulate the cost of every inference inside the agentic loop (orchestrator rounds and nested searcher calls) and MUST add the turn's sum to the conversation's total when the loop ends. Costs are OpenRouter credits.

#### Scenario: Normal loop completion

- GIVEN a conversation with total_cost 0.50
- WHEN the loop completes normally with orchestrator rounds costing 0.04 and 0.02 and a searcher call costing 0.01
- THEN total_cost equals 0.57

#### Scenario: Nested searcher cost included

- GIVEN an orchestrator round that triggers WebSearch
- WHEN the nested searcher inference completes
- THEN its cost adds to the same turn total

### Requirement: Partial Turn Persistence

The system MUST persist the accumulated cost when the loop ends via ERROR_TOKEN or an unhandled exception: completed rounds count, the interrupted round's unrecorded usage is excluded. The turn total MUST be persisted exactly once (no double-counting across turns).

#### Scenario: Early break on error token

- GIVEN total_cost 0.50 and a turn ending with ERROR_TOKEN after one completed round (0.04)
- WHEN the wrapper's finally block persists
- THEN total_cost becomes 0.54 with no further cost writes

#### Scenario: Exception during stream

- GIVEN total_cost 0.50 and a turn with one completed round (0.04) followed by a raised exception
- WHEN the loop unwinds and the wrapper's finally block runs
- THEN total_cost becomes 0.54

### Requirement: Missing Provider Cost

The system MUST treat an inference whose final usage omits cost (free models, BYOK, absent usage) as contributing 0, and MUST NOT crash the loop over it.

#### Scenario: Free model call

- GIVEN a :free model whose final chunk has no cost
- WHEN the round completes and the loop continues
- THEN the round contributes 0 to the total and the loop proceeds normally

### Requirement: New Conversation Initialization

A newly created conversation MUST start with total_cost 0.

#### Scenario: Fresh conversation

- GIVEN a conversation created via the API
- WHEN it is first listed or loaded
- THEN total_cost is 0

### Requirement: Legacy Conversations Backfill

Conversations existing before the migration MUST read total_cost 0 (server_default backfill). Historical spend MUST NOT be reconstructed retroactively.

#### Scenario: Pre-migration row

- GIVEN a conversation row created before the migration
- WHEN the migration applies and the row is read
- THEN total_cost is 0

### Requirement: Cost API Exposure

ConversationSchema and ConversationData MUST expose total_cost as a float. ConversationUpdate MUST NOT expose it; PATCH MUST NOT alter the persisted value.

#### Scenario: Detail response

- GIVEN a conversation with total_cost 0.57
- WHEN GET /api/conversations/{id}
- THEN the body includes total_cost as the number 0.57

#### Scenario: Sidebar list response

- GIVEN the same conversation
- WHEN GET /api/conversations/
- THEN its entry includes total_cost as a number

#### Scenario: Client cannot write cost

- GIVEN a PATCH /api/conversations/{id} body containing total_cost
- WHEN the update is applied
- THEN the stored total_cost is unchanged

### Requirement: Active Conversation Cost Display

A single cost display next to the model selector MUST show the ACTIVE conversation's total_cost and update it in place on a cost event, without rebuilding the list; the displayed value MUST equal the persisted one. (Amended 2026-09-19: replaces the original per-conversation sidebar badge — placement changed to a single display left of the model selector; backend API and event unchanged.)

#### Scenario: Cost shown on conversation load

- GIVEN a conversation with total_cost 0.57
- WHEN it is loaded or selected
- THEN the header shows a cost display with 0.57 next to the model selector

#### Scenario: In-place update after stream

- GIVEN an active stream on a conversation
- WHEN the backend persists the total and pushes the cost event
- THEN the header cost display updates to the new value, the list is not rebuilt, and selection is preserved