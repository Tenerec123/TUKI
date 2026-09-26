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
Respond only what is asked in the query as short as possible.
If you have not found all data asked, give what you have and say what lacks.
'''

# Hardcoded provider pins: when the orchestrator is one of these base models,
# OpenRouter is asked to try this provider FIRST, falling back to other
# providers automatically if it fails. Matches OpenRouter's provider routing
# ("provider.order" request field), not the deprecated ":provider" suffix.
ORCHESTRATOR_PROVIDER_PINS = {
    "openai/gpt-oss-120b": "cerebras",
    "z-ai/glm-5.3-flash": "together",
}


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
        # The reasoning effort is persisted INSIDE the 'orchestrator' value as a
        # composite "MODEL_ID EFFORT" (single space). Split it: parts[0] is the
        # model id, parts[1] (if present) is the effort. A plain id (legacy rows
        # or no effort saved) keeps the default effort 'none'.
        if 'orchestrator' in values:
            parts = values['orchestrator'].split(maxsplit=1)
            defaults['orchestrator'] = parts[0]
            if len(parts) == 2:
                defaults['orchestrator_effort'] = parts[1]
        for key in defaults:
            # 'orchestrator' was handled above and 'orchestrator_effort' comes
            # only from the composite split (a stale same-named row must not
            # clobber it), so both are skipped here.
            if key in ('orchestrator', 'orchestrator_effort'):
                continue
            if key in values:
                defaults[key] = values[key]
    except Exception as e:
        print(f"[CONFIG] Error reading model config: {e}")
    finally:
        db.close()
    return defaults 