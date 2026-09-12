# TUKI — AI Instructions

## Stack
- Backend: FastAPI + SQLAlchemy 2.0 + PostgreSQL/pgvector
- Frontend: Vanilla JS, HTML, CSS (no frameworks)
- AI: OpenRouter (orchestrator, searcher, STT, TTS; Gemini models as fallback via OpenRouter)
- STT: OpenRouter STT (nvidia/parakeet-tdt-0.6b-v3)

## Conventions
- PEP 8, 4 spaces, type hints on all functions
- Router/Logic separation (tasks.py → tasks_logic.py)
- All code, comments, and documentation in English
- Dates in ISO format (yyyy-mm-dd)
- RRULE syntax for routine frequencies

## AI Agent (T.U.K.I.)
- Tool calling via ProcessBatch for batch operations
- Never guess IDs — read first, mutate second
- OpenRouter primary, Gemini as model fallback stack
