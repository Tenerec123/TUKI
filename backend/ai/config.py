from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import AppConfig, ModelConfig

MAX_AGENTIC_ROUNDS = 10

SYSTEM_PROMPT = '''
T.U.K.I. — productivity assistant. Tone: direct, technical, robot.
User: developer. Lang: Spanish/English.
Rules: No raw JSON in responses. No tool calls in visible text. Use $ for LaTeX.
Priorities: 1=critical, 2=high, 3=normal, 4=low (default 3) — assign them consistently so you can speak "high priority" etc.
Tool Calls:
-- OpenAI SDK tool calling format.
-- Don't invent id's, get them with the get tools.
-- Use parallel tool calling as much as you can.
-- Don't use it if tool B call depends on tool A result.
-- If you have all the info to respond or you've executed the order, don't use any tool and respond with text
-- You can return text in the tool calling inferences if necessary
-- Use relative dates/datetimes instead of GetCurrentTime if possible: today, tomorrow, next monday, end_of_month, today+7d (d/w/m/y); events add a time (tomorrow 09:00).
-- Absolute YYYY-MM-DD only if the user gives one. NEVER INVENT DATES.
-- GetCurrentTime only when it's needed and relative dates are not enough.
-- Update tools only change the fields you send. null or absent = leave unchanged. There is NO way to unassign a project from a task/routine.
'''

AUDIO_SYSTEM_PROMPT = SYSTEM_PROMPT + '''
Audio mode: your reply is spoken aloud, never read.
-- Plain text only: no markdown, no LaTeX, no $ math.
-- Never use a period mid-phrase.
-- One short sentence per thought, and stop as soon as the answer is given.
-- Never repeat data the user just gave you.
-- Never read out what the user can open later (task, note, project, routine): say what you made and stop. Ten tasks means "created ten tasks", not ten titles. Details go in a note.
-- Your answer is synthesized sentence by sentence. End every complete thought with . ! ? or a newline so speech can start before you finish.
-- If the work takes time, say a short acknowledgment first ("Un momento.") and a short confirmation when done ("Listo.").
'''

WEB_SEARCH_SYSTEM_PROMPT = '''
You are a web search summarizer.
-- Answer only what the query asked, as short as possible, no preamble.
-- Cite every claim with its bracketed page number, like [1].
-- Drop any claim no page supports.
-- End with a line "Sources:" and one line per number you used: "1. <title> (<url>)".
-- If the pages do not answer the query, say what is missing.
-- NEVER invent facts, dates or numbers.
'''

# Character cap, not a token cap: max_tokens counts reasoning tokens for reasoning
# models, so it fights the summarizer instead of bounding it.
SUMMARY_MAX_CHARS = 1200

# Hardcoded provider pins: when the orchestrator is one of these base models,
# OpenRouter is asked to try this provider FIRST, falling back to other
# providers automatically if it fails. Matches OpenRouter's provider routing
# ("provider.order" request field), not the deprecated ":provider" suffix.
ORCHESTRATOR_PROVIDER_PINS = {
    "openai/gpt-oss-120b": "cerebras",
    "z-ai/glm-5.3-flash": "together",
}

# Each default is the same four-field row as ``model_config``:
# [usage, provider, model name, thinking process]. Keeping these as lists makes
# defaults use the exact shape the LLMS page posts and the database stores.
MODEL_DEFAULTS: list[list[str | None]] = [
    ['orchestrator', 'openrouter', 'openai/gpt-oss-120b', 'minimal'],
    ['searcher', 'openrouter', 'google/gemini-2.5-flash-lite', None],
    ['stt', 'deepgram', 'nova-2', None],
    ['namer', 'openrouter', 'mistralai/mistral-nemo', None],
]

MODEL_USAGES = tuple(row[0] for row in MODEL_DEFAULTS)
KNOWN_PROVIDERS = ('openrouter', 'opencode-go', 'deepgram')
KNOWN_EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')

def parse_model_config_row(row: list[str | None]) -> tuple[str, str, str, str | None]:
    """Validate and normalize a four-field model config list."""
    if len(row) != 4:
        raise ValueError('A model config row must contain exactly four fields')
    usage, provider, model_name, thinking_process = row
    if usage not in MODEL_USAGES:
        raise ValueError(f'Unknown model usage: {usage}')
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f'Unknown model provider: {provider}')
    if not model_name or not model_name.strip():
        raise ValueError('Model name cannot be empty')
    if usage == 'stt' and provider == 'opencode-go':
        raise ValueError('OpenCode Go does not provide a speech transcription model')
    if usage != 'stt' and provider == 'deepgram':
        raise ValueError('Deepgram is only supported for speech transcription')
    if thinking_process not in (None, *KNOWN_EFFORTS):
        raise ValueError(f'Unknown thinking process: {thinking_process}')
    if usage == 'stt':
        thinking_process = None
    return usage, provider, model_name.strip(), thinking_process


def get_orchestrator_provider_pin(model: str) -> str | None:
    """Return the pinned provider for an orchestrator model, if any.

    Only the base model slug matters: any routing/catalog suffix on the
    configured value (e.g. ":exacto") is stripped before lookup, so the pin
    applies no matter how the model id is written.
    """
    base = model.split(":")[0]
    return ORCHESTRATOR_PROVIDER_PINS.get(base)


def get_model_config(db: Session | None = None) -> list[list[str | None]]:
    """Return all model config rows, including provider-qualified defaults.

    Used by the LLMS all-roles listing; single-role call sites use
    ``get_model_config_for`` instead of loading every row.
    """
    if db is not None:
        return _get_model_config(db)
    with SessionLocal() as session:
        return _get_model_config(session)


def _get_model_config(db: Session) -> list[list[str | None]]:
    """Build the all-roles listing from the known usages and stored rows."""
    values = {row.usage: row for row in db.query(ModelConfig).all()}
    result: list[list[str | None]] = []
    for default in MODEL_DEFAULTS:
        row = values.get(default[0])
        if row is None:
            result.append(default.copy())
        else:
            result.append([
                row.usage,
                row.provider,
                row.model_name,
                row.thinking_process,
            ])
    return result


def get_model_config_for(
    usage: str,
    db: Session | None = None,
) -> list[str | None]:
    """Return one role's four-field config row with a direct per-role query.

    Issues a single ``ModelConfig.usage == usage`` query and falls back to the
    matching MODEL_DEFAULTS row when the database has no row for this usage.
    Callers write their own reads through their session via ``db``; runtime
    consumers omit it and a fresh session is opened here.
    """
    if usage not in MODEL_USAGES:
        raise ValueError(f'Unknown model usage: {usage}')
    if db is None:
        with SessionLocal() as session:
            return get_model_config_for(usage, session)
    row = db.query(ModelConfig).where(ModelConfig.usage == usage).first()
    if row is None:
        return next(
            default.copy() for default in MODEL_DEFAULTS if default[0] == usage
        )
    return [row.usage, row.provider, row.model_name, row.thinking_process]


def get_app_setting(key: str, default: str | None = None) -> str | None:
    """Read an unrelated application setting still stored in ``config``."""
    with SessionLocal() as db:
        row = db.query(AppConfig).where(AppConfig.key == key).first()
        return row.value if row is not None else default
