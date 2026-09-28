from ..database import SessionLocal
from ..models import Config

MAX_AGENTIC_ROUNDS = 10

SYSTEM_PROMPT = '''
T.U.K.I. — productivity assistant. Tone: direct, technical, robot.
User: developer. Lang: Spanish/English.
Rules: No raw JSON in responses. No tool calls in visible text. Use $ for LaTeX.
Tool Calls:
-- OpenAI SDK tool calling format.
-- Don't invent id's, get them with the get tools.
-- Use parallel tool calling as much as you can.
-- Don't use it if tool B call depends on tool A result.
-- If you have all the info to respond or you've executed the order, don't use any tool and respond with text
-- You can return text in the tool calling inferences if necessary
-- If the task involves dates, deadlines, or time, call GetCurrentTime first. NEVER INVENT DATES
-- Update tools only change the fields you send. null or absent = leave unchanged. There is NO way to unassign a project from a task/routine.
'''

AUDIO_SYSTEM_PROMPT = SYSTEM_PROMPT + '''
Audio mode: your reply is spoken aloud, never read.
-- Plain text only: no markdown, no LaTeX, no $ math.
-- Spell numbers for speech: "dos punto cero" not "2.0", "raíz de dos" not "sqrt(2)".
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

# Config rows that hold a model id rather than a free-form setting.
MODEL_KEYS = (
    'orchestrator', 'searcher', 'stt',
    'exec_tools', 'final_resp', 'get_data', 'general',
)
# Keys in the same dict that are NOT models, so they must not be split.
NON_MODEL_KEYS = ('stt_provider',)

# Reasoning effort levels OpenRouter accepts. Anything else in a stored value is
# ignored rather than sent, so a corrupted row degrades to the provider default
# instead of failing the call.
KNOWN_EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


def split_model(value: str) -> tuple[str, str | None]:
    """Split a stored "MODEL_ID EFFORT" value into (model_id, effort or None).

    The effort rides inside the model value, one space, for every model and not
    just the orchestrator. The frontend computes it from the OpenRouter catalog
    as the LOWEST effort the model supports: a model whose reasoning is mandatory
    needs a valid value or it fails, and one without reasoning gets 'none' to
    disable it outright. Omitting the effort is therefore not the same as
    sending 'none', and only the frontend knows which one applies.
    """
    # A row can be blank (every field of ModelConfig is optional, and a model
    # is only written when present). Indexing parts[0] blindly would raise and
    # the whole config read would fall back to defaults, so an empty row yields
    # an empty model instead of taking the other roles down with it.
    parts = (value or '').split(maxsplit=1)
    if not parts:
        return '', None
    return parts[0], (parts[1] if len(parts) == 2 else None)


def get_model_effort(model: str, cfg: dict) -> str | None:
    """Return the configured reasoning effort for `model`, or None if it has none.

    None means "not configured", and the provider default then applies, so
    nothing is sent. Effort is never invented here: it always comes from the
    per-model config.
    """
    base = model.split(":")[0]
    for key in MODEL_KEYS:
        if cfg.get(key, '').split(":")[0] == base:
            effort = cfg.get(f'{key}_effort')
            # A stored effort that is not a known level is treated as absent
            # rather than sent: a value the provider rejects would fail the
            # whole call, while sending nothing just falls back to the default.
            if effort in KNOWN_EFFORTS:
                return effort
            return None
    return None


def get_orchestrator_provider_pin(model: str) -> str | None:
    """Return the pinned provider for an orchestrator model, if any.

    Only the base model slug matters: any routing/catalog suffix on the
    configured value (e.g. ":exacto") is stripped before lookup, so the pin
    applies no matter how the model id is written.
    """
    base = model.split(":")[0]
    return ORCHESTRATOR_PROVIDER_PINS.get(base)


def get_model_config() -> dict:
    defaults = {
        'orchestrator': 'openai/gpt-oss-120b',
        'orchestrator_effort': 'none',   # reasoning effort for the orchestrator model
        'searcher': 'google/gemini-2.5-flash-lite',
        'stt': 'nvidia/parakeet-tdt-0.6b-v3',
        'stt_provider': 'openrouter',  # 'openrouter' or 'deepgram'
    }
    try: 
        db = SessionLocal()
        rows = db.query(Config).all()
        values = {row.key: row.value for row in rows}
        # Legacy migration: use the old 'general' (chat) value as orchestrator
        # (a plain model id, no effort).
        if 'orchestrator' not in values and 'general' in values:
            values['orchestrator'] = values['general']
        # Iterate over a snapshot: the loop below may ADD a "<key>_effort" entry
        # to defaults, and mutating a dict while iterating it raises
        # "dictionary changed size during iteration".
        for key in list(defaults):
            # Any model row may carry a composite "MODEL_ID EFFORT"; the effort
            # is split out so <key> stays a bare model id and the effort lands in
            # <key>_effort. Rows written before the composite existed (or a model
            # with no effort saved) keep their default and send nothing.
            if key.endswith('_effort') or key in NON_MODEL_KEYS:
                continue
            if key in values:
                model_id, effort = split_model(values[key])
                defaults[key] = model_id
                if effort is not None:
                    defaults[f'{key}_effort'] = effort
    except Exception as e:
        print(f"[CONFIG] Error reading model config: {e}")
    finally:
        db.close()
    return defaults 